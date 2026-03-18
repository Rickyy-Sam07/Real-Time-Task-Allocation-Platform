#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assert integration report quality gates")
    parser.add_argument("--report", default="docs/reports/ci_integration_report.json")
    parser.add_argument("--max-failed", type=int, default=2)
    parser.add_argument("--max-skew-ratio", type=float, default=0.90)
    parser.add_argument("--min-workers-for-skew", type=int, default=2)
    parser.add_argument("--min-submitted-for-skew", type=int, default=10)
    return parser.parse_args()


def _load_report(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"FAIL: report not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    args = _parse_args()
    report = _load_report(Path(args.report))

    pending_or_inflight = int(report.get("pending_or_inflight", 0))
    if pending_or_inflight != 0:
        raise SystemExit(f"FAIL: pending_or_inflight must be 0, got {pending_or_inflight}")

    status_counts = report.get("status_counts", {})
    failed = int(status_counts.get("failed", 0))
    if failed > args.max_failed:
        raise SystemExit(f"FAIL: failed tasks {failed} exceeded max_failed={args.max_failed}")

    submitted = int(report.get("submitted", 0))
    worker_distribution = report.get("worker_distribution", {})

    if submitted >= args.min_submitted_for_skew and len(worker_distribution) >= args.min_workers_for_skew:
        max_share = max(worker_distribution.values()) / submitted
        if max_share > args.max_skew_ratio:
            raise SystemExit(
                "FAIL: worker distribution skew too high "
                f"(max_share={max_share:.3f}, threshold={args.max_skew_ratio:.3f})"
            )

    print(
        "PASS: integration report assertions passed "
        f"(pending_or_inflight={pending_or_inflight}, failed={failed}, submitted={submitted})"
    )


if __name__ == "__main__":
    main()
