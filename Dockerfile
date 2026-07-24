# Provenance Observatory API. Serves the committed evidence tree read-only.
# The data/ tree is baked in at build time; a fresh nightly commit triggers a
# redeploy (Fly/Render auto-deploy on push). For sub-deploy freshness, run a
# sidecar `git pull` loop and POST /api/reload.
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV OBSERVATORY_RATE_LIMIT=120 OBSERVATORY_RATE_WINDOW=60
EXPOSE 8000

# Honor the platform's $PORT if set (Render), else 8000 (Fly internal_port).
CMD ["sh", "-c", "uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
