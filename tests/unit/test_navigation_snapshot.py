from typing import cast

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from linkedin_automation.application.exceptions import CollectionError, LayoutChangedError
from linkedin_automation.infrastructure.browser.linkedin import read_page_snapshot


class RedirectingPage:
    url = "https://www.linkedin.com/search/results/people/"

    def __init__(self, closed=False, failures=1):
        self.closed, self.failures, self.waits = closed, failures, 0

    def is_closed(self):
        return self.closed

    def title(self):
        return "People"

    def content(self):
        if self.failures:
            self.failures -= 1
            raise PlaywrightError("Unable to retrieve content because the page is navigating")
        return "settled results"

    def wait_for_timeout(self, delay):
        self.waits += 1


def test_retries_transient_post_verification_navigation():
    page = RedirectingPage()
    assert read_page_snapshot(cast(Page, page))[2] == "settled results"
    assert page.waits == 1


def test_closed_browser_produces_resumable_collection_error():
    with pytest.raises(CollectionError, match="closed"):
        read_page_snapshot(cast(Page, RedirectingPage(closed=True)))


def test_unsettled_navigation_is_bounded():
    with pytest.raises(LayoutChangedError, match="did not settle"):
        read_page_snapshot(cast(Page, RedirectingPage(failures=10)), timeout_seconds=0)
