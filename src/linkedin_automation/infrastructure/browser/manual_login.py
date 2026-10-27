"""Manual sign-in window, independent of collection and its error cleanup."""

import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from linkedin_automation.application.exceptions import CollectionError
from linkedin_automation.infrastructure.browser.linkedin import (
    detect_safety_state,
    read_page_snapshot,
)


def authenticated_location(url: str, title: str, html: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.hostname in {"linkedin.com", "www.linkedin.com"}
        and parsed.path.startswith(("/feed", "/search/results", "/in/"))
        and detect_safety_state(url=url, title=title, html=html) is None
    )


def wait_for_manual_close(
    page: Page, timeout_seconds: int, on_verified: Callable[[], None]
) -> bool:
    """Leave challenges and sign-in to the user; never close just after verification."""
    deadline = time.monotonic() + timeout_seconds
    verified = False
    while not page.is_closed() and time.monotonic() < deadline:
        try:
            snapshot = read_page_snapshot(page, timeout_seconds=2)
            if not verified and authenticated_location(*snapshot):
                verified = True
                on_verified()
            page.wait_for_timeout(1000)
        except (CollectionError, PlaywrightError):
            if page.is_closed():
                break
    return verified


def manual_login(
    profile_dir: Path,
    *,
    timeout_seconds: int,
    on_verified: Callable[[], None],
    channel: str = "chrome",
) -> bool:
    if channel not in {"chrome", "msedge"}:
        raise ValueError("Use chrome or msedge for the manual login window")
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir.resolve()), channel=channel, headless=False
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            # Keep the window available for manual navigation after a load error.
            with suppress(PlaywrightError):
                page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            return wait_for_manual_close(page, timeout_seconds, on_verified)
        finally:
            context.close()
