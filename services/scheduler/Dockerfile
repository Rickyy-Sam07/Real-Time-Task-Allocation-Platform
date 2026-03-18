FROM python:3.11-slim

WORKDIR /app

COPY services/scheduler/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/shared /app/services/shared
COPY services/scheduler /app/services/scheduler

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "services.scheduler.main"]
