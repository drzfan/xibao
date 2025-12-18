# We use the official Playwright image which has Python + Browsers pre-installed
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

# Copy dependency list
COPY requirements.txt .

# Install dependencies (no need to install playwright browsers again, they are in the image)
RUN pip install --no-cache-dir -r requirements.txt

# Copy all your code (main.py, svg, csvs, images)
COPY . .

# Start the server on port 10000 (Render's default)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
