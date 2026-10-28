"""Opt-in Chrome DOM regression: no network requests or account required."""

from playwright.sync_api import sync_playwright

from linkedin_automation.infrastructure.browser.linkedin import (
    SEARCH_CARD_SCRIPT,
    SEARCH_CARD_SELECTOR,
    parse_profile_anchors,
)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.set_content("""<main>
        <div data-view-name="people-search-result" style="display:contents">
        <a href="https://www.linkedin.com/in/accessible/">
        <p>Accessible Person</p><p>Independent Trader</p><p>London</p></a></div>
        <li class="reusable-search__result-container">
        <a href="https://www.linkedin.com/in/legacy/">
        <p>Legacy Person</p><p>Founder</p><p>London</p></a></li>
        <div data-view-name="people-search-result">
        <a href="https://www.linkedin.com/in/masked/">
        <p>LinkedIn Member</p><p>Independent Trader</p><p>London</p></a></div>
        <div data-view-name="people-search-result" style="display:none">
        <a href="https://www.linkedin.com/in/hidden/">
        <p>Hidden Person</p><p>Independent Trader</p><p>London</p></a></div>
        <aside><a href="https://www.linkedin.com/in/recommendation/">
        <p>Recommended Person</p><p>Founder</p><p>London</p></a></aside>
        </main><nav><a href="https://www.linkedin.com/in/me/">
        <p>Signed In Person</p><p>Founder</p><p>London</p></a></nav>""")
        records = page.locator(SEARCH_CARD_SELECTOR).evaluate_all(SEARCH_CARD_SCRIPT)
        candidates = parse_profile_anchors(records, "offline-regression")
        assert {x.full_name for x in candidates} == {"Accessible Person", "Legacy Person"}
        assert all(
            "masked" not in x.profile_url and "recommendation" not in x.profile_url
            for x in candidates
        )
        browser.close()
    print(
        "PASS: actual search cards collected; "
        "anonymous cards, sidebar and signed-in profile ignored"
    )


if __name__ == "__main__":
    main()
