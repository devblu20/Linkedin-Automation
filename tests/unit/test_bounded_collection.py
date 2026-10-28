from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from linkedin_automation.application.exceptions import BrowserClosedError, LimitedVisibilityError
from linkedin_automation.infrastructure.browser.linkedin import PlaywrightLinkedInCollector
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader


def anchor(name, suffix=""):
    return {
        "href": f"https://www.linkedin.com/in/{name}/{suffix}",
        "text": f"{name}\nFounder independent trading\nLondon",
    }


class SearchPage:
    def __init__(self, pages):
        self.pages = pages
        self.index = -1
        self.url = ""
        self.queries = []
        self.profile_visits = []

    def goto(self, url, **kwargs):
        self.url = url
        if "/search/results/people" in url:
            self.index += 1
            self.queries.append(url)
        else:
            self.profile_visits.append(url)

    def set_default_timeout(self, timeout):
        pass

    def is_closed(self):
        return False

    def title(self):
        return "People search"

    def content(self):
        return "LinkedIn Member " * self.pages[self.index][1] + " results"

    def locator(self, selector):
        page = self

        class Locator:
            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                if not page.pages[page.index][0]:
                    raise PlaywrightTimeoutError("No profile anchors")

            def evaluate_all(self, script):
                return page.pages[page.index][0]

            def get_by_text(self, text, **kwargs):
                return self

            def count(self):
                return page.pages[page.index][1]

        return Locator()

    def get_by_text(self, text, **kwargs):
        return self.locator("main")


def collect(monkeypatch, tmp_path, pages, limit=5, redirect=None, unreadable=False):
    page = SearchPage(pages)
    if redirect:
        original_goto = page.goto

        def redirected_goto(url, **kwargs):
            original_goto(url, **kwargs)
            if "/in/" in url:
                page.url = redirect

        page.goto = redirected_goto
    context = SimpleNamespace(pages=[page], close=lambda: None)

    @contextmanager
    def playwright():
        yield SimpleNamespace(
            chromium=SimpleNamespace(launch_persistent_context=lambda *a, **k: context)
        )

    monkeypatch.setattr(
        "linkedin_automation.infrastructure.browser.linkedin.sync_playwright", playwright
    )

    def extract(p, candidate):
        if unreadable == "closed":
            raise PlaywrightTimeoutError("Target page, context or browser has been closed")
        if unreadable:
            raise PlaywrightTimeoutError("Profile unavailable")
        return candidate

    monkeypatch.setattr(
        "linkedin_automation.infrastructure.browser.linkedin.extract_visible_profile",
        extract,
    )
    monkeypatch.setattr(
        "linkedin_automation.infrastructure.browser.linkedin.time.sleep", lambda _: None
    )
    monkeypatch.setattr(PlaywrightLinkedInCollector, "_capture", lambda *args: None)
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-independent-traders.yaml")
    )
    definition = definition.model_copy(
        update={
            "limits": definition.limits.model_copy(
                update={"max_results": limit, "max_pages": len(pages)}
            )
        }
    )
    collector = PlaywrightLinkedInCollector(
        browser_data_dir=tmp_path / "browser", screenshot_dir=tmp_path / "shots"
    )
    return collector.collect(definition), page


def test_hidden_first_query_does_not_prevent_later_accessible_results(monkeypatch, tmp_path):
    batch, page = collect(
        monkeypatch,
        tmp_path,
        [
            ([], 10),
            ([anchor("A"), anchor("B")], 8),
            ([anchor("A", "?tracking=1"), anchor("C"), anchor("D"), anchor("E")], 6),
        ],
    )
    assert len(batch.candidates) == 5
    assert len(page.queries) == 3
    assert len(set(page.profile_visits)) == 5
    assert batch.pages_visited == 3
    assert batch.hidden_result_cards == 24


def test_partial_results_continue_across_hidden_query_within_budget(monkeypatch, tmp_path):
    batch, page = collect(monkeypatch, tmp_path, [([anchor("A")], 9), ([], 10), ([anchor("B")], 9)])
    assert len(batch.candidates) == 2
    assert batch.pages_visited == 3
    assert len(page.queries) == 3


def test_all_hidden_results_stop_after_budget_with_no_fake_profiles(monkeypatch, tmp_path):
    with pytest.raises(LimitedVisibilityError) as stopped:
        collect(monkeypatch, tmp_path, [([], 10), ([], 10), ([], 10)])
    assert stopped.value.pages_visited == 3
    assert stopped.value.hidden_result_cards == 30
    assert stopped.value.checkpoint_page == 0


def test_read_limit_stops_before_additional_queries(monkeypatch, tmp_path):
    batch, page = collect(
        monkeypatch, tmp_path, [([anchor("A"), anchor("B")], 0), ([anchor("C")], 0)], limit=2
    )
    assert len(batch.candidates) == 2
    assert len(page.queries) == 1


@pytest.mark.parametrize(
    "redirect", ["https://www.linkedin.com/feed/", "https://www.linkedin.com/in/another-person/"]
)
def test_redirected_profile_is_not_saved_from_search_card(monkeypatch, tmp_path, redirect):
    batch, page = collect(monkeypatch, tmp_path, [([anchor("A")], 0)], redirect=redirect)
    assert batch.candidates == ()
    assert len(page.profile_visits) == 1


def test_unreadable_profile_is_not_accepted_from_stale_search_card(monkeypatch, tmp_path):
    batch, _ = collect(monkeypatch, tmp_path, [([anchor("A")], 0)], unreadable=True)
    assert batch.candidates == ()


def test_failed_profile_visits_also_respect_limit(monkeypatch, tmp_path):
    batch, page = collect(
        monkeypatch, tmp_path, [([anchor("A")], 0), ([anchor("B")], 0)], limit=1, unreadable=True
    )
    assert batch.candidates == ()
    assert len(page.profile_visits) == 1
    assert len(page.queries) == 1
    assert batch.complete


def test_closed_browser_stops_instead_of_reporting_empty_success(monkeypatch, tmp_path):
    with pytest.raises(BrowserClosedError) as stopped:
        collect(monkeypatch, tmp_path, [([anchor("A")], 9)], unreadable="closed")
    assert stopped.value.pages_visited == 1
    assert stopped.value.hidden_result_cards == 9
