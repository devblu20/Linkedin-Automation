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
from linkedin_automation.infrastructure.browser_hold import BrowserHold


def authenticated_location(url: str, title: str, html: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.hostname in {"linkedin.com", "www.linkedin.com"}
        and parsed.path.startswith(("/feed", "/search/results", "/in/"))
        and detect_safety_state(url=url, title=title, html=html) is None
    )


def wait_for_authenticated_page(page: Page, timeout_seconds: int) -> bool:
    """Wait in the existing visible window for the user to finish sign-in."""
    deadline = time.monotonic() + timeout_seconds
    while not page.is_closed() and time.monotonic() < deadline:
        try:
            snapshot = read_page_snapshot(page, timeout_seconds=2)
            if authenticated_location(*snapshot):
                return True
            if detect_safety_state(url=snapshot[0], title=snapshot[1], html=snapshot[2]) == (
                "rate_limited"
            ):
                return False
            page.wait_for_timeout(1000)
        except CollectionError:
            continue
        except PlaywrightError:
            if page.is_closed():
                return False
    return False


def wait_for_manual_close(
    page: Page, timeout_seconds: int, on_verified: Callable[[], None]
) -> bool:
    """Leave challenges and sign-in to the user; never close just after verification."""
    deadline = time.monotonic() + timeout_seconds
    verified = False
    while not page.is_closed() and time.monotonic() < deadline:
        try:
            snapshot = read_page_snapshot(page, timeout_seconds=2)
            authenticated = authenticated_location(*snapshot)
            if verified and detect_safety_state(
                url=snapshot[0], title=snapshot[1], html=snapshot[2]
            ):
                verified = False
            if not verified and authenticated:
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
            verified = wait_for_manual_close(page, timeout_seconds, on_verified)
        finally:
            context.close()
    # Release the browser profile before the worker is allowed to acquire it.
    if verified:
        BrowserHold(profile_dir).clear_after_manual_login()
    return verified
