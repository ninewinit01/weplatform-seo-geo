FROM python:3.12-slim

WORKDIR /srv
ENV PYTHONUNBUFFERED=1 DB_PATH=/srv/data/app.db PORT=8000

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN mkdir -p /srv/data

EXPOSE 8000
# Render는 PORT 환경변수를 지정합니다
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
