# Comprehensive Benchmark Report

Date: 2026-03-18
System: Real-Time Distributed Task Allocation System

## 1) Benchmark Approach

- Use consistent end-to-end load generation through API task submission and polling until terminal states.
- Measure before and after optimization on identical workload shape (600 tasks, 6 workers).
- Run additional throughput matrix at 500, 750, and 1000 tasks to validate scalability after optimization.
- Capture reliability signals (pending/in-flight closure, failure counts, assignment churn, processed events).
- Capture fairness signals (worker distribution share, spread, and stddev).

Primary sources:
- docs/reports/result.json
- docs/reports/benchmark_summary_600_requests.md
- docs/reports/throughput_optimization_comparison_20260318.md
- docs/reports/wave1_benchmark_matrix_20260318.md
- docs/reports/small_scaling_table_30tasks_20260318_123813.md
- docs/reports/ci_integration_report.json

## 2) Core Metrics Table (Baseline vs Optimized, 600 Tasks)

| Metric | Baseline | Optimized | Delta |
|---|---:|---:|---:|
| Elapsed time (s) | 520.827 | 57.556 | -88.95% |
| Throughput (tasks/s) | 1.152 | 10.425 | +805.12% |
| Throughput (tasks/min) | 69.12 | 625.51 | +556.39 |
| Completed | 599 | 600 | +1 |
| Failed | 1 | 0 | -1 |
| Pending/In-flight at end | 0 | 0 | 0 |
| Avg latency (ms) | 259872.73 | 30778.81 | -88.16% |
| P50 latency (ms) | 260700.75 | 29668.86 | -88.62% |
| Max latency (ms) | 520796.15 | 59762.58 | -88.52% |

Interpretation:
- Terminal closure quality remained strong (0 pending/in-flight at end) in both runs.
- Optimization primarily removed scheduler reuse delay, producing the largest observed gain.

## 2.1) Why Throughput Is So Much Better Than Baseline

The major gain came from removing a scheduler pacing bottleneck, not from changing infrastructure size.

Before optimization:
- Worker reuse was effectively heartbeat-gated for scheduling readiness.
- Heartbeat period is 5s, so practical reuse cadence was much slower than worker execution speed.
- With 6 workers, a rough upper-bound under heartbeat pacing is around $6 / 5 = 1.2$ tasks/s (about 72 tasks/min), which matches observed baseline 1.152 tasks/s (69.12 tasks/min).

After optimization:
- Scheduler releases worker availability immediately on terminal task updates (`completed`/`failed`).
- Worker reuse is now event-driven (hot path), while heartbeat remains a liveness safety mechanism.
- Fast-mode worker execution is sub-second, so throughput is no longer constrained by 5s heartbeat pacing.

Observed effect:
- Throughput increased from 1.152 to 10.425 tasks/s (about 9.05x).
- End-to-end latency dropped by about 88% across avg, p50, and max.
- Terminal closure quality stayed stable (0 pending/in-flight residue).

Why this is credible:
- The workload shape stayed comparable (600 tasks, 6 workers).
- The change maps directly to the prior bottleneck location in scheduler state transitions.
- Follow-up matrix runs (500, 750, 1000 tasks) show stable post-optimization throughput around 650-664 tasks/min.

## 2.2) Old Flow vs New Flow (Code-Level View)

### Old flow (heartbeat-gated reuse)

Flow:
1. Scheduler assigns task and marks worker busy.
2. Worker finishes task quickly.
3. Scheduler may keep worker effectively unavailable until next heartbeat update cycle.
4. Next assignment opportunity waits behind heartbeat pacing.

Key pacing points:

```python
# scheduler assignment side
worker.status = WorkerStatus.BUSY
worker.active_task_id = assignment_payload.task_id
```

```python
# worker heartbeat cadence
status = WorkerStatus.BUSY if self.active_task_id else WorkerStatus.AVAILABLE
await self.producer.send_and_wait(Topic.WORKER_STATUS.value, event.model_dump_json().encode("utf-8"))
await asyncio.sleep(5)
```

Throughput implication:
- With 6 workers and 5s heartbeat pacing, practical reuse ceiling is near $6 / 5 = 1.2$ tasks/s in this bottlenecked mode.

### New flow (`_on_task_updated -> _release_worker`)

Flow:
1. Worker publishes terminal update (`completed` or `failed`).
2. Scheduler consumes `task_updated` immediately.
3. Scheduler marks assignment terminal and calls `_release_worker` immediately.
4. Worker is reusable on event path, without waiting for next heartbeat tick.

New hot-path code:

```python
if task_update.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
   await self._set_assignment_terminal(task_update)
   self._release_worker(task_id=task_id, worker_id=task_update.worker_id)
```

```python
def _release_worker(self, task_id: UUID, worker_id: UUID | None) -> None:
   if worker_id is not None:
      worker = self.workers.get(worker_id)
      if worker is not None:
         if worker.active_task_id == task_id:
            worker.active_task_id = None
         if worker.status != WorkerStatus.OFFLINE:
            worker.status = WorkerStatus.AVAILABLE
         return
```

Result:
- Reuse path changed from timer-paced to event-paced.
- This is the primary reason throughput jumped from 69.12 tasks/min to 625.51 tasks/min on the 600-task comparison run.

## 3) Scalability Table (Post-Optimization)

| Tasks | Workers | Elapsed (s) | Throughput (tasks/s) | Throughput (tasks/min) | Completed | Failed | Pending/In-flight |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 500 | 6 | 46.156 | 10.833 | 649.97 | 500 | 0 | 0 |
| 750 | 6 | 67.794 | 11.063 | 663.78 | 750 | 0 | 0 |
| 1000 | 6 | 90.969 | 10.993 | 659.57 | 1000 | 0 | 0 |

Interpretation:
- Throughput remains stable around 650 to 664 tasks/min across larger loads.
- No tail collapse or terminal-state leakage observed in this range.

## 4) Fairness and Queue-Lag Evidence

| Tasks | Max worker share | Fairness spread (tasks) | Fairness stddev | Queue-wait proxy p50 (ms) |
|---:|---:|---:|---:|---:|
| 500 | 0.1720 | 6 | 1.97 | 24866.34 |
| 750 | 0.1720 | 11 | 3.92 | 33528.72 |
| 1000 | 0.1740 | 14 | 4.53 | 47756.61 |

Notes:
- Max worker share stays near 17.2% to 17.4% across 6 workers, indicating healthy distribution.
- Queue-wait proxy rises with workload size, consistent with finite worker concurrency and queue buildup.

## 5) Small Scaling Table (2 -> 4 -> 6 Workers)

| Workers | Tasks | Elapsed (s) | Throughput (tasks/s) | Throughput (tasks/min) | Avg Latency (ms) |
|---:|---:|---:|---:|---:|---:|
| 2 | 30 | 12.419 | 2.416 | 144.94 | 7930.93 |
| 4 | 30 | 7.841 | 3.826 | 229.56 | 4755.63 |
| 6 | 30 | 6.166 | 4.865 | 291.92 | 3926.12 |

Interpretation:
- Clear monotonic scaling improvement from 2 to 6 workers.
- Latency decreases as worker parallelism increases for same workload.

## 6) Reliability and Consistency Signals

From baseline 600-run DB deltas:
- tasks_total: +600
- tasks_completed: +600
- tasks_failed: +0
- assignments_total: +611
- assignments_completed: +600
- assignments_failed: +11
- task_dlq_total: +0
- processed_api_gateway_read_model: +2583
- processed_scheduler: +2572

Interpretation:
- Task closure is consistent with requested workload size.
- Assignment churn reflects retry and reassignment behavior rather than stuck tasks.
- Event-processing counters confirm active pipeline movement across services.

## 7) Bottlenecks Identified

Resolved bottleneck:
- Heartbeat-gated worker reuse (workers often reusable only on next heartbeat) throttled scheduler throughput.

Current residual bottlenecks:
- Queue wait increases with workload size due to finite worker slots and single active task per worker model.
- Worker capability homogeneity in compatible-mode tests can mask skill-constraint bottlenecks.
- Worker identity churn across recreates can inflate worker totals in long-running environments.

## 8) Tradeoffs Accepted

- Event-driven worker release introduces more frequent scheduler in-memory state transitions.
- Potential temporary drift risk between in-memory worker state and delayed or out-of-order heartbeat updates.
- Throughput-focused benchmark profile can under-represent failure-prone behavior unless paired with resilience runs.

Mitigations in place:
- Heartbeat timeout and offline requeue guardrails remain active.
- Integration pipeline includes crash-recovery and report quality assertions.

## 9) Optimization Summary (What Was Done)

1. Replaced heartbeat-gated reuse with event-driven worker release on terminal task updates.
2. Added explicit pending-state handling to safely requeue tasks and reset retry tracking.
3. Added scheduler fairness scoring:
   - lower cumulative assignment load first
   - least-recently-assigned tie-break second
4. Added Wave 1 unit tests for fairness, retry/backoff, and heartbeat timeout edges.
5. Added architecture and sequence artifacts plus demo runbook.
6. Added benchmark matrix and small scaling automation scripts/reports.

## 10) Final Status

- Wave 1 remaining implementation items are completed and validated.
- Throughput moved from non-standout baseline to stable high-throughput range under fast-mode benchmark conditions.
- Reliability gates pass with terminal-state closure and integration checks.

## 11) Artifact Index

- docs/reports/benchmark_summary_600_requests.md
- docs/reports/throughput_optimization_comparison_20260318.md
- docs/reports/wave1_benchmark_matrix_20260318.md
- docs/reports/wave1_benchmark_matrix_20260318.json
- docs/reports/small_scaling_table_30tasks_20260318_123813.md
- docs/reports/small_scaling_table_30tasks_20260318_123813.json
- docs/reports/ci_integration_report.json
- docs/reports/result.json
