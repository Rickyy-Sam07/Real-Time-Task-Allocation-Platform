# Architecture and Sequence Artifacts

## System Components

- API Gateway (FastAPI): accepts task and worker API calls, publishes events, serves dashboard and read-model views.
- Scheduler: consumes task and worker events, applies fairness-aware assignment, manages retries, heartbeat timeout/offline handling.
- Worker replicas: consume assignments and emit lifecycle updates (`in_progress`, `completed`, `failed`).
- DLQ Processor: consumes `task_dlq` events and persists operator-facing DLQ records.
- PostgreSQL: durable store for tasks, assignments, workers, event-log, processed-events, and DLQ rows.
- Kafka: event transport backbone (`task_created`, `task_assigned`, `task_updated`, `worker_status`, `task_dlq`).

## Sequence: Normal Assignment

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API Gateway
    participant K as Kafka
    participant S as Scheduler
    participant W as Worker
    participant P as Postgres

    C->>A: POST /task
    A->>P: upsert task(status=pending)
    A->>K: publish task_created
    K->>S: consume task_created
    S->>S: enqueue pending task
    S->>P: reserve assignment + task status assigned
    S->>K: publish task_assigned
    K->>W: consume task_assigned
    W->>K: publish task_updated(in_progress)
    K->>S: consume in_progress
    S->>P: assignment status in_progress
    W->>K: publish task_updated(completed)
    K->>S: consume completed
    S->>P: assignment terminal update
    S->>S: release worker immediately
```

## Sequence: Failure, Retry, DLQ

```mermaid
sequenceDiagram
    participant W as Worker
    participant K as Kafka
    participant S as Scheduler
    participant D as DLQ Processor
    participant P as Postgres

    W->>K: task_updated(failed)
    K->>S: consume failed
    S->>P: assignment terminal update
    S->>S: increment retry_count
    alt retry_count <= max_retries
      S->>S: enqueue with exponential backoff
    else retry exhausted
      S->>K: publish task_dlq
      K->>D: consume task_dlq
      D->>P: persist DLQ row
    end
```

## Sequence: Worker Heartbeat Timeout

```mermaid
sequenceDiagram
    participant W as Worker
    participant K as Kafka
    participant S as Scheduler

    loop every 5 sec
      W->>K: worker_status heartbeat
      K->>S: consume worker_status
      S->>S: refresh worker liveness
    end

    Note over S: If heartbeat age > timeout
    S->>S: mark worker offline
    S->>S: requeue active task if needed
```

## Wave 1 Design Notes

- Assignment fairness now considers cumulative assignment load and least-recently-assigned tie-break.
- Worker reuse is event-driven on terminal updates; heartbeat remains primarily a liveness safety mechanism.
- Idempotency is enforced using consumer-scoped `processed_events` plus bounded in-memory dedupe sets.
