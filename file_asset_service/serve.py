"""Persistent launcher for the file-asset console (Windows).

Modes (run with the project venv python):
  python serve.py                load .env, validate NAS services, run uvicorn
  python serve.py --install-task create the onlogon scheduled task and start it
  python serve.py --uninstall-task delete the scheduled task

Why this exists: config.py reads os.getenv (not dotenv), so .env must be loaded
into the environment before uvicorn imports the app. This launcher validates
the shared NAS PostgreSQL connection before starting and tees stdout/stderr to
console_service.log so the headless task leaves a trace.
"""

from __future__ import annotations

import os
import subprocess
import sys
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


def _check_database() -> None:
    """Require the configured NAS PostgreSQL to be reachable before startup.

    A failed connection is a configuration/network failure, not an invitation to
    start a local database or keep retrying in the background.  Exiting here
    keeps office and home deployments on the single shared NAS catalog.
    """
    os.chdir(HERE)
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from app.config import RuntimeConfigurationError, get_settings
    from app.database import init_db

    try:
        get_settings()
    except RuntimeConfigurationError as exc:
        raise SystemExit(f"[serve] configuration error: {exc}") from exc

    try:
        init_db()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"[serve] database unavailable: {exc}") from exc
    # The launcher has completed the migration before Uvicorn imports the app.
    # Avoid performing the same schema work a second time in the app lifespan:
    # a long-running crawler may hold regular data locks while the console is
    # being restarted.
    os.environ["FILE_ASSET_SCHEMA_READY"] = "1"
    print("[serve] shared NAS database ready", flush=True)


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


def main() -> None:
    if "--install-task" in sys.argv:
        install_task()
        return
    if "--install-docker-autostart" in sys.argv:
        raise SystemExit(
            "[serve] --install-docker-autostart was removed: PostgreSQL runs only on the NAS."
        )
    if "--uninstall-task" in sys.argv:
        uninstall_task()
        return
    _load_env()
    _install_tee()
    _check_database()
    _run_server()


if __name__ == "__main__":
    main()
