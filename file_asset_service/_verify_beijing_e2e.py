"""2026-08-10 verify: 端到端跑 1 个北京 archive,等 daemon thread 完成,实时打印事件。"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(Path(__file__).parent.parent / ".env"))

from app.config import get_settings  # noqa: E402
from app.database import get_session_factory  # noqa: E402
from app.info_price_parse import _JOBS, submit_parse_job  # noqa: E402
from app.storage import S3ObjectStore  # noqa: E402

ARCHIVE_ID = "0b365753-dfab-4a6b-9989-0fc5a2317f2f"  # 2024年12月


def main() -> None:
    settings = get_settings()
    storage = S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region_name,
    )
    factory = get_session_factory()

    print(f"=== submit 北京 archive={ARCHIVE_ID} ===", flush=True)
    print(f"PDF: 2024年12月北京工程造价信息.pdf", flush=True)
    print(f"MinerU API: {settings.mineru_api_url}", flush=True)
    print(flush=True)

    task_id = submit_parse_job(
        session_factory=factory,
        storage=storage,
        settings=settings,
        archive_id=ARCHIVE_ID,
        year=2024,
        period="12",
        city_code="北京",
    )
    print(f"task_id = {task_id}", flush=True)

    job = _JOBS[task_id]
    last_seq = 0
    t0 = time.time()
    while not job.done_event.is_set():
        time.sleep(2)
        elapsed = int(time.time() - t0)
        with job.lock:
            events = list(job.events)
        for ev in events:
            if ev.seq > last_seq:
                tag = f"[{elapsed:3d}s] seq={ev.seq:4d} {ev.status:9s}"
                if ev.line:
                    tag += f" | {ev.line[:200]}"
                print(tag, flush=True)
                last_seq = ev.seq

    with job.lock:
        events = list(job.events)
    for ev in events:
        if ev.seq > last_seq:
            tag = f"[final] seq={ev.seq:4d} {ev.status:9s}"
            if ev.line:
                tag += f" | {ev.line[:200]}"
            print(tag, flush=True)
            last_seq = ev.seq

    print(flush=True)
    print(f"=== 终态 status={job.status} ===", flush=True)
    if job.status == "succeeded":
        print(f"✅ final_xlsx_key = {job.final_xlsx_key}", flush=True)
    else:
        print(f"❌ error_code    = {job.error_code}", flush=True)
        print(f"❌ error_message = {(job.error_message or '')[:1500]}", flush=True)


if __name__ == "__main__":
    main()