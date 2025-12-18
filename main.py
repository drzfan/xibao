import base64
import os
from contextlib import asynccontextmanager

import pandas as pd
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import APIKeyCookie
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# !!! CHANGE THIS PASSWORD !!!
ADMIN_PASSWORD = "secret_password_123"
SESSION_TOKEN = "valid_session_token"

if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)


# --- LIFESPAN MANAGER (SPEED OPTIMIZATION) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("--> Launching Headless Browser (Keep-Alive)...")
    async with async_playwright() as p:
        # Launch browser once on startup.
        # --no-sandbox is crucial for Docker environments like Render
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        app.state.browser = browser
        yield
        print("--> Closing Browser...")
        await browser.close()


app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# --- AUTH HELPERS ---
def check_auth(request: Request):
    token = request.cookies.get("session_id")
    return token == SESSION_TOKEN


# --- DATA HELPERS ---
def get_image_base64(file_name):
    path = os.path.join(BASE_DIR, file_name)
    if os.path.exists(path):
        with open(path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode("utf-8")
    return None


def load_data():
    uni_path = os.path.join(BASE_DIR, "uni.csv")
    name_path = os.path.join(BASE_DIR, "namelist.csv")

    # Load CSVs, skipping bad lines
    uni_df = pd.read_csv(
        uni_path, encoding="utf-8", on_bad_lines="skip", engine="python"
    )
    name_df = pd.read_csv(
        name_path, encoding="utf-8", on_bad_lines="skip", engine="python"
    )

    # Clean headers
    uni_df.columns = uni_df.columns.str.strip()
    name_df.columns = name_df.columns.str.strip()
    return uni_df, name_df


# --- ROUTES ---


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        response = RedirectResponse(url="/", status_code=303)
        # Cookie lasts 7 days
        response.set_cookie(key="session_id", value=SESSION_TOKEN, max_age=604800)
        return response
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "Incorrect Password"}
    )


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie("session_id")
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not check_auth(request):
        return RedirectResponse(url="/login")

    try:
        uni_df, name_df = load_data()
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "unis": uni_df["English Name"].dropna().unique().tolist(),
                "students": name_df["NAME_CN"].dropna().unique().tolist(),
            },
        )
    except Exception as e:
        return f"Startup Error: {e}"


@app.post("/generate", response_class=HTMLResponse)
async def generate(
    request: Request, student_name: str = Form(...), uni_name: str = Form(...)
):
    if not check_auth(request):
        return RedirectResponse(url="/login")

    uni_df, name_df = load_data()
    s_match = name_df[name_df["NAME_CN"] == student_name]
    u_match = uni_df[uni_df["English Name"] == uni_name]

    if s_match.empty or u_match.empty:
        return "Error: Data not found."

    s, u = s_match.iloc[0], u_match.iloc[0]

    # --- 1. PREPARE SVG CONTENT ---
    with open(os.path.join(BASE_DIR, "xibaov1.svg"), "r", encoding="utf-8") as f:
        svg_content = f.read()

    # Inject Background Image (prevents broken image icon)
    bg_b64 = get_image_base64("xibaobackground.jpg")
    if bg_b64:
        svg_content = svg_content.replace(
            "xibaobackground.jpg", f"data:image/jpeg;base64,{bg_b64}"
        )

    # --- 2. SMART TEXT SCALING LOGIC ---
    uni_en_str = str(u["English Name"])
    uni_cn_str = str(u["Chinese Name"])

    # Heuristic: If English name is > 25 chars, force squash to 1600px
    if len(uni_en_str) > 25:
        en_attr = 'textLength="1600" lengthAdjust="spacingAndGlyphs"'
    else:
        en_attr = ""  # Natural width

    # Heuristic: If Chinese name is > 15 chars, force squash to 1600px
    if len(uni_cn_str) > 15:
        cn_attr = 'textLength="1600" lengthAdjust="spacingAndGlyphs"'
    else:
        cn_attr = ""  # Natural width

    # --- 3. REPLACE PLACEHOLDERS ---
    # Inject attributes first
    svg_content = svg_content.replace("{{UNI_EN_ATTR}}", en_attr)
    svg_content = svg_content.replace("{{UNI_CN_ATTR}}", cn_attr)

    # Inject text content
    svg_content = svg_content.replace("{{UNI_CN}}", uni_cn_str)
    svg_content = svg_content.replace("{{UNI_EN}}", uni_en_str)
    svg_content = svg_content.replace("{{NAME_CN}}", str(s["NAME_CN"]))
    svg_content = svg_content.replace("{{NAME_EN}}", f"{s['NAME_EN']} {s['FAM_NAME']}")

    output_filename = f"cert_{s['ID']}.png"
    output_path = os.path.join(STATIC_DIR, output_filename)

    # --- 4. FAST GENERATION WITH LARGE VIEWPORT ---
    browser = request.app.state.browser

    # FIX: We set height to 3000 to accommodate your 2926px SVG without scrolling
    context = await browser.new_context(
        viewport={"width": 1920, "height": 3000}, device_scale_factor=2.0
    )
    page = await context.new_page()

    await page.set_content(svg_content)

    # Wait for background image to fully load/render
    await page.wait_for_timeout(500)

    svg_element = await page.query_selector("svg")
    if svg_element:
        # Timeout set to 60s to avoid premature cancellation
        await svg_element.screenshot(
            path=output_path, type="png", omit_background=True, timeout=60000
        )

    await page.close()
    await context.close()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "image_url": f"/static/{output_filename}",
            "unis": uni_df["English Name"].tolist(),
            "students": name_df["NAME_CN"].tolist(),
        },
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
