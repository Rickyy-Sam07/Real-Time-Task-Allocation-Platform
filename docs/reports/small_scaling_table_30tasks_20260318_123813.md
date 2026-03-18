# Small Scaling Table (30 Tasks)

## Scope

- Worker counts: 2, 4, 6
- Skill mode: compatible

## Results

| Workers | Elapsed (s) | Throughput (tasks/s) | Throughput (tasks/min) | Completed | Failed | Pending/In-flight | Avg Lat (ms) | P50 Lat (ms) | Max Lat (ms) | Max Worker Share | Fairness Spread | Fairness Stddev |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | 12.419 | 2.416 | 144.94 | 30 | 0 | 0 | 7930.93 | 8082.47 | 11755.9 | 0.5000 | 0 | 0.0 |
| 4 | 7.841 | 3.826 | 229.56 | 30 | 0 | 0 | 4755.63 | 4591.77 | 6796.6 | 0.2667 | 1 | 0.5 |
| 6 | 6.166 | 4.865 | 291.92 | 30 | 0 | 0 | 3926.12 | 3901.85 | 5289.58 | 0.2000 | 2 | 0.82 |

## Source Reports

- docs/reports/scaling_demo_30tasks_2workers_20260318_123813.json
- docs/reports/scaling_demo_30tasks_4workers_20260318_123909.json
- docs/reports/scaling_demo_30tasks_6workers_20260318_124001.json
