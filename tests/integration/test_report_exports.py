import csv
import io
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.models import LeadCandidate, Observation, ResearchRun
from linkedin_automation.domain.services import create_outreach_record, process_candidate
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.persistence import SqliteResearchRepository
from linkedin_automation.interfaces.hosted_site import create_hosted_app
from linkedin_automation.interfaces.review_site import create_review_app


@pytest.fixture
def reports(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path))
    database = tmp_path / "research.sqlite3"
    repository = SqliteResearchRepository(database)
    repository.initialize()
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-independent-traders.yaml")
    )
    runs = []
    for n in range(2):
        run = ResearchRun.create(
            search_name=f"Campaign {n}",
            schema_version=2,
            definition_json=definition.model_dump_json(),
            definition_hash="b" * 64,
            dry_run=False,
        )
        run = replace(run, report_path="/private/local-report.xlsx")
        repository.add_run(run)
        runs.append(run)
        for i in range(28 if n == 0 else 1):
            candidate = LeadCandidate(
                profile_url=f"https://linkedin.com/in/export-{n}-{i}",
                full_name="=2+3" if i == 0 else "José Example",
                headline="Independent trader",
                current_title="Independent Trader",
                company="Example",
                location="London",
                about="About\x00text",
                profile_snapshot="private raw snapshot",
            )
            accepted = i % 2 == 0
            repository.add_observation(
                Observation(
                    uuid4(),
                    run.id,
                    candidate,
                    accepted,
                    candidate.profile_url,
                    None if accepted else "required:keywords",
                )
            )
            if accepted:
                lead = process_candidate(candidate, definition, run.id)
                assert lead is not None
                repository.upsert_lead(lead)
                repository.upsert_outreach(
                    replace(
                        create_outreach_record(lead, definition),
                        connection_message="@untrusted draft",
                        status=OutreachStatus.NEEDS_VERIFICATION,
                    )
                )
    repository.close()
    with TestClient(create_hosted_app()) as client:
        client.auth = ("reviewer", "test-password-at-least-24-characters")
        yield client, runs, database


def csv_rows(response):
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_csv_exports_all_pages_with_run_and_decision_filters(reports):
    client, runs, _ = reports
    response = client.get(f"/api/exports/observations.csv?run_id={runs[0].id}")
    rows = csv_rows(response)
    assert len(rows) == 28
    assert response.headers["x-export-row-count"] == "28"
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    rejected = csv_rows(
        client.get(f"/api/exports/observations.csv?run_id={runs[0].id}&decision=rejected")
    )
    assert len(rejected) == 14
    assert all(row["Collection decision"] == "rejected" for row in rejected)
    assert all(row["Rejection reasons"] == "Missing required keywords" for row in rejected)
    assert set(row["Run ID"] for row in rejected) == {str(runs[0].id)}


@pytest.mark.parametrize("dataset", ["leads", "observations"])
def test_xlsx_and_csv_are_private_unicode_and_formula_safe(reports, dataset):
    client, _, _ = reports
    response = client.get(f"/api/exports/{dataset}.csv")
    rows = csv_rows(response)
    assert any(row["Full name"] == "'=2+3" for row in rows)
    assert any(row["Full name"] == "José Example" for row in rows)
    assert "private raw snapshot" not in response.text
    assert "/private/local-report.xlsx" not in response.text
    assert "\x00" not in response.text
    if dataset == "leads":
        assert all(row["Connection draft"] == "'@untrusted draft" for row in rows)
    excel = client.get(f"/api/exports/{dataset}.xlsx")
    assert excel.status_code == 200
    workbook = load_workbook(io.BytesIO(excel.content))
    assert "Export scope" in workbook.sheetnames
    sheet = workbook.worksheets[0]
    assert sheet.max_row == len(rows) + 1
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref
    assert all(cell.data_type != "f" for page in workbook for row in page for cell in row)
    values = str(list(sheet.values))
    assert "private raw snapshot" not in values and "/private/local-report.xlsx" not in values
    workbook.close()


def test_saved_lead_filters_and_empty_report(reports):
    client, runs, _ = reports
    rows = csv_rows(client.get("/api/exports/leads.csv?q=Jos%C3%A9&status=needs_verification"))
    assert len(rows) == 13
    assert all(row["Full name"] == "José Example" for row in rows)
    assert csv_rows(client.get("/api/exports/leads.csv?status=approved")) == []
    selected = csv_rows(client.get(f"/api/exports/leads.csv?run_id={runs[1].id}"))
    assert len(selected) == 1 and selected[0]["Campaign"] == "Campaign 1"


def test_report_authentication_scope_and_validation(reports):
    client, runs, database = reports
    assert client.get("/api/exports/leads.pdf").status_code == 422
    assert client.get("/api/exports/unknown.csv").status_code == 422
    assert client.get("/api/exports/leads.csv?decision=rejected").status_code == 422
    assert client.get("/api/exports/observations.csv?status=approved").status_code == 422
    assert client.get(f"/api/exports/leads.csv?run_id={uuid4()}").status_code == 404
    client.auth = None
    assert client.get("/api/exports/leads.csv").status_code == 401
    assert client.get("/api/exports/observations.xlsx").status_code == 401
    with TestClient(create_review_app(database, runs[0].id)) as scoped:
        assert scoped.get(f"/api/exports/leads.csv?run_id={runs[1].id}").status_code == 404
        assert len(csv_rows(scoped.get("/api/exports/observations.csv"))) == 28


def test_export_limit_refuses_silent_truncation(reports, monkeypatch):
    from linkedin_automation.interfaces import report_exports

    client, _, _ = reports
    monkeypatch.setattr(report_exports, "MAX_EXPORT_ROWS", 1)
    for dataset in ("leads", "observations"):
        response = client.get(f"/api/exports/{dataset}.csv")
        assert response.status_code == 413
        assert "Too many rows" in response.json()["detail"]
