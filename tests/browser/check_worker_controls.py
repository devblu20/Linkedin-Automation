"""Isolated browser verification; never opens the Windows helper or real LinkedIn."""

import os
import socket
import tempfile
import threading
import time

import uvicorn
from playwright.sync_api import expect, sync_playwright

from linkedin_automation.interfaces.hosted_site import create_hosted_app


def main():
    with tempfile.TemporaryDirectory() as directory:
        os.environ.update(
            HOSTED_DATA_DIR=directory,
            DASHBOARD_USERNAME="reviewer",
            DASHBOARD_PASSWORD="test-password-at-least-24-characters",
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(create_hosted_app(), host="127.0.0.1", port=port, log_level="error")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("Temporary server not ready")
                time.sleep(0.05)
            origin = f"http://127.0.0.1:{port}"
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="chrome", headless=True)
                page = browser.new_page(
                    http_credentials={
                        "username": "reviewer",
                        "password": "test-password-at-least-24-characters",
                    },
                    viewport={"width": 1000, "height": 900},
                )
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/linkedin-automation/")
                expect(page.locator("#workerStatus")).to_contain_text("offline")
                expect(page.locator("#startWorker")).to_have_attribute(
                    "href", "bluqq-linkedin-worker://start"
                )
                page.get_by_role("button", name="Worker setup / always-on", exact=True).click()
                expect(page.locator("#workerSetupDialog")).to_be_visible()
                expect(page.locator("#workerSetupDialog")).to_contain_text("must be awake")
                page.get_by_role("button", name="Close", exact=True).click()
                assert (
                    page.request.post(
                        origin + "/linkedin-automation/api/search/heartbeat",
                        data={"managed": True, "autostart": True, "linkedin_paused": True},
                    ).status
                    == 200
                )
                page.get_by_role("button", name="Refresh worker status", exact=True).click()
                expect(page.locator("#startWorker")).to_have_text("Worker running")
                expect(page.locator("#workerStatus")).to_contain_text("LinkedIn jobs are paused")
                assert (
                    page.request.post(
                        origin + "/linkedin-automation/api/search/heartbeat",
                        data={"managed": True, "autostart": True},
                    ).status
                    == 200
                )
                page.get_by_role("button", name="Refresh worker status", exact=True).click()
                expect(page.locator("#startWorker")).to_have_text("Worker online")
                expect(page.locator("#workerStatus")).to_contain_text("online")
                state = page.request.get(origin + "/linkedin-automation/api/search/settings").json()
                assert state["jobs"] == [], "Worker controls must not create a search"
                assert not errors, errors
                browser.close()
                print(
                    "PASS: worker controls, offline/managed status, "
                    "setup guidance and prefixed APIs"
                )
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
