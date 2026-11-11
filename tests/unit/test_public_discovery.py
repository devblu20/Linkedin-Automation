import sqlite3
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.public_discovery import (
    BraveSearch,
    DiscoveryError,
    discover,
    parse_public_result,
    public_queries,
)
from linkedin_automation.interfaces.public_discovery import (
    DiscoveryRequest,
    PublicDiscovery,
    csv_cell,
    readiness,
)


def definition():
    return YamlSearchDefinitionLoader().load(Path("config/bluqq-crm-recruitment.yaml"))


def result(number=1, description="Recruitment agency owner in London"):
    return {
        "url": f"https://uk.linkedin.com/in/person-{number}/?tracking=1",
        "title": f"Person {number} - Recruitment agency founder | LinkedIn",
        "description": description,
    }


def test_new_campaign_fields_drive_queries_without_reusing_old_campaign():
    base = definition()
    other = base.model_copy(
        update={
            "search": base.search.model_copy(
                update={
                    "locations": ["Manchester"],
                    "keywords": base.search.keywords.model_copy(
                        update={"include": ["architecture studio"]}
                    ),
                    "titles": base.search.titles.model_copy(update={"include": ["Partner"]}),
                }
            )
        }
    )
    queries = public_queries(other)
    assert all("Manchester" in q and "recruitment" not in q for q in queries)
    assert any("Partner" in q for q in queries)
    assert all(len(q) <= 400 for q in queries)


def test_public_snippet_never_invents_missing_location_company_or_current_role():
    row = parse_public_result(
        result(description="Recruitment agency founder"), "London", definition()
    )
    assert row["profile_url"] == "https://www.linkedin.com/in/person-1/"
    assert "locations" in row["missing"]
    assert row["company"] == row["current_role"] == ""
    assert row["customer_need"] == "Not verified"
    assert "Needs verification" in row["review_status"]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/in/person/",
        "https://linkedin.com.evil.test/in/person/",
        "https://www.linkedin.com/company/business/",
        "http://www.linkedin.com/in/person/",
        "https://user:password@www.linkedin.com/in/person/",
        "https://[bad/in/person/",
    ],
)
def test_only_public_linkedin_profile_urls_are_accepted(url):
    item = {**result(), "url": url}
    assert parse_public_result(item, "query", definition()) is None


def test_hidden_identity_and_explicit_exclusions_are_not_added():
    assert parse_public_result({**result(), "title": "LinkedIn Member"}, "", definition()) is None
    item = result(description="Student seeking internship in London")
    item["title"] = "Person One - Student | LinkedIn"
    assert parse_public_result(item, "", definition()) is None


def test_sixty_unique_results_are_collected_without_any_profile_navigation():
    calls = []

    class Provider:
        def search(self, query, offset):
            calls.append((query, offset))
            return [result(number) for number in range((len(calls) - 1) * 20, len(calls) * 20)]

    rows, count = discover(definition(), 60, Provider(), lambda *args: None)
    assert len(rows) == len({row["profile_url"] for row in rows}) == 60
    assert count == len(calls) == 3


def test_location_only_results_do_not_fill_business_campaign_target():
    item = {
        **result(description="Software development in London"),
        "title": "Person One - Software engineer | LinkedIn",
    }
    assert parse_public_result(item, "recruitment founder London", definition()) is None


def test_person_name_does_not_count_as_business_keyword():
    item = {**result(description="London"), "title": "Recruitment Agency | LinkedIn"}
    assert parse_public_result(item, "recruitment", definition()) is None


def test_snippet_role_does_not_confirm_target_title():
    spec = definition()
    spec = spec.model_copy(
        update={
            "search": spec.search.model_copy(
                update={"required_dimensions": ["titles", "locations", "keywords"]}
            )
        }
    )
    item = {
        **result(description="Recruitment agency in London; works with Founder clients"),
        "title": "Person One - Consultant | LinkedIn",
    }
    row = parse_public_result(item, "Founder", spec)
    assert row is not None
    assert "titles" in row["missing"]
    assert not any(x.startswith("title:") for x in row["matched"])
    assert row["evidence_status"] == "needs_verification"


def test_explicit_headline_role_matches_without_inventing_current_job():
    row = parse_public_result(result(), "query", definition())
    assert any(x.startswith("title:") for x in row["matched"])
    assert row["current_role"] == ""


def test_duplicate_results_do_not_inflate_target_and_requests_stay_bounded():
    calls = []
    provider = SimpleNamespace(search=lambda *args: calls.append(args) or [result()])
    rows, count = discover(definition(), 60, provider, lambda *args: None)
    assert len(rows) == 1 and count == len(calls) <= 25


@pytest.mark.parametrize("code", [401, 403, 402, 429, 500])
def test_provider_errors_do_not_retry_or_expose_key(monkeypatch, code):
    calls = []

    def fail(request, **kwargs):
        calls.append(request)
        raise HTTPError(request.full_url, code, "private-token-body", {}, None)

    monkeypatch.setattr(
        "linkedin_automation.infrastructure.public_discovery.build_opener",
        lambda *args: SimpleNamespace(open=fail),
    )
    monkeypatch.setattr(
        "linkedin_automation.infrastructure.public_discovery.time.sleep", lambda _: None
    )
    with pytest.raises(DiscoveryError) as error:
        BraveSearch("secret-value").search("query", 0)
    assert len(calls) == 1
    assert "secret-value" not in str(error.value) and "private-token-body" not in str(error.value)


def store(tmp_path, monkeypatch, storage):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fake")
    monkeypatch.setenv("BRAVE_SEARCH_STORAGE_ALLOWED", "1" if storage else "0")
    catalog = SimpleNamespace(resolve=lambda _: (definition(), None))
    return PublicDiscovery(tmp_path, catalog)


def test_preview_works_without_storage_and_never_writes_prospects_to_disk(tmp_path, monkeypatch):
    subject = store(tmp_path, monkeypatch, False)
    assert readiness()["configured"] and not readiness()["storage_enabled"]
    identifier, spec = subject.enqueue(DiscoveryRequest(campaign="example", target=60))
    subject.provider_factory = lambda: SimpleNamespace(search=lambda *args: [result()])
    subject.run(identifier, spec, 60)
    status = subject.status(identifier)
    assert status["found"] == 1 and status["persistent"] is False
    with sqlite3.connect(subject.path) as db:
        assert db.execute("SELECT count(*) FROM public_discovery_jobs").fetchone()[0] == 0
    second = PublicDiscovery(tmp_path, subject.catalog)
    assert second.status(identifier) is None
    assert "Person" not in subject.path.read_bytes().decode("latin1")


def test_preview_expires_and_parallel_requests_are_rejected(tmp_path, monkeypatch):
    subject = store(tmp_path, monkeypatch, False)
    identifier, _ = subject.enqueue(DiscoveryRequest(campaign="example"))
    with pytest.raises(ValueError, match="already running"):
        subject.enqueue(DiscoveryRequest(campaign="example"))
    subject.transient[identifier]["created"] -= 1801
    assert subject.status(identifier) is None


def test_permitted_storage_is_workspace_isolated_and_keeps_partial_results_on_failure(
    tmp_path, monkeypatch
):
    subject = store(tmp_path, monkeypatch, True)
    identifier, spec = subject.enqueue(DiscoveryRequest(campaign="example"))
    calls = []

    def search(*args):
        calls.append(args)
        if len(calls) > 1:
            raise DiscoveryError("Brave quota/rate limit reached. No automatic retry.")
        return [result()]

    subject.provider_factory = lambda: SimpleNamespace(search=search)
    subject.run(identifier, spec, 60)
    status = subject.status(identifier)
    assert status["status"] == "failed" and status["found"] == 1 and status["api_calls"] == 2
    assert status["persistent"]
    other = tmp_path / "other"
    other.mkdir()
    assert PublicDiscovery(other, subject.catalog).status(identifier) is None


def test_csv_formula_cells_are_neutralized():
    assert csv_cell('=HYPERLINK("evil")').startswith("'")
    assert csv_cell("\t+cmd").startswith("'")
    assert csv_cell("Normal Name") == "Normal Name"


def test_precise_business_role_query_precedes_broad_discovery():
    queries = public_queries(definition())
    assert len(queries) <= 12
    assert '"Founder"' in queries[0] or '"Founder"'.lower() in queries[0].lower()
    assert all(len(query) <= 400 for query in queries)


def test_extra_public_excerpts_supply_missing_evidence_without_inventing_role():
    item = result(description="Recruitment agency founder")
    item["extra_snippets"] = ["Based in London", "Based in London", None, {"bad": True}]
    row = parse_public_result(item, "query", definition())
    assert "locations" not in row["missing"]
    assert row["snippet"].count("Based in London") == 1
    assert row["company"] == row["current_role"] == ""


def test_incomplete_target_is_replaced_by_stronger_result_within_budget():
    calls = []

    def search(*args):
        calls.append(args)
        return [result(1, "Recruitment agency founder")] if len(calls) == 1 else [result(2)]

    rows, count = discover(definition(), 1, SimpleNamespace(search=search), lambda *args: None)
    assert count == 2
    assert rows[0]["profile_url"].endswith("person-2/")
    assert rows[0]["missing"] == []


def test_duplicate_url_keeps_stronger_standalone_observation():
    calls = []

    def search(*args):
        calls.append(args)
        return [result(1, "Recruitment agency founder")] if len(calls) == 1 else [result(1)]

    rows, count = discover(definition(), 1, SimpleNamespace(search=search), lambda *args: None)
    assert count == 2 and len(rows) == 1
    assert rows[0]["missing"] == []
    assert rows[0]["query"] == calls[1][0]


def test_repeated_incomplete_results_stop_before_spending_full_budget():
    provider = SimpleNamespace(search=lambda *args: [result(1, "Recruitment agency founder")])
    rows, count = discover(definition(), 60, provider, lambda *args: None)
    assert len(rows) == 1 and count == 5


def test_user_request_cap_stops_search_even_when_target_not_reached():
    calls = []
    provider = SimpleNamespace(search=lambda *args: calls.append(args) or [result(len(calls))])
    rows, count = discover(definition(), 100, provider, lambda *args: None, max_requests=2)
    assert count == len(calls) == len(rows) == 2


def test_usage_counts_attempts_once_and_survives_preview_expiry(tmp_path, monkeypatch):
    subject = store(tmp_path, monkeypatch, False)
    identifier, spec = subject.enqueue(DiscoveryRequest(campaign="example", max_requests=2))
    subject.provider_factory = lambda: SimpleNamespace(search=lambda *args: [result()])
    subject.run(identifier, spec, 60)
    assert subject.status(identifier)["max_requests"] == 2
    assert subject.usage()["month_attempts"] == 2
    assert subject.usage()["total_attempts"] == 2
    subject.transient[identifier]["created"] -= 1801
    assert subject.status(identifier) is None
    restarted = PublicDiscovery(tmp_path, subject.catalog)
    assert restarted.usage()["total_attempts"] == 2
    with sqlite3.connect(subject.path) as db:
        assert db.execute("SELECT count(*) FROM public_discovery_jobs").fetchone()[0] == 0
    assert "Person" not in subject.path.read_bytes().decode("latin1")


def test_failed_request_is_tracked_without_retry(tmp_path, monkeypatch):
    subject = store(tmp_path, monkeypatch, False)
    identifier, spec = subject.enqueue(DiscoveryRequest(campaign="example", max_requests=1))

    def fail(*args):
        raise DiscoveryError("Brave quota/rate limit reached. No automatic retry.")

    subject.provider_factory = lambda: SimpleNamespace(search=fail)
    subject.run(identifier, spec, 60)
    assert subject.status(identifier)["status"] == "failed"
    assert subject.usage()["total_attempts"] == 1


def test_public_review_scores_incomplete_prospects_without_promoting_unknown_facts():
    from uuid import uuid4

    from linkedin_automation.application.public_prospect_review import prepare_public_review

    row = parse_public_result(
        result(description="Recruitment agency founder"), "query", definition()
    )
    job = {"id": str(uuid4()), "created": 1700000000, "results": [row]}
    _, incomplete = prepare_public_review(job, definition(), [row["profile_url"]])
    assert incomplete[0][1].status.value == "needs_verification"
    assert "required:locations" in incomplete[0][0].evidence.excluded
    assert "Missing campaign evidence: locations" in incomplete[0][0].notes
    row = parse_public_result(result(), "query", definition())
    job["results"] = [row]
    run, rows = prepare_public_review(job, definition(), [row["profile_url"]])
    again, repeated = prepare_public_review(job, definition(), [row["profile_url"]])
    assert run.id == again.id and rows[0][0].id == repeated[0][0].id
    lead, outreach = rows[0]
    assert outreach.status.value == "needs_verification"
    assert lead.location == lead.current_title == lead.company == ""
    assert "not independently verified" in lead.notes
    from linkedin_automation.domain.services import create_outreach_record

    assert outreach.score == create_outreach_record(lead, definition()).score
