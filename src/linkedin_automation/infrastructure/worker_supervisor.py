"""Single-instance Windows helper: start at sign-in and restart only this worker."""

import json
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

from linkedin_automation.infrastructure.search_worker import DashboardClient

ORIGIN = "https://linkedin-automation-production-7964.up.railway.app"
SHORTCUT = "BluQQ LinkedIn Worker.lnk"
log = logging.getLogger(__name__)


def startup_shortcut() -> Path:
    return (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft/Windows/Start Menu/Programs/Startup"
        / SHORTCUT
    )


@contextmanager
def supervisor_lock(path: Path):
    """OS-owned lock releases on crashes; a stale PID must never cause a duplicate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if not path.stat().st_size:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        try:
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def existing_workers(origin: str) -> list[int]:
    if os.name != "nt":
        return []
    # Static PowerShell code reads the origin from the environment; no URL,
    # path, credential or arbitrary command text is interpolated into a shell.
    code = """$ErrorActionPreference='Stop'; @(
      Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
      Where-Object { $_.CommandLine -and
        $_.CommandLine.Contains('-m linkedin_automation worker') -and
        $_.CommandLine.Contains($env:BLUQQ_WORKER_ORIGIN) } |
      Select-Object -ExpandProperty ProcessId
    ) | ConvertTo-Json -Compress"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
        env={**os.environ, "BLUQQ_WORKER_ORIGIN": origin},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    value = json.loads(result.stdout.strip() or "[]")
    return [value] if isinstance(value, int) else value


class WorkerSupervisor:
    def __init__(
        self, root: Path, *, finder=existing_workers, factory=subprocess.Popen, pause=time.sleep
    ):
        self.root, self.finder, self.factory, self.pause = root, finder, factory, pause
        self.origin = os.getenv("LINKEDIN_AUTOMATION_DASHBOARD_URL", ORIGIN)
        self.credentials = Path(
            os.getenv(
                "LINKEDIN_AUTOMATION_DASHBOARD_CREDENTIALS_FILE", str(root / ".env.railway.local")
            )
        )
        values = dotenv_values(self.credentials)
        DashboardClient(
            self.origin, values.get("DASHBOARD_USERNAME", ""), values.get("DASHBOARD_PASSWORD", "")
        )
        self.logs = root / "data/worker-logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    def cycle(self):
        if self.finder(self.origin):
            # Adopt a healthy legacy worker without interrupting an active search.
            self.pause(15)
            return
        environment = {
            **os.environ,
            "PYTHONPATH": str(self.root / "src"),
            "PYTHONUNBUFFERED": "1",
            "LINKEDIN_AUTOMATION_BROWSER_DATA": str(self.root / "browser-data-chrome"),
            "LINKEDIN_AUTOMATION_BROWSER_CHANNEL": "chrome",
            "LINKEDIN_AUTOMATION_DATABASE_URL": "sqlite:///data/local-research.sqlite3",
            "LINKEDIN_AUTOMATION_ENABLE_OUTREACH_WORKER": "1",
            "LINKEDIN_AUTOMATION_WORKER_MANAGED": "1",
            "LINKEDIN_AUTOMATION_WORKER_AUTOSTART": "1" if startup_shortcut().exists() else "0",
        }
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        with (
            (self.logs / (stamp + ".out.log")).open("ab") as stdout,
            (self.logs / (stamp + ".err.log")).open("ab") as stderr,
        ):
            process = self.factory(
                [
                    sys.executable,
                    "-m",
                    "linkedin_automation",
                    "worker",
                    "--url",
                    self.origin,
                    "--credentials-file",
                    str(self.credentials),
                ],
                cwd=self.root,
                env=environment,
                stdout=stdout,
                stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            log.info("Worker started; PID %s", process.pid)
            code = process.wait()
            log.warning("Worker exited (%s); retrying in 15 seconds", code)
        self.pause(15)


def main(root: Path):
    directory = root / "data/worker-logs"
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=directory / "supervisor.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    with supervisor_lock(directory / "supervisor.lock") as acquired:
        if not acquired:
            return
        supervisor = WorkerSupervisor(root)
        log.info("Worker supervisor ready")
        while True:
            try:
                supervisor.cycle()
            except Exception as error:
                # An unavailable process inventory must not start a duplicate worker.
                log.warning("Worker supervision paused (%s); retrying", type(error).__name__)
                time.sleep(15)
