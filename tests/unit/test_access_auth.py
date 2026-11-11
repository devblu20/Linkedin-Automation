"""Credential hashing, verification expiry and Google token checks."""

import logging
from urllib.parse import parse_qs, urlsplit

import pytest

from linkedin_automation.infrastructure import access_auth
from linkedin_automation.infrastructure.access_auth import AccessSettings, AccessStore, GoogleLogin
from linkedin_automation.interfaces.access_gateway import AuthLogFilter


def test_password_signup_is_single_use_and_wrong_codes_are_limited(tmp_path):
    store = AccessStore(tmp_path)
    code = store.register("first@gmail.com", "First", "a-good-test-password")
    for _ in range(5):
        with pytest.raises(ValueError, match="Invalid or expired"):
            store.verify_registration("first@gmail.com", "not-a-code")
    with pytest.raises(ValueError):
        store.verify_registration("first@gmail.com", code)
    code = store.register("first@gmail.com", "First", "a-good-test-password")
    user = store.verify_registration("first@gmail.com", code)
    assert store.login("first@gmail.com", "a-good-test-password")["id"] == user["id"]
    with pytest.raises(ValueError):
        store.verify_registration("first@gmail.com", code)
    with pytest.raises(ValueError):
        store.login("first@gmail.com", "wrong-password")
    with pytest.raises(ValueError):
        store.register("second@gmail.com", "Second", "short")


def test_expired_registration_and_session_are_rejected(tmp_path):
    store = AccessStore(tmp_path)
    code = store.register("first@gmail.com", "First", "a-good-test-password")
    with store.db() as db:
        db.execute("UPDATE registrations SET expires=0")
    with pytest.raises(ValueError):
        store.verify_registration("first@gmail.com", code)
    user = store.google_user({"sub": "first", "email": "first@gmail.com", "email_verified": True})
    token = store.session(user["id"])
    with store.db() as db:
        db.execute("UPDATE sessions SET expires=0")
    assert store.user(token) is None


def test_google_account_linking_preserves_verified_local_identity(tmp_path):
    store = AccessStore(tmp_path)
    code = store.register("first@gmail.com", "First", "a-good-test-password")
    local = store.verify_registration("first@gmail.com", code)
    info = {"sub": "google-first", "email": "first@gmail.com", "email_verified": True}
    assert store.google_user(info)["id"] == local["id"]
    with pytest.raises(ValueError):
        store.google_user({**info, "sub": "another-account"})
    with pytest.raises(ValueError):
        store.google_user({**info, "email": "second@gmail.com"})
    with pytest.raises(ValueError):
        store.google_user({**info, "email_verified": False})
    assert store.login("first@gmail.com", "a-good-test-password")["id"] == local["id"]


def test_google_flow_uses_pkce_nonce_and_validated_id_token(monkeypatch):
    settings = AccessSettings("https://www.bluqq.com", frozenset(), "client-id", "private-secret")
    client = GoogleLogin(settings)
    params = parse_qs(urlsplit(client.url("state", "verifier", "nonce")).query)
    assert params["scope"] == ["openid email profile"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["nonce"] == ["nonce"]
    assert "private-secret" not in repr(settings)
    calls = []

    def exchange(url, **kwargs):
        calls.append((url, kwargs))
        return {"id_token": "signed-token"}

    def verify(token, request, audience):
        assert token == "signed-token" and audience == "client-id"
        return {"sub": "subject", "nonce": "nonce", "email_verified": True}

    monkeypatch.setattr(access_auth, "json_request", exchange)
    monkeypatch.setattr(access_auth, "verify_oauth2_token", verify)
    assert client.identity("code", {"verifier": "verifier", "nonce": "nonce"})["sub"] == "subject"
    assert calls[0][0] == "https://oauth2.googleapis.com/token"
    assert calls[0][1]["payload"]["code_verifier"] == "verifier"
    with pytest.raises(ValueError, match="could not be verified"):
        client.identity("code", {"verifier": "verifier", "nonce": "wrong-nonce"})


def test_settings_fail_closed_and_auth_logs_strip_codes(monkeypatch):
    monkeypatch.setenv("AUTH_PUBLIC_ORIGIN", "https://www.bluqq.com")
    monkeypatch.setenv("AUTH_EMAIL_ALLOWLIST", "")
    settings = AccessSettings.from_environment()
    assert not settings.permits("first@gmail.com")
    with pytest.raises(ValueError):
        AccessSettings("https://www.bluqq.com/other", frozenset())
    with pytest.raises(ValueError):
        AccessSettings("https://www.bluqq.com", frozenset({"*@gmail.com"}))
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "%s %s %s %s %s",
        (
            "client",
            "GET",
            "/linkedin-automation/api/auth/google/callback?code=secret&state=secret",
            "1.1",
            303,
        ),
        None,
    )
    AuthLogFilter().filter(record)
    assert "secret" not in record.getMessage()
