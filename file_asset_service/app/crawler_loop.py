"""Task-and-loop runtime for scheduled incremental and campaign crawl work.

The process intentionally does not crawl sites itself.  Every round only creates
due tasks, then drains the normal database-backed worker queue.  It is therefore
safe to run on more than one node: task leasing remains the concurrency boundary.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.cost_info_scheduler import run_scheduler
from app.cost_info_worker import run_worker
from app.database import get_session_factory, init_db
from app.storage import ObjectStore

HERE = Path(__file__).resolve().parent
SERVICE_DIR = HERE.parent
ROOT = SERVICE_DIR.parent


def utcnow() -> datetime:
    return datetime.now(UTC)


def run_loop_once(
    session: Session,
    *,
    now: datetime | None = None,
    worker_limit: int = 20,
    max_worker_cycles: int = 20,
    force: bool = False,
    dry_run: bool = False,
    trigger: str = "crawler_loop",
    worker_id: str | None = None,
    lease_seconds: int = 14400,
    storage: ObjectStore | None = None,
) -> dict:
    """Schedule due work once and drain ready tasks without busy-waiting."""

    current = now or utcnow()
    scheduler = run_scheduler(
        session,
        now=current,
        trigger=trigger,
        dry_run=dry_run,
        force=force,
    )
    worker_reports: list[dict] = []
    totals = {"leased_count": 0, "done_count": 0, "retry_count": 0, "dead_letter_count": 0}
    for _ in range(max(1, max_worker_cycles)):
        report = run_worker(
            session,
            limit=max(1, worker_limit),
            dry_run=dry_run,
            trigger=trigger,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=current,
            storage=storage,
        ).to_dict()
        worker_reports.append(report)
        summary = report["summary"]
        for key in totals:
            totals[key] += int(summary.get(key, 0) or 0)
        if not int(report.get("leased_count", 0) or 0):
            break

    return {
        "run_type": "crawler_loop_round",
        "run_at": current.isoformat(),
        "dry_run": dry_run,
        "scheduler": scheduler,
        "worker_cycles": len(worker_reports),
        "worker_summary": totals,
        "worker_reports": worker_reports,
    }


def run_loop(
    session_factory: sessionmaker[Session],
    *,
    interval_seconds: int = 7200,
    max_rounds: int | None = None,
    **round_kwargs,
) -> list[dict]:
    """Run repeated scheduler/worker rounds; ``max_rounds`` makes it testable."""

    reports: list[dict] = []
    round_count = 0
    while max_rounds is None or round_count < max_rounds:
        with session_factory() as session:
            reports.append(run_loop_once(session, **round_kwargs))
        round_count += 1
        if max_rounds is not None and round_count >= max_rounds:
            break
        time.sleep(max(1, interval_seconds))
    return reports


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[crawler_loop] could not load .env: {exc}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the task-and-loop cost-info crawler runtime.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--interval-seconds", type=int, default=7200)
    run.add_argument("--rounds", type=int, help="Stop after this many rounds; omit to run continuously.")
    run.add_argument("--once", action="store_true", help="Alias for --rounds 1.")
    run.add_argument("--worker-limit", type=int, default=20)
    run.add_argument("--max-worker-cycles", type=int, default=20)
    run.add_argument("--worker-id")
    run.add_argument("--lease-seconds", type=int, default=14400)
    run.add_argument("--force", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "run":
        raise ValueError(f"unsupported command: {args.command}")
    _load_env()
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    init_db()
    rounds = 1 if args.once else args.rounds
    reports = run_loop(
        get_session_factory(),
        interval_seconds=args.interval_seconds,
        max_rounds=rounds,
        worker_limit=args.worker_limit,
        max_worker_cycles=args.max_worker_cycles,
        force=args.force,
        dry_run=args.dry_run,
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
    )
    for report in reports:
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
