"""Offline tests for LinkedIn query construction, parsing, and safety stops."""

from pathlib import Path

import pytest

from linkedin_automation.infrastructure.browser import (
    build_people_search_steps,
    build_people_search_url,
    detect_safety_state,
    is_empty_search_results,
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


def test_recognizes_normal_empty_results_page() -> None:
    html = (
        "<main><h2>No results found</h2>"
        "<p>Try removing filters or rephrasing your search.</p></main>"
    )
    assert is_empty_search_results(html)
    assert not is_empty_search_results("<main>People search</main>")


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


def test_parses_view_button_card_without_connection_degree() -> None:
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
    assert len(candidates) == 1
    assert candidates[0].full_name == "LinkedIn Member"
    assert candidates[0].headline == "Systematic Proprietary Trading"
    assert candidates[0].location == "London, England, United Kingdom"
    assert candidates[0].current_title == "Head of Proprietary Trading"
    assert candidates[0].company == "Undisclosed Hedge Fund"


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
