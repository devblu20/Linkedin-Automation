"""Bounded, supervised Playwright adapter for LinkedIn people search."""

from __future__ import annotations

import logging
import random
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from linkedin_automation.application.exceptions import (
    AuthenticationRequiredError,
    CollectionError,
    LayoutChangedError,
    RateLimitedError,
    SecurityChallengeError,
)
from linkedin_automation.domain.models import CollectionBatch, LeadCandidate
from linkedin_automation.domain.search_definition import SearchDefinition

logger = logging.getLogger(__name__)
_BASE_SEARCH_URL = "https://www.linkedin.com/search/results/people/"
_LOCATION_GEO_URNS = {
    "london": "102257491",
    "greater london": "102257491",
    "greater london area": "102257491",
}


def build_people_search_url(
    definition: SearchDefinition, page: int, *, search_term: str | None = None
) -> str:
    """Build a bounded LinkedIn people-search URL from positive criteria."""
    search = definition.search
    # Titles are qualification criteria, not free-text keywords. Including every accepted title
    # in one query makes LinkedIn interpret a long list as a single over-constrained search.
    subject_terms = [*search.keywords.include, *search.companies]
    if not subject_terms:
        subject_terms = list(search.industries)
    keywords = search_term or _boolean_or_group(subject_terms)
    parameters: dict[str, str | int] = {
        "keywords": keywords,
        "page": page,
        "origin": "FACETED_SEARCH",
    }
    geo_urns = list(
        dict.fromkeys(
            geo_urn
            for location in search.locations
            if (geo_urn := _LOCATION_GEO_URNS.get(location.casefold())) is not None
        )
    )
    if geo_urns:
        parameters["geoUrn"] = f'["{geo_urns[0]}"]'
    query = urlencode(parameters)
    return f"{_BASE_SEARCH_URL}?{query}"


def build_people_search_steps(definition: SearchDefinition) -> list[tuple[str, int]]:
    """Plan bounded single-term searches, cycling terms before requesting later pages."""
    search = definition.search
    terms = [*search.keywords.include, *search.companies]
    if not terms:
        terms = list(search.industries)
    if not terms:
        terms = list(search.titles.include)
    return [
        (terms[(step - 1) % len(terms)], ((step - 1) // len(terms)) + 1)
        for step in range(1, definition.limits.max_pages + 1)
    ]


def _boolean_or_group(terms: list[str]) -> str:
    quoted = [f'"{term}"' if " " in term else term for term in terms]
    return f"({' OR '.join(quoted)})" if quoted else ""


def is_empty_search_results(html: str) -> bool:
    """Recognize LinkedIn's normal empty-result page separately from layout failures."""
    text = " ".join(re.sub(r"<[^>]+>", " ", html).split()).casefold()
    return "no results found" in text and (
        "removing filters" in text or "rephrasing your search" in text
    )


def detect_safety_state(*, url: str, title: str, html: str) -> str | None:
    """Classify states that require the adapter to stop for user action."""
    combined = f"{url} {title} {html[:200_000]}".casefold()
    if (
        any(marker in url.casefold() for marker in ("/login", "/authwall", "/uas/login"))
        or "sign in | linkedin" in combined
    ):
        return "authentication"
    title_text = title.casefold()
    if "/checkpoint/" in url.casefold() or any(
        term in title_text for term in ("security verification", "captcha")
    ):
        return "security_challenge"
    if any(term in combined for term in ("too many requests", "rate limit", "status code 429")):
        return "rate_limited"
    return None


@dataclass(slots=True)
class _ParsedRecord:
    url: str = ""
    name: str = ""
    headline: str = ""
    location: str = ""


class _SearchResultParser(HTMLParser):
    """Small parser for stable semantic fragments in sanitized result markup."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[_ParsedRecord] = []
        self._record: _ParsedRecord | None = None
        self._field: str | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "li" and (
            "reusable-search__result-container" in classes
            or attributes.get("data-lead-result") is not None
        ):
            self._record = _ParsedRecord()
            self._depth = 1
            return
        if self._record is None:
            return
        self._depth += 1
        href = attributes.get("href") or ""
        if tag == "a" and "/in/" in href and not self._record.url:
            self._record.url = href
        if "entity-result__title-text" in classes or attributes.get("data-field") == "name":
            self._field = "name"
        elif (
            "entity-result__primary-subtitle" in classes
            or attributes.get("data-field") == "headline"
        ):
            self._field = "headline"
        elif (
            "entity-result__secondary-subtitle" in classes
            or attributes.get("data-field") == "location"
        ):
            self._field = "location"

    def handle_endtag(self, tag: str) -> None:
        if self._record is None:
            return
        self._field = None
        if tag == "li" and self._depth == 1:
            if self._record.url and self._record.name:
                self.records.append(self._record)
            self._record = None
            self._depth = 0
            return
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._record is None or self._field is None:
            return
        value = " ".join(data.split())
        if not value:
            return
        previous = getattr(self._record, self._field)
        setattr(self._record, self._field, f"{previous} {value}".strip())


def parse_search_results(html: str, source_search: str) -> list[LeadCandidate]:
    """Parse sanitized result markup into raw candidates without browser dependencies."""
    parser = _SearchResultParser()
    parser.feed(html)
    collected_at = datetime.now(UTC)
    return [
        LeadCandidate(
            profile_url=urljoin("https://www.linkedin.com", record.url),
            full_name=record.name,
            headline=record.headline,
            location=record.location,
            company_size_min=_visible_company_size(record.headline)[0],
            company_size_max=_visible_company_size(record.headline)[1],
            self_employed=_visible_self_employed(record.headline),
            source_search=source_search,
            collected_at=collected_at,
        )
        for record in parser.records
    ]


def parse_profile_anchors(anchors: list[dict[str, str]], source_search: str) -> list[LeadCandidate]:
    """Parse rendered profile-link text without relying on randomized CSS classes."""
    candidates: list[LeadCandidate] = []
    seen_urls: set[str] = set()
    collected_at = datetime.now(UTC)
    for anchor in anchors:
        url = urljoin("https://www.linkedin.com", anchor.get("href", ""))
        if "/in/" not in url or url in seen_urls:
            continue
        lines = [" ".join(line.split()) for line in anchor.get("text", "").splitlines()]
        lines = [line for line in lines if line]
        if len(lines) < 3:
            continue
        degree_marker = r"(?:[•·]|â€¢|Â·)"
        name = re.sub(rf"\s*{degree_marker}\s*(?:1st|2nd|3rd\+?).*$", "", lines[0]).strip()
        remaining = lines[1:]
        if remaining and re.fullmatch(rf"{degree_marker}\s*(?:1st|2nd|3rd\+?)", remaining[0]):
            remaining = remaining[1:]
        if len(remaining) < 2 or not name:
            continue
        headline, location = remaining[0], remaining[1]
        current_title = ""
        company = ""
        current_line = next(
            (
                line.removeprefix("Current:").strip()
                for line in remaining
                if line.startswith("Current:")
            ),
            "",
        )
        if current_line:
            current_title, separator, company = current_line.partition(" at ")
            if not separator:
                current_title = current_line
        candidates.append(
            LeadCandidate(
                profile_url=url,
                full_name=name,
                headline=headline,
                current_title=current_title,
                company=company,
                location=location,
                company_size_min=_visible_company_size(" ".join(remaining))[0],
                company_size_max=_visible_company_size(" ".join(remaining))[1],
                self_employed=_visible_self_employed(f"{headline} {current_line}"),
                source_search=source_search,
                collected_at=collected_at,
            )
        )
        seen_urls.add(url)
    return candidates


def _visible_company_size(text: str) -> tuple[int | None, int | None]:
    """Read an employee range only when LinkedIn rendered it in collected text."""
    match = re.search(r"\b([\d,]+)\s*[-–]\s*([\d,]+)\s+employees\b", text, re.I)
    if match is None:
        return None, None
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


def _visible_self_employed(text: str) -> bool | None:
    lowered = text.casefold()
    values = ("self-employed", "self employed", "sole trader", "independent trader", "solo founder")
    return True if any(value in lowered for value in values) else None


class PlaywrightLinkedInCollector:
    """Collect LinkedIn results in a user-visible persistent browser context."""

    def __init__(
        self,
        *,
        browser_data_dir: Path,
        screenshot_dir: Path,
        min_delay_seconds: float = 1.5,
        max_delay_seconds: float = 3.5,
        navigation_timeout_ms: int = 30_000,
        authentication_timeout_ms: int = 300_000,
        headless: bool = False,
    ) -> None:
        if min_delay_seconds < 0 or max_delay_seconds < min_delay_seconds:
            raise ValueError("browser delay range is invalid")
        self._browser_data_dir = browser_data_dir.resolve()
        self._screenshot_dir = screenshot_dir.resolve()
        self._min_delay = min_delay_seconds
        self._max_delay = max_delay_seconds
        self._timeout = navigation_timeout_ms
        self._authentication_timeout = authentication_timeout_ms
        self._headless = headless

    def collect(
        self, definition: SearchDefinition, *, start_page: int = 1, dry_run: bool = False
    ) -> CollectionBatch:
        if start_page < 1 or start_page > definition.limits.max_pages:
            raise ValueError("start_page is outside configured limits")
        if dry_run:
            logger.info("linkedin_collection_dry_run", extra={"start_page": start_page})
            return CollectionBatch(candidates=(), checkpoint_page=start_page - 1, complete=True)

        self._browser_data_dir.mkdir(parents=True, exist_ok=True)
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        candidates: list[LeadCandidate] = []
        seen_urls: set[str] = set()
        last_page = start_page - 1
        search_steps = build_people_search_steps(definition)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(self._browser_data_dir), headless=self._headless
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(self._timeout)
            try:
                try:
                    for step_number in range(start_page, definition.limits.max_pages + 1):
                        search_term, result_page = search_steps[step_number - 1]
                        source_url = build_people_search_url(
                            definition, result_page, search_term=search_term
                        )
                        for attempt in range(2):
                            try:
                                page.goto(source_url, wait_until="domcontentloaded")
                                break
                            except PlaywrightTimeoutError as error:
                                self._capture(page, step_number)
                                raise LayoutChangedError(
                                    "LinkedIn search page did not load in time"
                                ) from error
                            except PlaywrightError as error:
                                if "ERR_ABORTED" in str(error) and attempt == 0:
                                    logger.info(
                                        "linkedin_navigation_retry",
                                        extra={"step": step_number},
                                    )
                                    page.wait_for_timeout(1_000)
                                    continue
                                self._capture(page, step_number)
                                if "closed" in str(error).casefold():
                                    raise CollectionError(
                                        "LinkedIn browser window was closed before collection "
                                        "finished"
                                    ) from error
                                raise LayoutChangedError(
                                    "LinkedIn search navigation was interrupted"
                                ) from error
                        html = page.content()
                        state = detect_safety_state(url=page.url, title=page.title(), html=html)
                        if state == "authentication":
                            logger.warning("linkedin_authentication_waiting")
                            try:
                                page.wait_for_url(
                                    lambda current_url: (
                                        not any(
                                            marker in current_url.casefold()
                                            for marker in ("/login", "/authwall", "/uas/login")
                                        )
                                    ),
                                    timeout=self._authentication_timeout,
                                )
                            except PlaywrightTimeoutError as error:
                                raise AuthenticationRequiredError(
                                    "LinkedIn sign-in was not completed within five minutes"
                                ) from error
                            except PlaywrightError as error:
                                raise AuthenticationRequiredError(
                                    "browser navigation was interrupted during sign-in; "
                                    "use LinkedIn email/phone and password instead of "
                                    "Google sign-in"
                                ) from error
                            html = page.content()
                        state = detect_safety_state(url=page.url, title=page.title(), html=html)
                        if state == "security_challenge":
                            logger.warning("linkedin_security_challenge_waiting")
                            deadline = time.monotonic() + self._authentication_timeout / 1_000
                            while time.monotonic() < deadline:
                                page.wait_for_timeout(1_000)
                                html = page.content()
                                state = detect_safety_state(
                                    url=page.url, title=page.title(), html=html
                                )
                                if state != "security_challenge":
                                    break
                            else:
                                raise SecurityChallengeError(
                                    "LinkedIn security challenge was not completed within "
                                    "five minutes"
                                )
                        self._raise_for_safety_state(page.url, page.title(), html)
                        with suppress(PlaywrightTimeoutError):
                            page.locator('a[href*="/in/"]').first.wait_for(
                                state="attached", timeout=min(self._timeout, 15_000)
                            )
                        html = page.content()
                        try:
                            rendered_anchors = page.locator('a[href*="/in/"]').evaluate_all(
                                "elements => elements.flatMap(element => {"
                                "let card = element; "
                                "for (let depth = 0; depth < 8 && card; depth += 1) {"
                                "const text = card.innerText || ''; "
                                "const lines = text.split(/\\n/).filter(line => line.trim()); "
                                "if (lines.length >= 3 && text.length < 2000) "
                                "return [{href: element.href, text}]; "
                                "card = card.parentElement; "
                                "} "
                                "return []; })"
                            )
                        except PlaywrightError as error:
                            self._capture(page, step_number)
                            raise LayoutChangedError(
                                "LinkedIn result cards could not be inspected"
                            ) from error
                        page_candidates = (
                            parse_profile_anchors(rendered_anchors, source_url)
                            if isinstance(rendered_anchors, list)
                            else []
                        )
                        if not page_candidates:
                            page_candidates = parse_search_results(html, source_url)
                        if not page_candidates:
                            self._capture(page, step_number)
                            if is_empty_search_results(html):
                                last_page = step_number
                                time.sleep(random.uniform(self._min_delay, self._max_delay))
                                continue
                            if page.get_by_text("LinkedIn Member", exact=True).count():
                                logger.info(
                                    "linkedin_locked_results_skipped",
                                    extra={"step": step_number, "source_url": source_url},
                                )
                                last_page = step_number
                                time.sleep(random.uniform(self._min_delay, self._max_delay))
                                continue
                            raise LayoutChangedError(
                                "no recognizable people-search results were found; "
                                "page layout may have changed"
                            )
                        unique_candidates: list[LeadCandidate] = []
                        page_seen_urls: set[str] = set()
                        for candidate in page_candidates:
                            if (
                                candidate.profile_url in seen_urls
                                or candidate.profile_url in page_seen_urls
                            ):
                                continue
                            unique_candidates.append(candidate)
                            page_seen_urls.add(candidate.profile_url)
                        remaining = definition.limits.max_results - len(candidates)
                        accepted_candidates = unique_candidates[:remaining]
                        candidates.extend(accepted_candidates)
                        seen_urls.update(candidate.profile_url for candidate in accepted_candidates)
                        last_page = step_number
                        if len(candidates) >= definition.limits.max_results:
                            break
                        time.sleep(random.uniform(self._min_delay, self._max_delay))
                except CollectionError as error:
                    error.checkpoint_page = last_page
                    raise
            finally:
                context.close()
        complete = (
            last_page >= definition.limits.max_pages
            or len(candidates) >= definition.limits.max_results
        )
        return CollectionBatch(tuple(candidates), checkpoint_page=last_page, complete=complete)

    def _capture(self, page: object, page_number: int) -> None:
        path = self._screenshot_dir / f"linkedin-page-{page_number}.png"
        try:
            page.screenshot(path=str(path), full_page=True)  # type: ignore[attr-defined]
        except PlaywrightError as error:
            logger.warning(
                "linkedin_diagnostic_screenshot_failed",
                extra={"page": page_number, "error_type": error.__class__.__name__},
            )

    @staticmethod
    def _raise_for_safety_state(url: str, title: str, html: str) -> None:
        state = detect_safety_state(url=url, title=title, html=html)
        if state == "authentication":
            raise AuthenticationRequiredError("sign in manually in the supervised browser")
        if state == "security_challenge":
            raise SecurityChallengeError("LinkedIn security challenge requires user action")
        if state == "rate_limited":
            raise RateLimitedError("LinkedIn requested that collection stop")


class PlaywrightLinkedInOutreach:
    """Visible persistent-browser actions, called only after an explicit UI/CLI action."""

    def __init__(self, browser_data_dir: Path, timeout_ms: int = 30_000) -> None:
        self._browser_data_dir, self._timeout = browser_data_dir.resolve(), timeout_ms
        self._browser_data_dir.mkdir(parents=True, exist_ok=True)

    def send_connection(self, profile_url: str, note: str) -> None:
        def act(page: object) -> None:
            button = page.get_by_role("button", name=re.compile(r"^Connect$", re.I))
            if button.count() == 0:
                page.get_by_role("button", name=re.compile("More", re.I)).first.click()
                button = page.get_by_role("menuitem", name=re.compile("Connect", re.I))
            button.first.click(timeout=self._timeout)
            add_note = page.get_by_role("button", name=re.compile("Add a note", re.I))
            if note.strip() and add_note.count():
                add_note.click(); page.get_by_role("textbox").last.fill(note[:300])
            page.get_by_role("button", name=re.compile(r"^Send", re.I)).last.click()
        self._act(profile_url, act)

    def connection_is_accepted(self, profile_url: str) -> bool:
        value = [False]
        self._act(profile_url, lambda page: value.__setitem__(0,
            page.get_by_role("button", name=re.compile(r"^Message$", re.I)).count() > 0))
        return value[0]

    def send_message(self, profile_url: str, content: str) -> None:
        def act(page: object) -> None:
            page.get_by_role("button", name=re.compile(r"^Message$", re.I)).first.click()
            page.locator('[contenteditable="true"][role="textbox"]').last.fill(content)
            page.get_by_role("button", name=re.compile(r"^Send$", re.I)).last.click()
        self._act(profile_url, act)

    def _act(self, profile_url: str, action: object) -> None:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(self._browser_data_dir), headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(profile_url, wait_until="domcontentloaded", timeout=self._timeout)
                PlaywrightLinkedInCollector._raise_for_safety_state(
                    page.url, page.title(), page.content())
                action(page)  # type: ignore[operator]
                page.wait_for_timeout(random.randint(1500, 3500))
                PlaywrightLinkedInCollector._raise_for_safety_state(
                    page.url, page.title(), page.content())
            finally:
                context.close()
