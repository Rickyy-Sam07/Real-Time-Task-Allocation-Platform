from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, timezone
from uuid import UUID, uuid4

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from services.shared import (
    EventEnvelope,
    TaskStatus,
    TaskUpdatedPayload,
    Topic,
    WorkerStatus,
    WorkerStatusPayload,
)


KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
WORKER_ID = UUID(os.getenv("WORKER_ID", str(uuid4())))
WORKER_ZONE = os.getenv("WORKER_ZONE", "zone-a")
WORKER_SKILLS = [item.strip() for item in os.getenv("WORKER_SKILLS", "rescue,medical").split(",") if item.strip()]


class WorkerAgent:
    def __init__(self) -> None:
        self.active_task_id: UUID | None = None
        self.producer: AIOKafkaProducer | None = None
        self.consumer: AIOKafkaConsumer | None = None

    async def start(self) -> None:
        self.producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)
        self.consumer = AIOKafkaConsumer(
            Topic.TASK_ASSIGNED.value,
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            group_id=f"worker-{WORKER_ID}",
            auto_offset_reset="earliest",
        )

        for attempt in range(1, 16):
            try:
                await self.producer.start()
                await self.consumer.start()
                break
            except Exception as exc:
                if attempt == 15:
                    raise
                print(f"[worker:{WORKER_ID}] waiting for kafka (attempt {attempt}/15): {exc}")
                await asyncio.sleep(2)

        print(f"[worker:{WORKER_ID}] started")
        heartbeat_task = asyncio.create_task(self.heartbeat_loop())
        try:
            assert self.consumer is not None
            async for msg in self.consumer:
                envelope = EventEnvelope.model_validate_json(msg.value.decode("utf-8"))
                if envelope.topic != Topic.TASK_ASSIGNED:
                    continue
                await self.handle_assignment(envelope.payload)
        finally:
            heartbeat_task.cancel()
            if self.consumer is not None:
                await self.consumer.stop()
            if self.producer is not None:
                await self.producer.stop()

    async def heartbeat_loop(self) -> None:
        while True:
            assert self.producer is not None
            status = WorkerStatus.BUSY if self.active_task_id else WorkerStatus.AVAILABLE
            payload = WorkerStatusPayload(
                worker_id=WORKER_ID,
                status=status,
                location_zone=WORKER_ZONE,
                skills=WORKER_SKILLS,
                active_task_id=self.active_task_id,
                heartbeat_at=datetime.now(timezone.utc),
            )
            event = EventEnvelope(topic=Topic.WORKER_STATUS, payload=payload.model_dump(mode="json"))
            await self.producer.send_and_wait(Topic.WORKER_STATUS.value, event.model_dump_json().encode("utf-8"))
            await asyncio.sleep(5)

    async def handle_assignment(self, payload: dict) -> None:
        assert self.producer is not None
        if UUID(payload["worker_id"]) != WORKER_ID:
            return

        task_id = UUID(payload["task_id"])
        self.active_task_id = task_id

        started = TaskUpdatedPayload(task_id=task_id, status=TaskStatus.IN_PROGRESS, worker_id=WORKER_ID)
        started_event = EventEnvelope(topic=Topic.TASK_UPDATED, payload=started.model_dump(mode="json"))
        await self.producer.send_and_wait(Topic.TASK_UPDATED.value, started_event.model_dump_json().encode("utf-8"))

        await asyncio.sleep(random.uniform(1.0, 3.0))

        if random.random() < 0.1:
            failed = TaskUpdatedPayload(
                task_id=task_id,
                status=TaskStatus.FAILED,
                worker_id=WORKER_ID,
                failure_reason="simulated_failure",
            )
            failed_event = EventEnvelope(topic=Topic.TASK_UPDATED, payload=failed.model_dump(mode="json"))
            await self.producer.send_and_wait(Topic.TASK_UPDATED.value, failed_event.model_dump_json().encode("utf-8"))
            self.active_task_id = None
            return

        completed = TaskUpdatedPayload(task_id=task_id, status=TaskStatus.COMPLETED, worker_id=WORKER_ID)
        completed_event = EventEnvelope(topic=Topic.TASK_UPDATED, payload=completed.model_dump(mode="json"))
        await self.producer.send_and_wait(Topic.TASK_UPDATED.value, completed_event.model_dump_json().encode("utf-8"))
        self.active_task_id = None


if __name__ == "__main__":
    asyncio.run(WorkerAgent().start())
