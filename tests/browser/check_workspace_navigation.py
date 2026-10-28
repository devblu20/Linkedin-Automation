"""Opt-in real Chrome checks against a temporary hosted workspace; no production writes."""

import csv
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
        seed(root / "research.sqlite3")
        for key, value in {
            "HOSTED_DATA_DIR": str(root),
            "DASHBOARD_USERNAME": "reviewer",
            "DASHBOARD_PASSWORD": "local-test-password-at-least-24-characters",
        }.items():
            os.environ[key] = value
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
                    raise RuntimeError("Temporary workspace did not start")
                time.sleep(0.05)
            origin = "http://127.0.0.1:" + str(port)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
                context = browser.new_context(
                    http_credentials={
                        "username": "reviewer",
                        "password": "local-test-password-at-least-24-characters",
                    },
                    viewport={"width": 1440, "height": 1050},
                    accept_downloads=True,
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin)
                expect(page.locator("#leadRows tr")).to_have_count(1)
                before = page.request.get(origin + "/api/leads").json()
                page.get_by_role("link", name="◎ Campaigns").click()
                expect(page.locator("#campaignsView")).to_be_visible()
                expect(page.locator("#automaticSearch")).to_be_hidden()
                expect(page.locator("#workspaceCampaignRows tr")).to_have_count(3)
                page.locator("#workspaceNewCampaign").click()
                page.get_by_label("Campaign name", exact=True).fill("Browser campaign test")
                page.get_by_role("button", name="Save campaign", exact=True).click()
                expect(page.locator("#campaignDialog")).to_be_hidden()
                row = page.locator("#workspaceCampaignRows tr").filter(
                    has_text="Browser campaign test"
                )
                expect(row).to_have_count(1)
                row.get_by_role("button", name="Edit campaign", exact=True).click()
                page.get_by_label("Campaign name", exact=True).fill("Updated browser campaign")
                page.get_by_role("button", name="Save campaign", exact=True).click()
                expect(page.locator("#campaignDialog")).to_be_hidden()
                row = page.locator("#workspaceCampaignRows tr").filter(
                    has_text="Updated browser campaign"
                )
                expect(row).to_have_count(1)
                row.get_by_role("button", name="Use for search", exact=True).click()
                expect(page.locator("#automaticSearch")).to_be_visible()
                expect(page.locator("#searchCampaign option:checked")).to_have_text(
                    "Updated browser campaign"
                )
                assert page.request.get(origin + "/api/search/settings").json()["jobs"] == []

                page.get_by_role("link", name="↗ Outreach").click()
                expect(page.locator("#outreachView")).to_be_visible()
                expect(page.locator("#workspaceOutreachRows tr")).to_have_count(1)
                expect(
                    page.get_by_role("button", name="Copy connection", exact=True)
                ).to_be_disabled()
                page.get_by_label("Find a lead").fill("no-match")
                expect(page.locator("#workspaceOutreachRows tr")).to_have_count(0)
                page.get_by_label("Find a lead").fill("Alex")
                page.get_by_role("button", name="Review / edit drafts").click()
                expect(page.locator("#qualificationSummary")).to_contain_text("Qualification:")
                page.get_by_label("Connection message", exact=True).fill("Edited local test draft")
                page.get_by_role("button", name="Save client", exact=True).click()
                expect(page.locator("#editor")).to_be_hidden()
                expect(page.locator("#workspaceOutreachRows")).to_contain_text(
                    "Edited local test draft"
                )
                after = page.request.get(origin + "/api/leads").json()
                assert after[0]["outreach"]["status"] == before[0]["outreach"]["status"]

                page.get_by_role("button", name="Record / view activity", exact=True).click()
                page.get_by_label("Industry (missing; optional)", exact=True).fill(
                    "Investment services"
                )
                page.get_by_label("What did you verify or do?", exact=True).fill(
                    "Verified industry in the local test source"
                )
                page.get_by_label(
                    "I personally checked this evidence or completed this activity."
                ).check()
                page.get_by_role("button", name="Record activity", exact=True).click()
                expect(page.locator("#activityDialog")).to_be_hidden()
                assert (
                    page.request.get(origin + "/api/leads").json()[0]["lead"]["industry"]
                    == "Investment services"
                )
                page.get_by_role("button", name="Review / edit drafts").click()
                page.get_by_label("Verified funds (GBP)", exact=True).fill("1000000")
                page.get_by_label("Evidence URL", exact=True).fill("https://example.com/proof")
                page.get_by_label("Status", exact=True).select_option("approved")
                page.get_by_role("button", name="Save client", exact=True).click()
                expect(page.locator("#editor")).to_be_hidden()
                for action in ["connection_sent", "connected", "message_sent"]:
                    page.get_by_role("button", name="Record / view activity", exact=True).click()
                    page.get_by_label("Activity", exact=True).select_option(action)
                    page.get_by_label("What did you verify or do?", exact=True).fill(
                        "Recorded a manual step in the local browser test"
                    )
                    page.get_by_label(
                        "I personally checked this evidence or completed this activity."
                    ).check()
                    page.get_by_role("button", name="Record activity", exact=True).click()
                    expect(page.locator("#activityDialog")).to_be_hidden()
                    expect(page.locator("#workspaceOutreachRows")).to_contain_text(
                        action.replace("_", " ")
                    )
                page.get_by_role("button", name="Record / view activity", exact=True).click()
                expect(page.locator("#activityHistory")).to_contain_text("recorded manually")
                with page.expect_download() as history_download:
                    page.get_by_role("link", name="Download history CSV").click()
                assert history_download.value.suggested_filename.endswith(".csv")
                page.get_by_role("button", name="Close activity", exact=True).click()

                page.get_by_role("link", name="▦ Reports").click()
                expect(page.locator("#workspaceRunRows tr")).to_have_count(1)
                page.get_by_label("Lead review status", exact=True).select_option("approved")
                with page.expect_download() as download:
                    page.get_by_role("button", name="Download leads CSV", exact=True).click()
                output = root / "filtered.csv"
                download.value.save_as(output)
                with output.open(encoding="utf-8-sig", newline="") as report:
                    assert list(csv.DictReader(report)) == []
                page.get_by_label("Lead review status", exact=True).select_option("")
                with page.expect_download() as download:
                    page.get_by_role("button", name="Download leads Excel", exact=True).click()
                assert download.value.suggested_filename.endswith(".xlsx")
                page.reload()
                expect(page.locator("#reportsView")).to_be_visible()
                expect(page.locator('[data-view="reports"]')).to_have_attribute(
                    "aria-current", "page"
                )
                page.go_back()
                expect(page.locator("#outreachView")).to_be_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                expect(page.get_by_role("navigation", name="Workspace")).to_be_visible()
                page.get_by_role("link", name="◎ Campaigns").click()
                expect(page.locator("#campaignsView")).to_be_visible()
                page.get_by_role("link", name="◆ Lead pipeline").click()
                created = page.request.post(
                    origin + "/api/search/jobs",
                    data={
                        "campaign": "bluqq-london-independent-traders.yaml",
                        "max_results": 1,
                        "max_pages": 1,
                    },
                ).json()
                claimed = page.request.post(origin + "/api/search/claim", data={}).json()["job"]
                assert claimed["id"] == created["id"]
                page.request.post(
                    origin + "/api/search/jobs/" + claimed["id"] + "/complete",
                    data={"token": claimed["token"], "status": "failed", "resume_available": True},
                )
                page.reload()
                expect(page.locator("#resumeSearch")).to_be_visible()
                page.get_by_role("button", name="Resume interrupted search").click()
                expect(page.locator("#searchResult")).to_contain_text("Search queued")
                assert len(page.request.get(origin + "/api/search/settings").json()["jobs"]) == 2
                assert not errors, errors
                browser.close()
                print(
                    "PASS: campaign create/edit/search selection, draft persistence, "
                    "filtered downloads, reload/back and mobile navigation"
                )
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
