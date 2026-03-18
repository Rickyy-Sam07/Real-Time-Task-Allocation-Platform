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

## Delivery Hardening Scope
1. Completed: baseline GitHub Actions CI workflow (`.github/workflows/ci.yml`).
2. Completed: automated smoke and integration scripts (`scripts/ci_smoke.sh`, `scripts/ci_integration.sh`).
3. Remaining: add unit tests and richer integration assertions beyond status checks.
4. Remaining: add architecture/sequence diagram artifacts and demo runbook.

## Scaling Demo
1. Completed: baseline load generator script (`scripts/load_generator.py`).
2. Completed: scaling demo runner with JSON reporting (`scripts/run_scaling_demo.sh`).
3. Remaining: run and archive benchmark passes at 500, 750, and 1000 tasks.
4. Remaining: record fairness and queue-lag evidence from report outputs and logs.
