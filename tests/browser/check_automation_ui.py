"""Local browser test of auto-approval, queue authorization, and delivery status rendering."""

import os
import runpy
import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright

from linkedin_automation.interfaces.hosted_site import create_hosted_app


def main():
    seed = runpy.run_path("tests/integration/test_qualification_review.py")["seed"]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        seed(
            root / "research.sqlite3",
            template="bluqq-london-independent-traders.yaml",
            complete=True,
        )
        os.environ.update(
            HOSTED_DATA_DIR=str(root),
            DASHBOARD_USERNAME="reviewer",
            DASHBOARD_PASSWORD="test-password-at-least-24-characters",
            AUTOMATIC_QUALIFICATION="1",
            AUTOMATIC_QUEUE="1",
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
            origin = "http://127.0.0.1:" + str(port)
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="chrome", headless=True)
                context = browser.new_context(
                    http_credentials={
                        "username": "reviewer",
                        "password": "test-password-at-least-24-characters",
                    },
                    viewport={"width": 1440, "height": 1050},
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/#outreach")
                expect(page.locator("#automationMode")).to_contain_text("automatically")
                page.get_by_role("button", name="Prepare approved leads", exact=True).click()
                expect(page.locator("#deliveryRows")).to_contain_text(
                    "Awaiting delivery authorization"
                )
                expect(page.locator("#workspaceOutreachRows")).to_contain_text("connection queued")
                assert page.request.get(origin + "/api/summary").json()["connection_sent"] == 0
                page.get_by_role("button", name="Review / enable delivery", exact=True).click()
                expect(page.locator("#deliveryRecipient")).to_contain_text("Alex Example")
                expect(page.locator("#deliveryDraft")).not_to_be_empty()
                page.locator("#confirmDelivery").check()
                page.locator("#allowFollowUp").check()
                page.get_by_role("button", name="Enable delivery", exact=True).click()
                expect(page.locator("#deliveryDialog")).to_be_hidden()
                job = page.request.post(origin + "/api/outreach/claim", data={}).json()["job"]
                assert job["action"] == "connection"
                assert (
                    page.request.post(
                        origin + "/api/outreach/jobs/" + job["id"] + "/complete",
                        data={
                            "token": job["token"],
                            "outcome": "pending",
                            "proof": "Temporary browser fixture, no real request sent",
                        },
                    ).status
                    == 200
                )
                page.get_by_role("button", name="Refresh delivery", exact=True).click()
                expect(page.locator("#workspaceOutreachRows")).to_contain_text("connection sent")
                page.get_by_role("button", name="Check acceptance now", exact=True).click()
                check = page.request.post(origin + "/api/outreach/claim", data={}).json()["job"]
                page.request.post(
                    origin + "/api/outreach/jobs/" + check["id"] + "/complete",
                    data={
                        "token": check["token"],
                        "outcome": "connected",
                        "proof": "Temporary first-degree fixture confirmation",
                    },
                )
                message = page.request.post(origin + "/api/outreach/claim", data={}).json()["job"]
                assert message["action"] == "message"
                page.request.post(
                    origin + "/api/outreach/jobs/" + message["id"] + "/complete",
                    data={
                        "token": message["token"],
                        "outcome": "message_sent",
                        "proof": "Temporary message fixture confirmation",
                    },
                )
                page.get_by_role("button", name="Refresh delivery", exact=True).click()
                expect(page.locator("#workspaceOutreachRows")).to_contain_text("message sent")
                assert not errors, errors
                browser.close()
                print(
                    "PASS: auto qualification, queue preview/authorization "
                    "and verified outcome rendering"
                )
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
