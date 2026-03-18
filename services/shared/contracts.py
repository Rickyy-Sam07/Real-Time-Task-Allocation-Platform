from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Topic(str, Enum):
    TASK_CREATED = "task_created"
    TASK_ASSIGNED = "task_assigned"
    TASK_UPDATED = "task_updated"
    WORKER_STATUS = "worker_status"
    TASK_DLQ = "task_dlq"


class TaskStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkerStatus(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    OFFLINE = "offline"


class EventEnvelope(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    topic: Topic
    event_version: str = "1.0"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any]


class TaskCreatedPayload(BaseModel):
    task_id: UUID
    priority: int = Field(ge=1, le=5)
    estimated_duration_sec: int = Field(gt=0)
    location_zone: str | None = None
    skills_required: list[str] = Field(default_factory=list)
    max_retries: int = 3


class TaskAssignedPayload(BaseModel):
    assignment_id: UUID
    task_id: UUID
    worker_id: UUID
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskUpdatedPayload(BaseModel):
    task_id: UUID
    status: TaskStatus
    worker_id: UUID | None = None
    failure_reason: str | None = None


class WorkerStatusPayload(BaseModel):
    worker_id: UUID
    status: WorkerStatus
    location_zone: str | None = None
    skills: list[str] = Field(default_factory=list)
    active_task_id: UUID | None = None
    heartbeat_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
