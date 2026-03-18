FROM python:3.11-slim

WORKDIR /app

COPY services/worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/shared /app/services/shared
COPY services/worker /app/services/worker

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "services.worker.main"]
