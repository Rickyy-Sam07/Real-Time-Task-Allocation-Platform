# Throughput Optimization Comparison (March 18, 2026)

## Change Applied

- Scheduler now releases a worker immediately when it receives terminal task events (`completed` or `failed`), instead of waiting for the next heartbeat tick.
- Code change: `services/scheduler/main.py` (`_on_task_updated` + `_release_worker`).

## Baseline vs Optimized (600 Tasks, 6 Workers)

| Metric | Baseline (before patch) | Optimized (after patch) | Delta |
|---|---:|---:|---:|
| Elapsed time | 520.827 s | 57.556 s | -88.95% |
| Throughput (tasks/s) | 1.152 | 10.425 | +805.12% |
| Throughput (tasks/min) | 69.12 | 625.51 | +556.39 |
| Completed | 599 | 600 | +1 |
| Failed | 1 | 0 | -1 |
| Pending/In-flight at end | 0 | 0 | 0 |
| Avg latency | 259,872.73 ms | 30,778.81 ms | -88.16% |
| P50 latency | 260,700.75 ms | 29,668.86 ms | -88.62% |
| Max latency | 520,796.15 ms | 59,762.58 ms | -88.52% |

## Input Artifacts

- Baseline summary: `docs/reports/benchmark_summary_600_requests.md`
- Baseline raw: `docs/reports/result.json`
- Optimized raw: `docs/reports/scaling_demo_600tasks_6workers_20260318_111738.json`

## How Throughput Was Achieved

- Removed heartbeat-gated reuse path: previously, workers were often reusable only after the next heartbeat cycle.
- Introduced event-driven worker release in scheduler: when `task_updated` arrives with terminal state (`completed`/`failed`), scheduler now clears `active_task_id` and marks worker `available` immediately.
- Preserved existing liveness guardrails: heartbeat timeout/offline transitions still run, but heartbeat is no longer the critical path for normal worker reuse.
- Kept the benchmark workload constant (600 tasks, 6 workers, same load generator path), so the observed gain is attributable to scheduler-state transition timing.

## Tradeoffs and Engineering Decisions

- Tradeoff 1: More state transitions in scheduler memory.
	- Why accepted: lower scheduling idle time gives much larger throughput and latency gains.
	- Mitigation: release logic only applies to known task/worker terminal updates and preserves offline status.

- Tradeoff 2: Potential drift risk between scheduler in-memory worker state and delayed/out-of-order heartbeat updates.
	- Why accepted: event-driven release aligns closer to task lifecycle truth for hot-path scheduling.
	- Mitigation: heartbeat timeout/offline checks remain active as a safety net.

- Tradeoff 3: Throughput-focused benchmark profile can under-represent failure/retry overhead.
	- Why accepted: objective of this run was to isolate scheduling bottleneck removal.
	- Mitigation: continue running separate resilience runs with failure-prone mode and DLQ assertions.

## What We Did Not Change

- No change to topic contract shapes (`task_created`, `task_assigned`, `task_updated`, `worker_status`, `task_dlq`).
- No change to persistence model for assignments/tasks.
- No change to heartbeat-based offline detection and requeue logic.
- No change to worker execution model (single active task per worker at a time).

## Notes

- Baseline run had worker failure rate configured at 0.03 in fast mode; optimized rerun via the same scaling script completed all 600 tasks with no failures.
- The main bottleneck was heartbeat-gated worker reuse; this patch converted reuse to event-driven release on task terminal updates.
