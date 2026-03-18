#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required"
  exit 1
fi

payload="$(curl -fsS "${API_URL}/dashboard/health")"

python3 - <<'PY' "${payload}"
import json
import sys

obj = json.loads(sys.argv[1])

if obj.get("status") != "ok":
    raise SystemExit("FAIL: dashboard health status is not ok")

for key in ("service", "kafka", "websocket_connections", "read_model", "dedupe_cache"):
    if key not in obj:
        raise SystemExit(f"FAIL: missing top-level key: {key}")

if not isinstance(obj["websocket_connections"], int) or obj["websocket_connections"] < 0:
    raise SystemExit("FAIL: websocket_connections must be a non-negative integer")

read_model = obj["read_model"]
for key in ("tasks_total", "workers_total", "tasks_by_status", "workers_by_status", "in_flight_tasks"):
    if key not in read_model:
        raise SystemExit(f"FAIL: missing read_model key: {key}")

for key in ("tasks_total", "workers_total", "in_flight_tasks"):
    value = read_model[key]
    if not isinstance(value, int) or value < 0:
        raise SystemExit(f"FAIL: read_model.{key} must be a non-negative integer")

tasks_by_status = read_model["tasks_by_status"]
workers_by_status = read_model["workers_by_status"]

expected_task_keys = {"pending", "assigned", "in_progress", "completed", "failed"}
expected_worker_keys = {"available", "busy", "offline"}

if not isinstance(tasks_by_status, dict):
    raise SystemExit("FAIL: tasks_by_status must be an object")
if not isinstance(workers_by_status, dict):
    raise SystemExit("FAIL: workers_by_status must be an object")

missing_task = expected_task_keys - set(tasks_by_status.keys())
missing_worker = expected_worker_keys - set(workers_by_status.keys())
if missing_task:
    raise SystemExit(f"FAIL: missing task status keys: {sorted(missing_task)}")
if missing_worker:
    raise SystemExit(f"FAIL: missing worker status keys: {sorted(missing_worker)}")

for scope, data in (("tasks_by_status", tasks_by_status), ("workers_by_status", workers_by_status)):
    for k, v in data.items():
        if not isinstance(v, int) or v < 0:
            raise SystemExit(f"FAIL: {scope}.{k} must be a non-negative integer")

computed_in_flight = tasks_by_status["assigned"] + tasks_by_status["in_progress"]
if computed_in_flight != read_model["in_flight_tasks"]:
    raise SystemExit(
        "FAIL: in_flight_tasks mismatch "
        f"(expected {computed_in_flight}, got {read_model['in_flight_tasks']})"
    )

cache = obj["dedupe_cache"]
for key in ("seen_event_ids", "max_seen_events"):
    if key not in cache:
        raise SystemExit(f"FAIL: missing dedupe_cache key: {key}")
    if not isinstance(cache[key], int) or cache[key] < 0:
        raise SystemExit(f"FAIL: dedupe_cache.{key} must be a non-negative integer")

print(
    "PASS: dashboard health shape valid "
    f"(tasks_total={read_model['tasks_total']}, workers_total={read_model['workers_total']})"
)
PY
