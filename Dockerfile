FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NFC_ENV=production \
    NFC_DB_PATH=/data/nfc.db

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py .

# The volume is mounted here at runtime; creating it keeps local `docker run` working too.
RUN mkdir -p /data

EXPOSE 8080

# --proxy-headers so the app sees https and the real client IP through Fly's edge,
# which is what makes rate limiting and generated links correct.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
