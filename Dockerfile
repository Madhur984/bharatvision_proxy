# Use official Python image
FROM python:3.11-slim

# Install system deps required by Playwright and browsers
RUN apt-get update && apt-get install -y \
    wget curl ca-certificates gnupg unzip libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libxshmfence1 libgbm1 libasound2 \
    libpangocairo-1.0-0 libcups2 libdrm2 libx11-6 libxcb1 --no-install-recommends && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Create app dir
WORKDIR /app
COPY . /app

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN python -m playwright install --with-deps

# Expose port
EXPOSE 8000

# Start uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
