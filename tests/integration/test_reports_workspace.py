"""Report preview/export parity, actual metrics, access scope and pagination."""

import io
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from test_report_exports import csv_rows
from test_report_exports import reports as reports

from linkedin_automation.interfaces.review_site import create_review_app


@pytest.mark.parametrize("dataset", ["leads", "observations"])
def test_preview_paginates_actual_rows_and_matches_all_export_formats(reports, dataset):
    client, runs, _ = reports
    filters = {"dataset": dataset, "run_id": str(runs[0].id), "limit": 10}
    if dataset == "leads":
        filters.update(q="José", status="needs_verification")
    else:
        filters["decision"] = "rejected"
    first = client.get("/api/reports/preview", params=filters).json()
    assert len(first["items"]) == 10 and first["has_more"]
    second = client.get("/api/reports/preview", params={**filters, "offset": 10}).json()
    assert second["total"] == first["total"]
    assert len(second["items"]) == first["total"] - 10
    assert not second["has_more"]
    assert "profile_snapshot" not in str(first)
    params = {k: v for k, v in filters.items() if k not in {"dataset", "limit"}}
    csv = client.get(f"/api/exports/{dataset}.csv", params=params)
    rows = csv_rows(csv)
    assert len(rows) == first["total"]
    assert {row["Run ID"] for row in rows} == {str(runs[0].id)}
    xlsx = client.get(f"/api/exports/{dataset}.xlsx", params=params)
    book = load_workbook(io.BytesIO(xlsx.content))
    assert book.worksheets[0].max_row == first["total"] + 1
    book.close()


def test_preview_empty_state_and_metrics_do_not_guess_outreach(reports):
    client, _, _ = reports
    empty = client.get("/api/reports/preview?status=approved").json()
    assert empty["items"] == [] and empty["total"] == 0 and not empty["has_more"]
    stats = client.get("/api/reports/analytics").json()
    assert stats["profiles_searched"] == 29
    history = client.get("/api/research/runs").json()
    assert sum(run["read"] for run in history) == stats["profiles_searched"]
    for key in (
        "connection_requests_sent",
        "connections_accepted",
        "messages_sent",
        "replies_received",
        "connection_acceptance_rate",
        "message_reply_rate",
    ):
        assert stats[key] == 0


def test_preview_decisions_and_research_scope_are_consistent(reports):
    client, runs, _ = reports
    for decision, expected in (("all", 28), ("accepted", 14), ("rejected", 14)):
        data = client.get(
            "/api/reports/preview",
            params={
                "dataset": "observations",
                "run_id": str(runs[0].id),
                "decision": decision,
                "limit": 100,
            },
        ).json()
        assert len(data["items"]) == data["total"] == expected
        if decision != "all":
            assert all(row["decision"] == decision for row in data["items"])
    assert (
        client.get("/api/reports/analytics", params={"run_id": str(runs[1].id)}).json()[
            "profiles_searched"
        ]
        == 1
    )


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "dataset=unknown",
        "dataset=observations&status=approved",
        "dataset=observations&q=London",
        "decision=rejected",
    ],
)
def test_report_filter_validation(reports, query):
    client, _, _ = reports
    assert client.get("/api/reports/preview?" + query).status_code == 422


def test_report_endpoints_are_authenticated_scoped_and_read_only(reports):
    client, runs, database = reports
    before = client.get("/api/leads").json()
    assert client.get("/api/reports/preview").status_code == 200
    assert client.get("/api/reports/analytics").status_code == 200
    assert client.get("/api/leads").json() == before
    for endpoint in ("preview", "analytics"):
        assert client.get(f"/api/reports/{endpoint}?run_id={uuid4()}").status_code == 404
        client.auth = None
        assert client.get(f"/api/reports/{endpoint}").status_code == 401
        client.auth = ("reviewer", "test-password-at-least-24-characters")
    with TestClient(create_review_app(database, runs[0].id)) as scoped:
        for endpoint in ("preview", "analytics"):
            assert scoped.get(f"/api/reports/{endpoint}?run_id={runs[1].id}").status_code == 404
        assert scoped.get("/api/reports/preview?dataset=observations").json()["total"] == 28
        assert scoped.get("/api/reports/analytics").json()["profiles_searched"] == 28
