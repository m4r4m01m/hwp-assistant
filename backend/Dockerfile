FROM python:3.14.0-slim
WORKDIR /app
COPY backend/requirements.lock.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
RUN useradd --uid 10001 --create-home app && mkdir -p /data/files && chown -R app:app /data
USER app
CMD ["sh", "-c", "python -m app.migrate && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
