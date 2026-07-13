"""One-shot crawl cycle for Windows Task Scheduler (or manual runs).

Runs the cost_info scheduler once (enqueue due/all crawl tasks), then drains the
worker queue until no more tasks are leased. Loads the repo-root .env first so
FILE_ASSET_DATABASE_URL / MinIO credentials are present, mirroring serve.py.

Usage (from file_asset_service/, with deps installed)::

    python -m app.crawl_cycle                 # incremental: only due sources
    python -m app.crawl_cycle --force         # force all enabled sources now
    python -m app.crawl_cycle --limit 20 --max-cycles 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # app/
SERVICE_DIR = HERE.parent                        # file_asset_service/
ROOT = SERVICE_DIR.parent                        # repo root


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[crawl_cycle] could not load .env: {exc}", flush=True)


def _run(force: bool, limit: int, max_cycles: int) -> dict:
    from app.cost_info_scheduler import run_scheduler
    from app.cost_info_worker import run_worker
    from app.database import get_session_factory, init_db

    init_db()
    SessionFactory = get_session_factory()

    with SessionFactory() as session:
        scheduler = run_scheduler(session, trigger="crawl_cycle", dry_run=False, force=force)

    worker_runs: list[dict] = []
    total_done = 0
    with SessionFactory() as session:
        for _ in range(max(1, max_cycles)):
            report = run_worker(session, limit=limit, trigger="crawl_cycle").to_dict()
            worker_runs.append(report.get("summary", {}))
            leased = int(report.get("leased_count", 0) or 0)
            total_done += int(report.get("done_count", 0) or 0)
            if leased == 0:
                break

    return {
        "scheduler": {
            "task_created": scheduler.get("task_created", 0),
            "source_due": scheduler.get("source_due", 0),
            "task_skipped_pending": scheduler.get("task_skipped_pending", 0),
            "task_skipped_disabled": scheduler.get("task_skipped_disabled", 0),
        },
        "worker_cycles": len(worker_runs),
        "worker_done_total": total_done,
        "worker_runs": worker_runs,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one cost_info crawl cycle (scheduler + worker drain).")
    parser.add_argument("--force", action="store_true", help="Ignore due-check; enqueue all enabled sources now.")
    parser.add_argument("--limit", type=int, default=20, help="Tasks per worker pass.")
    parser.add_argument("--max-cycles", type=int, default=20, help="Max worker passes to drain the queue.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _load_env()
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    report = _run(force=args.force, limit=args.limit, max_cycles=args.max_cycles)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
