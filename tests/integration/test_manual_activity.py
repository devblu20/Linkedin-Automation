"""Human records are scoped, ordered, atomic and idempotent; no external send."""

from uuid import uuid4

import pytest
from test_qualification_review import hosted_review as hosted_review
from test_qualification_review import payload


def activity(status, action="evidence_checked", **extra):
    return {
        "id": str(uuid4()),
        "action": action,
        "expected_status": status,
        "note": "Personally checked the stated source",
        "source_url": "https://example.com/proof",
        "confirmed": True,
        **extra,
    }


def test_manual_tracking_requires_approval_and_preserves_timeline(hosted_review):
    client, lead, record, _ = hosted_review
    url = f"/api/leads/{lead.id}/activities"
    assert (
        client.post(url, json=activity("needs_verification", "connection_sent")).status_code == 422
    )
    update = payload(
        record,
        status="approved",
        funds_gbp=1_000_000,
        fund_evidence_url="https://example.com/proof",
    )
    assert client.put(f"/api/leads/{lead.id}", json=update).status_code == 200
    request = activity("approved", "connection_sent")
    assert client.post(url, json=request).status_code == 200
    assert client.post(url, json=request).status_code == 200
    assert len(client.get(url).json()) == 1
    changed = {**request, "note": "Different activity note"}
    assert client.post(url, json=changed).status_code == 409
    assert client.post(url, json=activity("connection_sent", "message_sent")).status_code == 409
    assert client.post(url, json=activity("connection_sent", "connected")).status_code == 200
    assert client.post(url, json=activity("connected", "message_sent")).status_code == 200
    history = client.get(url).json()
    assert len(history) == 3
    assert all(x["recorded_manually"] for x in history)
    exported = client.get(url + "/export.csv")
    assert exported.status_code == 200
    assert exported.headers["X-Export-Row-Count"] == "3"
    assert "Personally checked" in exported.text
    assert history[0]["draft_snapshot"] == record.follow_up_message
    summary = client.get("/api/summary").json()
    assert summary["connection_sent"] == summary["connected"] == summary["message_sent"] == 1
    assert len(summary["connection_requests"]) == len(summary["messages"]) == 1
    assert summary["connection_requests"][0]["status"] == "accepted"
    update["status"] = "message_sent"
    update["connection_message"] = "Updated draft after recording"
    assert client.put(f"/api/leads/{lead.id}", json=update).status_code == 200
    assert client.get(url).json()[-1]["draft_snapshot"] == record.connection_message
    update["status"] = "approved"
    assert client.put(f"/api/leads/{lead.id}", json=update).status_code == 409


@pytest.mark.parametrize(
    "bad",
    [
        {"confirmed": False},
        {"source_url": "javascript:alert(1)"},
        {"source_url": ""},
        {"source_url": "https://user:password@example.com"},
        {"note": "  "},
        {"profile_updates": {"unexpected": "value"}},
    ],
)
def test_invalid_manual_evidence_changes_nothing(hosted_review, bad):
    client, lead, _, _ = hosted_review
    before = client.get("/api/leads").json()
    url = f"/api/leads/{lead.id}/activities"
    assert client.post(url, json=activity("needs_verification", **bad)).status_code == 422
    assert client.get(url).json() == []
    assert client.get("/api/leads").json() == before
    assert client.get(f"/api/leads/{uuid4()}/activities").status_code == 404


def test_source_backed_supplement_fills_only_missing_fields(hosted_review):
    client, lead, _record, _ = hosted_review
    url = f"/api/leads/{lead.id}/activities"
    before = client.get("/api/leads").json()[0]
    data = activity(
        "needs_verification",
        profile_updates={
            "industry": "Investment services",
            "experience": "Founder, Example Capital",
        },
    )
    assert client.post(url, json=data).status_code == 200
    assert client.post(url, json=data).status_code == 200
    after = client.get("/api/leads").json()[0]
    assert after["lead"]["industry"] == "Investment services"
    assert after["lead"]["company"] == before["lead"]["company"]
    assert after["outreach"]["status"] == "needs_verification"
    assert len(client.get(url).json()) == 1
    assert "reviewer_supplied" in client.get(url).json()[0]["draft_snapshot"]
    attempted = activity(
        "needs_verification", profile_updates={"company": "Overwrite original", "industry": "Other"}
    )
    assert client.post(url, json=attempted).status_code == 409
    assert client.get("/api/leads").json() == [after]
    assessment = client.get(f"/api/leads/{lead.id}/qualification").json()
    assert "Industry" not in assessment["missing_fields"]
    assert any(x["field"] == "Industry" and x["available"] for x in assessment["data_coverage"])
