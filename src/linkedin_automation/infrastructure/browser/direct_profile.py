"""Collect an explicitly supplied, normally accessible profile for the same workflow."""

from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from linkedin_automation.application.exceptions import CollectionError, LimitedVisibilityError
from linkedin_automation.domain.models import CollectionBatch, LeadCandidate
from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.domain.services import canonicalize_linkedin_profile_url
from linkedin_automation.infrastructure.browser.linkedin import (
    PlaywrightLinkedInCollector,
    extract_visible_profile,
    read_page_snapshot,
)


def parse_visible_profile_summary(snapshot: str, profile_url: str) -> LeadCandidate:
    """Read the visible top card; never derive an identity from a URL slug."""
    lines = [" ".join(line.split()) for line in snapshot.splitlines() if line.strip()]
    if (
        len(lines) < 3
        or lines[0].casefold() in {"linkedin member", "profile not found", "page not found"}
        or len(lines[0]) > 200
        or lines[2].casefold() in {"connect", "message", "contact info", "more"}
    ):
        raise LimitedVisibilityError("A visible profile name, headline and location are required")
    return LeadCandidate(
        profile_url=canonicalize_linkedin_profile_url(profile_url),
        full_name=lines[0],
        headline=lines[1],
        location=lines[2],
        source_search="direct_profile",
    )


class DirectProfileCollector:
    def __init__(self, profile_url: str, profile_dir: Path, channel: str = "chrome") -> None:
        self.url = canonicalize_linkedin_profile_url(profile_url)
        self.profile_dir = profile_dir.resolve()
        if channel not in {"chrome", "msedge"}:
            raise ValueError("Use chrome or msedge")
        self.channel = channel

    def collect(
        self, definition: SearchDefinition, *, start_page: int = 1, dry_run: bool = False
    ) -> CollectionBatch:
        if start_page != 1:
            raise ValueError("Direct profile collection has one checkpoint")
        if dry_run:
            return CollectionBatch((), checkpoint_page=1, complete=True)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(self.profile_dir), channel=self.channel, headless=False
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(self.url, wait_until="domcontentloaded")
                PlaywrightLinkedInCollector._raise_for_safety_state(*read_page_snapshot(page))
                if canonicalize_linkedin_profile_url(page.url) != self.url:
                    raise LimitedVisibilityError(
                        "LinkedIn redirected away from the supplied profile"
                    )
                main = page.locator("main").first
                main.wait_for(timeout=20_000)
                snapshot = main.inner_text(timeout=15_000)
                candidate = parse_visible_profile_summary(snapshot, self.url)
                candidate = extract_visible_profile(page, candidate)
                return CollectionBatch((candidate,), checkpoint_page=1, complete=True)
            except PlaywrightError as error:
                raise CollectionError(
                    "Direct profile could not be read; sign in manually or retry"
                ) from error
            finally:
                context.close()
