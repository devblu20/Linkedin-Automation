"""Offline Chrome test of manual template authoring against isolated real API storage."""

import json
import os
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from linkedin_automation.interfaces.hosted_site import create_hosted_app


def main():
    with TemporaryDirectory(dir="data", prefix="manual-ui-") as directory:
        os.environ.update(
            DASHBOARD_USERNAME="reviewer",
            DASHBOARD_PASSWORD="test-password-at-least-24-characters",
            HOSTED_DATA_DIR=directory,
        )
        with TestClient(create_hosted_app()) as client, sync_playwright() as playwright:
            client.auth = ("reviewer", "test-password-at-least-24-characters")
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 1050})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def intercept(route):
                request = route.request
                url = urlsplit(request.url)
                if url.hostname != "manual-template.test":
                    return route.abort()
                response = client.request(
                    request.method,
                    url.path + ("?" + url.query if url.query else ""),
                    content=request.post_data_buffer,
                    headers={
                        "Content-Type": request.headers.get("content-type", "application/json")
                    },
                )
                route.fulfill(
                    status=response.status_code,
                    content_type=response.headers.get("content-type", "application/json"),
                    body=response.content,
                )

            page.route("**/*", intercept)
            page.goto("https://manual-template.test/")
            page.locator("#newCampaign").click()
            page.locator("#campaignTemplate").select_option("bluqq-crm-recruitment.yaml")
            page.locator("#campaignName").fill("My custom IT campaign")
            page.locator("#campaignKeywords").fill("recruitment agency\nstaffing agency")
            page.locator("#addManualTemplate").click()
            page.locator("#manualTemplateName").fill("My IT qualification")
            page.locator("#manualFirmTypes").fill("IT consulting\nIT consultancy")
            page.locator("#manualSearch-keywords").fill("IT consulting\nIT consultancy")
            page.locator("#manualSearch-industries").fill("IT Services and IT Consulting")
            page.locator('[name="manualRequired"][value="titles"]').check()
            page.locator("#manualMinimumScore").fill("45")
            page.screenshot(path="data/manual-template-form.png", full_page=True)
            page.locator("#saveManualTemplate").click()
            expect(page.locator("#manualTemplateDialog")).not_to_be_visible()
            expect(page.locator("#campaignTemplate option:checked")).to_have_text(
                "My IT qualification"
            )
            expect(page.locator("#campaignName")).to_have_value("My custom IT campaign")
            page.locator("#saveCampaign").click()
            expect(page.locator("#campaignDialog")).not_to_be_visible()
            page.locator("#editCampaign").click()
            expect(page.locator("#campaignTemplate")).to_be_disabled()
            expect(page.locator("#addManualTemplate")).to_be_disabled()
            page.locator("#closeCampaign").click()
            page.locator("#newCampaign").click()
            option = page.locator("#campaignTemplate option").filter(has_text="My IT qualification")
            expect(option).to_have_count(1)
            custom_id = option.get_attribute("value")
            page.locator("#campaignTemplate").select_option(custom_id)
            expect(page.locator("#campaignKeywords")).to_have_value("IT consulting\nIT consultancy")
            expect(page.locator('[name="campaignRequired"][value="titles"]')).to_be_checked()
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator("#addManualTemplate").click()
            expect(page.locator("#manualTemplateName")).to_be_visible()
            page.screenshot(path="data/manual-template-mobile.png", full_page=True)
            assert not errors, errors
            print(
                json.dumps(
                    {
                        "desktop": True,
                        "mobile": True,
                        "persisted": True,
                        "campaign_template_locked": True,
                        "outreach_sent": False,
                    }
                )
            )
            browser.close()


if __name__ == "__main__":
    main()
