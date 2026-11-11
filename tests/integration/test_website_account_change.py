"""Real account-change routes/callbacks with a fake provider; no external actions."""

import pytest
from fastapi.testclient import TestClient
from test_access_gateway import google_login
from test_cloud_delivery import cloud_delivery as cloud_delivery
from test_cloud_delivery import enable
from test_website_cloud import BASE, P
from test_website_cloud import cloud as cloud

from linkedin_automation.infrastructure.unipile_client import UnipileSettings


@pytest.fixture
def v1(cloud):
    app, provider = cloud
    provider.settings = UnipileSettings(
        BASE, "https://api70.unipile.com:20051", "test-key", 1, "s" * 32
    )
    provider.link_arguments = []
    original_link = provider.hosted_link

    def hosted(state, account_id=None, **kwargs):
        provider.link_arguments.append(account_id)
        return original_link(state, account_id, **kwargs)

    provider.hosted_link = hosted
    original_account = provider.account

    def account(identifier, **kwargs):
        result = original_account(identifier, **kwargs)
        result["connection_params"]["im"]["publicIdentifier"] = identifier
        return result

    provider.account = account
    return app, provider


def callback(client, provider, identifier):
    provider.accounts[identifier] = None
    return client.post(
        P + "/api/cloud/notify?secret=" + "s" * 32,
        json={
            "status": "CREATION_SUCCESS",
            "name": provider.flows[-1]["state"],
            "account_id": identifier,
        },
    )


def initial(client, provider):
    google_login(client, "first@gmail.com")
    assert client.post(P + "/api/cloud/connect", json={}).status_code == 200
    assert callback(client, provider, "old-profile").status_code == 200


def test_change_creates_fresh_auth_then_binds_only_verified_target(v1):
    app, provider = v1
    with TestClient(app, base_url=BASE) as client:
        assert client.post(P + "/api/cloud/change-account", json={}).status_code == 401
        initial(client, provider)
        data = {"confirmed": True, "profile_url": "https://www.linkedin.com/in/current-profile/"}
        before_leads = client.get(P + "/api/leads").json()
        assert client.get(P + "/api/cloud/account").json()["can_change_account"]
        assert (
            client.post(
                P + "/api/cloud/change-account", json={**data, "confirmed": False}
            ).status_code
            == 400
        )
        assert (
            client.post(
                P + "/api/cloud/change-account", json=data, headers={"Origin": "https://other.test"}
            ).status_code
            == 403
        )
        assert client.post(P + "/api/cloud/change-account", json=data).status_code == 200
        assert provider.link_arguments[-1] is None
        state = provider.flows[-1]["state"]
        owner = app.gateway.cloud.accounts.replacement_owner(state)
        assert app.gateway.cloud.connection(owner)["account_id"] == "old-profile"
        assert callback(client, provider, "wrong-profile").status_code == 400
        assert app.gateway.cloud.connection(owner)["account_id"] == "old-profile"
        assert callback(client, provider, "current-profile").status_code == 200
        assert app.gateway.cloud.connection(owner)["account_id"] == "current-profile"
        assert client.get(P + "/api/leads").json() == before_leads
        assert client.get(P + "/api/auth/me").json()["email"] == "first@gmail.com"


def test_revoked_owner_cannot_complete_change(v1):
    app, provider = v1
    with TestClient(app, base_url=BASE) as client:
        initial(client, provider)
        client.post(
            P + "/api/cloud/change-account",
            json={"confirmed": True, "profile_url": "https://www.linkedin.com/in/current-profile/"},
        )
        owner = app.gateway.cloud.accounts.replacement_owner(provider.flows[-1]["state"])
        app.gateway.operations.access("first@gmail.com", False, "test")
        assert callback(client, provider, "current-profile").status_code == 400
        assert app.gateway.cloud.connection(owner)["account_id"] == "old-profile"


def test_busy_delivery_prevents_change_callback(v1):
    app, provider = v1
    with TestClient(app, base_url=BASE) as client:
        initial(client, provider)
        client.get(P + "/api/cloud/account")
        assert (
            client.post(
                P + "/api/cloud/change-account",
                json={
                    "confirmed": True,
                    "profile_url": "https://www.linkedin.com/in/current-profile/",
                },
            ).status_code
            == 200
        )
        owner = app.gateway.cloud.accounts.replacement_owner(provider.flows[-1]["state"])
        queue = app.gateway.cloud.runners[owner].app.state.delivery_queue
        with queue.db() as db:
            db.execute(
                "INSERT INTO delivery_jobs(id,lead_id,profile_url,action,payload,"
                "status,created,due) "
                "VALUES ('busy','test','test','message','{}','running',0,0)"
            )
        assert callback(client, provider, "current-profile").status_code == 400
        assert app.gateway.cloud.connection(owner)["account_id"] == "old-profile"


def test_v2_switch_is_unavailable_without_creating_scope_or_login(cloud):
    app, provider = cloud
    with TestClient(app, base_url=BASE) as client:
        google_login(client, "first@gmail.com")
        assert (
            client.post(
                P + "/api/cloud/change-account",
                json={
                    "confirmed": True,
                    "profile_url": "https://www.linkedin.com/in/current-profile/",
                },
            ).status_code
            == 409
        )
        assert provider.flows == []
        with app.gateway.cloud.accounts.db() as db:
            assert db.execute("SELECT COUNT(*) FROM website_scopes").fetchone()[0] == 0


def test_account_change_does_not_reauthorize_old_queue(cloud_delivery):
    queue, _, _, sender, _ = cloud_delivery
    enable(queue, sender)
    before = queue.list()
    assert (
        queue.claim(cloud_account={"account_id": "new-account", "member_id": "new-member"}) is None
    )
    assert queue.list() == before
