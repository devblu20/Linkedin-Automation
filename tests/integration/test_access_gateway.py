"""Email allowlists, verified signup and Google sessions with no external requests."""

from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.infrastructure.access_auth import PREFIX, AccessSettings, digest
from linkedin_automation.interfaces.access_gateway import create_access_gateway
from linkedin_automation.interfaces.hosted_site import create_hosted_app

ORIGIN = "https://www.bluqq.com"
ALLOWED = frozenset({"first@gmail.com", "second@gmail.com"})
PASSWORD = "a-private-password-for-testing"


class Mailer:
    ready = True

    def __init__(self):
        self.messages = []

    def send(self, email, code):
        self.messages.append((email, code))


class Google:
    def url(self, state, verifier, nonce):
        return "https://accounts.google.com/o/oauth2/v2/auth?state=" + state

    def identity(self, code, flow):
        return {"email": code, "sub": "google-" + code, "email_verified": True, "name": "Member"}


@pytest.fixture
def auth(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_DEVICE_PAIRING", "0")
    monkeypatch.setenv("DASHBOARD_USERNAME", "owner")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "owner-password-for-testing-at-least-24")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path / "legacy"))
    settings = AccessSettings(ORIGIN, ALLOWED, "test-client", "test-secret", signup_mode="email")
    mailer = Mailer()
    app = create_access_gateway(
        create_hosted_app(), root=tmp_path, settings=settings, mailer=mailer, google=Google()
    )
    return app, mailer


def signup(client, mailer, email="first@gmail.com"):
    response = client.post(
        PREFIX + "/api/auth/signup",
        json={"name": "First member", "email": email, "password": PASSWORD},
    )
    assert response.status_code == 200
    code = mailer.messages[-1][1]
    assert code not in response.text
    response = client.post(PREFIX + "/api/auth/verify", json={"email": email, "code": code})
    assert response.status_code == 200
    return code


def google_login(client, email):
    response = client.get(PREFIX + "/api/auth/google", follow_redirects=False)
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    return client.get(
        PREFIX + "/api/auth/google/callback",
        params={"state": state, "code": email},
        follow_redirects=False,
    ), state


def test_admin_username_form_session_logout_and_private_workspace_boundary(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as admin, TestClient(app, base_url=ORIGIN) as member:
        assert 'href="/linkedin-automation/admin"' in admin.get(PREFIX + "/login").text
        page = admin.get(PREFIX + "/admin").text
        assert 'name="username" type="text"' in page
        assert 'name="email" type="email"' not in page
        assert "Continue with Google" not in page
        assert "const page='admin/login'" in page
        login = PREFIX + "/api/auth/admin/login"
        credentials = {"username": "owner", "password": "owner-password-for-testing-at-least-24"}
        assert admin.post(login, json={**credentials, "password": "incorrect"}).status_code == 401
        assert (
            admin.post(
                login, json=credentials, headers={"Origin": "https://outside.example"}
            ).status_code
            == 403
        )
        response = admin.post(login, json=credentials)
        assert response.status_code == 200
        assert credentials["password"] not in response.text + str(response.headers)
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
        assert admin.get(PREFIX + "/api/auth/me").json()["role"] == "admin"
        assert "Administrator · Shared workspace" in admin.get(PREFIX + "/").text
        for path in ("/api/leads", "/api/campaign-templates", "/api/outreach/jobs"):
            assert admin.get(PREFIX + path).status_code == 200
        assert admin.post(PREFIX + "/api/search/claim", json={}).status_code == 403
        assert admin.get(PREFIX + "/api/auth/devices/current").json()["paired"] is False
        assert admin.get(PREFIX + "/api/search/settings").json()["device_required"] is False
        google_login(member, "first@gmail.com")
        assert "Private workspace" in member.get(PREFIX + "/").text
        assert member.get(PREFIX + "/api/auth/me").json().get("role") != "admin"
        assert admin.post(PREFIX + "/api/auth/logout", json={}).status_code == 200
        assert admin.get(PREFIX + "/api/leads").status_code == 401


def test_admin_sessions_expire_rotate_and_do_not_override_google_login(auth):
    import time

    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as client:
        login = PREFIX + "/api/auth/admin/login"
        credentials = {"username": "owner", "password": "owner-password-for-testing-at-least-24"}
        assert client.post(login, json=credentials).status_code == 200
        with app.gateway.store.db() as db:
            db.execute("UPDATE admin_sessions SET expires=?", (time.time() - 1,))
        assert client.get(PREFIX + "/api/leads").status_code == 401
        assert client.post(login, json=credentials).status_code == 200
        original = app.gateway.owner_credentials
        app.gateway.owner_credentials = ("owner", "changed-admin-password-at-least-24")
        assert client.get(PREFIX + "/api/leads").status_code == 401
        app.gateway.owner_credentials = original
        google_login(client, "first@gmail.com")
        assert client.get(PREFIX + "/api/auth/me").json()["email"] == "first@gmail.com"
        assert "Private workspace" in client.get(PREFIX + "/").text


def test_admin_login_is_rate_limited_and_regular_email_cannot_be_admin(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as client:
        google_login(client, "first@gmail.com")
        for _ in range(10):
            assert (
                client.post(
                    PREFIX + "/api/auth/admin/login",
                    json={"username": "first@gmail.com", "password": PASSWORD},
                ).status_code
                == 401
            )
        assert (
            client.post(
                PREFIX + "/api/auth/admin/login",
                json={"username": "owner", "password": "owner-password-for-testing-at-least-24"},
            ).status_code
            == 429
        )


def pair_desktop(client, token, label):
    value = client.post(
        PREFIX + "/api/auth/devices/begin", json={"token_hash": digest(token), "label": label}
    ).json()
    response = client.post(PREFIX + "/api/auth/devices/approve", json={"code": value["code"]})
    assert response.status_code == 200
    paired = client.post(
        PREFIX + "/api/auth/devices/poll", json={"id": value["id"], "token": token}
    ).json()
    return value, (paired["username"], token)


def test_personal_browser_uses_owner_queue_without_deleting_pairings(auth, monkeypatch):
    app, _ = auth
    monkeypatch.setenv("PERSONAL_BROWSER_MODE", "1")
    with TestClient(app, base_url=ORIGIN) as admin:
        admin_login(admin)
        device, _ = pair_desktop(admin, "p" * 64, "Previous admin PC")
        settings = admin.get(PREFIX + "/api/search/settings").json()
        assert not settings["device_required"] and not settings["device_paired"]
        job = admin.post(
            PREFIX + "/api/search/jobs",
            json={"campaign": settings["campaigns"][0]["id"], "max_results": 1, "max_pages": 1},
        ).json()
        with TestClient(app, base_url=ORIGIN) as worker:
            claimed = worker.post(
                PREFIX + "/api/search/claim",
                auth=("owner", "owner-password-for-testing-at-least-24"),
                json={},
            ).json()["job"]
        assert claimed["id"] == job["id"]
        assert (
            app.gateway.devices.configured("admin:" + app.gateway.admin_binding())["id"]
            == device["id"]
        )


def admin_login(client):
    response = client.post(
        PREFIX + "/api/auth/admin/login",
        json={"username": "owner", "password": "owner-password-for-testing-at-least-24"},
    )
    assert response.status_code == 200


def test_admin_pairs_own_computer_and_worker_cannot_access_private_or_admin_apis(auth):
    app, _ = auth
    app.gateway.device_mode = True
    with TestClient(app, base_url=ORIGIN) as admin, TestClient(app, base_url=ORIGIN) as member:
        admin_login(admin)
        settings = admin.get(PREFIX + "/api/search/settings").json()
        payload = {"campaign": settings["campaigns"][0]["id"], "max_results": 1, "max_pages": 1}
        assert admin.post(PREFIX + "/api/search/jobs", json=payload).status_code == 409
        device, worker = pair_desktop(admin, "m" * 64, "ADMIN PC")
        assert (
            admin.get(PREFIX + "/api/auth/devices/current").json()["device"]["id"] == device["id"]
        )
        assert admin.get(PREFIX + "/api/search/settings").json()["device_paired"] is True
        job = admin.post(PREFIX + "/api/search/jobs", json=payload).json()
        google_login(member, "first@gmail.com")
        _, private_worker = pair_desktop(member, "n" * 64, "PRIVATE PC")
        assert (
            member.post(PREFIX + "/api/search/claim", auth=private_worker, json={}).json()["job"]
            is None
        )
        claimed = admin.post(PREFIX + "/api/search/claim", auth=worker, json={}).json()["job"]
        assert claimed["id"] == job["id"]
        assert admin.get(PREFIX + "/api/leads", auth=worker).status_code == 403
        assert admin.get(PREFIX + "/api/admin/overview", auth=worker).status_code == 403
        assert admin.post(PREFIX + "/api/outreach/claim", auth=worker, json={}).status_code == 403
        complete = {
            "status": "completed",
            "token": claimed["token"],
            "observed": 0,
            "accepted": 0,
            "rejected": 0,
        }
        assert (
            admin.post(
                PREFIX + "/api/search/jobs/" + job["id"] + "/complete", auth=worker, json=complete
            ).status_code
            == 200
        )
        assert any(
            d.get("admin_workspace")
            for d in admin.get(PREFIX + "/api/admin/overview").json()["devices"]
        )


def test_admin_pairing_is_bound_to_computer_and_current_owner_credentials(auth):
    app, _ = auth
    app.gateway.device_mode = True
    with TestClient(app, base_url=ORIGIN) as one, TestClient(app, base_url=ORIGIN) as two:
        admin_login(one)
        admin_login(two)
        _, worker_one = pair_desktop(one, "p" * 64, "ADMIN ONE")
        _, worker_two = pair_desktop(two, "q" * 64, "ADMIN TWO")
        campaign = one.get(PREFIX + "/api/search/settings").json()["campaigns"][0]["id"]
        job = one.post(
            PREFIX + "/api/search/jobs",
            json={"campaign": campaign, "max_results": 1, "max_pages": 1},
        ).json()
        assert (
            two.post(PREFIX + "/api/search/claim", auth=worker_two, json={}).json()["job"] is None
        )
        assert (
            one.post(PREFIX + "/api/search/claim", auth=worker_one, json={}).json()["job"]["id"]
            == job["id"]
        )
        app.gateway.owner_credentials = ("owner", "a-new-owner-password-at-least-24")
        assert one.post(PREFIX + "/api/search/claim", auth=worker_one, json={}).status_code == 401


def test_admin_controls_require_admin_and_hide_secrets(auth, monkeypatch):
    app, _ = auth
    monkeypatch.setenv("UNIPILE_API_KEY", "not-to-be-returned-in-the-admin-ui")
    with TestClient(app, base_url=ORIGIN) as admin, TestClient(app, base_url=ORIGIN) as member:
        assert admin.get(PREFIX + "/api/admin/overview").status_code == 401
        google_login(member, "first@gmail.com")
        assert member.get(PREFIX + "/api/admin/overview").status_code == 401
        assert member.get(PREFIX + "/admin/controls", follow_redirects=False).status_code == 303
        admin_login(admin)
        assert "Admin controls" in admin.get(PREFIX + "/").text
        page = admin.get(PREFIX + "/admin/controls")
        assert page.status_code == 200 and "Approved user access" in page.text
        response = admin.get(PREFIX + "/api/admin/overview")
        assert response.status_code == 200
        assert "not-to-be-returned" not in response.text
        assert "token_hash" not in response.text and "password" not in response.text
        assert {u["email"] for u in response.json()["users"]} == ALLOWED
        assert "Smart App Control" in response.json()["release_blocker"]
        assert (
            admin.post(
                PREFIX + "/api/admin/access",
                json={"email": "first@gmail.com", "enabled": False},
                headers={"Origin": "https://outside.example"},
            ).status_code
            == 403
        )
        assert app.gateway.operations.enabled("first@gmail.com")


def test_admin_can_block_restore_only_owner_approved_emails(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as admin, TestClient(app, base_url=ORIGIN) as member:
        google_login(member, "first@gmail.com")
        _, worker = pair_desktop(member, "j" * 64, "USER PC")
        admin_login(admin)
        route = PREFIX + "/api/admin/access"
        assert (
            admin.post(route, json={"email": "outsider@gmail.com", "enabled": True}).status_code
            == 403
        )
        assert (
            admin.post(route, json={"email": "first@gmail.com", "enabled": False}).status_code
            == 200
        )
        assert member.get(PREFIX + "/api/leads").status_code == 401
        assert member.post(PREFIX + "/api/search/claim", auth=worker, json={}).status_code == 401
        response, _ = google_login(member, "first@gmail.com")
        assert "error=access" in response.headers["location"]
        assert (
            admin.post(route, json={"email": "first@gmail.com", "enabled": True}).status_code == 200
        )
        assert member.get(PREFIX + "/api/leads").status_code == 401
        google_login(member, "first@gmail.com")
        assert member.get(PREFIX + "/api/leads").status_code == 200
        actions = {
            item["action"] for item in admin.get(PREFIX + "/api/admin/overview").json()["events"]
        }
        assert {"access_enabled", "access_blocked"} <= actions


def test_admin_device_revoke_invalidates_browser_pairing_and_worker(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as admin, TestClient(app, base_url=ORIGIN) as member:
        google_login(member, "first@gmail.com")
        device, worker = pair_desktop(member, "k" * 64, "ISOLATED PC")
        assert member.post(PREFIX + "/api/search/claim", auth=worker, json={}).status_code == 200
        admin_login(admin)
        before = admin.get(PREFIX + "/api/admin/overview")
        assert before.json()["devices"][0]["online"]
        assert "k" * 64 not in before.text and digest("k" * 64) not in before.text
        assert (
            admin.post(PREFIX + "/api/admin/devices/revoke", json={"id": device["id"]}).status_code
            == 200
        )
        assert member.post(PREFIX + "/api/search/claim", auth=worker, json={}).status_code == 401
        assert member.get(PREFIX + "/api/auth/devices/current").json()["paired"] is False
        assert admin.get(PREFIX + "/api/admin/overview").json()["devices"][0]["revoked"] == 1


def test_admin_can_cancel_only_queued_search_in_valid_scope(auth):
    from uuid import uuid4

    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as admin, TestClient(app, base_url=ORIGIN) as member:
        google_login(member, "first@gmail.com")
        user = member.get(PREFIX + "/api/auth/me").json()
        campaign = member.get(PREFIX + "/api/search/settings").json()["campaigns"][0]["id"]
        payload = {"campaign": campaign, "max_results": 1, "max_pages": 1}
        job = member.post(PREFIX + "/api/search/jobs", json=payload).json()
        admin_login(admin)
        route = PREFIX + "/api/admin/search/cancel"
        assert admin.post(route, json={"scope": str(uuid4()), "id": job["id"]}).status_code == 400
        assert admin.post(route, json={"scope": user["id"], "id": job["id"]}).status_code == 200
        assert member.get(PREFIX + "/api/search/settings").json()["jobs"][0]["status"] == "failed"
        running = member.post(PREFIX + "/api/search/jobs", json=payload).json()
        store = app.gateway.tenant(user)[0].state.search_store
        assert store.claim()["id"] == running["id"]
        assert admin.post(route, json={"scope": user["id"], "id": running["id"]}).status_code == 400
        assert member.get(PREFIX + "/api/search/settings").json()["jobs"][0]["status"] == "running"


def test_admin_backups_are_valid_server_snapshots_without_content_download(auth):
    import sqlite3

    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as admin:
        admin_login(admin)
        response = admin.post(PREFIX + "/api/admin/backups", json={})
        assert response.status_code == 200
        snapshot = response.json()
        assert snapshot["complete"] and snapshot["files"] > 0
        root = app.gateway.operations.shared_root / "backups" / snapshot["id"]
        for path in root.glob("*.sqlite3"):
            with sqlite3.connect(path) as db:
                assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        overview = admin.get(PREFIX + "/api/admin/overview")
        assert overview.json()["backups"][0]["id"] == snapshot["id"]
        assert "restore-map" not in overview.text and str(root) not in overview.text
        assert admin.get(PREFIX + "/api/admin/backups/" + snapshot["id"]).status_code == 404
        for _ in range(2):
            repeated = admin.post(PREFIX + "/api/admin/backups", json={})
            assert repeated.status_code == 200
            assert repeated.json()["files"] == snapshot["files"]
        assert admin.post(PREFIX + "/api/admin/backups", json={}).status_code == 429


def test_desktop_search_runs_only_on_selected_computer_not_another_pc(auth):
    app, _ = auth
    app.gateway.device_mode = True
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        google_login(first, "first@gmail.com")
        google_login(second, "first@gmail.com")
        settings = first.get(PREFIX + "/api/search/settings").json()
        campaign = settings["campaigns"][0]["id"]
        payload = {"campaign": campaign, "max_results": 1, "max_pages": 1}
        assert first.post(PREFIX + "/api/search/jobs", json=payload).status_code == 409
        one, worker_one = pair_desktop(first, "a" * 64, "PC ONE")
        two, worker_two = pair_desktop(second, "b" * 64, "PC TWO")
        job = second.post(PREFIX + "/api/search/jobs", json=payload).json()
        # A worker on the owner's PC must not take the other PC's job, even for the same email.
        assert (
            first.post(PREFIX + "/api/search/claim", json={}, auth=worker_one).json()["job"] is None
        )
        state = second.get(PREFIX + "/api/search/settings").json()
        assert state["worker_online"] is False
        claimed = second.post(PREFIX + "/api/search/claim", json={}, auth=worker_two).json()["job"]
        assert claimed["id"] == job["id"]
        assert second.get(PREFIX + "/api/search/settings").json()["worker_online"] is True
        assert first.get(PREFIX + "/api/search/settings").json()["jobs"] == []
        result = {"token": claimed["token"], "status": "completed", "message": "Device test"}
        assert (
            first.post(
                PREFIX + "/api/search/jobs/" + job["id"] + "/complete", json=result, auth=worker_one
            ).status_code
            == 409
        )
        assert (
            second.post(
                PREFIX + "/api/search/jobs/" + job["id"] + "/complete", json=result, auth=worker_two
            ).status_code
            == 200
        )
        assert first.get(PREFIX + "/api/leads", auth=worker_one).status_code == 403
        assert one["id"] != two["id"]


def test_pairing_requires_approved_login_is_one_time_and_revocable(auth):
    import time

    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as client:
        token = "c" * 64
        begun = client.post(
            PREFIX + "/api/auth/devices/begin", json={"token_hash": digest(token), "label": "MY PC"}
        ).json()
        code = {"code": begun["code"]}
        assert client.post(PREFIX + "/api/auth/devices/approve", json=code).status_code == 401
        assert (
            client.post(
                PREFIX + "/api/auth/devices/poll", json={"id": begun["id"], "token": "d" * 64}
            ).status_code
            == 400
        )
        google_login(client, "first@gmail.com")
        assert (
            client.post(PREFIX + "/api/auth/devices/preview", json=code).json()["label"] == "MY PC"
        )
        assert client.post(PREFIX + "/api/auth/devices/approve", json=code).status_code == 200
        assert client.post(PREFIX + "/api/auth/devices/approve", json=code).status_code == 400
        with app.gateway.store.db() as db:
            db.execute("UPDATE desktop_devices SET revoked=1 WHERE id=?", (begun["id"],))
        assert client.get(PREFIX + "/api/auth/devices/current").json()["paired"] is False
        auth_header = ("worker-device-" + begun["id"], token)
        assert (
            client.post(PREFIX + "/api/search/claim", auth=auth_header, json={}).status_code == 401
        )
        expired = client.post(
            PREFIX + "/api/auth/devices/begin",
            json={"token_hash": digest("e" * 64), "label": "EXPIRED PC"},
        ).json()
        with app.gateway.store.db() as db:
            db.execute(
                "UPDATE desktop_devices SET expires=? WHERE id=?", (time.time() - 1, expired["id"])
            )
        assert (
            client.post(
                PREFIX + "/api/auth/devices/approve", json={"code": expired["code"]}
            ).status_code
            == 400
        )


def test_pages_and_anonymous_api_are_protected(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as client:
        assert (
            client.get(PREFIX + "/", follow_redirects=False).headers["location"]
            == PREFIX + "/login"
        )
        for page in ("login", "signup", "verify"):
            response = client.get(PREFIX + "/" + page)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert "APPROVED ACCESS ONLY" in response.text
        for route in ("leads", "summary", "auth/me", "search/settings"):
            assert client.get(PREFIX + "/api/" + route).status_code == 401
        assert client.get("/healthz").status_code == 200


def test_desktop_download_uses_app_resource_not_installed_wheel_path(auth, tmp_path, monkeypatch):
    app, _ = auth
    artifact = tmp_path / "helper.zip"
    artifact.write_bytes(b"public helper test artifact")
    monkeypatch.setenv("DESKTOP_WORKER_DOWNLOAD", str(artifact))
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get(PREFIX + "/downloads/BluQQ-Worker.zip")
        assert response.status_code == 200
        assert response.content == artifact.read_bytes()
        assert response.headers["cache-control"] == "no-store"
        assert client.get(PREFIX + "/downloads/secret.env").status_code == 401


def test_browser_remembers_separate_pairings_for_each_allowed_account(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as client:
        google_login(client, "first@gmail.com")
        first, _ = pair_desktop(client, "f" * 64, "SHARED PC - FIRST")
        google_login(client, "second@gmail.com")
        assert client.get(PREFIX + "/api/auth/devices/current").json()["paired"] is False
        second, _ = pair_desktop(client, "g" * 64, "SHARED PC - SECOND")
        assert (
            client.get(PREFIX + "/api/auth/devices/current").json()["device"]["id"] == second["id"]
        )
        google_login(client, "first@gmail.com")
        assert (
            client.get(PREFIX + "/api/auth/devices/current").json()["device"]["id"] == first["id"]
        )


def test_only_allowlisted_emails_can_register_and_login(auth):
    app, mailer = auth
    with TestClient(app, base_url=ORIGIN) as client:
        for route in ("signup", "login"):
            values = {"email": "outsider@gmail.com", "password": PASSWORD}
            if route == "signup":
                values["name"] = "Outsider"
            assert client.post(PREFIX + "/api/auth/" + route, json=values).status_code == 403
        assert not mailer.messages
        assert (
            client.post(
                PREFIX + "/api/auth/verify",
                json={"email": "outsider@gmail.com", "code": "12345678"},
            ).status_code
            == 403
        )


def test_email_must_be_verified_and_session_survives_login_then_revokes(auth):
    app, mailer = auth
    with TestClient(app, base_url=ORIGIN) as client:
        values = {"email": "first@gmail.com", "password": PASSWORD}
        response = client.post(PREFIX + "/api/auth/signup", json={"name": "First", **values})
        assert response.status_code == 200
        assert client.post(PREFIX + "/api/auth/login", json=values).status_code == 400
        assert (
            client.post(
                PREFIX + "/api/auth/verify", json={"email": values["email"], "code": "wrong"}
            ).status_code
            == 422
        )
        code = mailer.messages[-1][1]
        response = client.post(
            PREFIX + "/api/auth/verify", json={"email": values["email"], "code": code}
        )
        assert response.status_code == 200
        assert (
            "HttpOnly" in response.headers["set-cookie"]
            and "Secure" in response.headers["set-cookie"]
        )
        assert client.get(PREFIX + "/api/auth/me").json()["email"] == values["email"]
        old_cookie = client.cookies.get(app.gateway.cookie_name)
        assert PASSWORD.encode() not in app.gateway.store.path.read_bytes()
        assert old_cookie.encode() not in app.gateway.store.path.read_bytes()
        client.post(PREFIX + "/api/auth/logout", json={})
        client.cookies.set(app.gateway.cookie_name, old_cookie)
        assert client.get(PREFIX + "/api/leads").status_code == 401
        client.cookies.clear()
        assert client.post(PREFIX + "/api/auth/login", json=values).status_code == 200
        assert client.get(PREFIX + "/api/leads").json() == []
        assert "Private workspace" in client.get(PREFIX + "/").text


def test_google_rejects_unlisted_email_and_state_replay(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as client:
        response, state = google_login(client, "outsider@gmail.com")
        assert response.headers["location"].endswith("?error=access")
        assert client.get(PREFIX + "/api/auth/me").status_code == 401
        assert (
            client.get(
                PREFIX + "/api/auth/google/callback?state=forged&code=first@gmail.com"
            ).status_code
            == 400
        )
        response, state = google_login(client, "first@gmail.com")
        assert response.headers["location"] == PREFIX + "/"
        assert client.get(PREFIX + "/api/auth/me").json()["email"] == "first@gmail.com"
        assert (
            client.get(
                PREFIX + "/api/auth/google/callback",
                params={"state": state, "code": "first@gmail.com"},
            ).status_code
            == 400
        )


def test_users_cannot_access_each_others_jobs_or_internal_worker_routes(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        google_login(first, "first@gmail.com")
        google_login(second, "second@gmail.com")
        campaign = first.get(PREFIX + "/api/search/settings").json()["campaigns"][0]["id"]
        response = first.post(
            PREFIX + "/api/search/jobs",
            json={"campaign": campaign, "max_results": 1, "max_pages": 1},
        )
        assert response.status_code == 200
        assert len(first.get(PREFIX + "/api/search/settings").json()["jobs"]) == 1
        assert second.get(PREFIX + "/api/search/settings").json()["jobs"] == []
        for route in (
            "/api/search/claim",
            "/api/search/heartbeat",
            "/api/outreach/claim",
            "/api/import/research",
            "/api/search/jobs/test/complete",
        ):
            assert first.post(PREFIX + route, json={}).status_code == 404
        assert first.get(PREFIX + "/api/leads?user_id=second@gmail.com").json() == []


def test_removed_allowlist_access_is_immediately_denied(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as client:
        google_login(client, "first@gmail.com")
        app.gateway.settings = replace(app.gateway.settings, allowed_emails=frozenset())
        assert client.get(PREFIX + "/api/leads").status_code == 401
        assert client.get(PREFIX + "/api/auth/me").status_code == 401


def test_csrf_body_limits_and_login_throttling(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as client:
        values = {"email": "first@gmail.com", "password": PASSWORD}
        assert (
            client.post(
                PREFIX + "/api/auth/login", json=values, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert client.post(PREFIX + "/api/auth/login", data=values).status_code == 403
        assert (
            client.post(
                PREFIX + "/api/auth/login",
                content=b"x" * 8193,
                headers={"Content-Type": "application/json"},
            ).status_code
            == 400
        )
        assert (
            client.post(PREFIX + "/api/auth/login", json={**values, "admin": True}).status_code
            == 422
        )
        for _ in range(10):
            assert client.post(PREFIX + "/api/auth/login", json=values).status_code == 400
        assert client.post(PREFIX + "/api/auth/login", json=values).status_code == 429


def test_unconfigured_providers_fail_honestly_and_legacy_worker_still_authenticates(auth):
    app, mailer = auth
    app.gateway.settings = replace(
        app.gateway.settings, google_client_id="", google_client_secret=""
    )
    mailer.ready = False
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get(PREFIX + "/api/auth/google").status_code == 503
        assert (
            client.post(
                PREFIX + "/api/auth/signup",
                json={"name": "Member", "email": "first@gmail.com", "password": PASSWORD},
            ).status_code
            == 503
        )
        assert not mailer.messages
        assert (
            client.get(
                PREFIX + "/api/leads", auth=("owner", "owner-password-for-testing-at-least-24")
            ).status_code
            == 200
        )


def test_invited_signup_needs_no_mail_or_google_and_consumes_bound_code(auth):
    app, mailer = auth
    app.gateway.settings = replace(
        app.gateway.settings, signup_mode="invite", google_client_id="", google_client_secret=""
    )
    mailer.ready = False
    code = app.gateway.store.invite("first@gmail.com")
    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get(PREFIX + "/signup")
        assert 'id="invite_code"' in page.text
        assert 'id="submit" type="submit" disabled' not in page.text
        assert "Email signup is currently unavailable" not in page.text
        assert (
            client.get(PREFIX + "/verify", follow_redirects=False)
            .headers["location"]
            .endswith("/signup")
        )
        values = {"name": "Member", "email": "first@gmail.com", "password": PASSWORD}
        assert client.post(PREFIX + "/api/auth/signup", json=values).status_code == 400
        # Possession of another user's code cannot activate a different email.
        assert (
            client.post(
                PREFIX + "/api/auth/signup",
                json={**values, "email": "second@gmail.com", "invite_code": code},
            ).status_code
            == 400
        )
        response = client.post(PREFIX + "/api/auth/signup", json={**values, "invite_code": code})
        assert response.status_code == 200 and response.json()["next"] == PREFIX + "/"
        assert client.get(PREFIX + "/api/auth/me").json()["email"] == values["email"]
        client.post(PREFIX + "/api/auth/logout", json={})
        assert (
            client.post(
                PREFIX + "/api/auth/login", json={"email": values["email"], "password": PASSWORD}
            ).status_code
            == 200
        )
        assert not mailer.messages
    with pytest.raises(ValueError):
        app.gateway.store.register_invited("first@gmail.com", "Other", PASSWORD, code)


def test_login_only_hides_and_blocks_signup_but_keeps_existing_logins(auth):
    app, mailer = auth
    with TestClient(app, base_url=ORIGIN) as client:
        signup(client, mailer)
        client.post(PREFIX + "/api/auth/logout", json={})
        app.gateway.settings = replace(app.gateway.settings, signup_mode="disabled")
        page = client.get(PREFIX + "/login")
        assert "Create an account" not in page.text
        assert 'href="/linkedin-automation/signup"' not in page.text
        assert "Continue with Google" in page.text
        for path in ("/signup", "/verify"):
            response = client.get(PREFIX + path, follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == PREFIX + "/login"
        for route in ("signup", "verify"):
            assert client.post(PREFIX + "/api/auth/" + route, json={}).status_code == 403
        assert (
            client.post(
                PREFIX + "/api/auth/login", json={"email": "first@gmail.com", "password": PASSWORD}
            ).status_code
            == 200
        )
        assert client.get(PREFIX + "/api/leads").status_code == 200


def test_google_can_open_preapproved_accounts_in_login_only_mode(auth):
    app, _ = auth
    app.gateway.settings = replace(app.gateway.settings, signup_mode="disabled")
    with TestClient(app, base_url=ORIGIN) as client:
        response, _ = google_login(client, "first@gmail.com")
        assert response.headers["location"] == PREFIX + "/"
        assert client.get(PREFIX + "/api/auth/me").json()["email"] == "first@gmail.com"


def test_private_workers_claim_only_their_users_jobs_and_cannot_browse(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        google_login(first, "first@gmail.com")
        google_login(second, "second@gmail.com")
        one = first.get(PREFIX + "/api/auth/me").json()
        two = second.get(PREFIX + "/api/auth/me").json()
        token_one, token_two = "a" * 64, "b" * 64
        app.gateway.store.bind_worker(one["id"], digest(token_one))
        app.gateway.store.bind_worker(two["id"], digest(token_two))
        worker_one = ("worker-" + one["id"].replace("-", ""), token_one)
        worker_two = ("worker-" + two["id"].replace("-", ""), token_two)
        campaign = first.get(PREFIX + "/api/search/settings").json()["campaigns"][0]["id"]
        job = first.post(
            PREFIX + "/api/search/jobs",
            json={"campaign": campaign, "max_results": 1, "max_pages": 1},
        ).json()
        assert (
            second.post(PREFIX + "/api/search/claim", json={}, auth=worker_two).json()["job"]
            is None
        )
        claimed = first.post(PREFIX + "/api/search/claim", json={}, auth=worker_one).json()["job"]
        assert claimed["id"] == job["id"]
        assert first.get(PREFIX + "/api/search/settings").json()["worker_online"] is True
        for path in ("/", "/api/leads", "/api/auth/me"):
            assert first.get(PREFIX + path, auth=worker_one).status_code == 403
        assert (
            first.post(PREFIX + "/api/outreach/prepare", json={}, auth=worker_one).status_code
            == 403
        )
        assert (
            first.post(
                PREFIX + "/api/search/claim", json={}, auth=(worker_one[0], "x" * 64)
            ).status_code
            == 401
        )
        app.gateway.store.bind_worker(one["id"], digest("c" * 64))
        assert first.post(PREFIX + "/api/search/claim", json={}, auth=worker_one).status_code == 401


def test_private_worker_cannot_complete_another_users_claim_or_bypass_removed_email(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        google_login(first, "first@gmail.com")
        google_login(second, "second@gmail.com")
        users = [client.get(PREFIX + "/api/auth/me").json() for client in (first, second)]
        auths = []
        for user, token in zip(users, ("a" * 64, "b" * 64), strict=True):
            app.gateway.store.bind_worker(user["id"], digest(token))
            auths.append(("worker-" + user["id"].replace("-", ""), token))
        campaign = second.get(PREFIX + "/api/search/settings").json()["campaigns"][0]["id"]
        second.post(
            PREFIX + "/api/search/jobs",
            json={"campaign": campaign, "max_results": 1, "max_pages": 1},
        )
        job = second.post(PREFIX + "/api/search/claim", json={}, auth=auths[1]).json()["job"]
        response = first.post(
            PREFIX + "/api/search/jobs/" + job["id"] + "/complete",
            json={"token": job["token"], "status": "completed"},
            auth=auths[0],
        )
        assert response.status_code == 409
        assert second.get(PREFIX + "/api/search/settings").json()["jobs"][0]["status"] == "running"
        app.gateway.settings = replace(
            app.gateway.settings, allowed_emails=frozenset({"second@gmail.com"})
        )
        assert (
            first.post(PREFIX + "/api/search/heartbeat", json={}, auth=auths[0]).status_code == 401
        )


def test_existing_workspace_worker_works_without_browser_pairing(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as setup, TestClient(app, base_url=ORIGIN) as browser:
        admin_login(setup)
        _, worker = pair_desktop(setup, "z" * 64, "EXISTING ADMIN WORKER")
        admin_login(browser)
        assert 'href="/linkedin-automation/connect-computer"' not in browser.get(PREFIX + "/").text
        assert "connect-computer" not in browser.get(PREFIX + "/admin/controls").text
        assert browser.get(PREFIX + "/connect-computer", follow_redirects=False).status_code == 303
        browser.post(PREFIX + "/api/search/heartbeat", auth=worker, json={})
        settings = browser.get(PREFIX + "/api/search/settings").json()
        assert settings["device_required"] is False
        assert settings["worker_online"] is True
        campaign = settings["campaigns"][0]["id"]
        job = browser.post(
            PREFIX + "/api/search/jobs",
            json={
                "campaign": campaign,
                "max_results": 1,
                "max_pages": 1,
            },
        )
        assert job.status_code == 200
        claimed = browser.post(PREFIX + "/api/search/claim", auth=worker, json={}).json()["job"]
        assert claimed["id"] == job.json()["id"]


def test_no_pairing_flow_keeps_private_worker_and_revocation_boundaries(auth):
    app, _ = auth
    with TestClient(app, base_url=ORIGIN) as setup, TestClient(app, base_url=ORIGIN) as browser:
        google_login(setup, "first@gmail.com")
        device, worker = pair_desktop(setup, "y" * 64, "PRIVATE WORKER")
        google_login(browser, "second@gmail.com")
        assert browser.get(PREFIX + "/api/search/settings").json()["device_paired"] is False
        google_login(browser, "first@gmail.com")
        assert browser.get(PREFIX + "/api/search/settings").json()["device_paired"] is True
        app.gateway.operations.revoke_device(device["id"], "owner")
        assert browser.get(PREFIX + "/api/search/settings").json()["device_paired"] is False
        assert browser.post(PREFIX + "/api/search/claim", auth=worker, json={}).status_code == 401
