FROM python:3.11-slim

WORKDIR /app

COPY services/dlq_processor/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/shared /app/services/shared
COPY services/dlq_processor /app/services/dlq_processor

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "services.dlq_processor.main"]
