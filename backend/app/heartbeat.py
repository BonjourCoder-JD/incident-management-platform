"""Failure visibility for the long-running services (Milestone 6 Phase D).

The worker and the daily demo reset record the outcome of every attempt in a small status file in
the container's /tmp (STATUS_DIR). Their Docker health checks run `python -m app.cli health-check
NAME --max-age SECONDS`, so `docker compose ps` shows "unhealthy" when the latest attempt failed or
the service stopped making progress. No external monitoring service is involved.
"""
import json
import os
import time
from pathlib import Path


def status_dir() -> Path:
    return Path(os.environ.get("STATUS_DIR", "/tmp/imp-status"))


def record(name: str, ok: bool, detail: str = "") -> None:
    """Never raises: a status file problem must not stop the service it describes."""
    try:
        directory = status_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / f"{name}.json"
        partial = path.with_suffix(".partial")
        partial.write_text(json.dumps({"ok": ok, "at": time.time(), "detail": detail[:200]}), encoding="utf-8")
        partial.replace(path)
    except OSError:
        pass


def check(name: str, max_age_seconds: float, now: float | None = None) -> tuple[bool, str]:
    """(healthy, reason): the latest attempt succeeded and is at most max_age_seconds old."""
    try:
        status = json.loads((status_dir() / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "no status recorded yet"
    age = (time.time() if now is None else now) - float(status.get("at", 0))
    if not status.get("ok"):
        return False, f"latest attempt failed {int(age)} s ago: {status.get('detail') or 'see the log'}"
    if age > max_age_seconds:
        return False, f"no successful attempt for {int(age)} s (limit {int(max_age_seconds)} s)"
    return True, f"ok {int(age)} s ago"
