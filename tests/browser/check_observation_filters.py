"""Opt-in browser regression against a running local dashboard with collected data."""

import argparse
import asyncio
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from playwright.async_api import async_playwright


async def check(run_id: str, url: str) -> None:
    started, finished = asyncio.Event(), asyncio.Event()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = await browser.new_page()

            async def delay_old_response(route):
                query = parse_qs(urlsplit(route.request.url).query)
                response = await route.fetch()
                if "run_id" not in query:
                    started.set()
                    await asyncio.sleep(1.5)
                    await route.fulfill(response=response)
                    finished.set()
                else:
                    await route.fulfill(response=response)

            await page.route("**/api/observations?**", delay_old_response)
            await page.goto(url)
            await asyncio.wait_for(started.wait(), timeout=10)
            await page.get_by_role("combobox", name="Collected profile run").select_option(run_id)
            await page.wait_for_function(
                "document.querySelectorAll('#observationRows tr').length===1"
            )
            await asyncio.wait_for(finished.wait(), timeout=10)
            await page.wait_for_timeout(200)
            rows = page.locator("#observationRows tr")
            assert await rows.count() == 1, "Old all-runs data replaced the selected run"
            assert run_id[:8] in await rows.inner_text()
            print("PASS: delayed all-runs response did not overwrite the selected run")
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_id", type=UUID, help="Existing run containing exactly one collected profile"
    )
    parser.add_argument("--url", default="http://127.0.0.1:8766/")
    args = parser.parse_args()
    target = urlsplit(args.url)
    if target.hostname not in {"localhost", "127.0.0.1"}:
        parser.error("Use a local dashboard for this isolated browser test")
    asyncio.run(check(str(args.run_id), args.url))
