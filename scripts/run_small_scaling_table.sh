#!/usr/bin/env bash
set -euo pipefail

# Small scaling matrix runner:
# Executes benchmark runs for worker counts 2, 4, 6 and writes a compact table report.

TASKS="${1:-300}"
TIMEOUT="${2:-600}"
SKILL_MODE="${3:-compatible}"
WORKER_SERIES="${4:-2,4,6}"
API_URL="${API_URL:-http://localhost:8000}"
REPORT_DIR="docs/reports"
TS="$(date +%Y%m%d_%H%M%S)"
MATRIX_JSON="${REPORT_DIR}/small_scaling_table_${TASKS}tasks_${TS}.json"
MATRIX_MD="${REPORT_DIR}/small_scaling_table_${TASKS}tasks_${TS}.md"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required"
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required"
  exit 1
fi

if ! curl -fsS "${API_URL}/health" >/dev/null; then
  echo "ERROR: API gateway is not reachable at ${API_URL}"
  echo "Start stack first: docker compose up --build -d"
  exit 1
fi

mkdir -p "${REPORT_DIR}"

cleanup() {
  # Restore a predictable default runtime footprint after matrix execution.
  WORKER_EXECUTION_MODE=normal docker compose up -d --no-deps --scale worker=2 --force-recreate worker >/dev/null 2>&1 || true
  docker compose up -d --no-deps --force-recreate scheduler >/dev/null 2>&1 || true
}
trap cleanup EXIT

IFS=',' read -r -a WORKERS <<< "${WORKER_SERIES}"
declare -a REPORT_FILES=()

for w in "${WORKERS[@]}"; do
  w_trimmed="$(echo "${w}" | tr -d '[:space:]')"
  if [[ -z "${w_trimmed}" ]]; then
    continue
  fi

  echo "Running scaling pass: workers=${w_trimmed} tasks=${TASKS} timeout=${TIMEOUT}s skill_mode=${SKILL_MODE}"
  ./scripts/run_scaling_demo.sh "${TASKS}" "${w_trimmed}" "${TIMEOUT}" "${SKILL_MODE}"

  latest="$(ls -1t "${REPORT_DIR}"/scaling_demo_"${TASKS}"tasks_"${w_trimmed}"workers_*.json 2>/dev/null | head -n 1 || true)"
  if [[ -z "${latest}" ]]; then
    echo "ERROR: could not find generated report for workers=${w_trimmed}"
    exit 1
  fi
  REPORT_FILES+=("${latest}")
done

python3 - <<'PY' "${TASKS}" "${SKILL_MODE}" "${MATRIX_JSON}" "${MATRIX_MD}" "${REPORT_FILES[@]}"
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

if len(sys.argv) < 6:
    raise SystemExit("ERROR: expected args: tasks skill_mode matrix_json matrix_md report_files...")

tasks = int(sys.argv[1])
skill_mode = sys.argv[2]
out_json = Path(sys.argv[3])
out_md = Path(sys.argv[4])
report_files = [Path(p) for p in sys.argv[5:]]

rows = []
for report in report_files:
    data = json.loads(report.read_text(encoding="utf-8"))
    stem = report.stem
    workers = None
    # stem format: scaling_demo_{tasks}tasks_{workers}workers_{timestamp}
    parts = stem.split("_")
    for i, token in enumerate(parts):
        if token.endswith("tasks") and i + 1 < len(parts):
            nxt = parts[i + 1]
            if nxt.endswith("workers"):
                workers = int(nxt.replace("workers", ""))
                break

    if workers is None:
        raise SystemExit(f"ERROR: unable to parse worker count from report: {report}")

    elapsed = float(data.get("elapsed_sec", 0.0))
    throughput_tps = (tasks / elapsed) if elapsed > 0 else 0.0
    throughput_tpm = throughput_tps * 60.0
    status_counts = data.get("status_counts", {})
    worker_dist = data.get("worker_distribution", {})
    dist_values = list(worker_dist.values())

    rows.append(
        {
            "workers": workers,
            "report": str(report),
            "elapsed_sec": round(elapsed, 3),
            "throughput_tps": round(throughput_tps, 3),
            "throughput_tpm": round(throughput_tpm, 2),
            "completed": int(status_counts.get("completed", 0)),
            "failed": int(status_counts.get("failed", 0)),
            "pending_or_inflight": int(data.get("pending_or_inflight", 0)),
            "latency_avg_ms": data.get("latency_ms", {}).get("avg"),
            "latency_p50_ms": data.get("latency_ms", {}).get("p50"),
            "latency_max_ms": data.get("latency_ms", {}).get("max"),
            "max_worker_share": round((max(dist_values) / tasks), 4) if dist_values else 0.0,
            "fairness_spread": (max(dist_values) - min(dist_values)) if dist_values else 0,
            "fairness_stddev": round(statistics.pstdev(dist_values), 2) if len(dist_values) > 1 else 0.0,
        }
    )

rows.sort(key=lambda r: r["workers"])

payload = {
    "run_label": f"small_scaling_table_{tasks}tasks",
    "tasks": tasks,
    "skill_mode": skill_mode,
    "rows": rows,
}
out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

md = []
md.append(f"# Small Scaling Table ({tasks} Tasks)")
md.append("")
md.append("## Scope")
md.append("")
md.append(f"- Worker counts: {', '.join(str(r['workers']) for r in rows)}")
md.append(f"- Skill mode: {skill_mode}")
md.append("")
md.append("## Results")
md.append("")
md.append("| Workers | Elapsed (s) | Throughput (tasks/s) | Throughput (tasks/min) | Completed | Failed | Pending/In-flight | Avg Lat (ms) | P50 Lat (ms) | Max Lat (ms) | Max Worker Share | Fairness Spread | Fairness Stddev |")
md.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
for r in rows:
    md.append(
        f"| {r['workers']} | {r['elapsed_sec']} | {r['throughput_tps']} | {r['throughput_tpm']} | {r['completed']} | {r['failed']} | {r['pending_or_inflight']} | {r['latency_avg_ms']} | {r['latency_p50_ms']} | {r['latency_max_ms']} | {r['max_worker_share']:.4f} | {r['fairness_spread']} | {r['fairness_stddev']} |"
    )

md.append("")
md.append("## Source Reports")
md.append("")
for r in rows:
    md.append(f"- {r['report']}")

out_md.write_text("\n".join(md) + "\n", encoding="utf-8")

print(f"Wrote {out_json}")
print(f"Wrote {out_md}")
PY

echo "Small scaling table completed"
echo "JSON: ${MATRIX_JSON}"
echo "Markdown: ${MATRIX_MD}"
