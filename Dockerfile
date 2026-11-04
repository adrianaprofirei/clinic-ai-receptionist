# Clinica Zâmbet – AI receptionist demo
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATABASE_URL=sqlite:////app/data/clinic.db

WORKDIR /app

RUN useradd --create-home --uid 10001 app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
RUN mkdir -p /app/data && chown app:app /app/data

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status != 200)"

# One worker on purpose: SQLite, the in-memory rate limits and the live-update
# event bus all live in this process. --proxy-headers reads the client IP and
# scheme from nginx (the port is only published on 127.0.0.1).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
