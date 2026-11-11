"""Optional multi-user gateway. Legacy data never becomes a public/new-user workspace."""

import base64
import json
import os
import re
import secrets
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from linkedin_automation.infrastructure.cloud_accounts import CloudAccounts
from linkedin_automation.infrastructure.cloud_connector import (
    CloudConnector,
    CloudSettings,
    LinkedInIdentity,
)
from linkedin_automation.interfaces.cloud_ui import welcome
from linkedin_automation.interfaces.public_path import PUBLIC_PATH, PublicPathMiddleware

COOKIE = "__Secure-bluqq_cloud"
LOGIN_COOKIE = "__Secure-bluqq_login"


class CloudGateway:
    def __init__(self, legacy, root, settings, *, connector=None, identity=None, workers=None):
        self.legacy, self.settings = legacy, settings
        self.accounts = CloudAccounts(root / "cloud")
        self.connector = connector or CloudConnector(settings)
        self.identity = identity or LinkedInIdentity(settings)
        self.workers = workers
        self.tenants = {}

    def tenant(self, user_id):
        if user_id not in self.tenants:
            from linkedin_automation.interfaces.hosted_site import create_hosted_app

            credentials = (user_id, secrets.token_urlsafe(48))
            app = create_hosted_app(
                data_root=self.accounts.workspace(user_id),
                access_credentials=credentials,
                worker_mode="cloud",
            )
            self.tenants[user_id] = (app, credentials)
        return self.tenants[user_id]

    @staticmethod
    def cookie(response, name, value, *, age=30 * 86400):
        response.set_cookie(
            name,
            value,
            secure=True,
            httponly=True,
            samesite="lax",
            path=PUBLIC_PATH + "/",
            max_age=age,
        )

    async def endpoint(self, request):
        path = request.scope["path"]
        token = request.cookies.get(COOKIE)
        user = self.accounts.user(token)
        if path == "/api/cloud/notify" and request.method == "POST":
            provided = request.query_params.get("secret", "")
            if not secrets.compare_digest(provided, self.settings.callback_secret):
                return JSONResponse({"detail": "Invalid callback"}, status_code=403)
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 65536:
                    return JSONResponse({"detail": "Callback too large"}, status_code=413)
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Invalid callback")
            if payload.get("status") not in {"CREATION_SUCCESS", "RECONNECTED"}:
                return JSONResponse({"ok": True})
            state, account_id = payload.get("name", ""), payload.get("account_id", "")
            if (
                not isinstance(state, str)
                or len(state) != 64
                or not isinstance(account_id, str)
                or len(account_id) > 150
            ):
                raise ValueError("Invalid callback")
            account = await run_in_threadpool(self.connector.account, account_id)
            if account.get("id") != account_id:
                raise ValueError("Account confirmation did not match")
            linked = self.accounts.connection(state, account)
            if self.workers:
                self.workers.ensure(linked["id"], self.tenant(linked["id"])[0])
            return JSONResponse({"ok": True})
        if path == "/api/cloud/login" and request.method == "GET":
            state = self.accounts.flow("identity")
            response = RedirectResponse(self.identity.login_url(state), status_code=303)
            self.cookie(response, LOGIN_COOKIE, state, age=600)
            return response
        if path == "/api/cloud/identity" and request.method == "GET":
            state, code = (
                request.query_params.get("state", ""),
                request.query_params.get("code", ""),
            )
            if not state or not secrets.compare_digest(
                state, request.cookies.get(LOGIN_COOKIE, "")
            ):
                raise ValueError("LinkedIn sign-in state did not match")
            if not code or request.query_params.get("error"):
                raise ValueError("LinkedIn sign-in was not completed")
            self.accounts.consume(state, "identity")
            info = await run_in_threadpool(self.identity.identity, code)
            owner = self.accounts.identity(info["sub"], info.get("name", "LinkedIn member"))
            response = RedirectResponse(PUBLIC_PATH + "/", status_code=303)
            if token:
                self.accounts.revoke(token)
            self.cookie(response, COOKIE, self.accounts.session(owner["id"]))
            response.delete_cookie(LOGIN_COOKIE, path=PUBLIC_PATH + "/")
            return response
        if path == "/api/cloud/return" and request.method == "GET":
            return RedirectResponse(PUBLIC_PATH + "/", status_code=303)
        if not user:
            return JSONResponse({"detail": "Sign in with LinkedIn"}, status_code=401)
        if path == "/api/cloud/account" and request.method == "GET":
            return JSONResponse(
                {
                    "connected": bool(user["account_id"] and user["status"] == "connected"),
                    "name": user["name"],
                }
            )
        if path == "/api/cloud/connect" and request.method == "POST":
            state = self.accounts.flow("connection", user["id"])
            url = await run_in_threadpool(self.connector.hosted_link, state, user["account_id"])
            return JSONResponse({"url": url})
        if path == "/api/cloud/logout" and request.method == "POST":
            self.accounts.revoke(token)
            response = JSONResponse({"ok": True})
            response.delete_cookie(COOKIE, path=PUBLIC_PATH + "/")
            return response
        return JSONResponse({"detail": "Not found"}, status_code=404)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.legacy(scope, receive, send)
        request = Request(scope, receive)
        path = scope["path"]
        is_cloud = path.startswith("/api/cloud/")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and path != "/api/cloud/notify":
            origin = request.headers.get("origin")
            if (
                request.headers.get("sec-fetch-site") == "cross-site"
                or (origin and origin != self.settings.public_origin)
                or not request.headers.get("content-type", "").startswith("application/json")
            ):
                return await JSONResponse({"detail": "Invalid request origin"}, status_code=403)(
                    scope, receive, send
                )
        try:
            if is_cloud:
                response = await self.endpoint(request)
                response.headers["Cache-Control"] = "no-store"
                response.headers["Referrer-Policy"] = "no-referrer"
                return await response(scope, receive, send)
            if path == "/healthz":
                return await self.legacy(scope, receive, send)
            user = self.accounts.user(request.cookies.get(COOKIE))
            if not user:
                if request.headers.get("authorization", "").startswith("Basic "):
                    return await self.legacy(scope, receive, send)
                response = (
                    HTMLResponse(welcome())
                    if path == "/"
                    else JSONResponse({"detail": "Sign in with LinkedIn"}, status_code=401)
                )
                response.headers["Cache-Control"] = "no-store"
                return await response(scope, receive, send)
            if not user["account_id"] or user["status"] != "connected":
                response = (
                    HTMLResponse(
                        welcome("Connect your LinkedIn account to continue.", signed_in=True)
                    )
                    if path == "/"
                    else JSONResponse({"detail": "Connect LinkedIn first"}, status_code=409)
                )
                response.headers["Cache-Control"] = "no-store"
                return await response(scope, receive, send)
            # Browser users cannot impersonate the internal cloud worker or import receipts.
            internal = (
                path in {"/api/search/claim", "/api/search/heartbeat", "/api/outreach/claim"}
                or path.startswith("/api/import/")
                or re.fullmatch(r"/api/(?:search|outreach)/jobs/[^/]+/complete", path)
            )
            if internal:
                return await JSONResponse({"detail": "Not found"}, status_code=404)(
                    scope, receive, send
                )
            if (
                self.workers is None
                and request.method == "POST"
                and (path == "/api/search/jobs" or path.startswith("/api/outreach/"))
            ):
                return await JSONResponse(
                    {"detail": "Cloud execution is not activated yet. No job was started."},
                    status_code=503,
                )(scope, receive, send)
            app, credentials = self.tenant(user["id"])
            if self.workers:
                self.workers.ensure(user["id"], app)
            delegated = dict(scope)
            auth = base64.b64encode((credentials[0] + ":" + credentials[1]).encode())
            delegated["headers"] = [
                (k, v) for k, v in scope["headers"] if k.lower() != b"authorization"
            ] + [(b"authorization", b"Basic " + auth)]
            return await app(delegated, receive, send)
        except (ValueError, KeyError, TypeError):
            return await JSONResponse(
                {"detail": "Connection could not be verified. Please sign in or reconnect again."},
                status_code=409,
            )(scope, receive, send)


def create_cloud_gateway(
    legacy, *, root=None, settings=None, connector=None, identity=None, workers=None
):
    from linkedin_automation.infrastructure.cloud_logging import protect_cloud_access_logs

    protect_cloud_access_logs()
    settings = settings or CloudSettings.from_environment()
    gateway = CloudGateway(
        legacy,
        root or Path(os.getenv("HOSTED_DATA_DIR", "/data")),
        settings,
        connector=connector,
        identity=identity,
        workers=workers,
    )
    wrapped = PublicPathMiddleware(gateway)
    wrapped.gateway = gateway
    return wrapped
