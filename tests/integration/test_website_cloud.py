"""Cloud auth/collection boundaries without contacting LinkedIn or Unipile."""

import pytest
from fastapi.testclient import TestClient
from test_access_gateway import Google, Mailer, admin_login, google_login

from linkedin_automation.infrastructure.access_auth import AccessSettings
from linkedin_automation.infrastructure.unipile_client import UnipileClient, UnipileSettings
from linkedin_automation.interfaces.access_gateway import create_access_gateway
from linkedin_automation.interfaces.hosted_site import create_hosted_app

BASE = "https://www.bluqq.com"
P = "/linkedin-automation"


class Provider:
    settings = UnipileSettings(BASE, "https://api.unipile.com", "test-key", 2)

    def __init__(self):
        self.flows = []
        self.accounts = {}
        self.calls = []

    def create_scope(self, reference):
        return "scope-" + reference

    def hosted_link(self, state, account_id=None, *, scope_id=None):
        self.flows.append({"state": state, "scope": scope_id})
        return "https://auth.unipile.com/test"

    def account(self, identifier, *, scope_id=None):
        if self.accounts[identifier] != scope_id:
            raise ValueError("Wrong workspace")
        return {
            "id": identifier,
            "type": "LINKEDIN",
            "connection_params": {"im": {"id": "member-" + identifier}},
        }

    def search(self, account_id, url, *, cursor=None):
        self.calls.append(("search", account_id))
        return {
            "items": [
                {
                    "id": "person1",
                    "name": "Example Founder",
                    "profile_url": "https://www.linkedin.com/in/example-cloud/",
                    "headline": "Fund Manager - small fund",
                    "location": "London",
                }
            ]
        }

    def profile(self, account_id, identifier):
        self.calls.append(("profile", account_id))
        return {
            "provider_id": identifier,
            "name": "Example Founder",
            "profile_url": "https://www.linkedin.com/in/example-cloud/",
            "headline": "Fund Manager - small fund",
            "location": "London",
            "work_experience": [{"position": "Fund Manager", "company": "Example small fund"}],
        }


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBSITE_CLOUD_MODE", "1")
    monkeypatch.setenv("DASHBOARD_USERNAME", "owner")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "owner-password-for-testing-at-least-24")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path / "shared"))
    monkeypatch.delenv("UNIPILE_API_KEY", raising=False)
    monkeypatch.setattr(
        "linkedin_automation.infrastructure.cloud_search_runner.CloudSearchRunner.ensure",
        lambda self: None,
    )
    app = create_access_gateway(
        create_hosted_app(),
        root=tmp_path,
        settings=AccessSettings(
            BASE, frozenset({"first@gmail.com", "second@gmail.com"}), "id", "secret"
        ),
        google=Google(),
        mailer=Mailer(),
    )
    provider = Provider()
    app.gateway.cloud.client = provider
    return app, provider


def connect(client, provider, account):
    response = client.post(P + "/api/cloud/connect", json={})
    assert response.status_code == 200, response.text
    flow = provider.flows[-1]
    provider.accounts[account] = flow["scope"]
    callback = client.get(
        P + "/api/cloud/return",
        params={"flow": flow["state"], "account_id": account},
        follow_redirects=False,
    )
    assert callback.status_code == 303, callback.text
    return flow


def test_cloud_keeps_google_login_and_has_no_worker_setup(cloud):
    app, provider = cloud
    with TestClient(app, base_url=BASE) as client:
        assert client.get(P + "/api/cloud/account").status_code == 401
        assert "Continue with Google" in client.get(P + "/login").text
        google_login(client, "first@gmail.com")
        page = client.get(P + "/").text
        assert "LinkedIn cloud connection" in page
        assert 'id="startWorker"' not in page and 'id="workerSetup"' not in page
        assert (
            client.post(
                P + "/api/cloud/connect", json={}, headers={"Origin": "https://other.test"}
            ).status_code
            == 403
        )
        settings = client.get(P + "/api/search/settings").json()
        assert settings["mode"] == "cloud" and settings["device_required"] is False
        campaign = settings["campaigns"][0]["id"]
        assert (
            client.post(
                P + "/api/search/jobs",
                json={"campaign": campaign, "max_results": 1, "max_pages": 1},
            ).status_code
            == 409
        )
        flow = connect(client, provider, "acc_one")
        assert client.get(P + "/api/cloud/account").json()["connected"]
        assert (
            client.get(
                P + "/api/cloud/return", params={"flow": flow["state"], "account_id": "acc_one"}
            ).status_code
            == 400
        )


def test_cloud_callback_cannot_attach_another_user_account(cloud):
    app, provider = cloud
    with TestClient(app, base_url=BASE) as first, TestClient(app, base_url=BASE) as second:
        google_login(first, "first@gmail.com")
        google_login(second, "second@gmail.com")
        connect(first, provider, "acc_one")
        second.post(P + "/api/cloud/connect", json={})
        flow = provider.flows[-1]
        assert (
            second.get(
                P + "/api/cloud/return", params={"flow": flow["state"], "account_id": "acc_one"}
            ).status_code
            == 400
        )
        assert not second.get(P + "/api/cloud/account").json()["connected"]
        assert first.get(P + "/api/cloud/account").json()["connected"]
        assert (
            first.get(
                P + "/api/cloud/return", params={"flow": flow["state"], "account_id": "acc_one"}
            ).status_code
            == 400
        )


def test_cloud_search_uses_only_connected_account_and_saves_research(cloud):
    app, provider = cloud
    with TestClient(app, base_url=BASE) as first, TestClient(app, base_url=BASE) as second:
        google_login(first, "first@gmail.com")
        google_login(second, "second@gmail.com")
        connect(first, provider, "acc_one")
        connect(second, provider, "acc_two")
        options = first.get(P + "/api/search/settings").json()["campaigns"]
        campaign = next(x["id"] for x in options if "small-funds" in x["name"])
        job = first.post(
            P + "/api/search/jobs", json={"campaign": campaign, "max_results": 1, "max_pages": 1}
        )
        assert job.status_code == 200, job.text
        record = app.gateway.cloud.record(
            app.gateway.store.user(first.cookies.get(app.gateway.cookie_name))
        )
        app.gateway.cloud.runners[record["id"]].cycle()
        state = first.get(P + "/api/search/settings").json()
        assert state["jobs"][0]["status"] == "completed", state
        assert state["jobs"][0]["result"]["observed"] == 1
        assert ("search", "acc_one") in provider.calls and ("profile", "acc_one") in provider.calls
        assert not any(x[1] == "acc_two" for x in provider.calls)
        assert first.get(P + "/api/leads").json()
        assert second.get(P + "/api/leads").json() == []
        assert first.post(P + "/api/outreach/claim", json={}).status_code in (404, 503)
        assert first.post(P + "/api/search/claim", json={}).status_code == 404


def test_cloud_admin_connection_uses_shared_research_but_not_private_accounts(cloud):
    app, provider = cloud
    with TestClient(app, base_url=BASE) as admin, TestClient(app, base_url=BASE) as member:
        admin_login(admin)
        google_login(member, "first@gmail.com")
        connect(admin, provider, "acc_admin")
        assert not member.get(P + "/api/cloud/account").json()["connected"]
        page = admin.get(P + "/").text
        assert "LinkedIn cloud connection" in page and 'id="startWorker"' not in page
        assert admin.get(P + "/api/admin/overview").status_code == 200


def test_cloud_missing_key_fails_closed_instead_of_using_desktop_worker(cloud):
    app, _provider = cloud
    app.gateway.cloud.client = None
    with TestClient(app, base_url=BASE) as client:
        google_login(client, "first@gmail.com")
        assert not client.get(P + "/api/cloud/account").json()["configured"]
        assert client.post(P + "/api/cloud/connect", json={}).status_code == 503
        settings = client.get(P + "/api/search/settings").json()
        assert settings["mode"] == "cloud" and settings["cloud_connected"] is False
        assert (
            client.post(
                P + "/api/search/jobs", json={"campaign": settings["campaigns"][0]["id"]}
            ).status_code
            == 503
        )


def test_v2_transport_verifies_scope_and_does_not_require_linkedin_oauth(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "a-test-only-api-key")
    monkeypatch.setenv("UNIPILE_API_VERSION", "2")
    monkeypatch.setenv("UNIPILE_API_URL", "https://api.unipile.com")
    monkeypatch.delenv("LINKEDIN_CLIENT_ID", raising=False)
    settings = UnipileSettings.environment(BASE)
    assert settings.version == 2 and "a-test-only-api-key" not in repr(settings)
    client = UnipileClient(settings)
    monkeypatch.setattr(
        client,
        "request",
        lambda *args: {
            "id": "acc_one",
            "user_id": "member1",
            "provider": "linkedin",
            "status": "running",
            "account_scope_id": "scope_one",
        },
    )
    assert client.account("acc_one", scope_id="scope_one")["type"] == "LINKEDIN"
    with pytest.raises(ValueError):
        client.account("acc_one", scope_id="scope_two")


def test_cloud_quota_failure_is_not_retried_or_recorded_as_success(cloud):
    from linkedin_automation.infrastructure.unipile_client import UnipileError

    app, provider = cloud
    with TestClient(app, base_url=BASE) as client:
        google_login(client, "first@gmail.com")
        connect(client, provider, "acc_one")
        campaign = client.get(P + "/api/search/settings").json()["campaigns"][0]["id"]
        count = []

        def exhausted(*args, **kwargs):
            count.append(1)
            raise UnipileError("LinkedIn search allowance is exhausted.", status=429)

        provider.search = exhausted
        assert (
            client.post(
                P + "/api/search/jobs",
                json={"campaign": campaign, "max_results": 1, "max_pages": 1},
            ).status_code
            == 200
        )
        record = app.gateway.cloud.record(
            app.gateway.store.user(client.cookies.get(app.gateway.cookie_name))
        )
        runner = app.gateway.cloud.runners[record["id"]]
        runner.cycle()
        runner.cycle()
        result = client.get(P + "/api/search/settings").json()["jobs"][0]
        assert result["status"] == "failed" and "allowance" in result["result"]["message"]
        assert count == [1]
        assert client.get(P + "/api/leads").json() == []


def test_cloud_empty_search_completes_without_fake_profiles_or_deliveries(cloud):
    app, provider = cloud
    provider.search = lambda *args, **kwargs: {"items": []}
    with TestClient(app, base_url=BASE) as client:
        google_login(client, "first@gmail.com")
        connect(client, provider, "acc_one")
        campaign = client.get(P + "/api/search/settings").json()["campaigns"][0]["id"]
        assert (
            client.post(
                P + "/api/search/jobs",
                json={"campaign": campaign, "max_results": 1, "max_pages": 1},
            ).status_code
            == 200
        )
        owner = app.gateway.store.user(client.cookies.get(app.gateway.cookie_name))
        record = app.gateway.cloud.record(owner)
        app.gateway.cloud.runners[record["id"]].cycle()
        job = client.get(P + "/api/search/settings").json()["jobs"][0]
        assert job["status"] == "completed" and job["result"]["observed"] == 0
        assert "no profiles" in job["result"]["message"]
        assert client.get(P + "/api/leads").json() == []
        assert client.get(P + "/api/outreach/jobs").json()["jobs"] == []
        runs = client.get(P + "/api/research/runs").json()
        assert runs[0]["status"] == "completed" and runs[0]["read"] == 0


def test_disconnected_preflight_updates_status_and_does_not_queue_search(cloud):
    from linkedin_automation.infrastructure.unipile_client import UnipileAccountUnavailable

    app, provider = cloud
    with TestClient(app, base_url=BASE) as client:
        google_login(client, "first@gmail.com")
        connect(client, provider, "acc_one")
        campaign = client.get(P + "/api/search/settings").json()["campaigns"][0]["id"]

        def disconnected(*args, **kwargs):
            raise UnipileAccountUnavailable("LinkedIn needs reconnection or verification.")

        provider.account = disconnected
        response = client.post(
            P + "/api/search/jobs", json={"campaign": campaign, "max_results": 1, "max_pages": 1}
        )
        assert response.status_code == 409
        assert not client.get(P + "/api/cloud/account").json()["connected"]
        assert client.get(P + "/api/search/settings").json()["jobs"] == []


def test_refresh_outage_does_not_destroy_working_binding(cloud):
    from linkedin_automation.infrastructure.unipile_client import UnipileError

    app, provider = cloud
    with TestClient(app, base_url=BASE) as client:
        google_login(client, "first@gmail.com")
        connect(client, provider, "acc_one")
        owner = app.gateway.store.user(client.cookies.get(app.gateway.cookie_name))
        original = app.gateway.cloud.record(owner)

        def unavailable(*args, **kwargs):
            raise UnipileError("Provider temporarily unavailable", status=503)

        provider.account = unavailable
        assert client.post(P + "/api/cloud/refresh", json={}).status_code == 400
        current = app.gateway.cloud.record(owner)
        assert current["status"] == "connected"
        assert current["member_id"] == original["member_id"]
        assert current["account_id"] == original["account_id"]


def test_refresh_rejects_sender_identity_drift(cloud):
    app, provider = cloud
    with TestClient(app, base_url=BASE) as client:
        google_login(client, "first@gmail.com")
        connect(client, provider, "acc_one")
        owner = app.gateway.store.user(client.cookies.get(app.gateway.cookie_name))
        original = app.gateway.cloud.record(owner)
        provider.account = lambda *args, **kwargs: {
            "id": "acc_one",
            "connection_params": {"im": {"id": "different-member"}},
        }
        response = client.post(P + "/api/cloud/refresh", json={})
        assert response.status_code == 200 and not response.json()["connected"]
        assert app.gateway.cloud.record(owner)["member_id"] == original["member_id"]


def test_blocked_approved_user_cannot_continue_cloud_research(cloud):
    app, provider = cloud
    with TestClient(app, base_url=BASE) as client, TestClient(app, base_url=BASE) as admin:
        google_login(client, "first@gmail.com")
        connect(client, provider, "acc_one")
        record = app.gateway.cloud.record(
            app.gateway.store.user(client.cookies.get(app.gateway.cookie_name))
        )
        admin_login(admin)
        assert (
            admin.post(
                P + "/api/admin/access", json={"email": "first@gmail.com", "enabled": False}
            ).status_code
            == 200
        )
        assert client.get(P + "/api/cloud/account").status_code == 401
        app.gateway.cloud.runners[record["id"]].cycle()
        assert provider.calls == []


def test_v1_callback_requires_secret_and_binds_only_its_issued_flow(cloud):
    from dataclasses import replace

    app, provider = cloud
    provider.settings = replace(
        provider.settings, version=1, callback_secret="private-callback-test-secret-long-enough"
    )
    with TestClient(app, base_url=BASE) as client, TestClient(app, base_url=BASE) as server:
        google_login(client, "first@gmail.com")
        assert client.post(P + "/api/cloud/connect", json={}).status_code == 200
        flow = provider.flows[-1]
        provider.accounts["v1-account"] = None
        payload = {"status": "CREATION_SUCCESS", "name": flow["state"], "account_id": "v1-account"}
        assert server.post(P + "/api/cloud/notify", json=payload).status_code == 403
        result = server.post(
            P + "/api/cloud/notify?secret=" + provider.settings.callback_secret, json=payload
        )
        assert result.status_code == 200, result.text
        assert client.get(P + "/api/cloud/account").json()["connected"]
        assert (
            server.post(
                P + "/api/cloud/notify?secret=" + provider.settings.callback_secret,
                json={**payload, "name": "z" * 64},
            ).status_code
            == 400
        )


def test_cloud_outreach_authorization_binds_only_server_account(cloud, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    from test_qualification_review import seed

    app, provider = cloud
    monkeypatch.setenv("AUTOMATIC_QUALIFICATION", "1")
    with TestClient(app, base_url=BASE) as client:
        google_login(client, "first@gmail.com")
        connect(client, provider, "acc_one")
        owner = app.gateway.store.user(client.cookies.get(app.gateway.cookie_name))
        tenant, _ = app.gateway.tenant(owner)
        root = app.gateway.store.workspace(owner["id"])
        lead, _, _ = seed(
            root / "research.sqlite3",
            template="bluqq-london-independent-traders.yaml",
            complete=True,
        )
        queue = tenant.state.delivery_queue
        record = queue.repository.get_outreach(lead.id)
        queue.repository.upsert_outreach(
            replace(record, connection_message="Hi Alex, may we connect?")
        )
        assert client.post(P + "/api/outreach/prepare", json={}).status_code == 200
        job = client.get(P + "/api/outreach/jobs").json()["jobs"][0]
        endpoint = P + "/api/outreach/jobs/" + job["id"] + "/authorize"
        payload = {"confirmed": True, "draft_hash": job["draft_hash"]}
        assert client.post(endpoint, json=payload).status_code == 503
        monkeypatch.setenv("CLOUD_OUTREACH_ENABLED", "1")
        provider.settings = SimpleNamespace(version=1)
        assert (
            client.post(endpoint, json={**payload, "account_id": "someone_else"}).status_code == 422
        )
        assert client.post(endpoint, json=payload).status_code == 200
        account = app.gateway.cloud.record(owner)
        claimed = queue.claim(cloud_account=account)
        assert claimed["cloud_account_id"] == "acc_one"
        assert claimed["cloud_member_id"] == "member-acc_one"
        assert client.post(P + "/api/outreach/claim", json={}).status_code == 404
        assert provider.calls == []


def test_known_profile_lookup_is_scoped_to_controlled_campaign_and_exact_url(cloud):
    app, provider = cloud
    with TestClient(app, base_url=BASE) as client:
        google_login(client, "first@gmail.com")
        connect(client, provider, "acc_one")
        options = client.get(P + "/api/search/settings").json()["campaigns"]
        ordinary = next(x["id"] for x in options if not x["controlled_test"])
        values = {
            "campaign": ordinary,
            "max_results": 1,
            "max_pages": 1,
            "profile_url": "https://www.linkedin.com/in/example-cloud/",
        }
        assert client.post(P + "/api/search/jobs", json=values).status_code == 409
        spec = client.get(P + "/api/campaigns/zz-controlled-queue-test.yaml").json()["spec"]
        spec.update(
            name="TEST ONLY - Fixture",
            companies=["Example small fund"],
            required_dimensions=["companies"],
        )
        campaign = client.post(P + "/api/campaigns", json={"spec": spec}).json()["id"]
        assert (
            client.post(
                P + "/api/search/jobs",
                json={
                    **values,
                    "campaign": campaign,
                    "profile_url": "https://other.test/in/example/",
                },
            ).status_code
            == 409
        )
        assert (
            client.post(P + "/api/search/jobs", json={**values, "campaign": campaign}).status_code
            == 200
        )
        owner = app.gateway.store.user(client.cookies.get(app.gateway.cookie_name))
        record = app.gateway.cloud.record(owner)
        app.gateway.cloud.runners[record["id"]].cycle()
        job = client.get(P + "/api/search/settings").json()["jobs"][0]
        assert job["status"] == "completed" and job["result"]["observed"] == 1
        assert not any(c[0] == "search" for c in provider.calls)
        assert ("profile", "acc_one") in provider.calls
        assert client.get(P + "/api/leads").json()[0]["outreach"]["status"] == "needs_verification"
        assert client.get(P + "/api/outreach/jobs").json()["jobs"] == []
