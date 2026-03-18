# Wave 1 Benchmark Matrix (March 18, 2026)

## Scope

- Workloads: 500, 750, 1000 tasks
- Workers: 6
- Mode: fast
- Metric note: queue wait is reported as a p50 proxy (`max(0, p50 latency - 500ms)`).

## Results

| Tasks | Elapsed (s) | Throughput (tasks/s) | Throughput (tasks/min) | Completed | Failed | Pending/In-flight | Avg Lat (ms) | P50 Lat (ms) | Max Lat (ms) | Max Worker Share | Fairness Stddev | Fairness Spread | Queue Wait Proxy P50 (ms) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 500 | 46.156 | 10.833 | 649.97 | 500 | 0 | 0 | 24150.15 | 25366.34 | 45852.53 | 0.1720 | 1.97 | 6 | 24866.34 |
| 750 | 67.794 | 11.063 | 663.78 | 750 | 0 | 0 | 34233.76 | 34028.72 | 71531.59 | 0.1720 | 3.92 | 11 | 33528.72 |
| 1000 | 90.969 | 10.993 | 659.57 | 1000 | 0 | 0 | 48270.55 | 48256.61 | 98535.88 | 0.1740 | 4.53 | 14 | 47756.61 |

## Artifacts

- docs/reports/scaling_demo_500tasks_6workers_20260318_120004.json
- docs/reports/scaling_demo_750tasks_6workers_20260318_120201.json
- docs/reports/scaling_demo_1000tasks_6workers_20260318_120421.json
- docs/reports/wave1_benchmark_matrix_20260318.json
