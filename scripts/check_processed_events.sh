#!/usr/bin/env bash
set -euo pipefail

# Quick integration check:
# 1) Capture processed_events counts for scheduler and api_gateway_read_model.
# 2) Publish one task via API.
# 3) Wait for both counters to increase.

POSTGRES_USER="task_user"
POSTGRES_DB="task_alloc"
API_URL="http://localhost:8000"
TIMEOUT_SEC="${1:-20}"

get_count() {
  local consumer_name="$1"
  docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA \
    -c "SELECT COUNT(*) FROM processed_events WHERE consumer_name = '${consumer_name}';" | tr -d '[:space:]'
}

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed or not in PATH."
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is not installed or not in PATH."
  exit 1
fi

if ! curl -fsS "${API_URL}/health" >/dev/null; then
  echo "ERROR: API gateway is not reachable at ${API_URL}."
  echo "Start stack first: docker compose up --build -d"
  exit 1
fi

before_api="$(get_count "api_gateway_read_model")"
before_scheduler="$(get_count "scheduler")"

echo "Before: api_gateway_read_model=${before_api}, scheduler=${before_scheduler}"

payload='{"priority":3,"estimated_duration_sec":25,"location_zone":"zone-a","skills_required":[],"max_retries":1}'
response="$(curl -fsS -X POST "${API_URL}/task" -H "Content-Type: application/json" -d "${payload}")"
task_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["task_id"])' "${response}")"

echo "Published task_id=${task_id}"

deadline=$((SECONDS + TIMEOUT_SEC))
while [ "$SECONDS" -lt "$deadline" ]; do
  after_api="$(get_count "api_gateway_read_model")"
  after_scheduler="$(get_count "scheduler")"

  if [ "$after_api" -gt "$before_api" ] && [ "$after_scheduler" -gt "$before_scheduler" ]; then
    echo "PASS: counts increased"
    echo "After: api_gateway_read_model=${after_api}, scheduler=${after_scheduler}"
    exit 0
  fi

  sleep 1
done

after_api="$(get_count "api_gateway_read_model")"
after_scheduler="$(get_count "scheduler")"
echo "FAIL: counts did not increase within ${TIMEOUT_SEC}s"
echo "After: api_gateway_read_model=${after_api}, scheduler=${after_scheduler}"
exit 1
