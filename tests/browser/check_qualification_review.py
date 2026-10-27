"""Opt-in Chrome test with temporary, local-only qualification evidence."""

import runpy
import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from linkedin_automation.interfaces.review_site import create_review_app


def main():
    seed = runpy.run_path("tests/integration/test_qualification_review.py")["seed"]
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "browser-review.sqlite3"
        lead, _, _ = seed(database)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_review_app(database, review_only=True),
                host="127.0.0.1",
                port=port,
                log_level="error",
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("Temporary review server did not start")
                time.sleep(0.05)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                page.goto(f"http://127.0.0.1:{port}")
                page.get_by_role("button", name="Review", exact=True).click()
                page.locator("#qualificationSummary").get_by_text(
                    "Qualification: 55/100 · threshold 70", exact=True
                ).wait_for()
                assert page.get_by_label("Status", exact=True).locator("option").count() == 4
                page.get_by_label("Status", exact=True).select_option("approved")
                with page.expect_response(lambda r: r.request.method == "PUT") as response:
                    page.get_by_role("button", name="Save client", exact=True).click()
                assert response.value.status == 422
                assert page.locator("#editor").is_visible()
                page.get_by_label("Verified funds (GBP)", exact=True).fill("1000000")
                page.get_by_label("Evidence URL", exact=True).fill("https://example.com/filing")
                page.locator("#qualificationSummary").get_by_text(
                    "Qualification: 85/100 · threshold 70", exact=True
                ).wait_for()
                before = page.request.get(f"http://127.0.0.1:{port}/api/leads").json()[0]
                assert before["outreach"]["status"] == "needs_verification"
                assert before["outreach"]["funds_gbp"] is None
                with page.expect_response(lambda r: r.request.method == "PUT") as response:
                    page.get_by_role("button", name="Save client", exact=True).click()
                assert response.value.status == 200
                page.locator("#editor").wait_for(state="hidden")
                page.get_by_role("button", name="Review", exact=True).click()
                page.locator("#qualificationSummary").get_by_text(
                    "Qualification: 85/100 · threshold 70", exact=True
                ).wait_for()
                assert page.get_by_label("Status", exact=True).input_value() == "approved"
                page.get_by_label("Status", exact=True).select_option("rejected")
                with page.expect_response(lambda r: r.request.method == "PUT") as response:
                    page.get_by_role("button", name="Save client", exact=True).click()
                assert response.value.status == 200
                final = page.request.get(f"http://127.0.0.1:{port}/api/leads").json()[0]
                assert final["lead"]["id"] == str(lead.id)
                assert final["outreach"]["status"] == "rejected"
                browser.close()
            print(
                "PASS: preview is read-only; blocked approval, evidence-backed save, "
                "reopen and rejection work"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
