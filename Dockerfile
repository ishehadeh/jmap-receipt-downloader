FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

# Point the playwright Python package at the browsers already in the base image
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY receipts.py .
COPY rules.yaml .

ENTRYPOINT ["python", "receipts.py"]
