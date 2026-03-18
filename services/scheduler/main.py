from __future__ import annotations

import asyncio
import heapq
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from services.shared import EventEnvelope, TaskAssignedPayload, Topic, WorkerStatus


KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


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
        self.pending: list[tuple[int, float, dict[str, Any]]] = []
        self.consumer: AIOKafkaConsumer | None = None
        self.producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        self.consumer = AIOKafkaConsumer(
            Topic.TASK_CREATED.value,
            Topic.WORKER_STATUS.value,
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

    async def run(self) -> None:
        assert self.consumer is not None
        async for msg in self.consumer:
            raw = msg.value.decode("utf-8")
            envelope = EventEnvelope.model_validate_json(raw)

            if envelope.topic == Topic.WORKER_STATUS:
                await self._on_worker_status(envelope.payload)
            elif envelope.topic == Topic.TASK_CREATED:
                await self._on_task_created(envelope.payload)

            await self.assign_pending_tasks()

    async def _on_task_created(self, payload: dict[str, Any]) -> None:
        priority = int(payload["priority"])
        heapq.heappush(self.pending, (-priority, datetime.now(timezone.utc).timestamp(), payload))

    async def _on_worker_status(self, payload: dict[str, Any]) -> None:
        worker_id = UUID(payload["worker_id"])
        self.workers[worker_id] = WorkerState(
            worker_id=worker_id,
            status=WorkerStatus(payload["status"]),
            location_zone=payload.get("location_zone"),
            skills=payload.get("skills", []),
            active_task_id=UUID(payload["active_task_id"]) if payload.get("active_task_id") else None,
            heartbeat_at=datetime.now(timezone.utc),
        )

    async def assign_pending_tasks(self) -> None:
        if not self.pending:
            return

        requeue: list[tuple[int, float, dict[str, Any]]] = []
        while self.pending:
            item = heapq.heappop(self.pending)
            task_payload = item[2]
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
