## Plan: Real-Time Task Allocation Platform

Build a Kafka-first, event-driven distributed system with FastAPI ingress, a priority scheduler, multiple worker instances, PostgreSQL plus Redis state, real-time dashboard updates, and explicit failure recovery (retry, DLQ, reassignment). The approach is phased so each phase is demoable while preserving production-quality architecture boundaries.

**Steps**
1. Phase 0: Platform baseline and contracts
1. Confirm canonical domain model for Task, Worker, Assignment, EventEnvelope, and Status enums; lock event naming and version field before service coding starts.
1. Define event contracts for task_created, task_assigned, task_updated, worker_status, and task_dlq including required idempotency identifiers and timestamps.
1. Decide service ownership boundaries and source-of-truth rules: PostgreSQL for durable state, Redis for volatile liveness/state cache, Kafka for inter-service communication and replay.
1. Phase 1: Infrastructure and repository bootstrap
1. Initialize mono-repo service layout with isolated Python environments and shared contracts package for event schemas and status enums.
1. Provision local infra with Docker Compose: Kafka, Zookeeper or KRaft mode, PostgreSQL, Redis, and optional monitoring stack.
1. Add baseline config management (.env template, service-level settings, health checks, startup dependencies).
1. Add migration framework and create initial schema for tasks, workers, assignments, and event audit table.
1. Phase 2: API Gateway and ingestion pipeline
1. Implement FastAPI API Gateway endpoints for task intake, worker register/update, task update, and task query.
1. Enforce asynchronous behavior: HTTP handlers only validate, persist minimal request metadata, and publish events; no scheduling logic in API path.
1. Add WebSocket broadcasting channel in gateway that streams normalized updates for incoming tasks, assignment changes, and worker status.
1. Phase 3: Scheduler brain and assignment strategy
1. Build Scheduler consumer for task_created and worker_status topics with consumer group semantics and offset management.
1. Implement priority queue strategy with zone-aware matching and availability filtering.
1. Scoring logic should combine task priority, worker load, and zone affinity; tie-break by least recently assigned to improve fairness.
1. Persist assignment transactionally: update task status, insert assignment row, publish task_assigned event.
1. Add pending-queue recheck loop for unassigned tasks and worker availability changes.
1. Phase 4: Worker service and lifecycle events
1. Implement Worker service capable of running multiple instances; each instance consumes assignments and emits started, completed, and failed task updates.
1. Add simulated execution modes for deterministic demo behavior and stress behavior (fast, normal, failure-prone).
1. Add heartbeat publishing every fixed interval and active-task metadata for liveness detection.
1. Phase 5: Reliability and fault tolerance
1. Implement retry orchestration with exponential backoff and max retry policy per task.
1. Route exhausted failures to DLQ topic and persist DLQ reason/context for operator review.
1. Add worker failure handling: heartbeat timeout marks worker offline and triggers reassignment of in-progress work.
1. Enforce idempotency at scheduler and worker boundaries using event_id plus assignment_id constraints and duplicate-drop checks.
1. Phase 6: Dashboard and operational visibility
1. Build dashboard views for incoming tasks, assignment timeline, worker status, and task state transitions.
1. Include charts for throughput, completion latency, failure rate, retry count, and DLQ depth.
1. Add operator controls for manual retry of DLQ tasks and optional task priority bump for urgent incidents.
1. Phase 7: Load simulation and scaling demonstration
1. Build load generator for 500 to 1000 incoming tasks with configurable priority mix and zone distribution.
1. Run scaled worker replicas in Docker Compose and capture distribution evidence (task split, queue lag, completion metrics).
1. Execute failure injection scenarios (kill worker/container, delayed worker, transient DB/network errors) and verify recovery paths.
1. Phase 8: Delivery hardening and showcase prep
1. Add CI pipeline for lint, unit tests, integration tests, and minimal end-to-end smoke test.
1. Add runbooks for local startup, demo script, and known-failure recovery actions.
1. Prepare architecture and sequence diagrams for judges/interviewers highlighting async and resilience design.

**Parallelization and dependencies**
1. Contract definition blocks all service implementation work and should complete first.
1. Infra bootstrap and schema migration can run in parallel with API route scaffolding after contracts are stable.
1. Scheduler and Worker implementation can proceed in parallel once topics and schema are finalized.
1. Dashboard implementation can start early against mocked WebSocket payloads, then integrate with live events.
1. Reliability features (retry, DLQ, reassignment) depend on baseline Scheduler plus Worker event loop completion.
1. Load testing should start after end-to-end happy path and basic failure handling are stable.

**Relevant files**
1. Compose and infra bootstrap: docker-compose.yml, .env.example, db/schema.sql.
1. API Gateway implementation: services/api_gateway/main.py.
1. Scheduler implementation: services/scheduler/main.py.
1. Worker implementation: services/worker/main.py.
1. Shared event contracts: services/shared/contracts.py.
1. Existing implementation notes: docs/implementation_next_steps.md.

**Verification**
1. Contract tests validate event schema compatibility and required fields across producer/consumer boundaries.
1. Unit tests validate scheduler scoring, retry/backoff calculation, heartbeat timeout detection, and idempotency guards.
1. Integration tests validate API to Kafka to Scheduler to Worker to DB end-to-end state transitions.
1. Failure tests validate worker death reassignment, repeated execution failure to DLQ routing, and duplicate event suppression.
1. Load tests validate stability at 500 to 1000 tasks, acceptable queue lag, and no orphaned tasks.
1. Demo acceptance criteria: high-priority tasks are handled first, real-time dashboard reflects lifecycle changes, failures recover automatically, and horizontal worker scaling improves throughput.

**Decisions**
1. Message broker: Kafka as primary implementation target.
1. Location logic: simple zone-based matching in MVP.
1. Dashboard scope: live task/worker views plus charts and operator controls.
1. Delivery posture: production-leaning architecture with clean showcase quality.

**Scope boundaries**
1. Included: distributed async processing, scheduling, retries, DLQ, reassignment, live dashboard, load simulation, Dockerized deployment, baseline CI.
1. Excluded from initial scope: multi-region deployment, advanced geospatial optimization, auto-scaling orchestration beyond Compose, strict exactly-once semantics across all components.

**Further considerations**
1. Authentication model recommendation: start with service tokens plus simple role-based access for dashboard operators; defer full OAuth provider integration.
1. Monitoring recommendation: add Prometheus metrics in MVP if timeline allows, with Grafana optional for polished demo.
1. Kafka operations recommendation: start single broker for development, then document replication-ready configuration for production narrative.
