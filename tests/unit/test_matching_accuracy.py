from pathlib import Path

from linkedin_automation.domain.models import LeadCandidate
from linkedin_automation.domain.search_definition import IncludeExcludeCriteria
from linkedin_automation.domain.services import evaluate_candidate
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader


def definition():
    result = YamlSearchDefinitionLoader().load(Path("config/bluqq-crm-recruitment.yaml"))
    search = result.search.model_copy(
        update={
            "titles": IncludeExcludeCriteria(include=["Founder", "CEO"], exclude=["Intern"]),
            "keywords": IncludeExcludeCriteria(include=["IT consulting"], exclude=[]),
            "required_dimensions": ["titles", "keywords", "locations"],
        }
    )
    return result.model_copy(update={"search": search})


def candidate(**updates):
    return LeadCandidate(
        **{
            "profile_url": "https://www.linkedin.com/in/example/",
            "full_name": "Example Person",
            "current_title": "Founder",
            "headline": "Founder of an international IT consulting business",
            "company": "Example IT Consulting",
            "location": "London, England, United Kingdom",
            **updates,
        }
    )


def test_complete_terms_do_not_reject_international_as_intern():
    result = evaluate_candidate(candidate(), definition())
    assert not result.excluded
    assert "title:Founder" in result.matched


def test_actual_intern_is_still_rejected():
    result = evaluate_candidate(candidate(current_title="IT Consulting Intern"), definition())
    assert "title:Intern" in result.excluded


def test_title_uses_current_role_not_company_or_past_role_in_headline():
    result = evaluate_candidate(
        candidate(
            current_title="Software Engineer",
            headline="Former founder, now an engineer in IT consulting",
            company="Founder IT Consulting",
        ),
        definition(),
    )
    assert "required:titles" in result.excluded
    assert not any(x.startswith("title:") for x in result.matched)


def test_missing_role_can_use_headline_without_guessing():
    result = evaluate_candidate(candidate(current_title=""), definition())
    assert not result.excluded


def test_short_keyword_it_does_not_match_equity():
    original = definition()
    original = original.model_copy(
        update={
            "search": original.search.model_copy(
                update={
                    "keywords": IncludeExcludeCriteria(include=["IT"]),
                }
            )
        }
    )
    result = evaluate_candidate(
        candidate(headline="Founder, equity trading", company="Equity Co"), original
    )
    assert "required:keywords" in result.excluded


def test_company_intern_does_not_override_structured_founder_role():
    result = evaluate_candidate(candidate(company="Intern Placement IT Consulting"), definition())
    assert not result.excluded


def test_phrase_whitespace_and_word_boundaries():
    result = evaluate_candidate(
        candidate(headline="Founder in IT   consulting", company=""), definition()
    )
    assert not result.excluded
    result = evaluate_candidate(candidate(location="Londonderry, United Kingdom"), definition())
    assert "required:locations" in result.excluded
