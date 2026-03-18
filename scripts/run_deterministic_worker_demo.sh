#!/usr/bin/env bash
set -euo pipefail

# Runs workers in deterministic mode for a short demo, submits tasks,
# prints final outcomes, and restores workers to normal mode.

API_URL="${API_URL:-http://localhost:8000}"
TASK_COUNT="${1:-6}"
TIMEOUT_SEC="${2:-90}"
DETERMINISTIC_FAIL_PERCENT="${WORKER_DETERMINISTIC_FAIL_PERCENT:-35}"
DETERMINISTIC_DELAY_SEC="${WORKER_DETERMINISTIC_DELAY_SEC:-1.0}"

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required"
  exit 1
fi

if ! curl -fsS "${API_URL}/health" >/dev/null; then
  echo "ERROR: API gateway is not reachable at ${API_URL}"
  echo "Start stack first: docker compose up --build -d"
  exit 1
fi

echo "Switching workers to deterministic mode..."
WORKER_EXECUTION_MODE=deterministic \
WORKER_DETERMINISTIC_FAIL_PERCENT="${DETERMINISTIC_FAIL_PERCENT}" \
WORKER_DETERMINISTIC_DELAY_SEC="${DETERMINISTIC_DELAY_SEC}" \
docker compose up -d --no-deps --force-recreate worker >/dev/null

echo "Resetting scheduler state for a clean demo run..."
docker compose up -d --no-deps --force-recreate scheduler >/dev/null
sleep 6

declare -a TASK_IDS=()
for _ in $(seq 1 "${TASK_COUNT}"); do
  response="$(curl -fsS -X POST "${API_URL}/task" -H "Content-Type: application/json" -d '{"priority":3,"estimated_duration_sec":20,"location_zone":"zone-a","skills_required":[],"max_retries":1}')"
  task_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["task_id"])' "${response}")"
  TASK_IDS+=("${task_id}")
  echo "Submitted task ${task_id}"
done

ids_csv="$(IFS=,; echo "${TASK_IDS[*]}")"

echo "Waiting for terminal task outcomes..."
start_ts="$(date +%s)"
while true; do
  result="$(curl -fsS "${API_URL}/tasks")"
  summary="$(python3 - <<'PY' "${result}" "${ids_csv}"
import json
import sys

all_tasks = json.loads(sys.argv[1])
selected = set(sys.argv[2].split(','))
terminals = {"completed", "failed"}

index = {t.get("task_id"): t for t in all_tasks if t.get("task_id") in selected}
ready = all(index.get(tid, {}).get("status") in terminals for tid in selected)

print("READY" if ready else "WAIT")
for tid in selected:
    status = index.get(tid, {}).get("status", "missing")
    print(f"{tid} {status}")
PY
)"

  first_line="$(printf '%s\n' "${summary}" | head -n 1)"
  if [ "${first_line}" = "READY" ]; then
    echo "Final outcomes:"
    printf '%s\n' "${summary}" | tail -n +2
    break
  fi

  now_ts="$(date +%s)"
  if [ $((now_ts - start_ts)) -ge "${TIMEOUT_SEC}" ]; then
    echo "Timeout waiting for terminal outcomes (${TIMEOUT_SEC}s). Latest status snapshot:"
    printf '%s\n' "${summary}" | tail -n +2
    break
  fi

  sleep 1
done

echo "Restoring workers to normal mode..."
WORKER_EXECUTION_MODE=normal docker compose up -d --no-deps --force-recreate worker >/dev/null

echo "Restarting scheduler after restore..."
docker compose up -d --no-deps --force-recreate scheduler >/dev/null

echo "Done"
