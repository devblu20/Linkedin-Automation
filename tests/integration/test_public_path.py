"""Public-path routing preserves authentication, API isolation and origin checks."""

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.interfaces.hosted_site import create_hosted_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DASHBOARD_PUBLIC_ORIGIN", "https://www.bluqq.com")
    with TestClient(create_hosted_app()) as client:
        yield client


def test_public_path_dashboard_and_api(client):
    prefix = "/linkedin-automation"
    redirect = client.get(prefix + "?view=outreach", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == prefix + "/?view=outreach"
    assert client.get(prefix + "/healthz").status_code == 200
    assert client.get(prefix).status_code == 401
    assert client.get(prefix + "/api/leads").status_code == 401
    client.auth = ("reviewer", "test-password-at-least-24-characters")
    for path in [prefix, prefix + "/"]:
        html = client.get(path)
        assert html.status_code == 200
        assert "fetch('/linkedin-automation/api/leads')" in html.text
        assert "fetch('/api/" not in html.text
        assert "fetch(`/api/" not in html.text
        assert html.headers["cache-control"] == "no-store"
    assert client.get(prefix + "/api/leads").json() == []
    assert client.get(prefix + "/api/search/settings").status_code == 200
    assert client.get(prefix + "/api/outreach/jobs").status_code == 200
    assert client.get("/api/leads").json() == []
    assert client.get("/linkedin-automation-other/api/leads").status_code == 404


def test_public_proxy_origin_does_not_weaken_direct_or_cross_site_checks(client):
    client.auth = ("reviewer", "test-password-at-least-24-characters")
    origin = {"Origin": "https://www.bluqq.com", "Sec-Fetch-Site": "same-origin"}
    assert (
        client.post(
            "/linkedin-automation/api/outreach/prepare", json={}, headers=origin
        ).status_code
        == 200
    )
    assert client.post("/api/outreach/prepare", json={}, headers=origin).status_code == 403
    for headers in [
        {"Origin": "https://evil.example"},
        {"Origin": "https://www.bluqq.com.evil.example"},
        {**origin, "Sec-Fetch-Site": "cross-site"},
    ]:
        assert (
            client.post(
                "/linkedin-automation/api/outreach/prepare", json={}, headers=headers
            ).status_code
            == 403
        )


def test_public_origin_requires_an_exact_https_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path))
    for origin in [
        "http://www.bluqq.com",
        "https://www.bluqq.com/path",
        "https://user:password@www.bluqq.com",
    ]:
        monkeypatch.setenv("DASHBOARD_PUBLIC_ORIGIN", origin)
        with pytest.raises(RuntimeError, match="HTTPS origin"):
            create_hosted_app()
