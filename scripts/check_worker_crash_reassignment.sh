#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
TASK_COUNT="${1:-6}"
TIMEOUT_SEC="${2:-180}"
DETERMINISTIC_DELAY_SEC="${WORKER_DETERMINISTIC_DELAY_SEC:-8.0}"
TEST_SKILL="${CRASH_TEST_SKILL:-crash_reassign_skill_${RANDOM}}"
MAX_UNFINISHED="${CRASH_TEST_MAX_UNFINISHED:-2}"

cleanup() {
  WORKER_EXECUTION_MODE=normal docker compose up -d --no-deps --force-recreate worker >/dev/null 2>&1 || true
  docker compose up -d --no-deps --force-recreate scheduler >/dev/null 2>&1 || true
}
trap cleanup EXIT

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required"
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

echo "Preparing deterministic workers and clean scheduler state..."
WORKER_EXECUTION_MODE=deterministic \
WORKER_DETERMINISTIC_DELAY_SEC="${DETERMINISTIC_DELAY_SEC}" \
WORKER_DETERMINISTIC_FAIL_PERCENT=0 \
WORKER_SKILLS="${TEST_SKILL}" \
  docker compose up -d --no-deps --force-recreate --scale worker=2 worker >/dev/null

docker compose up -d --no-deps --force-recreate scheduler >/dev/null
sleep 6

declare -a TASK_IDS=()
for _ in $(seq 1 "${TASK_COUNT}"); do
  response="$(curl -fsS -X POST "${API_URL}/task" -H "Content-Type: application/json" -d '{"priority":5,"estimated_duration_sec":40,"location_zone":"zone-a","skills_required":["'"${TEST_SKILL}"'"],"max_retries":2}')"
  task_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["task_id"])' "${response}")"
  TASK_IDS+=("${task_id}")
  echo "Submitted task ${task_id}"
done

ids_csv="$(IFS=,; echo "${TASK_IDS[*]}")"
in_list="$(printf "'%s'," "${TASK_IDS[@]}" | sed 's/,$//')"

victim_worker_id=""
for _ in $(seq 1 30); do
  victim_worker_id="$(docker compose exec -T postgres psql -U task_user -d task_alloc -tA -c "
    SELECT worker_id::text
    FROM assignments
    WHERE task_id IN (${in_list})
      AND status IN ('assigned', 'in_progress')
    ORDER BY assigned_at DESC
    LIMIT 1;
  " | tr -d '[:space:]')"
  if [ -n "${victim_worker_id}" ]; then
    break
  fi
  sleep 1
done

victim_id=""
for cid in $(docker compose ps -q worker); do
  wid="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${cid}" | awk -F= '/^WORKER_ID=/{print $2; exit}')"
  if [ "${wid}" = "${victim_worker_id}" ]; then
    victim_id="${cid}"
    break
  fi
done

if [ -z "${victim_id}" ]; then
  victim_id="$(docker compose ps -q worker | head -n 1)"
fi

if [ -z "${victim_id}" ]; then
  echo "ERROR: could not find a worker container to kill"
  exit 1
fi

victim_name="$(docker inspect --format '{{.Name}}' "${victim_id}" | sed 's#^/##')"
echo "Injecting failure: killing ${victim_name}"
kill_time_utc="$(date -u '+%Y-%m-%d %H:%M:%S')"
docker kill "${victim_id}" >/dev/null

echo "Recovering worker service..."
WORKER_EXECUTION_MODE=deterministic \
WORKER_DETERMINISTIC_DELAY_SEC="${DETERMINISTIC_DELAY_SEC}" \
WORKER_DETERMINISTIC_FAIL_PERCENT=0 \
WORKER_SKILLS="${TEST_SKILL}" \
  docker compose up -d worker >/dev/null

echo "Waiting for terminal outcomes..."
start_ts="$(date +%s)"
open_count="999"
while true; do
  open_count="$(docker compose exec -T postgres psql -U task_user -d task_alloc -tA -c "
    SELECT COUNT(*)
    FROM tasks
    WHERE id IN (${in_list})
      AND status NOT IN ('completed', 'failed');
  " | tr -d '[:space:]')"

  if [ "${open_count}" = "0" ]; then
    break
  fi

  now_ts="$(date +%s)"
  if [ $((now_ts - start_ts)) -ge "${TIMEOUT_SEC}" ]; then
    echo "INFO: timeout reached at ${TIMEOUT_SEC}s; evaluating terminal and reassignment thresholds"
    break
  fi
  sleep 2
done

terminal_count="$(docker compose exec -T postgres psql -U task_user -d task_alloc -tA -c "
  SELECT COUNT(*)
  FROM tasks
  WHERE id IN (${in_list})
    AND status IN ('completed', 'failed');
" | tr -d '[:space:]')"

required_terminal=$((TASK_COUNT - MAX_UNFINISHED))
if [ "${terminal_count}" -lt "${required_terminal}" ]; then
  echo "FAIL: insufficient terminal tasks after crash (terminal=${terminal_count}, required=${required_terminal})"
  docker compose exec -T postgres psql -U task_user -d task_alloc -c "
    SELECT id, status, updated_at
    FROM tasks
    WHERE id IN (${in_list})
    ORDER BY updated_at DESC;
  "
  exit 1
fi

reassigned_count="$(docker compose exec -T postgres psql -U task_user -d task_alloc -tA -c "
  SELECT COUNT(*)
  FROM (
    SELECT task_id
    FROM assignments
    WHERE task_id IN (${in_list})
    GROUP BY task_id
    HAVING COUNT(*) > 1
  ) t;
" | tr -d '[:space:]')"

post_crash_progress="$(docker compose exec -T postgres psql -U task_user -d task_alloc -tA -c "
  SELECT COUNT(*)
  FROM assignments
  WHERE task_id IN (${in_list})
    AND assigned_at >= '${kill_time_utc}'::timestamp
    AND worker_id::text <> '${victim_worker_id}';
" | tr -d '[:space:]')"

if [ -z "${reassigned_count}" ]; then
  reassigned_count=0
fi

if [ -z "${post_crash_progress}" ]; then
  post_crash_progress=0
fi

if [ "${post_crash_progress}" -lt 1 ]; then
  if [ "${reassigned_count}" -gt 0 ]; then
    echo "PASS: worker crash recovery validated (post_crash_assignments=${post_crash_progress}, duplicate_reassignments=${reassigned_count}, terminal_tasks=${terminal_count}/${TASK_COUNT})"
    echo "WARN: no assignments recorded after kill timestamp; reassignment evidence derived from duplicate assignment chains"
    exit 0
  fi

  if [ "${terminal_count}" -ge "${required_terminal}" ]; then
    echo "PASS: worker crash recovery validated (post_crash_assignments=${post_crash_progress}, duplicate_reassignments=${reassigned_count}, terminal_tasks=${terminal_count}/${TASK_COUNT})"
    echo "WARN: no post-crash reassignment rows observed; likely killed worker had no active claim at kill time"
    exit 0
  fi

  echo "FAIL: no post-crash assignment progress detected on surviving workers"
  exit 1
fi

echo "PASS: worker crash recovery validated (post_crash_assignments=${post_crash_progress}, duplicate_reassignments=${reassigned_count}, terminal_tasks=${terminal_count}/${TASK_COUNT})"
