"""Single-owner browser helper; only explicitly authorized deliveries can run."""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from linkedin_automation.infrastructure.personal_worker import PersonalBrowserSupervisor  # noqa: E402
from linkedin_automation.infrastructure.worker_supervisor import supervisor_lock  # noqa: E402


def recover_linkedin_login(helper):
    from playwright.sync_api import Error as PlaywrightError

    from linkedin_automation.infrastructure.browser.manual_login import manual_login
    from linkedin_automation.infrastructure.browser_hold import BrowserHold

    profile_dir = ROOT / "browser-data-chrome"
    with supervisor_lock(helper.logs / "delivery-login.lock") as acquired:
        if not acquired:
            return False
        if BrowserHold(profile_dir).active:
            try:
                return manual_login(profile_dir, timeout_seconds=1200, on_verified=lambda: None)
            except (OSError, PlaywrightError, ValueError):
                logging.warning("Could not open the LinkedIn sign-in window. Check Chrome.")
                return False
    return True


def main():
    helper = PersonalBrowserSupervisor(ROOT)
    logging.basicConfig(
        filename=helper.logs / "helper.log",
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if sys.argv[1:] == ["--delivery"] and not recover_linkedin_login(helper):
        return
    with supervisor_lock(helper.logs / "helper.lock") as acquired:
        if acquired:
            helper.run()


if __name__ == "__main__":
    main()
