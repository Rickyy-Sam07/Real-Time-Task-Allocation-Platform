# Real-Time Distributed Task Allocation System

Kafka-first, event-driven platform for real-time disaster/NGO task allocation.

## Architecture
- API Gateway (FastAPI): task and worker ingestion, WebSocket dashboard stream, event publishing.
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

## Useful Commands
- Stop services:
  - docker compose down
- Follow logs:
  - docker compose logs -f api_gateway scheduler worker kafka
- Rebuild from scratch:
  - docker compose down && docker compose up --build -d

## Project Structure
- services/api_gateway: FastAPI HTTP and WebSocket entrypoint.
- services/scheduler: priority/availability assignment logic.
- services/worker: simulated task execution agents.
- services/shared: shared event contracts and enums.
- db/schema.sql: initial PostgreSQL schema bootstrap.
- docs/implementation_next_steps.md: implementation notes.

## Next Milestones
- Add durable scheduler state transitions in PostgreSQL.
- Implement retry, DLQ, and reassignment orchestration.
- Add integration tests for end-to-end event flow.
- Build operator-facing dashboard views and metrics.
