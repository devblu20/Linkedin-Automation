"""Exercise Reports in Chrome with isolated real database fixtures; no external API calls."""

import csv
import io
import json
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from openpyxl import load_workbook
from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path("tests/integration").resolve()))
from test_report_exports import reports


def main():
    root = Path("data") / ("reports-ui-" + uuid4().hex[:8])
    root.mkdir(parents=True)
    patch = pytest.MonkeyPatch()
    fixture = reports.__wrapped__(root, patch)
    client, runs, _ = next(fixture)
    server = uvicorn.Server(
        uvicorn.Config(client.app, host="127.0.0.1", port=8785, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(
                http_credentials={
                    "username": "reviewer",
                    "password": "test-password-at-least-24-characters",
                },
                viewport={"width": 1440, "height": 1050},
                accept_downloads=True,
            )
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto("http://127.0.0.1:8785/#reports")
            expect(page.locator("#reportMatchCount")).to_have_text("15 matching leads")
            expect(page.locator("#reportMetrics .reports-stat").first).to_contain_text("29")
            expect(page.locator("#workspaceRunRows tr")).to_have_count(2)
            assert page.locator("#analyticsRun").count() == 0
            page.get_by_label("Research scope", exact=True).select_option(str(runs[0].id))
            expect(page.locator("#reportMatchCount")).to_have_text("14 matching leads")
            expect(page.locator("#reportMetrics .reports-stat").first).to_contain_text("28")
            page.get_by_role("button", name="Collected profiles", exact=True).click()
            page.locator("#reportsView").get_by_label(
                "Collection decision", exact=True
            ).select_option("rejected")
            expect(page.locator("#reportMatchCount")).to_have_text("14 matching profiles")
            expect(page.locator("#reportPreviewRows tr")).to_have_count(10)
            page.get_by_role("button", name="Next", exact=True).click()
            expect(page.locator("#reportPreviewRows tr")).to_have_count(4)
            expect(page.locator("#reportNext")).to_be_disabled()
            for extension, button in (
                ("csv", "Download profiles CSV"),
                ("xlsx", "Download profiles Excel"),
            ):
                with page.expect_download() as event:
                    page.get_by_role("button", name=button, exact=True).click()
                path = root / ("rejected." + extension)
                event.value.save_as(path)
                if extension == "csv":
                    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
                    assert len(rows) == 14 and all(
                        r["Collection decision"] == "rejected" for r in rows
                    )
                    assert {r["Run ID"] for r in rows} == {str(runs[0].id)}
                else:
                    workbook = load_workbook(path)
                    assert workbook.worksheets[0].max_row == 15
                    workbook.close()
            page.get_by_role("button", name="Saved leads", exact=True).click()
            page.locator("#reportsView").get_by_label("Find a lead", exact=True).fill("José")
            expect(page.locator("#reportMatchCount")).to_have_text("13 matching leads")
            with page.expect_download() as event:
                page.get_by_role("button", name="Download leads CSV", exact=True).click()
            path = root / "leads.csv"
            event.value.save_as(path)
            assert (
                len(list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))) == 13
            )
            page.get_by_label("Lead review status", exact=True).select_option("approved")
            expect(page.locator("#reportMatchCount")).to_have_text("0 matching leads")
            expect(page.locator("#reportPreviewEmpty")).to_be_visible()
            expect(page.locator("#reportCSV")).to_be_disabled()
            page.get_by_role("button", name="Reset filters", exact=True).click()
            expect(page.locator("#reportMatchCount")).to_have_text("15 matching leads")
            page.locator("#workspaceRunRows tr").filter(has_text="Campaign 1").get_by_role(
                "button", name="View run"
            ).click()
            expect(page.locator("#reportRun")).to_have_value(str(runs[1].id))
            expect(page.locator("#reportMatchCount")).to_have_text("1 matching leads")
            expect(page.locator("#reportMetrics .reports-stat").first).to_contain_text("1")
            page.get_by_role("button", name="Reset filters", exact=True).click()
            expect(page.locator("#reportMatchCount")).to_have_text("15 matching leads")

            def fail_preview(route):
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"detail": "Temporary preview failure"}),
                )

            page.route("**/api/reports/preview?*", fail_preview)
            page.get_by_role("button", name="Refresh reports", exact=True).click()
            expect(page.locator("#reportPreviewStatus")).to_contain_text(
                "Temporary preview failure"
            )
            expect(page.locator("#reportCSV")).to_be_disabled()
            page.unroute("**/api/reports/preview?*", fail_preview)
            page.get_by_role("button", name="Refresh reports", exact=True).click()
            expect(page.locator("#reportMatchCount")).to_have_text("15 matching leads")
            page.route(
                "**/api/exports/leads.csv?*",
                lambda route: route.fulfill(
                    status=413,
                    content_type="application/json",
                    body=json.dumps(
                        {"detail": "Too many rows. Narrow the filters before exporting."}
                    ),
                ),
            )
            page.get_by_role("button", name="Download leads CSV", exact=True).click()
            expect(page.locator("#reportExportStatus")).to_contain_text("Too many rows")
            page.unroute("**/api/exports/leads.csv?*")
            page.get_by_role("button", name="Reset filters", exact=True).click()
            expect(page.locator("#reportMatchCount")).to_have_text("15 matching leads")
            page.evaluate("window.scrollTo(0,0)")
            page.screenshot(path="data/reports-redesign-desktop.png", full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.evaluate("window.scrollTo(0,0)")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path="data/reports-redesign-mobile.png", full_page=True)
            page.get_by_role("link", name="↗ Outreach", exact=True).click()
            expect(page.locator("#outreachView")).to_be_visible()
            assert not errors, errors
            for source in page.locator("script").all_text_contents():
                page.evaluate("source=>{new Function(source);return true}", source)
            browser.close()
            print(
                json.dumps(
                    {
                        "reports_ui": "passed",
                        "csv_and_excel": "passed",
                        "pagination_filters_scope": "passed",
                        "errors_and_recovery": "passed",
                        "mobile_overflow": False,
                        "javascript_errors": errors,
                    }
                )
            )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        fixture.close()
        patch.undo()


if __name__ == "__main__":
    main()
