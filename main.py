import asyncio
import base64
import os

import pandas as pd
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from playwright.async_api import async_playwright

app = FastAPI()

# --- Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# Ensure static folder exists
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# --- Helper Functions ---
def get_image_base64(file_name):
    """Encodes local images to Base64 so Playwright can render them without path issues."""
    path = os.path.join(BASE_DIR, file_name)
    if os.path.exists(path):
        with open(path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode("utf-8")
    return None


def load_data():
    """Loads CSVs, skipping bad lines (like the extra comma error)."""
    uni_path = os.path.join(BASE_DIR, "uni.csv")
    name_path = os.path.join(BASE_DIR, "namelist.csv")

    # on_bad_lines='skip' prevents crashes if a row has too many commas
    uni_df = pd.read_csv(
        uni_path, encoding="utf-8", on_bad_lines="skip", engine="python"
    )
    name_df = pd.read_csv(
        name_path, encoding="utf-8", on_bad_lines="skip", engine="python"
    )

    # Clean whitespace from headers
    uni_df.columns = uni_df.columns.str.strip()
    name_df.columns = name_df.columns.str.strip()

    return uni_df, name_df


# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
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
        return f"<h1>Startup Error</h1><p>{str(e)}</p>"


@app.post("/generate", response_class=HTMLResponse)
async def generate(
    request: Request, student_name: str = Form(...), uni_name: str = Form(...)
):
    uni_df, name_df = load_data()

    # Find the specific rows
    s_match = name_df[name_df["NAME_CN"] == student_name]
    u_match = uni_df[uni_df["English Name"] == uni_name]

    if s_match.empty or u_match.empty:
        return "Error: Student or University not found in CSV."

    s = s_match.iloc[0]
    u = u_match.iloc[0]

    # Load SVG Template
    svg_path = os.path.join(BASE_DIR, "xibaov1.svg")
    with open(svg_path, "r", encoding="utf-8") as f:
        svg_content = f.read()

    # 1. Inject Background Image (Base64)
    # Ensure your file is named exactly 'xibaobackground.jpg'
    bg_b64 = get_image_base64("xibaobackground.jpg")
    if bg_b64:
        svg_content = svg_content.replace(
            "xibaobackground.jpg", f"data:image/jpeg;base64,{bg_b64}"
        )

    # 2. Replace Text Placeholders
    svg_content = svg_content.replace("{{UNI_CN}}", str(u["Chinese Name"]))
    svg_content = svg_content.replace("{{UNI_EN}}", str(u["English Name"]))
    svg_content = svg_content.replace("{{NAME_CN}}", str(s["NAME_CN"]))
    svg_content = svg_content.replace("{{NAME_EN}}", f"{s['NAME_EN']} {s['FAM_NAME']}")

    # 3. Render SVG to PNG using Playwright
    output_filename = f"cert_{s['ID']}.png"
    output_path = os.path.join(STATIC_DIR, output_filename)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        # Scale 2.0 = High Resolution (Retina)
        context = await browser.new_context(device_scale_factor=2.0)
        page = await context.new_page()

        # Load the SVG content directly into the browser page
        await page.set_content(svg_content)

        # Wait for any fonts/images to settle
        await page.wait_for_timeout(200)

        # Select the <svg> element and take a screenshot
        svg_element = await page.query_selector("svg")
        if svg_element:
            await svg_element.screenshot(
                path=output_path, type="png", omit_background=True
            )

        await browser.close()

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
    # This starts the server locally
    uvicorn.run(app, host="127.0.0.1", port=8000)
