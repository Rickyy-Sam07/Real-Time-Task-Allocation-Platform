from __future__ import annotations

import asyncio
import heapq
import json
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from services.shared import (
    EventEnvelope,
    TaskAssignedPayload,
    TaskDLQPayload,
    TaskStatus,
    TaskUpdatedPayload,
    Topic,
    WorkerStatus,
    WorkerStatusPayload,
)


KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "task_alloc")
POSTGRES_USER = os.getenv("POSTGRES_USER", "task_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "task_pass")
SCHEDULER_CONSUMER_NAME = "scheduler"


def postgres_dsn() -> str:
    return f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"


@dataclass
class WorkerState:
    worker_id: UUID
    status: WorkerStatus
    location_zone: str | None
    skills: list[str]
    active_task_id: UUID | None
    heartbeat_at: datetime


class Scheduler:
    def __init__(self) -> None:
        self.workers: dict[UUID, WorkerState] = {}
        self.pending: list[tuple[int, float, float, dict[str, Any]]] = []
        self.task_catalog: dict[UUID, dict[str, Any]] = {}
        self.task_retries: dict[UUID, int] = {}
        self._seen_event_ids: set[UUID] = set()
        self._seen_event_order: deque[UUID] = deque()
        self._max_seen_events = int(os.getenv("SCHEDULER_MAX_SEEN_EVENTS", "5000"))
        self.heartbeat_timeout_sec = int(os.getenv("WORKER_HEARTBEAT_TIMEOUT_SEC", "15"))
        self.db_pool: asyncpg.Pool | None = None
        self.consumer: AIOKafkaConsumer | None = None
        self.producer: AIOKafkaProducer | None = None

    async def _ensure_tables(self) -> None:
        assert self.db_pool is not None
        async with self.db_pool.acquire() as conn:
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

    async def _claim_event(self, envelope: EventEnvelope) -> bool:
        assert self.db_pool is not None
        async with self.db_pool.acquire() as conn:
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
                    SCHEDULER_CONSUMER_NAME,
                    envelope.event_id,
                )
        return result is not None

    async def start(self) -> None:
        for attempt in range(1, 16):
            try:
                self.db_pool = await asyncpg.create_pool(dsn=postgres_dsn(), min_size=1, max_size=5)
                await self._ensure_tables()
                break
            except Exception as exc:
                if attempt == 15:
                    raise
                print(f"[scheduler] waiting for postgres (attempt {attempt}/15): {exc}")
                await asyncio.sleep(2)

        self.consumer = AIOKafkaConsumer(
            Topic.TASK_CREATED.value,
            Topic.WORKER_STATUS.value,
            Topic.TASK_UPDATED.value,
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            group_id="scheduler-group",
            auto_offset_reset="earliest",
        )
        self.producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)

        for attempt in range(1, 16):
            try:
                await self.consumer.start()
                await self.producer.start()
                break
            except Exception as exc:
                if attempt == 15:
                    raise
                print(f"[scheduler] waiting for kafka (attempt {attempt}/15): {exc}")
                await asyncio.sleep(2)

        print("[scheduler] started")
        try:
            await self.run()
        finally:
            if self.consumer is not None:
                await self.consumer.stop()
            if self.producer is not None:
                await self.producer.stop()
            if self.db_pool is not None:
                await self.db_pool.close()
                self.db_pool = None

    async def run(self) -> None:
        assert self.consumer is not None
        async for msg in self.consumer:
            raw = msg.value.decode("utf-8")
            envelope = EventEnvelope.model_validate_json(raw)

            if self._is_duplicate_event(envelope.event_id):
                continue

            if not await self._claim_event(envelope):
                continue

            if envelope.topic == Topic.WORKER_STATUS:
                await self._on_worker_status(envelope.payload)
            elif envelope.topic == Topic.TASK_CREATED:
                await self._on_task_created(envelope.payload)
            elif envelope.topic == Topic.TASK_UPDATED:
                await self._on_task_updated(envelope.payload)

            self._refresh_worker_liveness()
            await self.assign_pending_tasks()

    async def _on_task_created(self, payload: dict[str, Any]) -> None:
        task_id = UUID(payload["task_id"])
        self.task_catalog[task_id] = payload
        priority = int(payload["priority"])
        now_ts = datetime.now(timezone.utc).timestamp()
        heapq.heappush(self.pending, (-priority, now_ts, now_ts, payload))

    async def _on_worker_status(self, payload: dict[str, Any]) -> None:
        status_payload = WorkerStatusPayload.model_validate(payload)
        worker_id = status_payload.worker_id
        self.workers[worker_id] = WorkerState(
            worker_id=worker_id,
            status=status_payload.status,
            location_zone=status_payload.location_zone,
            skills=status_payload.skills,
            active_task_id=status_payload.active_task_id,
            heartbeat_at=status_payload.heartbeat_at,
        )

    async def _on_task_updated(self, payload: dict[str, Any]) -> None:
        task_update = TaskUpdatedPayload.model_validate(payload)
        task_id = task_update.task_id

        if task_update.status == TaskStatus.COMPLETED:
            self.task_catalog.pop(task_id, None)
            self.task_retries.pop(task_id, None)
            return

        if task_update.status != TaskStatus.FAILED:
            return

        task_payload = self.task_catalog.get(task_id)
        if task_payload is None:
            return

        retry_count = self.task_retries.get(task_id, 0) + 1
        self.task_retries[task_id] = retry_count
        max_retries = int(task_payload.get("max_retries", 3))

        if retry_count <= max_retries:
            priority = int(task_payload.get("priority", 1))
            now_ts = datetime.now(timezone.utc).timestamp()
            delay_sec = min(30, 2 ** (retry_count - 1))
            available_at = now_ts + delay_sec
            heapq.heappush(self.pending, (-priority, available_at, now_ts, task_payload))
            print(f"[scheduler] retry scheduled for {task_id} attempt={retry_count} in {delay_sec}s")
            return

        dlq = TaskDLQPayload(
            task_id=task_id,
            reason=task_update.failure_reason or "max_retries_exhausted",
            retry_count=retry_count,
            max_retries=max_retries,
            worker_id=task_update.worker_id,
        )
        event = EventEnvelope(topic=Topic.TASK_DLQ, payload=dlq.model_dump(mode="json"))
        assert self.producer is not None
        await self.producer.send_and_wait(Topic.TASK_DLQ.value, event.model_dump_json().encode("utf-8"))
        print(f"[scheduler] routed task {task_id} to DLQ after {retry_count} attempts")

    async def assign_pending_tasks(self) -> None:
        if not self.pending:
            return

        requeue: list[tuple[int, float, float, dict[str, Any]]] = []
        while self.pending:
            item = heapq.heappop(self.pending)
            available_at = item[1]
            now_ts = datetime.now(timezone.utc).timestamp()
            if available_at > now_ts:
                requeue.append(item)
                continue

            task_payload = item[3]
            worker = self.select_worker(task_payload)
            if worker is None:
                requeue.append(item)
                continue
            await self._assign(worker, task_payload)

        for entry in requeue:
            heapq.heappush(self.pending, entry)

    def select_worker(self, task_payload: dict[str, Any]) -> WorkerState | None:
        required_skills = set(task_payload.get("skills_required", []))
        zone = task_payload.get("location_zone")

        candidates: list[WorkerState] = []
        for worker in self.workers.values():
            if worker.status != WorkerStatus.AVAILABLE:
                continue
            if required_skills and not required_skills.issubset(set(worker.skills)):
                continue
            candidates.append(worker)

        if not candidates:
            return None

        zone_candidates = [worker for worker in candidates if zone and worker.location_zone == zone]
        if zone_candidates:
            return zone_candidates[0]
        return candidates[0]

    def _refresh_worker_liveness(self) -> None:
        now = datetime.now(timezone.utc)
        for worker in self.workers.values():
            if worker.status == WorkerStatus.OFFLINE:
                continue

            elapsed = (now - worker.heartbeat_at).total_seconds()
            if elapsed <= self.heartbeat_timeout_sec:
                continue

            worker.status = WorkerStatus.OFFLINE
            print(f"[scheduler] worker offline due to heartbeat timeout: {worker.worker_id}")

            if worker.active_task_id is None:
                continue

            task_payload = self.task_catalog.get(worker.active_task_id)
            if task_payload is None or self._is_task_pending(worker.active_task_id):
                continue

            priority = int(task_payload.get("priority", 1))
            now_ts = now.timestamp()
            heapq.heappush(self.pending, (-priority, now_ts, now_ts, task_payload))
            print(f"[scheduler] requeued task due to worker timeout: {worker.active_task_id}")

    def _is_task_pending(self, task_id: UUID) -> bool:
        for _, _, _, payload in self.pending:
            if payload.get("task_id") == str(task_id):
                return True
        return False

    def _is_duplicate_event(self, event_id: UUID) -> bool:
        if event_id in self._seen_event_ids:
            return True

        self._seen_event_ids.add(event_id)
        self._seen_event_order.append(event_id)

        if len(self._seen_event_order) > self._max_seen_events:
            oldest = self._seen_event_order.popleft()
            self._seen_event_ids.discard(oldest)

        return False

    async def _assign(self, worker: WorkerState, task_payload: dict[str, Any]) -> None:
        assert self.producer is not None
        assignment_payload = TaskAssignedPayload(
            assignment_id=uuid4(),
            task_id=UUID(task_payload["task_id"]),
            worker_id=worker.worker_id,
        )
        event = EventEnvelope(topic=Topic.TASK_ASSIGNED, payload=assignment_payload.model_dump(mode="json"))
        await self.producer.send_and_wait(Topic.TASK_ASSIGNED.value, event.model_dump_json().encode("utf-8"))
        worker.status = WorkerStatus.BUSY
        worker.active_task_id = assignment_payload.task_id
        print(
            "[scheduler] assigned",
            assignment_payload.task_id,
            "to",
            assignment_payload.worker_id,
        )


if __name__ == "__main__":
    asyncio.run(Scheduler().start())
