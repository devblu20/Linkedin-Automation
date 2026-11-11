"""Offline tests for LinkedIn query construction, parsing, and safety stops."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from linkedin_automation.infrastructure.browser import (
    build_people_search_steps,
    build_people_search_url,
    detect_safety_state,
    is_empty_search_results,
    is_limited_search_results,
    parse_profile_anchors,
    parse_search_results,
)
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader


def test_builds_bounded_people_search_url() -> None:
    definition = YamlSearchDefinitionLoader().load(Path("config/search.example.yaml"))
    url = build_people_search_url(definition, 2)
    assert url.startswith("https://www.linkedin.com/search/results/people/")
    assert "page=2" in url
    assert "LLM" in url
    assert "network=" not in url
    assert "AI+Engineer" not in url
    assert "geoUrn=%5B%22102257491%22%5D" not in url


def test_builds_boolean_subject_query_without_title_or_location_lists() -> None:
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-prop-family-offices.yaml")
    )
    url = build_people_search_url(definition, 1)
    assert "%22family+office%22" in url
    assert "+OR+" in url
    assert "London" not in url
    assert "Chief+Executive+Officer" not in url
    assert "geoUrn=%5B%22102257491%22%5D" in url


def test_plans_separate_round_robin_term_searches() -> None:
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-prop-family-offices.yaml")
    )
    # Optional titles retain the subject-only round-robin plan. Required titles are
    # now included in discovery queries and covered by test_required_role_search.
    definition = definition.model_copy(
        update={
            "search": definition.search.model_copy(
                update={
                    "required_dimensions": [
                        x for x in definition.search.required_dimensions if x != "titles"
                    ]
                }
            )
        }
    )
    steps = build_people_search_steps(definition)
    assert len(steps) == definition.limits.max_pages
    assert steps[:3] == [
        ("family office", 1),
        ("private investment office", 1),
        ("single family office", 1),
    ]
    assert steps[8] == ("family office", 2)


def test_builds_single_term_search_url() -> None:
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-prop-family-offices.yaml")
    )
    url = build_people_search_url(definition, 1, search_term="family office")
    assert "keywords=family+office" in url
    assert "+OR+" not in url


@pytest.mark.parametrize("location", ["United Kingdom", "UK"])
def test_uk_campaign_applies_country_filter_to_people_search(location):
    definition = YamlSearchDefinitionLoader().load(Path("config/bluqq-crm-recruitment.yaml"))
    definition = definition.model_copy(
        update={"search": definition.search.model_copy(update={"locations": [location]})}
    )
    query = parse_qs(urlsplit(build_people_search_url(definition, 1)).query)
    assert query["geoUrn"] == ['["101165590"]']
    assert definition.search.locations == [location]


def test_optional_connection_filter_is_explicit_and_deduplicated() -> None:
    definition = YamlSearchDefinitionLoader().load(Path("config/search.example.yaml"))
    data = definition.model_dump()
    data["search"]["connection_degrees"] = ["1st", "2nd", "1st"]
    filtered = type(definition).model_validate(data)
    query = parse_qs(urlsplit(build_people_search_url(filtered, 1)).query)
    assert query["network"] == ['["F","S"]']
    assert definition.search.connection_degrees == []
    data["search"]["connection_degrees"] = ["4th"]
    with pytest.raises(ValueError):
        type(definition).model_validate(data)


def test_recognizes_normal_empty_results_page() -> None:
    html = (
        "<main><h2>No results found</h2>"
        "<p>Try removing filters or rephrasing your search.</p></main>"
    )
    assert is_empty_search_results(html)
    assert not is_empty_search_results("<main>People search</main>")


def test_recognizes_commercial_search_limit() -> None:
    html = (
        "<main><h2>You might benefit from unlimited search</h2>"
        "<p>Upgrade to Premium Business so you can search and browse anyone without limits.</p>"
        "<div>LinkedIn Member</div><div>LinkedIn Member</div></main>"
    )
    assert is_limited_search_results(html)
    assert not is_limited_search_results("<main>Ada Example - Founder</main>")


def test_parses_sanitized_people_results() -> None:
    html = Path("tests/fixtures/linkedin_people_results.html").read_text(encoding="utf-8")
    candidates = parse_search_results(html, "fixture-search")
    assert [candidate.full_name for candidate in candidates] == ["Ada Example", "Grace Example"]
    assert candidates[0].headline == "Senior AI Engineer at Example Labs"
    assert candidates[1].profile_url == "https://www.linkedin.com/in/grace-example/"


def test_parses_rendered_profile_anchors_and_ignores_mutual_connections() -> None:
    candidates = parse_profile_anchors(
        [
            {
                "href": "https://www.linkedin.com/in/ada-example/",
                "text": (
                    "Ada Example\n • 2nd\nAI Engineer | LLM systems\nIndia\nFollow\n"
                    "Current: Senior AI Engineer at Example Labs"
                ),
            },
            {
                "href": "https://www.linkedin.com/in/mutual-example/",
                "text": "Mutual Example",
            },
            {
                "href": "https://www.linkedin.com/in/ada-example/",
                "text": "Ada Example",
            },
        ],
        "rendered-search",
    )
    assert len(candidates) == 1
    assert candidates[0].full_name == "Ada Example"
    assert candidates[0].current_title == "Senior AI Engineer"
    assert candidates[0].company == "Example Labs"
    assert candidates[0].location == "India"


def test_parses_new_result_card_text_around_profile_anchor() -> None:
    candidates = parse_profile_anchors(
        [
            {
                "href": "https://www.linkedin.com/in/g7fx-neerav-vadera/",
                "text": (
                    "Neerav Vadera · 2nd\n"
                    "Head of Proprietary Trading at G7FX\n"
                    "London, England, United Kingdom\n"
                    "Devansh Duggal is a mutual connection"
                ),
            }
        ],
        "rendered-search",
    )
    assert len(candidates) == 1
    assert candidates[0].full_name == "Neerav Vadera"
    assert candidates[0].headline == "Head of Proprietary Trading at G7FX"
    assert candidates[0].location == "London, England, United Kingdom"


def test_parses_degree_marker_on_separate_line() -> None:
    candidates = parse_profile_anchors(
        [
            {
                "href": "https://www.linkedin.com/in/g7fx-neerav-vadera/",
                "text": (
                    "Neerav Vadera\n"
                    "• 2nd\n"
                    "Head of Proprietary Trading at G7FX\n"
                    "London, England, United Kingdom\n"
                    "Connect\n"
                    "Current: Managing Director at G7FX"
                ),
            }
        ],
        "rendered-search",
    )
    assert len(candidates) == 1
    assert candidates[0].full_name == "Neerav Vadera"
    assert candidates[0].headline == "Head of Proprietary Trading at G7FX"
    assert candidates[0].location == "London, England, United Kingdom"
    assert candidates[0].company == "G7FX"


def test_skips_anonymous_view_button_card_even_with_profile_link() -> None:
    candidates = parse_profile_anchors(
        [
            {
                "href": "https://www.linkedin.com/in/private-trader/",
                "text": (
                    "LinkedIn Member\n"
                    "Systematic Proprietary Trading\n"
                    "London, England, United Kingdom\n"
                    "Current: Head of Proprietary Trading at Undisclosed Hedge Fund\n"
                    "View"
                ),
            }
        ],
        "rendered-search",
    )
    assert candidates == []


@pytest.mark.parametrize(
    ("url", "title", "html", "expected"),
    [
        ("https://www.linkedin.com/login", "LinkedIn", "", "authentication"),
        ("https://www.linkedin.com/authwall", "LinkedIn", "", "authentication"),
        ("https://www.linkedin.com/checkpoint/challenge", "", "", "security_challenge"),
        ("https://www.linkedin.com", "Security verification", "", "security_challenge"),
        ("https://www.linkedin.com", "CAPTCHA", "", "security_challenge"),
        ("https://www.linkedin.com", "People", "CAPTCHA help text", None),
        ("https://www.linkedin.com", "", "Too many requests", "rate_limited"),
        ("https://www.linkedin.com/search/results/people", "People", "results", None),
    ],
)
def test_detects_safety_states(url: str, title: str, html: str, expected: str | None) -> None:
    assert detect_safety_state(url=url, title=title, html=html) == expected


@pytest.mark.parametrize(
    "notice",
    [
        "You've reached the monthly limit for profile searches.",
        "You&#39;ve reached the monthly limit for profile searches.",
        "You have reached your commercial use limit.",
    ],
)
def test_explicit_search_quota_is_distinct_from_network_visibility(notice):
    from linkedin_automation.application.exceptions import SearchQuotaExceededError
    from linkedin_automation.infrastructure.browser.linkedin import PlaywrightLinkedInCollector

    html = "<main><p>" + notice + "</p><div>Named Person - Founder - London</div></main>"
    assert (
        detect_safety_state(
            url="https://www.linkedin.com/search/results/people/", title="Search", html=html
        )
        == "search_quota"
    )
    with pytest.raises(SearchQuotaExceededError, match="monthly profile-search limit"):
        PlaywrightLinkedInCollector._raise_for_safety_state(
            "https://www.linkedin.com/search/results/people/", "Search", html
        )


def test_premium_ad_and_script_text_do_not_claim_exhausted_quota():
    for html in (
        "<main>You might benefit from unlimited search. Upgrade to Premium Business.</main>",
        "<main>Named Person</main><script>"
        '"reached the monthly limit for profile searches"</script>',
    ):
        assert (
            detect_safety_state(
                url="https://www.linkedin.com/search/results/people/", title="Search", html=html
            )
            is None
        )
