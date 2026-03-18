#!/usr/bin/env bash
set -euo pipefail

# Scaling demo runner:
# 1) Scale worker replicas
# 2) Generate load
# 3) Persist summary report under docs/reports/

TASKS="${1:-500}"
WORKERS="${2:-5}"
TIMEOUT="${3:-420}"
SKILL_MODE="${4:-compatible}"
API_URL="${API_URL:-http://localhost:8000}"
REPORT_DIR="docs/reports"
TS="$(date +%Y%m%d_%H%M%S)"
REPORT_PATH="${REPORT_DIR}/scaling_demo_${TASKS}tasks_${WORKERS}workers_${TS}.json"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required"
  exit 1
fi

if ! curl -fsS "${API_URL}/health" >/dev/null; then
  echo "ERROR: API gateway is not reachable at ${API_URL}"
  echo "Start stack first: docker compose up --build -d"
  exit 1
fi

echo "Scaling workers to ${WORKERS} with fast mode for demo throughput..."
WORKER_EXECUTION_MODE=fast \
WORKER_FAILURE_RATE=0.03 \
docker compose up -d --no-deps --scale worker="${WORKERS}" --force-recreate worker >/dev/null

echo "Restarting scheduler for clean worker view..."
docker compose up -d --no-deps --force-recreate scheduler >/dev/null
sleep 6

echo "Running load generator: tasks=${TASKS} timeout=${TIMEOUT}s skill_mode=${SKILL_MODE}"
python3 scripts/load_generator.py \
  --api "${API_URL}" \
  --tasks "${TASKS}" \
  --timeout "${TIMEOUT}" \
  --poll-interval 1.5 \
  --submit-delay-ms 2 \
  --skill-mode "${SKILL_MODE}" \
  --out "${REPORT_PATH}"

status=$?

echo "Restoring worker mode to normal..."
WORKER_EXECUTION_MODE=normal docker compose up -d --no-deps --force-recreate worker >/dev/null

echo "Report saved to ${REPORT_PATH}"
exit ${status}
