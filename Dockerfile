FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright with Chromium for browser automation (optional at runtime)
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium

# Copy application code
COPY . .

# Make entrypoint executable
RUN chmod +x deploy/docker-entrypoint.sh

# Create a non-root user
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

ENTRYPOINT ["deploy/docker-entrypoint.sh"]
CMD ["uvicorn", "teb.main:asgi_app", "--host", "0.0.0.0", "--port", "8000"]
