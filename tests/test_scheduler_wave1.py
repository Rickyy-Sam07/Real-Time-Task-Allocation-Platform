from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

from services.scheduler.main import Scheduler, WorkerState
from services.shared import TaskStatus, TaskUpdatedPayload, WorkerStatus


class SchedulerWave1Tests(unittest.IsolatedAsyncioTestCase):
    def test_select_worker_prefers_lower_assignment_load(self) -> None:
        scheduler = Scheduler()
        now = datetime.now(timezone.utc)

        worker_low = WorkerState(
            worker_id=uuid4(),
            status=WorkerStatus.AVAILABLE,
            location_zone="zone-a",
            skills=["rescue"],
            active_task_id=None,
            heartbeat_at=now,
            assignments_count=2,
            last_assigned_at=now - timedelta(minutes=2),
        )
        worker_high = WorkerState(
            worker_id=uuid4(),
            status=WorkerStatus.AVAILABLE,
            location_zone="zone-a",
            skills=["rescue"],
            active_task_id=None,
            heartbeat_at=now,
            assignments_count=5,
            last_assigned_at=now - timedelta(minutes=10),
        )

        scheduler.workers = {
            worker_low.worker_id: worker_low,
            worker_high.worker_id: worker_high,
        }

        picked = scheduler.select_worker(
            {
                "task_id": str(uuid4()),
                "priority": 5,
                "location_zone": "zone-a",
                "skills_required": ["rescue"],
            }
        )

        self.assertIsNotNone(picked)
        self.assertEqual(picked.worker_id, worker_low.worker_id)

    def test_select_worker_tiebreak_prefers_least_recently_assigned(self) -> None:
        scheduler = Scheduler()
        now = datetime.now(timezone.utc)

        older = WorkerState(
            worker_id=uuid4(),
            status=WorkerStatus.AVAILABLE,
            location_zone="zone-b",
            skills=["medical"],
            active_task_id=None,
            heartbeat_at=now,
            assignments_count=3,
            last_assigned_at=now - timedelta(minutes=20),
        )
        newer = WorkerState(
            worker_id=uuid4(),
            status=WorkerStatus.AVAILABLE,
            location_zone="zone-b",
            skills=["medical"],
            active_task_id=None,
            heartbeat_at=now,
            assignments_count=3,
            last_assigned_at=now - timedelta(minutes=1),
        )

        scheduler.workers = {
            older.worker_id: older,
            newer.worker_id: newer,
        }

        picked = scheduler.select_worker(
            {
                "task_id": str(uuid4()),
                "priority": 3,
                "location_zone": "zone-b",
                "skills_required": ["medical"],
            }
        )

        self.assertIsNotNone(picked)
        self.assertEqual(picked.worker_id, older.worker_id)

    async def test_failed_task_schedules_retry_with_backoff(self) -> None:
        scheduler = Scheduler()
        task_id = uuid4()
        scheduler.task_catalog[task_id] = {
            "task_id": str(task_id),
            "priority": 4,
            "location_zone": "zone-a",
            "skills_required": [],
            "max_retries": 3,
        }
        scheduler.task_retries[task_id] = 0
        scheduler._set_assignment_terminal = AsyncMock()  # type: ignore[method-assign]

        update = TaskUpdatedPayload(task_id=task_id, status=TaskStatus.FAILED, worker_id=uuid4())
        await scheduler._on_task_updated(update.model_dump(mode="json"))

        self.assertEqual(scheduler.task_retries[task_id], 1)
        self.assertEqual(len(scheduler.pending), 1)

        _, available_at, enqueued_at, payload = scheduler.pending[0]
        self.assertGreaterEqual(available_at - enqueued_at, 0.9)
        self.assertLessEqual(available_at - enqueued_at, 1.5)
        self.assertEqual(payload["task_id"], str(task_id))

    def test_heartbeat_timeout_marks_offline_and_requeues_once(self) -> None:
        scheduler = Scheduler()
        now = datetime.now(timezone.utc)
        task_id = uuid4()

        worker = WorkerState(
            worker_id=uuid4(),
            status=WorkerStatus.BUSY,
            location_zone="zone-a",
            skills=["rescue"],
            active_task_id=task_id,
            heartbeat_at=now - timedelta(seconds=scheduler.heartbeat_timeout_sec + 3),
            assignments_count=1,
            last_assigned_at=now - timedelta(seconds=30),
        )
        scheduler.workers[worker.worker_id] = worker
        scheduler.task_catalog[task_id] = {
            "task_id": str(task_id),
            "priority": 5,
            "location_zone": "zone-a",
            "skills_required": [],
            "max_retries": 2,
        }

        scheduler._refresh_worker_liveness()
        self.assertEqual(worker.status, WorkerStatus.OFFLINE)
        self.assertEqual(len(scheduler.pending), 1)

        scheduler._refresh_worker_liveness()
        self.assertEqual(len(scheduler.pending), 1)


if __name__ == "__main__":
    unittest.main()
