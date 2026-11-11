"""Restart the existing single-owner browser worker after transient local failures."""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from linkedin_automation.infrastructure.worker_supervisor import existing_workers, startup_shortcut

log = logging.getLogger(__name__)


class PersonalBrowserSupervisor:
    def __init__(self, root: Path, *, finder=None, factory=subprocess.Popen, pause=time.sleep):
        self.root = Path(root).resolve()
        self.credentials = self.root / ".env.railway.local"
        self.origin = "https://linkedin-automation-production-7964.up.railway.app"
        self.logs = self.root / "data/personal-browser-worker"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.finder = finder or (lambda: existing_workers(self.origin, self.credentials))
        self.factory, self.pause = factory, pause

    def cycle(self):
        # An inventory error propagates to run(), which waits without launching a duplicate.
        if self.finder():
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
            "PERSONAL_BROWSER_MODE": "1",
            "LINKEDIN_AUTOMATION_SENDER_NAME": "silky work",
            "LINKEDIN_AUTOMATION_WORKER_MANAGED": "1",
            "LINKEDIN_AUTOMATION_WORKER_AUTOSTART": "1" if startup_shortcut().exists() else "0",
        }
        with (self.logs / "worker.log").open("ab") as output:
            child = self.factory(
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
                stdout=output,
                stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            child.wait()
        self.pause(15)

    def run(self):
        while True:
            try:
                self.cycle()
            except (OSError, subprocess.SubprocessError, ValueError):
                # Do not include exception text: process diagnostics could contain credentials.
                log.warning("Worker status/start temporarily unavailable; retrying in 15 seconds.")
                self.pause(15)
