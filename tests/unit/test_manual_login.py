from typing import cast

from playwright.sync_api import Page

from linkedin_automation.infrastructure.browser.manual_login import (
    authenticated_location,
    wait_for_manual_close,
)


def test_only_authenticated_linkedin_locations_count():
    assert authenticated_location("https://www.linkedin.com/feed/", "Feed", "content")
    assert not authenticated_location("https://accounts.google.com/feed/", "Feed", "content")
    assert not authenticated_location("https://www.linkedin.com/login", "Login", "content")
    assert not authenticated_location(
        "https://www.linkedin.com/feed/", "Security verification", "content"
    )


def test_does_not_close_when_login_completes():
    class FakePage:
        url = "https://www.linkedin.com/feed/"
        waits = 0

        def is_closed(self):
            return self.waits >= 3

        def title(self):
            return "Feed"

        def content(self):
            return "content"

        def wait_for_timeout(self, delay):
            self.waits += 1

    page = FakePage()
    signals = []
    assert wait_for_manual_close(cast(Page, page), 30, lambda: signals.append(True))
    assert signals == [True]
    assert page.waits == 3
