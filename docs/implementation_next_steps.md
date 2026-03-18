# Next Implementation Steps

## Immediate (Slice 2)
1. Persist API task and worker writes to PostgreSQL instead of in-memory dictionaries.
2. Add scheduler retry and DLQ handling (`task_dlq`) with backoff metadata.
3. Implement worker heartbeat timeout detection in scheduler and reassignment flow.
4. Consume `task_updated` in API to keep read model synchronized from events.

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

## Scaling Demo
1. Run `docker compose up --build --scale worker=5`.
2. Use load generator to push 500-1000 tasks.
3. Capture metrics: completion rate, average latency, DLQ count.
