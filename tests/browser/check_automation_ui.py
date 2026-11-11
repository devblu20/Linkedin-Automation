"""Local browser test of auto-approval, queue authorization, and delivery status rendering."""

import os
import runpy
import socket
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

import uvicorn
from playwright.sync_api import expect, sync_playwright

from linkedin_automation.application.public_prospect_review import prepare_public_collection
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository
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
        definition = YamlSearchDefinitionLoader().load(
            Path("config/bluqq-london-independent-traders.yaml")
        )
        run, rows, observations = prepare_public_collection(
            {
                "id": str(uuid4()),
                "created": 1700000000,
                "results": [
                    {
                        "name": "Public Example",
                        "profile_url": "https://www.linkedin.com/in/public-example/",
                        "public_title": "Public Example - Independent Trader",
                        "snippet": "Location: London",
                        "query": "London trading",
                        "matched": ["location:London"],
                        "missing": ["keywords"],
                    }
                ],
            },
            definition,
        )
        repository = DatabaseResearchRepository(root / "research.sqlite3")
        try:
            repository.import_review_run(run, rows, observations=observations)
        finally:
            repository.close()
        os.environ.update(
            HOSTED_DATA_DIR=str(root),
            DASHBOARD_USERNAME="reviewer",
            DASHBOARD_PASSWORD="test-password-at-least-24-characters",
            AUTOMATIC_QUALIFICATION="0",
            AUTOMATIC_QUEUE="0",
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
            prefix = os.getenv("DASHBOARD_TEST_PREFIX", "")
            origin = "http://127.0.0.1:" + str(port) + prefix
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
                page.add_init_script("""
                    window.deliverySignInOpens = 0;
                    const click = HTMLAnchorElement.prototype.click;
                    HTMLAnchorElement.prototype.click = function () {
                        if (this.protocol === 'bluqq-linkedin-delivery:') {
                            window.deliverySignInOpens += 1;
                        } else {
                            click.call(this);
                        }
                    };
                """)

                def simulate_offline_worker(route):
                    response = route.fetch()
                    data = response.json()
                    data.update(local_sign_in_available=True, local_worker_online=False)
                    route.fulfill(response=response, json=data)

                page.route("**/api/outreach/jobs", simulate_offline_worker)
                errors = []
                api_requests = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on(
                    "request",
                    lambda request: (
                        api_requests.append(request.url) if "/api/" in request.url else None
                    ),
                )
                page.goto(origin + "/#outreach")
                brave = page.get_by_role("table", name="Brave public-web leads")
                worker = page.get_by_role("table", name="LinkedIn worker and other leads")
                expect(brave).to_contain_text("Public Example")
                expect(brave).not_to_contain_text("Alex Example")
                expect(worker).to_contain_text("Alex Example")
                expect(worker).not_to_contain_text("Public Example")
                search = page.locator("#outreachSearch")
                search.fill("Public Example")
                expect(worker.locator("tbody tr")).to_have_count(0)
                expect(brave.locator("tbody tr")).to_have_count(1)
                search.clear()
                expect(page.locator("#automationMode")).to_contain_text("automatically")
                expect(page.locator("#deliveryRows")).to_contain_text(
                    "Awaiting delivery authorization"
                )
                expect(page.locator("#workspaceOutreachRows")).to_contain_text("connection queued")
                assert page.request.get(origin + "/api/summary").json()["connection_sent"] == 0
                page.get_by_role("button", name="Review / enable delivery", exact=True).click()
                expect(page.locator("#deliveryRecipient")).to_contain_text("Alex Example")
                expect(page.locator("#deliveryDraft")).not_to_be_empty()
                expect(page.locator("#allowFollowUp")).to_be_checked()
                page.get_by_role("button", name="Not now", exact=True).click()
                assert page.evaluate("window.deliverySignInOpens") == 0
                assert (
                    page.request.post(origin + "/api/outreach/claim", data={}).json()["job"]
                    is None
                )
                page.get_by_role("button", name="Review / enable delivery", exact=True).click()
                page.get_by_role("button", name="Confirm request and follow-up", exact=True).click()
                expect(page.locator("#deliveryDialog")).to_be_hidden()
                expect(page.get_by_role("link", name="Open LinkedIn sign-in")).to_be_visible()
                assert page.evaluate("window.deliverySignInOpens") == 1
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
                assert api_requests and all(
                    url.startswith(origin + "/api/") for url in api_requests
                ), api_requests
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
