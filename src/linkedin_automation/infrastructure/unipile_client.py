"""Unipile V1/V2 cloud transport. Keys and provider IDs stay on the server."""

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener

from linkedin_automation.infrastructure.run_sync import _NoRedirect


class UnipileError(ValueError):
    def __init__(self, message, *, status=0):
        super().__init__(message)
        self.status = status


class UnipileAccountUnavailable(UnipileError):
    """The provider explicitly reports an unusable LinkedIn session."""


@dataclass(frozen=True)
class UnipileSettings:
    origin: str
    api_url: str
    api_key: str = field(repr=False)
    version: int = 2
    callback_secret: str = field(default="", repr=False)

    @classmethod
    def environment(cls, origin):
        version = int(os.getenv("UNIPILE_API_VERSION", "2"))
        api = os.getenv("UNIPILE_API_URL", "https://api.unipile.com").rstrip("/")
        parsed = urlsplit(api)
        if (
            version not in (1, 2)
            or parsed.scheme != "https"
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError("Invalid Unipile configuration")
        if version == 2 and api != "https://api.unipile.com":
            raise ValueError("V2 uses https://api.unipile.com")
        if version == 1 and not re.fullmatch(r"api\d+\.unipile\.com", parsed.hostname or ""):
            raise ValueError("V1 needs the DSN from your Unipile dashboard")
        key = os.getenv("UNIPILE_API_KEY", "").strip()
        secret = os.getenv("CLOUD_CALLBACK_SECRET", "").strip()
        if not key or (version == 1 and len(secret) < 32):
            raise ValueError("Unipile owner configuration is incomplete")
        return cls(origin, api, key, version, secret)


class UnipileClient:
    def __init__(self, settings):
        self.settings = settings

    def request(self, path, payload=None, *, multipart=False):
        if not path.startswith(("/v2/", "/api/v1/")) or ".." in path:
            raise ValueError("Invalid provider endpoint")
        content_type = "application/json"
        data = json.dumps(payload).encode() if payload is not None else None
        if multipart:
            boundary = "bluqq-" + secrets.token_hex(24)
            parts = []
            for name, value in payload.items():
                if name not in {"account_id", "text", "attendees_ids"} or not isinstance(
                    value, str
                ):
                    raise ValueError("Invalid messaging field")
                parts.append(
                    (
                        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                        + value
                        + "\r\n"
                    ).encode("utf-8")
                )
            data = b"".join(parts) + f"--{boundary}--\r\n".encode()
            content_type = "multipart/form-data; boundary=" + boundary
        request = Request(
            self.settings.api_url + path,
            data=data,
            headers={
                "X-API-KEY": self.settings.api_key,
                "Accept": "application/json",
                "Content-Type": content_type,
            },
        )
        try:
            with build_opener(_NoRedirect).open(request, timeout=30) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise UnipileError("Provider response exceeds the supported size.")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise UnipileError("Provider returned an unsupported response.")
                return result
        except HTTPError as error:
            try:
                failure = json.loads(error.read(16000))
                text = str(failure.get("type", "")) + " " + str(failure.get("detail", ""))
            except (ValueError, AttributeError):
                text = ""
            if (
                error.code == 429
                or "monthly limit" in text.casefold()
                or "quota" in text.casefold()
            ):
                message = (
                    "LinkedIn or Unipile search allowance is exhausted. "
                    "Collection stopped; review account limits before retrying."
                )
            elif error.code in (401, 403):
                message = (
                    "Unipile access, subscription or LinkedIn connection needs attention. "
                    "Ask the app owner to check configuration or reconnect LinkedIn."
                )
            else:
                message = "Unipile could not complete this request. No automatic retry was made."
            raise UnipileError(message, status=error.code) from None
        except (URLError, TimeoutError, OSError):
            raise UnipileError(
                "Unipile is unavailable. Try again later; no automatic retry was made."
            ) from None

    def create_scope(self, reference):
        data = self.request("/v2/scopes?" + urlencode({"search": reference}))
        for item in data.get("items", data.get("data", [])):
            if item.get("reference") == reference and item.get("status") == "active":
                return item["id"]
        result = self.request(
            "/v2/scopes", {"name": "BluQQ LinkedIn workspace", "reference": reference}
        )
        result = result.get("data", result)
        scope = result.get("id")
        if not isinstance(scope, str) or not scope or len(scope) > 150:
            raise UnipileError(
                "Could not establish a private Unipile Scope. "
                "An owner Service API key is required for V2 setup."
            )
        return scope

    def hosted_link(self, state, account_id=None, *, scope_id=None):
        callback = self.settings.origin + "/linkedin-automation/api/cloud/return"
        if self.settings.version == 2:
            if not scope_id:
                raise UnipileError("A private account Scope is required.")
            body = {
                "expires_on": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                "redirect_uri": callback + "?flow=" + quote(state, safe=""),
                "state": state,
                "account_scope_id": scope_id,
            }
            body.update({"account_id": account_id} if account_id else {"providers": ["linkedin"]})
            result = self.request("/v2/auth/link", body)
            url = result.get("data", result).get("link", "")
        else:
            from linkedin_automation.infrastructure.cloud_connector import (
                CloudConnector,
                CloudSettings,
            )

            config = CloudSettings(
                self.settings.origin,
                self.settings.api_url,
                self.settings.api_key,
                self.settings.callback_secret,
                "",
                "",
            )
            return CloudConnector(config).hosted_link(state, account_id)
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"account.unipile.com", "auth.unipile.com"}
            or parsed.username
        ):
            raise UnipileError("Unipile returned an invalid hosted login URL.")
        return url

    def account(self, account_id, *, scope_id=None):
        value = self.request(
            ("/v2/accounts/" if self.settings.version == 2 else "/api/v1/accounts/")
            + quote(account_id, safe="")
        )
        value = value.get("data", value)
        if self.settings.version == 1:
            if value.get("id") != account_id or value.get("type") != "LINKEDIN":
                raise UnipileError("The LinkedIn connection could not be verified.")
            sources = value.get("sources", [])
            if (
                not isinstance(sources, list)
                or not sources
                or any(not isinstance(item, dict) or item.get("status") != "OK" for item in sources)
            ):
                raise UnipileAccountUnavailable("LinkedIn needs reconnection or verification.")
            return value
        if (
            value.get("id") != account_id
            or value.get("provider") != "linkedin"
            or not scope_id
            or value.get("account_scope_id") != scope_id
        ):
            raise UnipileError("The account does not belong to this private workspace.")
        member = value.get("user_id")
        if not isinstance(member, str) or not member:
            raise UnipileError("LinkedIn identity could not be verified.")
        if value.get("status") not in {"running", "connected"}:
            raise UnipileAccountUnavailable(
                "LinkedIn is disconnected or needs verification. Reconnect to continue."
            )
        return {
            "id": account_id,
            "type": "LINKEDIN",
            "name": value.get("name", ""),
            "connection_params": {"im": {"id": member}},
        }

    def search(self, account_id, search_url, *, cursor=None):
        query = {"cursor": cursor} if cursor else {}
        if self.settings.version == 1:
            query["account_id"] = account_id
            path = "/api/v1/linkedin/search"
        else:
            path = "/v2/" + quote(account_id, safe="") + "/linkedin/search"
        return self.request(path + ("?" + urlencode(query) if query else ""), {"url": search_url})

    def profile(self, account_id, identifier):
        if self.settings.version == 1:
            path = (
                "/api/v1/users/"
                + quote(identifier, safe="")
                + "?"
                + urlencode({"account_id": account_id, "linkedin_sections": "*"})
            )
        else:
            path = (
                "/v2/"
                + quote(account_id, safe="")
                + "/users/"
                + quote(identifier, safe="")
                + "?"
                + urlencode({"variant": "linkedin_classic", "with_sections": "linkedin_*"})
            )
        return self.request(path)

    def delivery_profile(self, account_id, public_id):
        if self.settings.version != 1:
            raise UnipileError("Cloud sending currently requires a Unipile V1 application.")
        return self.request(
            "/api/v1/users/"
            + quote(public_id, safe="")
            + "?"
            + urlencode({"account_id": account_id})
        )

    def invite(self, account_id, provider_id, note):
        if self.settings.version != 1 or not 1 <= len(note) <= 300:
            raise UnipileError(
                "Invitation notes must contain 1-300 characters. Edit and review the draft."
            )
        return self.request(
            "/api/v1/users/invite",
            {"account_id": account_id, "provider_id": provider_id, "message": note},
        )

    def send_message(self, account_id, provider_id, text):
        if self.settings.version != 1 or not text.strip() or len(text) > 8000:
            raise UnipileError("Message content is empty or exceeds the supported length.")
        return self.request(
            "/api/v1/chats",
            {"account_id": account_id, "attendees_ids": provider_id, "text": text},
            multipart=True,
        )
