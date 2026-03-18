# Demo Runbook

## Objective

Demonstrate Wave 1 capabilities end-to-end:
- real-time event-driven assignment
- fairness-aware scheduling
- retry and DLQ behavior
- crash recovery and reassignment
- benchmark throughput and latency evidence

## Pre-Flight

1. Start stack:
   - `docker compose up --build -d`
2. Confirm health:
   - `curl http://localhost:8000/health`
   - `curl http://localhost:8000/dashboard/health`
3. Open dashboard:
   - `http://localhost:8000/dashboard`

## Demo Flow

1. Smoke lifecycle validation:
   - `bash scripts/ci_smoke.sh`
2. Crash-recovery validation:
   - `bash scripts/check_worker_crash_reassignment.sh 4 240`
3. Integration validation:
   - `bash scripts/ci_integration.sh`
4. Throughput benchmark matrix:
   - `./scripts/run_scaling_demo.sh 500 6 900 compatible`
   - `./scripts/run_scaling_demo.sh 750 6 900 compatible`
   - `./scripts/run_scaling_demo.sh 1000 6 900 compatible`

## What To Highlight During Demo

- Scheduler fairness: assignments spread across workers without pathological skew.
- Immediate worker release: terminal updates unlock next assignment without waiting heartbeat tick.
- Reliability: retry backoff, DLQ persistence, and replay API.
- Operational checks: dashboard health, CI scripts, and report assertions.

## Artifacts To Present

- `docs/reports/benchmark_summary_600_requests.md`
- `docs/reports/throughput_optimization_comparison_20260318.md`
- `docs/reports/wave1_benchmark_matrix_20260318.md`

## Troubleshooting

- If health fails: `docker compose ps -a` and `docker compose logs -f api_gateway scheduler worker kafka`.
- If throughput regresses: ensure scheduler container rebuilt after latest code changes.
- If assignments stall: inspect worker heartbeats and scheduler logs for timeout/offline transitions.
