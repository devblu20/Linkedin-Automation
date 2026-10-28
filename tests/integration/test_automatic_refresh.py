"""Automatic import refreshes unreviewed facts and preserves explicit human reviews."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_research_transfer import payload

from linkedin_automation.interfaces.hosted_site import create_hosted_app


@pytest.mark.parametrize("human_reviewed", [False, True])
def test_automatic_refresh_preserves_source_observations_and_human_edits(
    tmp_path, monkeypatch, human_reviewed
):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOMATIC_QUALIFICATION", "1")
    monkeypatch.setenv("AUTOMATIC_QUEUE", "0")
    with TestClient(create_hosted_app()) as client:
        client.auth = ("reviewer", "test-password-at-least-24-characters")
        original = payload()
        assert client.post("/api/import/research", json=original).status_code == 200
        old = client.get("/api/leads").json()[0]
        assert old["outreach"]["status"] == "needs_verification"
        if human_reviewed:
            fields = [
                "firm_type",
                "funds_gbp",
                "fund_evidence_url",
                "fund_evidence_notes",
                "connection_message",
                "follow_up_message",
                "status",
            ]
            values = {key: old["outreach"][key] for key in fields}
            assert client.put("/api/leads/" + old["lead"]["id"], json=values).status_code == 200
        fresh = payload()
        fresh["run_id"] = str(uuid4())
        fresh["profiles"][0].update(
            current_title="Independent Quant Trader", company="Example Trading"
        )
        assert client.post("/api/import/research", json=fresh).status_code == 200
        current = next(
            x for x in client.get("/api/leads").json() if x["lead"]["id"] == old["lead"]["id"]
        )
        if human_reviewed:
            assert current["lead"] == old["lead"]
            assert current["outreach"]["status"] == "needs_verification"
        else:
            assert current["lead"]["company"] == "Example Trading"
            assert current["lead"]["current_title"] == "Independent Quant Trader"
            assert current["outreach"]["status"] == "approved"
        observations = client.get(
            "/api/observations", params={"run_id": original["run_id"]}
        ).json()["items"]
        accepted = next(x for x in observations if x["decision"] == "accepted")
        assert not accepted["profile"]["company"]
        assert not accepted["profile"]["current_title"]
