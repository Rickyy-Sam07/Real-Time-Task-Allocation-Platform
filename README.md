# Real-Time Distributed Task Allocation System

Kafka-first, event-driven platform for real-time disaster/NGO task allocation.

## Architecture
- API Gateway (FastAPI): task and worker ingestion, WebSocket dashboard stream, event publishing.
- DLQ Processor: consumes `task_dlq` events and persists operator review records.
- Scheduler: consumes task and worker events, maintains pending queue, performs assignment.
- Worker (replicated): consumes assignments, emits started/completed/failed task lifecycle events.

## Infrastructure
- Kafka: asynchronous service-to-service event bus.
- PostgreSQL: durable state and schema initialization.
- Redis: worker cache and short-lived runtime state.
- Docker Compose: local orchestration for full stack demos.

## Current Status (March 2026)
- WSL2-based local environment validated for Linux-native development workflow.
- Docker stack builds and runs successfully with all services up.
- Kafka listener configuration fixed for Compose-based networking.
- API gateway, scheduler, and workers now include Kafka startup retry behavior.
- Health endpoint is available at http://localhost:8000/health.

## Quick Start
1. Create a local env file:
   - cp .env.example .env
2. Start platform services:
   - docker compose up --build -d
3. Check container health:
   - docker compose ps -a
4. Check API gateway health:
   - curl http://localhost:8000/health
5. Open API docs:
   - http://localhost:8000/docs
6. Open live dashboard:
   - http://localhost:8000/dashboard
7. Check dashboard health JSON:
   - curl http://localhost:8000/dashboard/health

## Useful Commands
- Stop services:
  - docker compose down
- Follow logs:
  - docker compose logs -f api_gateway scheduler worker kafka
- Rebuild from scratch:
  - docker compose down && docker compose up --build -d

## Reliability Check
- Run the processed-events smoke check:
   - ./scripts/check_processed_events.sh
- Optional timeout override (seconds):
   - ./scripts/check_processed_events.sh 25
- What it validates:
   - Publishes one task to the API.
   - Confirms processed event counters increase for both `api_gateway_read_model` and `scheduler` in PostgreSQL.
- Expected successful output (example):
   - Before: api_gateway_read_model=65, scheduler=63
   - Published task_id=<uuid>
   - PASS: counts increased
   - After: api_gateway_read_model=67, scheduler=65

## DLQ Operator Workflow
- List DLQ entries:
   - curl http://localhost:8000/dlq/tasks
- Replay a DLQ task (optional priority bump):
   - curl -X POST http://localhost:8000/dlq/replay -H "Content-Type: application/json" -d '{"task_id":"<uuid>","priority_bump":1,"replay_note":"manual replay"}'
- Behavior:
   - Marks the DLQ row as replayed with timestamp and note.
   - Resets the task to `pending` and republishes `task_created` for scheduler reassignment.

## Worker Simulation Modes
- Configure behavior with `WORKER_EXECUTION_MODE` in `.env`:
   - `normal`: random duration and failure using `WORKER_MIN_EXEC_SEC`, `WORKER_MAX_EXEC_SEC`, `WORKER_FAILURE_RATE`.
   - `fast`: short execution with very low failure probability.
   - `failure_prone`: normal execution time with high failure probability.
   - `deterministic`: fixed delay and deterministic fail decision per task id using `WORKER_DETERMINISTIC_DELAY_SEC` and `WORKER_DETERMINISTIC_FAIL_PERCENT`.
- Run a deterministic demo and print final outcomes:
   - ./scripts/run_deterministic_worker_demo.sh
   - Optional args: ./scripts/run_deterministic_worker_demo.sh <task_count> <timeout_sec>
   - Example with explicit timeout: ./scripts/run_deterministic_worker_demo.sh 6 90

## Failure Injection Demo
- Kill one worker during active processing and observe eventual outcomes:
   - ./scripts/run_worker_failure_injection_demo.sh
- Optional args: ./scripts/run_worker_failure_injection_demo.sh <task_count> <timeout_sec>
- Example: ./scripts/run_worker_failure_injection_demo.sh 6 140

## Load And Scaling Demo
- Generate load directly:
   - python3 scripts/load_generator.py --tasks 200 --timeout 240
- Run scaling demo (scale workers + load + report):
   - ./scripts/run_scaling_demo.sh
   - Optional args: ./scripts/run_scaling_demo.sh <task_count> <worker_count> <timeout_sec> <skill_mode>
   - skill_mode: compatible (default) or mixed
   - Example: ./scripts/run_scaling_demo.sh 500 5 420 compatible
- Reports are written to:
   - docs/reports/

## CI And Automated Checks
- GitHub Actions workflow:
   - .github/workflows/ci.yml
- Local smoke check script:
   - bash scripts/ci_smoke.sh
- Local integration check script:
   - bash scripts/ci_integration.sh
- CI pipeline coverage:
   - Python syntax validation
   - Docker Compose stack startup
   - API and dashboard health validation
   - Smoke task lifecycle check
   - Worker crash and reassignment integration check
   - Processed-events check and load-generator integration run
   - Report artifact upload from docs/reports/

## Project Structure
- services/api_gateway: FastAPI HTTP and WebSocket entrypoint.
- services/dlq_processor: dedicated task DLQ event persistence consumer.
- services/scheduler: priority/availability assignment logic.
- services/worker: simulated task execution agents.
- services/shared: shared event contracts and enums.
- db/schema.sql: initial PostgreSQL schema bootstrap.
- docs/implementation_next_steps.md: implementation notes.

## Next Milestones
- Add scheduler fairness scoring (load and least-recently-assigned tie-break).
- Build operator-facing dashboard controls and advanced metrics (failure/retry/DLQ).
