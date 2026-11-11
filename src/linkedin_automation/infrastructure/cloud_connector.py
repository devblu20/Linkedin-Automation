"""Server-only clients for LinkedIn identity and Unipile's configured V1 DSN."""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener

from linkedin_automation.infrastructure.run_sync import _NoRedirect


@dataclass(frozen=True)
class CloudSettings:
    public_origin: str
    api_url: str
    api_key: str = field(repr=False)
    callback_secret: str = field(repr=False)
    linkedin_client_id: str
    linkedin_client_secret: str = field(repr=False)

    @classmethod
    def from_environment(cls):
        values = {
            name: os.getenv(name, "").strip()
            for name in (
                "CLOUD_PUBLIC_ORIGIN",
                "UNIPILE_API_URL",
                "UNIPILE_API_KEY",
                "CLOUD_CALLBACK_SECRET",
                "LINKEDIN_CLIENT_ID",
                "LINKEDIN_CLIENT_SECRET",
            )
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError("Cloud account mode needs: " + ", ".join(missing))
        origin = values["CLOUD_PUBLIC_ORIGIN"].rstrip("/")
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use an exact HTTPS public origin")
        api = values["UNIPILE_API_URL"].rstrip("/")
        parsed = urlsplit(api)
        if (
            parsed.scheme != "https"
            or not re.fullmatch(r"api\d*\.unipile\.com", parsed.hostname or "")
            or parsed.username
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use the Unipile HTTPS DSN from your dashboard")
        if len(values["CLOUD_CALLBACK_SECRET"]) < 32:
            raise ValueError("Cloud callback secret must contain at least 32 characters")
        return cls(
            origin,
            api,
            values["UNIPILE_API_KEY"],
            values["CLOUD_CALLBACK_SECRET"],
            values["LINKEDIN_CLIENT_ID"],
            values["LINKEDIN_CLIENT_SECRET"],
        )

    def callback(self, kind: str) -> str:
        return self.public_origin + "/linkedin-automation/api/cloud/" + kind


def json_request(url, *, payload=None, headers=None, form=False):
    body = (
        (urlencode(payload).encode() if form else json.dumps(payload).encode())
        if payload is not None
        else None
    )
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            **(headers or {}),
            **(
                {
                    "Content-Type": "application/x-www-form-urlencoded"
                    if form
                    else "application/json"
                }
                if body
                else {}
            ),
        },
    )
    try:
        with build_opener(_NoRedirect).open(request, timeout=30) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("Cloud connector response is too large")
            return json.loads(raw)
    except HTTPError as error:
        raise ValueError(f"Cloud connector rejected request (HTTP {error.code})") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Cloud connector is unavailable") from None


class CloudConnector:
    def __init__(self, settings: CloudSettings):
        self.settings = settings

    def request(self, path, payload=None):
        if not path.startswith("/api/v1/") or ".." in path:
            raise ValueError("Invalid provider endpoint")
        return json_request(
            self.settings.api_url + path,
            payload=payload,
            headers={"X-API-KEY": self.settings.api_key},
        )

    def hosted_link(self, state: str, account_id: str | None = None):
        body = {
            "type": "reconnect" if account_id else "create",
            "api_url": self.settings.api_url,
            "expiresOn": (datetime.now(UTC) + timedelta(minutes=10))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "name": state,
            "notify_url": self.settings.callback("notify")
            + "?secret="
            + quote(self.settings.callback_secret, safe=""),
            "success_redirect_url": self.settings.callback("return"),
            "failure_redirect_url": self.settings.callback("return") + "?failed=1",
        }
        if account_id:
            body["reconnect_account"] = account_id
        else:
            body["providers"] = ["LINKEDIN"]
        data = self.request("/api/v1/hosted/accounts/link", body)
        url = data.get("url", "")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"account.unipile.com", "auth.unipile.com"}
            or parsed.username
        ):
            raise ValueError("Cloud connector returned an invalid sign-in URL")
        return url

    def account(self, account_id):
        return self.request("/api/v1/accounts/" + quote(account_id, safe=""))


class LinkedInIdentity:
    def __init__(self, settings: CloudSettings):
        self.settings = settings

    def login_url(self, state):
        return "https://www.linkedin.com/oauth/v2/authorization?" + urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.linkedin_client_id,
                "redirect_uri": self.settings.callback("identity"),
                "state": state,
                "scope": "openid profile email",
            }
        )

    def identity(self, code):
        token = json_request(
            "https://www.linkedin.com/oauth/v2/accessToken",
            form=True,
            payload={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.callback("identity"),
                "client_id": self.settings.linkedin_client_id,
                "client_secret": self.settings.linkedin_client_secret,
            },
        )
        access = token.get("access_token")
        if not access:
            raise ValueError("LinkedIn sign-in did not complete")
        info = json_request(
            "https://api.linkedin.com/v2/userinfo", headers={"Authorization": "Bearer " + access}
        )
        if not isinstance(info.get("sub"), str) or not info["sub"]:
            raise ValueError("LinkedIn did not return a verified identity")
        return info
