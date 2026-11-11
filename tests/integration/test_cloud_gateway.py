"""Private-workspace authentication boundaries without third-party network calls."""

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.infrastructure.cloud_connector import CloudSettings
from linkedin_automation.interfaces.cloud_gateway import COOKIE, create_cloud_gateway
from linkedin_automation.interfaces.hosted_site import create_hosted_app

BASE = "https://www.bluqq.com"
PREFIX = "/linkedin-automation"
SETTINGS = CloudSettings(
    BASE,
    "https://api56.unipile.com:18643",
    "test-key",
    "callback-secret-for-test-only-32-characters",
    "client",
    "secret",
)


class Identity:
    def login_url(self, state):
        return "https://www.linkedin.com/oauth/v2/authorization?state=" + state

    def identity(self, code):
        return {"sub": code, "name": "Member " + code}


class Connector:
    def __init__(self):
        self.states = []

    def hosted_link(self, state, account_id=None):
        self.states.append(state)
        return "https://account.unipile.com/test"

    def account(self, account_id):
        return {
            "type": "LINKEDIN",
            "id": account_id,
            "connection_params": {"im": {"id": "member-" + account_id}},
        }


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "owner")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "owner-password-for-testing-at-least-24")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path / "legacy"))
    connector = Connector()
    app = create_cloud_gateway(
        create_hosted_app(),
        root=tmp_path,
        settings=SETTINGS,
        connector=connector,
        identity=Identity(),
    )
    return app, connector


def login(client, subject):
    response = client.get(PREFIX + "/api/cloud/login", follow_redirects=False)
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    response = client.get(
        PREFIX + "/api/cloud/identity",
        params={"state": state, "code": subject},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    return state


def connect(client, connector, account_id):
    response = client.post(PREFIX + "/api/cloud/connect", json={})
    assert response.json()["url"] == "https://account.unipile.com/test"
    response = client.post(
        PREFIX + "/api/cloud/notify",
        params={"secret": SETTINGS.callback_secret},
        json={"status": "CREATION_SUCCESS", "name": connector.states[-1], "account_id": account_id},
    )
    assert response.status_code == 200


def test_anonymous_has_no_shared_data_or_connection_access(cloud):
    app, _ = cloud
    with TestClient(app, base_url=BASE) as client:
        assert "Continue with LinkedIn" in client.get(PREFIX + "/").text
        assert client.get(PREFIX + "/api/leads").status_code == 401
        assert client.post(PREFIX + "/api/cloud/connect", json={}).status_code == 401
        assert client.get(PREFIX + "/api/cloud/return?account_id=forged").status_code == 200
        assert client.get(PREFIX + "/api/cloud/account").status_code == 401


def test_oauth_state_must_match_cookie_and_is_single_use(cloud):
    app, _ = cloud
    with TestClient(app, base_url=BASE) as client:
        assert client.get(PREFIX + "/api/cloud/identity?state=forged&code=one").status_code == 409
        state = login(client, "one")
        assert (
            client.get(
                PREFIX + "/api/cloud/identity", params={"state": state, "code": "one"}
            ).status_code
            == 409
        )
        assert client.get(PREFIX + "/api/cloud/account").json() == {
            "connected": False,
            "name": "Member one",
        }
        client.post(PREFIX + "/api/cloud/logout", json={})
        assert client.get(PREFIX + "/api/cloud/account").status_code == 401


def test_two_users_are_isolated_and_legacy_remains_accessible_to_owner(cloud):
    app, connector = cloud
    with TestClient(app, base_url=BASE) as first, TestClient(app, base_url=BASE) as second:
        login(first, "one")
        connect(first, connector, "account-one")
        login(second, "two")
        connect(second, connector, "account-two")
        users = app.gateway.accounts.connected_users()
        assert len(users) == 2
        apps = [app.gateway.tenant(user["id"])[0] for user in users]
        assert apps[0].state.search_store.path != apps[1].state.search_store.path
        for client in (first, second):
            assert client.get(PREFIX + "/api/leads").json() == []
            assert client.get(PREFIX + "/api/search/settings").json()["mode"] == "cloud"
            assert client.post(PREFIX + "/api/import/research", json={}).status_code == 404
            assert client.post(PREFIX + "/api/outreach/claim", json={}).status_code == 404
            assert client.post(PREFIX + "/api/search/jobs", json={}).status_code == 503
        # Revoking one user never revokes the other's session.
        first.post(PREFIX + "/api/cloud/logout", json={})
        assert second.get(PREFIX + "/api/cloud/account").json()["connected"] is True
        assert (
            first.get(
                PREFIX + "/api/leads", auth=("owner", "owner-password-for-testing-at-least-24")
            ).status_code
            == 200
        )


def test_fake_callback_oversize_and_csrf_do_not_bind_account(cloud):
    app, connector = cloud
    with TestClient(app, base_url=BASE) as client:
        login(client, "one")
        assert (
            client.post(
                PREFIX + "/api/cloud/connect", json={}, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        client.post(PREFIX + "/api/cloud/connect", json={})
        payload = {"status": "CREATION_SUCCESS", "name": connector.states[-1], "account_id": "test"}
        assert (
            client.post(PREFIX + "/api/cloud/notify?secret=wrong", json=payload).status_code == 403
        )
        assert not client.get(PREFIX + "/api/cloud/account").json()["connected"]
        assert (
            client.post(
                PREFIX + "/api/cloud/notify",
                params={"secret": SETTINGS.callback_secret},
                content=b"x" * 65537,
            ).status_code
            == 413
        )
        assert (
            client.post(
                PREFIX + "/api/cloud/notify", params={"secret": SETTINGS.callback_secret}, json=[]
            ).status_code
            == 409
        )


def test_session_cookie_cannot_select_another_workspace(cloud):
    app, _ = cloud
    user = app.gateway.accounts.identity("one", "One")
    token = app.gateway.accounts.session(user["id"])
    with TestClient(app, base_url=BASE) as client:
        client.cookies.set(COOKIE, token + "tampered")
        assert client.get(PREFIX + "/api/cloud/account").status_code == 401
