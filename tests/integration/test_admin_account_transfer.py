"""Owner-approved admin transfer preserves research and cannot carry send approvals."""

import json

import pytest
from fastapi.testclient import TestClient
from test_access_gateway import admin_login
from test_website_account_change import initial
from test_website_account_change import v1 as v1
from test_website_cloud import BASE, P
from test_website_cloud import cloud as cloud

from linkedin_automation.infrastructure.admin_account_transfer import AdminAccountTransfer

PAYLOAD = {
    "confirmed": True,
    "source_email": "first@gmail.com",
    "profile_url": "https://www.linkedin.com/in/old-profile/",
}


@pytest.fixture
def transfer(v1):
    app, provider = v1
    private = TestClient(app, base_url=BASE)
    admin = TestClient(app, base_url=BASE)
    initial(private, provider)
    private.get(P + "/api/leads")
    admin_login(admin)
    admin.get(P + "/api/leads")
    source_owner = app.gateway.store.user(private.cookies.get(app.gateway.cookie_name))
    source = app.gateway.cloud.record(source_owner)
    destination = app.gateway.cloud.record(
        {"id": "admin:" + app.gateway.admin_binding(), "name": "Administrator"}
    )
    original_account = provider.account

    def account(identifier, **kwargs):
        if identifier == "refreshed-account":
            return {
                "id": identifier,
                "type": "LINKEDIN",
                "sources": [{"status": "OK"}],
                "connection_params": {
                    "im": {"id": "member-old-profile", "publicIdentifier": "old-profile"}
                },
            }
        return original_account(identifier, **kwargs)

    provider.account = account
    old = account("old-profile")
    old.update(sources=[{"status": "CREDENTIALS"}], created_at="2026-01-01")
    fresh = account("refreshed-account")
    fresh["created_at"] = "2026-01-02"
    provider.request = lambda *args: {"items": [old, fresh]}
    root = app.gateway.store.workspace(source_owner["id"])
    marker = root / "research-preservation.txt"
    marker.write_text("Keep this user's existing research")
    queue = app.gateway.tenant(source_owner)[0].state.delivery_queue
    with queue.db() as db:
        db.execute(
            "INSERT INTO delivery_jobs(id,lead_id,profile_url,action,payload,status,"
            "authorized,created,due) "
            "VALUES ('pending','existing','https://www.linkedin.com/in/contact/','message',?,'queued',1,0,0)",
            (
                json.dumps(
                    {
                        "draft": "Existing draft",
                        "follow_up_authorized": True,
                        "cloud_account_id": "old-profile",
                        "cloud_member_id": "member-old-profile",
                    }
                ),
            ),
        )
    yield app, provider, private, admin, source, destination, queue, marker
    private.close()
    admin.close()


def test_transfer_is_exact_verified_and_preserves_history_without_send(transfer):
    app, provider, private, admin, source, destination, queue, marker = transfer
    cloud = app.gateway.cloud
    stale = cloud.accounts.flow("connection", source["id"])
    before = private.get(P + "/api/leads").json()
    preview = admin.post(P + "/api/cloud/admin-transfer/preview", json=PAYLOAD)
    assert preview.status_code == 200, preview.text
    assert cloud.connection(source["id"])["account_id"] == "old-profile"
    assert not preview.json()["old_delivery_approvals_transferred"]
    result = admin.post(P + "/api/cloud/admin-transfer", json=PAYLOAD)
    assert result.status_code == 200, result.text
    assert result.json()["pending_approvals_revoked"] == 1
    assert cloud.connection(source["id"])["account_id"] is None
    assert cloud.connection(source["id"])["member_id"] is None
    target = cloud.connection(destination["id"])
    assert target["account_id"] == "refreshed-account" and target["status"] == "connected"
    assert target["member_id"] == "member-old-profile"
    assert private.get(P + "/api/leads").json() == before
    assert marker.read_text() == "Keep this user's existing research"
    with queue.db() as db:
        row = db.execute("SELECT * FROM delivery_jobs WHERE id='pending'").fetchone()
        payload = json.loads(row["payload"])
    assert row["status"] == "queued" and not row["authorized"]
    assert payload["draft"] == "Existing draft" and not payload["follow_up_authorized"]
    assert payload["cloud_account_id"] == "old-profile"
    assert queue.claim(cloud_account=target) is None
    with pytest.raises(ValueError, match="already used"):
        cloud.accounts.connection(stale, provider.account("old-profile"))
    assert admin.get(P + "/api/cloud/account").json()["connected"]
    assert not private.get(P + "/api/cloud/account").json()["connected"]
    # Replaying the operation cannot consume newly granted destination permissions.
    assert admin.post(P + "/api/cloud/admin-transfer", json=PAYLOAD).status_code == 400


@pytest.mark.parametrize("status", ["running", "uncertain"])
def test_busy_delivery_rolls_back_every_binding_and_approval(transfer, status):
    app, _provider, _private, admin, source, destination, queue, _marker = transfer
    with queue.db() as db:
        db.execute("UPDATE delivery_jobs SET status=? WHERE id='pending'", (status,))
    result = admin.post(P + "/api/cloud/admin-transfer", json=PAYLOAD)
    assert result.status_code == 400
    assert app.gateway.cloud.connection(source["id"])["account_id"] == "old-profile"
    assert app.gateway.cloud.connection(destination["id"])["account_id"] is None
    with queue.db() as db:
        assert db.execute("SELECT authorized FROM delivery_jobs").fetchone()[0] == 1


def test_transfer_requires_admin_origin_confirmation_and_exact_source(transfer):
    app, _provider, private, admin, source, _destination, _queue, _marker = transfer
    path = P + "/api/cloud/admin-transfer"
    assert private.post(path, json=PAYLOAD).status_code == 403
    assert (
        admin.post(path, json=PAYLOAD, headers={"Origin": "https://other.test"}).status_code == 403
    )
    for payload in (
        {**PAYLOAD, "confirmed": False},
        {**PAYLOAD, "profile_url": "https://www.linkedin.com/in/different/"},
        {**PAYLOAD, "source_email": "second@gmail.com"},
    ):
        assert admin.post(path, json=payload).status_code == 400
    assert app.gateway.cloud.connection(source["id"])["account_id"] == "old-profile"


def test_transfer_rechecks_binding_and_rolls_back_if_plan_is_stale(transfer):
    app, _provider, _private, _admin, source, destination, queue, _marker = transfer
    cloud = app.gateway.cloud
    owner = {"id": "admin:" + app.gateway.admin_binding(), "name": "Administrator"}
    service = AdminAccountTransfer(cloud)
    plan = service.plan(owner, PAYLOAD["source_email"], PAYLOAD["profile_url"])
    with cloud.accounts.db() as db:
        db.execute("UPDATE cloud_users SET account_id='changed' WHERE id=?", (source["id"],))
    with pytest.raises(ValueError, match="binding changed"):
        service.commit(plan)
    assert cloud.connection(destination["id"])["account_id"] is None
    with queue.db() as db:
        assert db.execute("SELECT authorized FROM delivery_jobs").fetchone()[0] == 1


def test_busy_destination_rolls_back_source_queue_changes_too(transfer):
    app, _provider, _private, admin, source, destination, queue, _marker = transfer
    shared_queue = app.gateway.legacy.state.delivery_queue
    with shared_queue.db() as db:
        db.execute(
            "INSERT INTO delivery_jobs(id,lead_id,profile_url,action,payload,status,"
            "authorized,created,due) VALUES ('busy','test','test','message','{}','running',1,0,0)"
        )
    assert admin.post(P + "/api/cloud/admin-transfer", json=PAYLOAD).status_code == 400
    with queue.db() as db:
        row = db.execute(
            "SELECT authorized,payload FROM delivery_jobs WHERE id='pending'"
        ).fetchone()
    assert row["authorized"] == 1 and json.loads(row["payload"])["follow_up_authorized"]
    assert app.gateway.cloud.connection(source["id"])["account_id"] == "old-profile"
    assert app.gateway.cloud.connection(destination["id"])["account_id"] is None


def test_source_is_located_by_verified_member_without_guessing_a_private_email(transfer):
    app, _provider, _private, admin, source, _destination, _queue, _marker = transfer
    payload = {k: v for k, v in PAYLOAD.items() if k != "source_email"}
    preview = admin.post(P + "/api/cloud/admin-transfer/preview", json=payload)
    assert preview.status_code == 200
    assert preview.json()["source_email"] == "first@gmail.com"
    assert preview.json()["source_kind"] == "private_workspace"
    assert app.gateway.cloud.connection(source["id"])["account_id"] == "old-profile"


def test_unbound_verified_session_does_not_disconnect_an_unrelated_private_sender(transfer):
    app, _provider, _private, admin, source, destination, queue, marker = transfer
    with app.gateway.cloud.accounts.db() as db:
        db.execute(
            "UPDATE cloud_users SET account_id='unrelated-account',member_id='unrelated-member' "
            "WHERE id=?",
            (source["id"],),
        )
    payload = {k: v for k, v in PAYLOAD.items() if k != "source_email"}
    preview = admin.post(P + "/api/cloud/admin-transfer/preview", json=payload)
    assert (
        preview.status_code == 200 and preview.json()["source_kind"] == "unbound_provider_session"
    )
    assert admin.post(P + "/api/cloud/admin-transfer", json=payload).status_code == 200
    assert app.gateway.cloud.connection(source["id"])["account_id"] == "unrelated-account"
    assert app.gateway.cloud.connection(destination["id"])["account_id"] == "refreshed-account"
    assert marker.exists()
    with queue.db() as db:
        assert db.execute("SELECT authorized FROM delivery_jobs").fetchone()[0] == 1


def test_previous_admin_binding_uses_shared_queue_once_and_preserves_private_workspace(transfer):
    app, _provider, _private, admin, source, destination, queue, _marker = transfer
    cloud = app.gateway.cloud
    previous = cloud.accounts.identity("website:admin:previous-binding", "Previous administrator")
    with cloud.accounts.db() as db:
        db.execute(
            "UPDATE cloud_users SET account_id=NULL,member_id=NULL WHERE id=?", (source["id"],)
        )
        db.execute(
            "UPDATE cloud_users SET account_id='old-profile',"
            "member_id='member-old-profile' WHERE id=?",
            (previous["id"],),
        )
    stale = cloud.accounts.flow("connection", previous["id"])
    payload = {k: v for k, v in PAYLOAD.items() if k != "source_email"}
    preview = admin.post(P + "/api/cloud/admin-transfer/preview", json=payload)
    assert preview.status_code == 200
    assert preview.json()["source_kind"] == "previous_admin_workspace"
    result = admin.post(P + "/api/cloud/admin-transfer", json=payload)
    assert result.status_code == 200, result.text
    assert cloud.connection(previous["id"])["account_id"] is None
    assert cloud.connection(destination["id"])["account_id"] == "refreshed-account"
    with pytest.raises(ValueError, match="already used"):
        cloud.accounts.connection(
            stale,
            {
                "id": "old-profile",
                "type": "LINKEDIN",
                "connection_params": {"im": {"id": "member-old-profile"}},
            },
        )
    with queue.db() as db:
        assert db.execute("SELECT authorized FROM delivery_jobs").fetchone()[0] == 1
