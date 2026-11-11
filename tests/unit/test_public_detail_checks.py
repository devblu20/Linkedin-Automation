from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from linkedin_automation.application.public_prospect_review import prepare_public_collection
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.public_detail_checks import discover_and_check
from linkedin_automation.infrastructure.public_discovery import DiscoveryError


def definition():
    return YamlSearchDefinitionLoader().load(Path("config/bluqq-crm-recruitment.yaml"))


def original():
    return {
        "url": "https://www.linkedin.com/in/alex-person/",
        "title": "Alex Person - Recruitment agency Founder | LinkedIn",
        "description": "Recruitment agency in London",
    }


def test_missing_details_are_checked_by_exact_profile_within_shared_budget():
    calls = []

    def search(query, offset):
        calls.append((query, offset))
        if len(calls) == 1:
            return [original()]
        return [
            original(),
            {
                **original(),
                "url": "https://uk.linkedin.com/in/alex-person/",
                "title": "Alex Person - Founder at Example Agency | LinkedIn",
                "description": "Location: London",
                "extra_snippets": ["Education: Example University", "Skills: CRM, Python"],
            },
            {**original(), "description": "Industry: Recruitment · 500+ connections"},
        ]

    rows, used = discover_and_check(
        definition(), 1, SimpleNamespace(search=search), lambda *args: None, max_requests=2
    )
    assert used == len(calls) == 2
    assert calls[1][0] == 'site:linkedin.com "Alex Person"'
    assert rows[0]["profile_details"]["current_role"] == "Founder"
    assert rows[0]["profile_details"]["company"] == "Example Agency"
    assert rows[0]["profile_details"]["skills"] == ["CRM", "Python"]
    assert rows[0]["profile_details"]["education"] == ["Example University"]
    assert rows[0]["profile_details"]["industry"] == "Recruitment"
    assert rows[0]["profile_details"]["connections"] == "500+"
    assert rows[0]["profile_details"]["field_evidence"]["company"]["query"] == calls[1][0]
    assert "remaining fields need verification" in rows[0]["automatic_detail_check"].lower()
    assert rows[0]["location"] == "London"


def test_repeat_discovery_hits_fill_gaps_and_raise_score_within_existing_rules():
    calls = []

    def search(query, offset):
        calls.append((query, offset))
        if len(calls) == 1:
            return [original()]
        return [{**original(), "description": "Recruitment agency · Location: London"}]

    def collect(cap):
        calls.clear()
        rows, used = discover_and_check(
            definition(), 2, SimpleNamespace(search=search), lambda *args: None, max_requests=cap
        )
        assert used == cap
        _, leads, _ = prepare_public_collection(
            {"id": str(uuid4()), "created": 1700000000, "results": rows}, definition()
        )
        return leads[0]

    original_lead, original_record = collect(1)
    enriched_lead, enriched_record = collect(2)
    assert original_lead.location == ""
    assert enriched_lead.location == "London"
    assert enriched_record.score == original_record.score + 15
    assert enriched_record.status.value == "needs_verification"


def test_other_profile_or_identity_cannot_supply_details():
    calls = []

    def search(*args):
        calls.append(args)
        return (
            [original()]
            if len(calls) == 1
            else [
                {
                    **original(),
                    "title": "Other Person - Founder at WrongCo | LinkedIn",
                    "description": "Location: London",
                }
            ]
        )

    rows, used = discover_and_check(
        definition(), 1, SimpleNamespace(search=search), lambda *args: None, max_requests=2
    )
    assert used == 2 and rows[0]["profile_details"]["company"] == ""


def test_one_request_budget_cannot_trigger_additional_searches():
    calls = []
    provider = SimpleNamespace(search=lambda *args: calls.append(args) or [original()])
    rows, used = discover_and_check(definition(), 1, provider, lambda *args: None, max_requests=1)
    assert used == len(calls) == 1
    assert rows[0]["profile_details"]["company"] == ""


def test_additional_lookup_failure_preserves_discovered_profiles_without_retry():
    calls = []

    def search(*args):
        calls.append(args)
        if len(calls) == 1:
            return [original()]
        raise DiscoveryError("Brave quota/rate limit reached. No automatic retry.")

    rows, used = discover_and_check(
        definition(), 1, SimpleNamespace(search=search), lambda *args: None, max_requests=5
    )
    assert used == len(calls) == 2 and len(rows) == 1
    assert "stopped" in rows[0]["automatic_detail_check"]


def test_conflicting_public_fields_are_not_overwritten():
    from linkedin_automation.infrastructure.public_profile_details import hydrate_public_details

    item = {
        "name": "Alex Person",
        "profile_url": original()["url"],
        "public_title": "Alex Person - Founder at OriginalCo",
        "snippet": "Location: London",
        "additional_evidence": [
            {
                "profile_url": original()["url"],
                "public_title": "Alex Person - Director at ConflictingCo",
                "snippet": "Location: Bristol",
                "query": "exact",
            }
        ],
    }
    details = hydrate_public_details(item)
    assert details["company"] == "OriginalCo" and details["location"] == "London"
