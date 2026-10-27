"""Explicit authenticated upload with no redirects or credential-bearing URLs."""

import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from linkedin_automation.application.run_transfer import MAX_TRANSFER_BYTES, RunTransfer


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def sync_transfer(
    payload: RunTransfer, url: str, username: str, password: str, *, dry_run: bool = False
) -> dict:
    parsed = urlsplit(url)
    local = parsed.hostname in {"localhost", "127.0.0.1"}
    if (parsed.scheme != "https" and not (local and parsed.scheme == "http")) or (
        not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Use the dashboard HTTPS origin without a path, query or credentials")
    if not username or not password:
        raise ValueError("Dashboard credentials are missing")
    body = payload.model_dump_json().encode()
    if len(body) > MAX_TRANSFER_BYTES:
        raise ValueError("Run exceeds the 2 MiB transfer limit")
    authorization = base64.b64encode(f"{username}:{password}".encode()).decode()
    target = url.rstrip("/") + "/api/import/runs" + ("?dry_run=true" if dry_run else "")
    request = Request(
        target,
        data=body,
        headers={"Authorization": f"Basic {authorization}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(_NoRedirect).open(request, timeout=30) as response:
            return json.loads(response.read(65536))
    except HTTPError as error:
        raise ValueError(
            f"Dashboard sync rejected (HTTP {error.code}); no credentials logged"
        ) from None
    except URLError:
        raise ValueError("Cannot reach dashboard; check its URL and connection") from None
