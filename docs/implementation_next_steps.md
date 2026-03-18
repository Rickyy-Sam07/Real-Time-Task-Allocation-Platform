# Next Implementation Steps

## Immediate (Slice 2)
1. Completed: persist API task and worker writes to PostgreSQL instead of in-memory dictionaries.
2. Completed: add scheduler retry and DLQ handling (`task_dlq`) with backoff metadata.
3. Completed (baseline): worker heartbeat timeout detection and task requeue logic in scheduler.
4. Completed: consume `task_updated` in API to keep read model synchronized from events.
5. Completed: stronger scheduler liveness gates before assignment (status + active-task + heartbeat freshness).
6. Completed: transactional assignment reservation in scheduler with active-assignment uniqueness protection.
7. Completed: periodic scheduler maintenance loop to enforce heartbeat timeout and reassignment even during low-event windows.
8. Completed: explicit scheduler fairness scoring (load + least-recently-assigned tie-break).

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
2. Completed: assignment lifecycle persistence updates (`assigned` -> `in_progress` -> terminal states) to keep active constraints valid.
3. Completed: explicit scheduler handling for `task_updated=pending` to reset retry tracking and requeue tasks safely.
4. Completed: dead-letter replay endpoint/workflow for operator-triggered retry (`/dlq/tasks`, `/dlq/replay`).
5. Completed: dedicated DLQ processor service to persist `task_dlq` events (`services/dlq_processor`).
6. Completed: integration failure-path check for worker crash with post-crash assignment progress validation (`scripts/check_worker_crash_reassignment.sh`).

## Delivery Hardening Scope
1. Completed: baseline GitHub Actions CI workflow (`.github/workflows/ci.yml`).
2. Completed: automated smoke and integration scripts (`scripts/ci_smoke.sh`, `scripts/ci_integration.sh`).
3. Completed: richer integration assertions using report quality gates (`scripts/assert_integration_report.py`).
4. Completed: unit tests for scheduler fairness, retry/backoff, and heartbeat timeout edge cases (`tests/test_scheduler_wave1.py`).
5. Completed: architecture/sequence artifacts and demo runbook (`docs/architecture_and_sequence.md`, `docs/demo_runbook.md`).

## Scaling Demo
1. Completed: baseline load generator script (`scripts/load_generator.py`).
2. Completed: scaling demo runner with JSON reporting (`scripts/run_scaling_demo.sh`).
3. Completed: benchmark passes archived at 500, 750, and 1000 tasks.
4. Completed: fairness and queue-lag evidence consolidated in benchmark matrix reports (`docs/reports/wave1_benchmark_matrix_20260318.md`, `docs/reports/wave1_benchmark_matrix_20260318.json`).
