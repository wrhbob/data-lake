"""Persistent launcher for the file-asset console (Windows).

Modes (run with the project venv python):
  python serve.py                load .env, wait for PostgreSQL, run uvicorn
  python serve.py --install-task create the onlogon scheduled task and start it
  python serve.py --uninstall-task delete the scheduled task

Why this exists: config.py reads os.getenv (not dotenv), so .env must be loaded
into the environment before uvicorn imports the app. This launcher also waits
for PostgreSQL to be ready (so it survives the boot/logon race with Docker) and
tees stdout/stderr to console_service.log so the headless task leaves a trace.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent  # file_asset_service
ROOT = HERE.parent                        # data_lake_handoff
LOG_PATH = HERE / "console_service.log"
TASK_NAME = "file-asset-console"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[serve] could not load .env via python-dotenv: {exc}", flush=True)


class _Tee:
    def __init__(self, stream, log) -> None:
        self._stream = stream
        self._log = log

    def write(self, data):
        try:
            self._stream.write(data)
            self._stream.flush()
        except Exception:
            pass
        try:
            self._log.write(data)
        except Exception:
            pass
        return len(data)

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass
        try:
            self._log.flush()
        except Exception:
            pass

    def isatty(self):
        return False

    def fileno(self):
        return self._log.fileno()

    def writable(self):
        return True

    def reconfigure(self, *args, **kwargs):
        pass


def _install_tee() -> None:
    log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    log.write(f"\n==== serve.py start ====\n")
    sys.stdout = _Tee(sys.stdout, log)  # type: ignore[assignment]
    sys.stderr = _Tee(sys.stderr, log)  # type: ignore[assignment]


def _wait_for_db(max_tries: int = 48, delay: float = 5.0) -> None:
    os.chdir(HERE)
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from app.database import init_db

    last: object = None
    for attempt in range(1, max_tries + 1):
        try:
            init_db()
            print(f"[serve] database ready (attempt {attempt}/{max_tries})", flush=True)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"[serve] waiting for database ({attempt}/{max_tries}): {exc}", flush=True)
            time.sleep(delay)
    raise SystemExit(f"[serve] database not ready after {max_tries} attempts: {last}")


def _run_server() -> None:
    import uvicorn

    host = os.getenv("FILE_ASSET_HOST", "0.0.0.0")
    port = int(os.getenv("FILE_ASSET_PORT", "8010"))
    print(f"[serve] starting uvicorn on {host}:{port}", flush=True)
    uvicorn.run("app.main:create_app", factory=True, host=host, port=port)


def install_task() -> None:
    python = HERE / ".venv" / "Scripts" / "python.exe"
    serve = HERE / "serve.py"
    if not python.exists():
        raise SystemExit(f"[serve] venv python not found: {python}")
    task_cmd = f'"{python}" "{serve}"'
    create = [
        "schtasks", "/create",
        "/tn", TASK_NAME,
        "/tr", task_cmd,
        "/sc", "onlogon",
        "/rl", "HIGHEST",
        "/f",
    ]
    print("[serve] creating scheduled task:", " ".join(create))
    subprocess.run(create, check=True)
    print(f"[serve] starting task '{TASK_NAME}' now ...")
    subprocess.run(["schtasks", "/run", "/tn", TASK_NAME], check=True)
    print(f"[serve] done. The console will auto-start at every login.")


def uninstall_task() -> None:
    subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], check=False)
    print(f"[serve] task '{TASK_NAME}' removed.")


def install_docker_autostart() -> None:
    """Create an onlogon task that launches Docker Desktop, so the postgres/minio
    containers (restart=always) come back at login and the console can reach PG."""
    exe = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
    if not exe.exists():
        print(f"[serve] Docker Desktop.exe not found at {exe} — skipping docker-autostart")
        return
    name = "docker-autostart"
    cmd = [
        "schtasks", "/create",
        "/tn", name,
        "/tr", f'"{exe}"',
        "/sc", "onlogon",
        "/rl", "HIGHEST",
        "/f",
    ]
    subprocess.run(cmd, check=True)
    print(f"[serve] created task '{name}' (launches Docker Desktop at every login)")


def main() -> None:
    if "--install-task" in sys.argv:
        install_task()
        return
    if "--install-docker-autostart" in sys.argv:
        install_docker_autostart()
        return
    if "--uninstall-task" in sys.argv:
        uninstall_task()
        return
    _load_env()
    _install_tee()
    _wait_for_db()
    _run_server()


if __name__ == "__main__":
    main()
