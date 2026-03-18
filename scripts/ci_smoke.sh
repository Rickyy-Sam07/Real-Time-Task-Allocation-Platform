#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
TIMEOUT_SEC="${1:-120}"

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required"
  exit 1
fi

echo "Checking API health..."
curl -fsS "${API_URL}/health" >/dev/null

echo "Checking dashboard health shape..."
./scripts/check_dashboard_health.sh

echo "Waiting for at least one real worker heartbeat..."
start_ts="$(date +%s)"
while true; do
  health_payload="$(curl -fsS "${API_URL}/dashboard/health")"
  active_workers="$(python3 - <<'PY' "${health_payload}"
import json
import sys

obj = json.loads(sys.argv[1])
workers = obj.get("read_model", {}).get("workers_by_status", {})
available = int(workers.get("available", 0))
busy = int(workers.get("busy", 0))
print(available + busy)
PY
)"

  if [ "${active_workers}" -ge 1 ]; then
    echo "Detected active worker count=${active_workers}"
    break
  fi

  now_ts="$(date +%s)"
  if [ $((now_ts - start_ts)) -ge "${TIMEOUT_SEC}" ]; then
    echo "FAIL: no active workers detected within ${TIMEOUT_SEC}s"
    exit 1
  fi

  sleep 1
done

echo "Submitting task..."
task_resp="$(curl -fsS -X POST "${API_URL}/task" -H "Content-Type: application/json" -d '{"priority":5,"estimated_duration_sec":20,"location_zone":"zone-a","skills_required":["rescue"],"max_retries":1}')"
task_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["task_id"])' "${task_resp}")"
echo "Submitted task_id=${task_id}"

echo "Waiting for terminal state..."
start_ts="$(date +%s)"
while true; do
  tasks="$(curl -fsS "${API_URL}/tasks")"
  status="$(python3 -c 'import json,sys
tasks=json.load(sys.stdin)
task_id=sys.argv[1]
status="missing"
for item in tasks:
    if item.get("task_id")==task_id:
        status=item.get("status","unknown")
        break
print(status)' "${task_id}" <<< "${tasks}")"

  if [[ "${status}" == "completed" || "${status}" == "failed" ]]; then
    echo "PASS: task reached terminal status=${status}"
    exit 0
  fi

  now_ts="$(date +%s)"
  if [ $((now_ts - start_ts)) -ge "${TIMEOUT_SEC}" ]; then
    echo "FAIL: task did not reach terminal state within ${TIMEOUT_SEC}s (status=${status})"
    exit 1
  fi

  sleep 1
done
