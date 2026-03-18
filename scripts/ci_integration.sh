#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
REPORT_PATH="${REPORT_PATH:-docs/reports/ci_integration_report.json}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required"
  exit 1
fi

echo "Checking dashboard health shape..."
./scripts/check_dashboard_health.sh

echo "Running processed-events check..."
./scripts/check_processed_events.sh 45

echo "Running worker crash reassignment check..."
./scripts/check_worker_crash_reassignment.sh 4 360

echo "Running load generator integration check..."
python3 scripts/load_generator.py \
  --api "${API_URL}" \
  --tasks 25 \
  --timeout 180 \
  --poll-interval 1.2 \
  --submit-delay-ms 2 \
  --skill-mode compatible \
  --out "${REPORT_PATH}"

echo "Asserting integration report quality gates..."
python3 scripts/assert_integration_report.py --report "${REPORT_PATH}"

echo "PASS: integration checks completed"
