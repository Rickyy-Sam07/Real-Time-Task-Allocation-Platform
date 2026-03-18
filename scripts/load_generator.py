#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Task load generator for API gateway")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--tasks", type=int, default=100, help="Number of tasks to submit")
    parser.add_argument("--timeout", type=int, default=180, help="Polling timeout in seconds")
    parser.add_argument("--poll-interval", type=float, default=1.5, help="Polling interval in seconds")
    parser.add_argument("--submit-delay-ms", type=int, default=5, help="Delay between submissions in ms")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--skill-mode",
        choices=["compatible", "mixed"],
        default="compatible",
        help="compatible uses only current worker skill set; mixed can include unsupported skills",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    return parser.parse_args()


def http_json(method: str, url: str, payload: dict | None = None) -> dict | list:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    normalized = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def summarize(tasks: list[dict], selected_ids: set[str], elapsed_sec: float) -> dict:
    selected = [t for t in tasks if t.get("task_id") in selected_ids]
    status_counts: dict[str, int] = {}
    worker_counts: dict[str, int] = {}
    terminal = {"completed", "failed"}

    latencies_ms: list[float] = []
    for task in selected:
        status = str(task.get("status", "unknown"))
        status_counts[status] = status_counts.get(status, 0) + 1
        worker_id = task.get("worker_id")
        if worker_id:
            wid = str(worker_id)
            worker_counts[wid] = worker_counts.get(wid, 0) + 1

        if status in terminal:
            created = parse_iso(task.get("created_at"))
            updated = parse_iso(task.get("updated_at"))
            if created and updated:
                latencies_ms.append(max(0.0, (updated - created).total_seconds() * 1000.0))

    pending_like = 0
    for status, count in status_counts.items():
        if status not in terminal:
            pending_like += count

    summary = {
        "submitted": len(selected_ids),
        "observed": len(selected),
        "elapsed_sec": round(elapsed_sec, 3),
        "status_counts": status_counts,
        "pending_or_inflight": pending_like,
        "worker_distribution": worker_counts,
        "latency_ms": {
            "count": len(latencies_ms),
            "avg": round(statistics.mean(latencies_ms), 2) if latencies_ms else None,
            "p50": round(statistics.median(latencies_ms), 2) if latencies_ms else None,
            "max": round(max(latencies_ms), 2) if latencies_ms else None,
        },
    }
    return summary


def main() -> int:
    args = parse_args()
    random.seed(args.seed)

    api = args.api.rstrip("/")

    try:
        health = http_json("GET", f"{api}/health")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: API health check failed: {exc}")
        return 2

    if not isinstance(health, dict) or health.get("status") != "ok":
        print("ERROR: API health endpoint did not return status=ok")
        return 2

    zones = ["zone-a", "zone-b", "zone-c"]
    skill_pool = ["rescue", "medical"] if args.skill_mode == "compatible" else ["rescue", "medical", "logistics", "shelter"]

    task_ids: list[str] = []
    submit_start = time.time()
    for _ in range(args.tasks):
        priority = random.choice([1, 2, 3, 4, 5])
        duration = random.randint(20, 180)
        zone = random.choice(zones)
        max_retries = random.choice([1, 2, 3])
        skill_count = random.choice([0, 1, 2])
        skills = random.sample(skill_pool, k=skill_count)

        payload = {
            "priority": priority,
            "estimated_duration_sec": duration,
            "location_zone": zone,
            "skills_required": skills,
            "max_retries": max_retries,
        }

        try:
            resp = http_json("POST", f"{api}/task", payload)
        except urllib.error.HTTPError as exc:
            print(f"ERROR: submission failed with HTTP {exc.code}")
            return 3
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: submission failed: {exc}")
            return 3

        task_id = str(resp.get("task_id")) if isinstance(resp, dict) else ""
        if not task_id:
            print("ERROR: API response missing task_id")
            return 3
        task_ids.append(task_id)

        if args.submit_delay_ms > 0:
            time.sleep(args.submit_delay_ms / 1000.0)

    submit_elapsed = time.time() - submit_start
    print(f"Submitted {len(task_ids)} tasks in {submit_elapsed:.2f}s")

    selected = set(task_ids)
    start = time.time()
    last_summary: dict | None = None

    while True:
        tasks = http_json("GET", f"{api}/tasks")
        if not isinstance(tasks, list):
            print("ERROR: /tasks returned unexpected payload")
            return 4

        elapsed = time.time() - start
        last_summary = summarize(tasks, selected, elapsed)
        pending = int(last_summary["pending_or_inflight"])
        observed = int(last_summary["observed"])

        print(
            f"Progress: observed={observed}/{len(selected)} pending_or_inflight={pending} elapsed={elapsed:.1f}s",
            flush=True,
        )

        if observed == len(selected) and pending == 0:
            break
        if elapsed >= args.timeout:
            break
        time.sleep(max(0.2, args.poll_interval))

    assert last_summary is not None
    print("Summary:")
    print(json.dumps(last_summary, indent=2))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(last_summary, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote report: {out_path}")

    return 0 if int(last_summary["pending_or_inflight"]) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
