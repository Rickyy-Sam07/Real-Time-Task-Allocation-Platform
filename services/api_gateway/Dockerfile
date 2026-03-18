FROM python:3.11-slim

WORKDIR /app

COPY services/api_gateway/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/shared /app/services/shared
COPY services/api_gateway /app/services/api_gateway

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["uvicorn", "services.api_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
