# Next Implementation Steps

## Immediate (Slice 2)
1. Completed: persist API task and worker writes to PostgreSQL instead of in-memory dictionaries.
2. Completed: add scheduler retry and DLQ handling (`task_dlq`) with backoff metadata.
3. Completed (baseline): worker heartbeat timeout detection and task requeue logic in scheduler.
4. Completed: consume `task_updated` in API to keep read model synchronized from events.
5. Remaining hardening: reduce scheduler reliance on stale worker status by adding stronger liveness gates before assignment.
6. Remaining hardening: persist assignment/task transitions transactionally in scheduler with idempotency constraints.

## Dashboard Scope
1. Build a small frontend that connects to `/ws/dashboard`.
2. Show live cards for:
   - Incoming tasks
   - Task priority
   - Task status (pending, assigned, in_progress, completed)
   - Assigned worker
3. Add charts:
   - Throughput per minute
   - Failure and retry count
   - DLQ depth
4. Add controls:
   - Manual retry for DLQ task
   - Priority bump for selected task

## Reliability Scope
1. Enforce idempotency with assignment and event uniqueness checks.
2. Add a dedicated DLQ processor service.
3. Add integration tests for worker crash and task reassignment paths.
4. Add dead-letter replay endpoint/workflow for operator-triggered retry.

## Scaling Demo
1. Run `docker compose up --build --scale worker=5`.
2. Use load generator to push 500-1000 tasks.
3. Capture metrics: completion rate, average latency, DLQ count.
4. Record scheduler fairness evidence (per-worker task distribution histogram).
