from pathlib import Path

from linkedin_automation.application.search_plan import build_people_search_steps
from linkedin_automation.domain.models import LeadCandidate
from linkedin_automation.infrastructure.browser.linkedin import (
    enrich_visible_profile_text,
    refresh_profile_header,
)
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader


def test_focused_role_query_is_already_covered_by_keywords_and_budget_is_unchanged():
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-independent-traders.yaml")
    )
    before = definition.model_dump_json()
    steps = build_people_search_steps(definition)
    assert steps[0] == ("Independent Trader", 1)
    assert steps[1] == ("trading", 1)
    assert len(steps) == definition.limits.max_pages
    assert definition.model_dump_json() == before


def test_query_deduplication_preserves_first_priority_and_later_page_cycle():
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-independent-traders.yaml")
    )
    definition = definition.model_copy(
        update={
            "search": definition.search.model_copy(
                update={
                    "titles": definition.search.titles.model_copy(
                        update={"include": ["Independent Trader"]}
                    ),
                    "keywords": definition.search.keywords.model_copy(
                        update={"include": ["independent", "Independent Trader", "options"]}
                    ),
                }
            ),
            "limits": definition.limits.model_copy(update={"max_pages": 5}),
        }
    )
    assert build_people_search_steps(definition) == [
        ("Independent Trader", 1),
        ("independent", 1),
        ("options", 1),
        ("Independent Trader", 2),
        ("independent", 2),
    ]


def stale_candidate():
    return LeadCandidate(
        profile_url="https://www.linkedin.com/in/example/",
        full_name="Example Person",
        headline="Founder independent trader at Old Company",
        current_title="Founder",
        company="Old Company",
        location="London",
        self_employed=True,
        company_size_min=1,
        company_size_max=5,
    )


def test_current_profile_header_replaces_old_role_location_and_inferences():
    fresh = refresh_profile_header(
        "Example Person\nShe/Her\n· 3rd+\nAnalyst at New Company\n"
        "Birmingham, England\nContact info\nAbout\nSubject bio",
        stale_candidate(),
    )
    assert fresh is not None
    result = enrich_visible_profile_text(
        "Example Person\nAnalyst at New Company\nBirmingham, England\nAbout\nSubject bio", fresh
    )
    assert result.location == "Birmingham, England"
    assert result.current_title == "Analyst"
    assert result.company == "New Company"
    assert result.self_employed is None
    assert result.company_size_max is None


def test_header_of_other_person_or_partial_header_is_not_used():
    assert refresh_profile_header("Other Person\nFounder\nLondon", stale_candidate()) is None
    assert refresh_profile_header("Example Person\nFounder\nConnect", stale_candidate()) is None
