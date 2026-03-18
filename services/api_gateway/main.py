from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from services.shared import (
    EventEnvelope,
    TaskCreatedPayload,
    TaskStatus,
    TaskUpdatedPayload,
    Topic,
    WorkerStatus,
    WorkerStatusPayload,
)


KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

app = FastAPI(title="Real-Time Task API Gateway")
producer: AIOKafkaProducer | None = None

# In-memory read model for the first implementation slice.
tasks: dict[str, dict[str, Any]] = {}
workers: dict[str, dict[str, Any]] = {}


class WebSocketHub:
    def __init__(self) -> None:
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        if not self.connections:
            return
        dead: list[WebSocket] = []
        for connection in self.connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for connection in dead:
            self.disconnect(connection)


hub = WebSocketHub()


class TaskCreateRequest(BaseModel):
    priority: int = Field(ge=1, le=5)
    estimated_duration_sec: int = Field(gt=0)
    location_zone: str | None = None
    skills_required: list[str] = Field(default_factory=list)
    max_retries: int = 3


class WorkerRegisterRequest(BaseModel):
    name: str
    location_zone: str | None = None
    skills: list[str] = Field(default_factory=list)


class WorkerUpdateRequest(BaseModel):
    worker_id: UUID
    status: WorkerStatus
    location_zone: str | None = None
    skills: list[str] = Field(default_factory=list)


class TaskUpdateRequest(BaseModel):
    task_id: UUID
    status: TaskStatus
    worker_id: UUID | None = None
    failure_reason: str | None = None


async def publish_event(topic: Topic, payload: dict[str, Any]) -> None:
    if producer is None:
        raise RuntimeError("Kafka producer is not initialized")

    envelope = EventEnvelope(topic=topic, payload=payload)
    await producer.send_and_wait(topic.value, envelope.model_dump_json().encode("utf-8"))
    await hub.broadcast({"topic": topic.value, "payload": payload})


@app.on_event("startup")
async def startup_event() -> None:
    global producer
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)
    for attempt in range(1, 16):
        try:
            await producer.start()
            break
        except Exception as exc:
            if attempt == 15:
                raise
            print(f"[api_gateway] waiting for kafka (attempt {attempt}/15): {exc}")
            await asyncio.sleep(2)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    if producer is not None:
        await producer.stop()


@app.post("/task", status_code=202)
async def create_task(request: TaskCreateRequest) -> dict[str, str]:
    task_id = uuid4()
    payload = TaskCreatedPayload(task_id=task_id, **request.model_dump())
    tasks[str(task_id)] = {
        "task_id": str(task_id),
        "priority": request.priority,
        "status": TaskStatus.PENDING.value,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await publish_event(Topic.TASK_CREATED, payload.model_dump(mode="json"))
    return {"task_id": str(task_id), "message": "accepted"}


@app.get("/tasks")
async def get_tasks(status: TaskStatus | None = None) -> list[dict[str, Any]]:
    values = list(tasks.values())
    if status is None:
        return values
    return [task for task in values if task.get("status") == status.value]


@app.post("/worker/register", status_code=202)
async def register_worker(request: WorkerRegisterRequest) -> dict[str, str]:
    worker_id = uuid4()
    workers[str(worker_id)] = {
        "worker_id": str(worker_id),
        "name": request.name,
        "status": WorkerStatus.AVAILABLE.value,
        "location_zone": request.location_zone,
        "skills": request.skills,
    }

    payload = WorkerStatusPayload(
        worker_id=worker_id,
        status=WorkerStatus.AVAILABLE,
        location_zone=request.location_zone,
        skills=request.skills,
    )
    await publish_event(Topic.WORKER_STATUS, payload.model_dump(mode="json"))
    return {"worker_id": str(worker_id), "message": "registered"}


@app.post("/worker/update", status_code=202)
async def update_worker(request: WorkerUpdateRequest) -> dict[str, str]:
    worker = workers.get(str(request.worker_id))
    if worker is None:
        raise HTTPException(status_code=404, detail="worker not found")

    worker["status"] = request.status.value
    worker["location_zone"] = request.location_zone
    worker["skills"] = request.skills

    payload = WorkerStatusPayload(**request.model_dump())
    await publish_event(Topic.WORKER_STATUS, payload.model_dump(mode="json"))
    return {"message": "accepted"}


@app.post("/task/update", status_code=202)
async def update_task(request: TaskUpdateRequest) -> dict[str, str]:
    existing = tasks.get(str(request.task_id))
    if existing is None:
        raise HTTPException(status_code=404, detail="task not found")

    existing["status"] = request.status.value
    existing["worker_id"] = str(request.worker_id) if request.worker_id else None
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()

    payload = TaskUpdatedPayload(**request.model_dump())
    await publish_event(Topic.TASK_UPDATED, payload.model_dump(mode="json"))
    return {"message": "accepted"}


@app.websocket("/ws/dashboard")
async def dashboard_socket(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    try:
        await websocket.send_json(
            {
                "topic": "snapshot",
                "payload": {
                    "tasks": list(tasks.values()),
                    "workers": list(workers.values()),
                },
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(websocket)
    except Exception:
        hub.disconnect(websocket)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "api_gateway", "kafka": KAFKA_BOOTSTRAP_SERVERS}
