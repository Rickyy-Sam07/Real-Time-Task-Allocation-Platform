from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from uuid import UUID

import asyncpg
from aiokafka import AIOKafkaConsumer

from services.shared import EventEnvelope, TaskDLQPayload, Topic


KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "task_alloc")
POSTGRES_USER = os.getenv("POSTGRES_USER", "task_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "task_pass")
DLQ_PROCESSOR_CONSUMER = "dlq_processor"


def postgres_dsn() -> str:
    return f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"


class DLQProcessor:
    def __init__(self) -> None:
        self.db_pool: asyncpg.Pool | None = None
        self.consumer: AIOKafkaConsumer | None = None
        self._seen_event_ids: set[UUID] = set()
        self._seen_event_order: deque[UUID] = deque()
        self._max_seen_events = int(os.getenv("DLQ_PROCESSOR_MAX_SEEN_EVENTS", "5000"))

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
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_dlq (
                  task_id UUID PRIMARY KEY,
                  reason TEXT NOT NULL,
                  retry_count INT NOT NULL,
                  max_retries INT NOT NULL,
                  worker_id UUID,
                  failed_at TIMESTAMP NOT NULL,
                  replayed BOOLEAN NOT NULL DEFAULT FALSE,
                  replayed_at TIMESTAMP,
                  replay_note TEXT,
                  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
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
                    DLQ_PROCESSOR_CONSUMER,
                    envelope.event_id,
                )
        return result is not None

    def _is_duplicate_event(self, event_id: UUID) -> bool:
        if event_id in self._seen_event_ids:
            return True

        self._seen_event_ids.add(event_id)
        self._seen_event_order.append(event_id)

        if len(self._seen_event_order) > self._max_seen_events:
            oldest = self._seen_event_order.popleft()
            self._seen_event_ids.discard(oldest)

        return False

    async def _persist_dlq(self, payload: TaskDLQPayload) -> None:
        assert self.db_pool is not None
        failed_at = payload.failed_at.replace(tzinfo=None)
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO task_dlq (
                    task_id, reason, retry_count, max_retries, worker_id, failed_at, replayed, replayed_at, replay_note, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, FALSE, NULL, NULL, NOW())
                ON CONFLICT (task_id)
                DO UPDATE SET
                    reason = EXCLUDED.reason,
                    retry_count = EXCLUDED.retry_count,
                    max_retries = EXCLUDED.max_retries,
                    worker_id = EXCLUDED.worker_id,
                    failed_at = EXCLUDED.failed_at,
                    replayed = FALSE,
                    replayed_at = NULL,
                    replay_note = NULL,
                    updated_at = NOW()
                """,
                payload.task_id,
                payload.reason,
                payload.retry_count,
                payload.max_retries,
                payload.worker_id,
                failed_at,
            )

    async def start(self) -> None:
        for attempt in range(1, 16):
            try:
                self.db_pool = await asyncpg.create_pool(dsn=postgres_dsn(), min_size=1, max_size=5)
                await self._ensure_tables()
                break
            except Exception as exc:
                if attempt == 15:
                    raise
                print(f"[dlq_processor] waiting for postgres (attempt {attempt}/15): {exc}")
                await asyncio.sleep(2)

        self.consumer = AIOKafkaConsumer(
            Topic.TASK_DLQ.value,
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            group_id="dlq-processor-group",
            auto_offset_reset="earliest",
        )

        for attempt in range(1, 16):
            try:
                await self.consumer.start()
                break
            except Exception as exc:
                if attempt == 15:
                    raise
                print(f"[dlq_processor] waiting for kafka (attempt {attempt}/15): {exc}")
                await asyncio.sleep(2)

        print("[dlq_processor] started")
        try:
            assert self.consumer is not None
            async for msg in self.consumer:
                envelope = EventEnvelope.model_validate_json(msg.value.decode("utf-8"))
                if envelope.topic != Topic.TASK_DLQ:
                    continue
                if self._is_duplicate_event(envelope.event_id):
                    continue
                if not await self._claim_event(envelope):
                    continue

                payload = TaskDLQPayload.model_validate(envelope.payload)
                await self._persist_dlq(payload)
                print(f"[dlq_processor] persisted dlq task_id={payload.task_id}")
        finally:
            if self.consumer is not None:
                await self.consumer.stop()
            if self.db_pool is not None:
                await self.db_pool.close()


if __name__ == "__main__":
    asyncio.run(DLQProcessor().start())
