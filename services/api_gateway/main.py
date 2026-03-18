from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from services.shared import (
    EventEnvelope,
    TaskAssignedPayload,
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
event_consumer: AIOKafkaConsumer | None = None
event_consumer_task: asyncio.Task[None] | None = None
db_pool: asyncpg.Pool | None = None
seen_event_ids: set[UUID] = set()
seen_event_order: deque[UUID] = deque()
MAX_SEEN_EVENTS = int(os.getenv("API_MAX_SEEN_EVENTS", "5000"))
API_READ_MODEL_CONSUMER = "api_gateway_read_model"
DASHBOARD_HTML_PATH = Path(__file__).with_name("dashboard.html")

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


def _postgres_dsn() -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "task_alloc")
    user = os.getenv("POSTGRES_USER", "task_user")
    pwd = os.getenv("POSTGRES_PASSWORD", "task_pass")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


def _ensure_pool() -> asyncpg.Pool:
    if db_pool is None:
        raise RuntimeError("Database pool is not initialized")
    return db_pool


async def upsert_task_created(payload: TaskCreatedPayload) -> None:
    pool = _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tasks (
                id, priority, estimated_duration_sec, location_zone, skills_required, status, retry_count, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, 0, NOW(), NOW())
            ON CONFLICT (id)
            DO UPDATE SET
                priority = EXCLUDED.priority,
                estimated_duration_sec = EXCLUDED.estimated_duration_sec,
                location_zone = EXCLUDED.location_zone,
                skills_required = EXCLUDED.skills_required
            """,
            payload.task_id,
            payload.priority,
            payload.estimated_duration_sec,
            payload.location_zone,
            json.dumps(payload.skills_required),
            TaskStatus.PENDING.value,
        )


async def upsert_worker_status(payload: WorkerStatusPayload, name: str | None = None) -> None:
    pool = _ensure_pool()
    worker_name = name if name is not None else f"worker-{str(payload.worker_id)[:8]}"
    heartbeat_at = payload.heartbeat_at.replace(tzinfo=None)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO workers (id, name, status, location_zone, skills, last_heartbeat)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            ON CONFLICT (id)
            DO UPDATE SET
                status = EXCLUDED.status,
                location_zone = COALESCE(EXCLUDED.location_zone, workers.location_zone),
                skills = EXCLUDED.skills,
                last_heartbeat = EXCLUDED.last_heartbeat
            """,
            payload.worker_id,
            worker_name,
            payload.status.value,
            payload.location_zone,
            json.dumps(payload.skills),
            heartbeat_at,
        )


async def persist_assignment(payload: TaskAssignedPayload) -> None:
    pool = _ensure_pool()
    assigned_at = payload.assigned_at.replace(tzinfo=None)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO assignments (id, task_id, worker_id, status, assigned_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (id) DO NOTHING
                """,
                payload.assignment_id,
                payload.task_id,
                payload.worker_id,
                TaskStatus.ASSIGNED.value,
                assigned_at,
            )
            await conn.execute(
                """
                UPDATE tasks
                SET status = $2, updated_at = NOW()
                WHERE id = $1
                """,
                payload.task_id,
                TaskStatus.ASSIGNED.value,
            )


async def persist_task_update(payload: TaskUpdatedPayload) -> None:
    pool = _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE tasks
            SET status = $2, updated_at = NOW()
            WHERE id = $1
            """,
            payload.task_id,
            payload.status.value,
        )


async def load_read_models() -> None:
    pool = _ensure_pool()
    async with pool.acquire() as conn:
        task_rows = await conn.fetch(
            """
            SELECT id, priority, status, created_at, updated_at
            FROM tasks
            ORDER BY created_at DESC
            """
        )
        worker_rows = await conn.fetch(
            """
            SELECT id, name, status, location_zone, skills, last_heartbeat
            FROM workers
            ORDER BY created_at DESC
            """
        )

    tasks.clear()
    for row in task_rows:
        tasks[str(row["id"])] = {
            "task_id": str(row["id"]),
            "priority": row["priority"],
            "status": row["status"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }

    workers.clear()
    for row in worker_rows:
        workers[str(row["id"])] = {
            "worker_id": str(row["id"]),
            "name": row["name"],
            "status": row["status"],
            "location_zone": row["location_zone"],
            "skills": row["skills"] or [],
            "last_heartbeat": row["last_heartbeat"].isoformat() if row["last_heartbeat"] else None,
        }


async def ensure_processed_events_table() -> None:
    pool = _ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (
              consumer_name VARCHAR(100) NOT NULL,
              event_id UUID NOT NULL,
              processed_at TIMESTAMP NOT NULL DEFAULT NOW(),
              PRIMARY KEY (consumer_name, event_id)
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_processed_events_consumer
            ON processed_events(consumer_name)
            """
        )


async def claim_event(envelope: EventEnvelope) -> bool:
    pool = _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO event_log (event_id, topic, payload)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (event_id) DO NOTHING
                """,
                envelope.event_id,
                envelope.topic.value,
                json.dumps(envelope.payload),
            )
            result = await conn.fetchval(
                """
                INSERT INTO processed_events (consumer_name, event_id)
                VALUES ($1, $2)
                ON CONFLICT (consumer_name, event_id) DO NOTHING
                RETURNING event_id
                """,
                API_READ_MODEL_CONSUMER,
                envelope.event_id,
            )
    return result is not None


async def publish_event(topic: Topic, payload: dict[str, Any]) -> None:
    if producer is None:
        raise RuntimeError("Kafka producer is not initialized")

    envelope = EventEnvelope(topic=topic, payload=payload)
    await producer.send_and_wait(topic.value, envelope.model_dump_json().encode("utf-8"))
    await hub.broadcast({"topic": topic.value, "payload": payload})


def is_duplicate_event(event_id: UUID) -> bool:
    if event_id in seen_event_ids:
        return True

    seen_event_ids.add(event_id)
    seen_event_order.append(event_id)

    if len(seen_event_order) > MAX_SEEN_EVENTS:
        oldest = seen_event_order.popleft()
        seen_event_ids.discard(oldest)

    return False


async def consume_events_loop() -> None:
    global event_consumer
    print("[api_gateway] starting read-model consumer")
    event_consumer = AIOKafkaConsumer(
        Topic.TASK_UPDATED.value,
        Topic.TASK_ASSIGNED.value,
        Topic.WORKER_STATUS.value,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id="api-gateway-read-model",
        auto_offset_reset="earliest",
    )

    for attempt in range(1, 16):
        try:
            await event_consumer.start()
            break
        except Exception as exc:
            if attempt == 15:
                raise
            print(f"[api_gateway] waiting for kafka consumer (attempt {attempt}/15): {exc}")
            await asyncio.sleep(2)

    try:
        print("[api_gateway] read-model consumer started")
        async for msg in event_consumer:
            envelope = EventEnvelope.model_validate_json(msg.value.decode("utf-8"))
            if is_duplicate_event(envelope.event_id):
                continue
            if not await claim_event(envelope):
                continue

            if envelope.topic == Topic.TASK_UPDATED:
                payload = TaskUpdatedPayload.model_validate(envelope.payload)
                await persist_task_update(payload)
                existing = tasks.get(str(payload.task_id), {"task_id": str(payload.task_id)})
                existing["status"] = payload.status.value
                existing["worker_id"] = str(payload.worker_id) if payload.worker_id else None
                existing["updated_at"] = datetime.now(timezone.utc).isoformat()
                tasks[str(payload.task_id)] = existing

            elif envelope.topic == Topic.TASK_ASSIGNED:
                payload = TaskAssignedPayload.model_validate(envelope.payload)
                await persist_assignment(payload)
                existing = tasks.get(str(payload.task_id), {"task_id": str(payload.task_id)})
                existing["status"] = TaskStatus.ASSIGNED.value
                existing["worker_id"] = str(payload.worker_id)
                existing["updated_at"] = datetime.now(timezone.utc).isoformat()
                tasks[str(payload.task_id)] = existing

            elif envelope.topic == Topic.WORKER_STATUS:
                payload = WorkerStatusPayload.model_validate(envelope.payload)
                await upsert_worker_status(payload)
                existing = workers.get(str(payload.worker_id), {"worker_id": str(payload.worker_id)})
                if "name" not in existing:
                    existing["name"] = f"worker-{str(payload.worker_id)[:8]}"
                existing["status"] = payload.status.value
                existing["location_zone"] = payload.location_zone
                existing["skills"] = payload.skills
                existing["last_heartbeat"] = payload.heartbeat_at.isoformat()
                workers[str(payload.worker_id)] = existing

            await hub.broadcast({"topic": envelope.topic.value, "payload": envelope.payload})
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print(f"[api_gateway] read-model consumer crashed: {exc}")
        raise
    finally:
        if event_consumer is not None:
            await event_consumer.stop()


def _on_consumer_task_done(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        print(f"[api_gateway] read-model consumer task failed: {exc}")


def _task_status_counts() -> dict[str, int]:
    counts = {
        TaskStatus.PENDING.value: 0,
        TaskStatus.ASSIGNED.value: 0,
        TaskStatus.IN_PROGRESS.value: 0,
        TaskStatus.COMPLETED.value: 0,
        TaskStatus.FAILED.value: 0,
    }
    for task in tasks.values():
        status = task.get("status")
        if isinstance(status, str) and status in counts:
            counts[status] += 1
    return counts


def _worker_status_counts() -> dict[str, int]:
    counts = {
        WorkerStatus.AVAILABLE.value: 0,
        WorkerStatus.BUSY.value: 0,
        WorkerStatus.OFFLINE.value: 0,
    }
    for worker in workers.values():
        status = worker.get("status")
        if isinstance(status, str) and status in counts:
            counts[status] += 1
    return counts


@app.on_event("startup")
async def startup_event() -> None:
    global producer, db_pool, event_consumer_task

    for attempt in range(1, 16):
        try:
            db_pool = await asyncpg.create_pool(dsn=_postgres_dsn(), min_size=1, max_size=5)
            await ensure_processed_events_table()
            await load_read_models()
            break
        except Exception as exc:
            if attempt == 15:
                raise
            print(f"[api_gateway] waiting for postgres (attempt {attempt}/15): {exc}")
            await asyncio.sleep(2)

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

    event_consumer_task = asyncio.create_task(consume_events_loop())
    event_consumer_task.add_done_callback(_on_consumer_task_done)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global event_consumer_task, db_pool

    if event_consumer_task is not None:
        event_consumer_task.cancel()
        try:
            await event_consumer_task
        except asyncio.CancelledError:
            pass

    if producer is not None:
        await producer.stop()

    if db_pool is not None:
        await db_pool.close()
        db_pool = None


@app.post("/task", status_code=202)
async def create_task(request: TaskCreateRequest) -> dict[str, str]:
    task_id = uuid4()
    payload = TaskCreatedPayload(task_id=task_id, **request.model_dump())
    await upsert_task_created(payload)
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
    payload = WorkerStatusPayload(
        worker_id=worker_id,
        status=WorkerStatus.AVAILABLE,
        location_zone=request.location_zone,
        skills=request.skills,
    )
    await upsert_worker_status(payload, name=request.name)

    workers[str(worker_id)] = {
        "worker_id": str(worker_id),
        "name": request.name,
        "status": WorkerStatus.AVAILABLE.value,
        "location_zone": request.location_zone,
        "skills": request.skills,
    }

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
    await upsert_worker_status(payload, name=worker.get("name"))
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
    await persist_task_update(payload)
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


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page() -> HTMLResponse:
    html = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/dashboard/health")
async def dashboard_health() -> dict[str, Any]:
    task_counts = _task_status_counts()
    worker_counts = _worker_status_counts()
    in_flight = task_counts[TaskStatus.ASSIGNED.value] + task_counts[TaskStatus.IN_PROGRESS.value]
    return {
        "status": "ok",
        "service": "api_gateway_dashboard",
        "kafka": KAFKA_BOOTSTRAP_SERVERS,
        "websocket_connections": len(hub.connections),
        "read_model": {
            "tasks_total": len(tasks),
            "workers_total": len(workers),
            "tasks_by_status": task_counts,
            "workers_by_status": worker_counts,
            "in_flight_tasks": in_flight,
        },
        "dedupe_cache": {
            "seen_event_ids": len(seen_event_ids),
            "max_seen_events": MAX_SEEN_EVENTS,
        },
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "api_gateway", "kafka": KAFKA_BOOTSTRAP_SERVERS}
