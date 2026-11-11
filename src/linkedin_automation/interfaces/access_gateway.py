"""Allowlisted website sessions with private dashboards and legacy worker continuity."""

import base64
import binascii
import json
import logging
import os
import re
import secrets
import sqlite3
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from linkedin_automation.infrastructure.access_auth import (
    PREFIX,
    AccessSettings,
    AccessStore,
    GoogleLogin,
    VerificationMail,
    digest,
    email_address,
)
from linkedin_automation.infrastructure.admin_operations import AdminOperations
from linkedin_automation.infrastructure.device_pairing import DevicePairings
from linkedin_automation.interfaces.public_path import PublicPathMiddleware


class DeviceBegin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    label: str = Field(min_length=1, max_length=80, pattern=r"^[\w .()\-]+$")


class DevicePoll(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    token: str = Field(min_length=48, max_length=128)


class DeviceCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[A-Z2-9]{10}$")


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class AdminCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class AdminAccessChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    email: str = Field(max_length=254)
    enabled: bool


class AdminDeviceRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID


class AdminCancelSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = Field(min_length=1, max_length=40)
    id: UUID


class AdminBackup(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=100)
    invite_code: str = Field(default="", max_length=128)


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    code: str = Field(pattern=r"^\d{8}$")


class AuthLogFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if isinstance(args[2], str) and "/api/auth/" in args[2]:
                args[2] = args[2].split("?", 1)[0]
                record.args = tuple(args)
        return True


class AccessGateway:
    def __init__(self, legacy, root, settings, *, google=None, mailer=None):
        self.legacy, self.settings = legacy, settings
        self.store = AccessStore(root / "website-auth")
        self.devices = DevicePairings(self.store)
        self.operations = AdminOperations(self.store, os.getenv("HOSTED_DATA_DIR", str(root)))
        self.device_mode = os.getenv("LOCAL_DEVICE_PAIRING", "0") == "1"
        self.google, self.mailer = google or GoogleLogin(settings), mailer or VerificationMail()
        self.tenants = {}
        self.cookie_name = "__Secure-bluqq_access" if settings.secure else "bluqq_access_local"
        self.state_cookie = self.cookie_name + "_oauth"
        self.device_cookie = self.cookie_name + "_device"
        self.admin_cookie = self.cookie_name + "_admin"
        self.owner_credentials = (
            os.getenv("DASHBOARD_USERNAME", "").strip(),
            os.getenv("DASHBOARD_PASSWORD", ""),
        )
        self.cloud = None
        if os.getenv("WEBSITE_CLOUD_MODE") == "1":
            from linkedin_automation.interfaces.website_cloud import WebsiteCloud

            self.cloud = WebsiteCloud(self, root)
            self.cloud.restore()

    def admin(self, request):
        username, password = self.owner_credentials
        return bool(
            username
            and len(password) >= 24
            and self.store.admin(
                request.cookies.get(self.admin_cookie), username, digest(username + "\n" + password)
            )
        )

    def user(self, request):
        user = self.store.user(request.cookies.get(self.cookie_name))
        return user if user and self.permits(user["email"]) else None

    def permits(self, email):
        return self.settings.permits(email) and self.operations.enabled(email)

    def device_cookie_for(self, user):
        if user["id"].startswith("admin:"):
            return self.device_cookie + "_shared_admin"
        return self.device_cookie + "_" + user["id"].replace("-", "")

    def admin_binding(self):
        return digest("\n".join(self.owner_credentials))

    async def delegate_admin(self, scope, receive, send, *, device=None, browser=False):
        delegated = dict(scope)
        personal_browser = os.getenv("PERSONAL_BROWSER_MODE") == "1" and not self.device_mode
        if browser and not personal_browser:
            selected = self.devices.browser(
                Request(scope).cookies.get(self.device_cookie + "_shared_admin"),
                "admin:" + self.admin_binding(),
            )
            if not selected and not self.device_mode:
                selected = self.devices.configured("admin:" + self.admin_binding())
            device = selected["id"] if selected else None
        delegated["state"] = {
            **scope.get("state", {}),
            "website_admin": browser,
            "device_required": self.device_mode,
            "worker_device": device,
        }
        if self.cloud:
            owner = self.cloud.owner(Request(scope))
            cloud_status = self.cloud.status(owner) if owner else {}
            delegated["state"].update(
                website_cloud=True,
                device_required=False,
                worker_device="cloud",
                cloud_connected=cloud_status.get("connected", False),
                cloud_sending_enabled=cloud_status.get("sending_enabled", False),
                cloud_delivery_account=self.cloud.record(owner) if owner else None,
            )
        authorization = base64.b64encode(":".join(self.owner_credentials).encode())
        delegated["headers"] = [
            (k, v) for k, v in scope["headers"] if k.lower() != b"authorization"
        ] + [(b"authorization", b"Basic " + authorization)]
        return await self.legacy(delegated, receive, send)

    def worker(self, request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Basic "):
            return False, None
        try:
            decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
            username, separator, token = decoded.partition(":")
        except (ValueError, UnicodeError, binascii.Error):
            return False, None
        if not username.startswith("worker-"):
            return False, None
        user = (
            (
                self.devices.worker(username[14:], token)
                if username.startswith("worker-device-")
                else self.store.worker_user(username, token)
            )
            if separator
            else None
        )
        if user:
            if user.get("admin_worker"):
                if not secrets.compare_digest(user["binding"], self.admin_binding()):
                    user = None
            elif not self.permits(user["email"]):
                user = None
        return True, user

    async def delegate(self, user, scope, receive, send, *, worker=False):
        app, credentials = self.tenant(user)
        delegated = dict(scope)
        device = user.get("device_id")
        if not device and not worker:
            browser = self.devices.browser(
                Request(scope).cookies.get(self.device_cookie_for(user)), user["id"]
            )
            if not browser and not self.device_mode:
                browser = self.devices.configured(user["id"])
            device = browser["id"] if browser else None
        delegated["state"] = {
            **scope.get("state", {}),
            "worker_device": device,
            "device_required": self.device_mode,
        }
        if self.cloud:
            cloud_status = self.cloud.status(user)
            delegated["state"].update(
                website_cloud=True,
                device_required=False,
                worker_device="cloud",
                cloud_connected=cloud_status.get("connected", False),
                cloud_sending_enabled=cloud_status.get("sending_enabled", False),
                cloud_delivery_account=self.cloud.record(user),
            )
        authorization = base64.b64encode(":".join(credentials).encode())
        delegated["headers"] = [
            (k, v) for k, v in scope["headers"] if k.lower() != b"authorization"
        ] + [(b"authorization", b"Basic " + authorization)]
        return await app(delegated, receive, send)

    def cookie(self, response, name, value, *, age=7 * 86400):
        response.set_cookie(
            name,
            value,
            max_age=age,
            path=PREFIX + "/",
            secure=self.settings.secure,
            httponly=True,
            samesite="lax",
        )

    def signed_in(self, request, user, response=None):
        self.store.revoke(request.cookies.get(self.cookie_name))
        response = response or JSONResponse({"ok": True, "next": PREFIX + "/"})
        self.cookie(response, self.cookie_name, self.store.session(user["id"]))
        self.store.revoke(request.cookies.get(self.admin_cookie))
        response.delete_cookie(
            self.admin_cookie,
            path=PREFIX + "/",
            secure=self.settings.secure,
            httponly=True,
            samesite="lax",
        )
        return response

    def tenant(self, user):
        identifier = user["id"]
        if identifier not in self.tenants:
            from linkedin_automation.interfaces.hosted_site import create_hosted_app

            # A private user's helper pairing is a separate next feature. Never
            # point a new user at the owner's worker, cookies, jobs or database.
            credentials = (identifier, secrets.token_urlsafe(48))
            app = create_hosted_app(
                data_root=self.store.workspace(identifier), access_credentials=credentials
            )
            app.state.website_user = {"name": user["name"], "email": user["email"]}
            self.tenants[identifier] = app, credentials
        return self.tenants[identifier]

    async def payload(self, request, model):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                raise ValueError("Request is too large")
        return model.model_validate_json(bytes(raw))

    async def auth(self, request):
        path, method = request.scope["path"], request.method
        user = self.user(request)
        if path == "/api/auth/admin/login" and method == "POST":
            ip = (request.scope.get("client") or ("unknown",))[0]
            if not self.store.rate("admin-login:" + ip, maximum=10):
                return JSONResponse(
                    {"detail": "Too many attempts; try again later"}, status_code=429
                )
            values = await self.payload(request, AdminCredentials)
            username, password = self.owner_credentials
            valid_user = secrets.compare_digest(values.username.encode(), username.encode())
            valid_password = secrets.compare_digest(values.password.encode(), password.encode())
            if not username or len(password) < 24 or not (valid_user and valid_password):
                return JSONResponse(
                    {"detail": "Invalid admin username or password"}, status_code=401
                )
            self.store.revoke(request.cookies.get(self.admin_cookie))
            self.store.revoke(request.cookies.get(self.cookie_name))
            response = JSONResponse({"ok": True, "next": PREFIX + "/", "role": "admin"})
            token = self.store.admin_session(username, digest(username + "\n" + password))
            self.cookie(response, self.admin_cookie, token, age=8 * 3600)
            response.delete_cookie(
                self.cookie_name,
                path=PREFIX + "/",
                secure=self.settings.secure,
                httponly=True,
                samesite="lax",
            )
            return response
        if path.startswith("/api/auth/devices/"):
            return await self.device_auth(request, user)
        if path == "/api/auth/config" and method == "GET":
            return JSONResponse(
                {
                    "google": self.settings.google_ready,
                    "signup_mode": self.settings.signup_mode,
                    "email_signup": self.mailer.ready
                    if self.settings.signup_mode == "email"
                    else False,
                }
            )
        if path == "/api/auth/me" and method == "GET":
            if self.admin(request):
                return JSONResponse(
                    {
                        "username": self.owner_credentials[0],
                        "role": "admin",
                        "name": "Administrator",
                        "workspace": "shared",
                    }
                )
            return JSONResponse(
                user if user else {"detail": "Sign in required"}, status_code=200 if user else 401
            )
        if path == "/api/auth/logout" and method == "POST":
            self.store.revoke(request.cookies.get(self.cookie_name))
            self.store.revoke(request.cookies.get(self.admin_cookie))
            response = JSONResponse({"ok": True})
            for name in (self.cookie_name, self.state_cookie, self.admin_cookie):
                response.delete_cookie(
                    name,
                    path=PREFIX + "/",
                    secure=self.settings.secure,
                    httponly=True,
                    samesite="lax",
                )
            return response
        if self.settings.signup_mode == "disabled" and path in {
            "/api/auth/signup",
            "/api/auth/verify",
        }:
            return JSONResponse(
                {"detail": "Signup is disabled. Sign in with an approved account."},
                status_code=403,
            )
        ip = (request.scope.get("client") or ("unknown",))[0]
        if path == "/api/auth/google" and method == "GET":
            if not self.settings.google_ready:
                return JSONResponse(
                    {"detail": "Google sign-in awaits app-owner configuration"}, status_code=503
                )
            if not self.store.rate("google:" + ip, maximum=20):
                return JSONResponse(
                    {"detail": "Too many sign-in attempts; try again later"}, status_code=429
                )
            state, verifier, nonce = self.store.oauth_flow()
            response = RedirectResponse(self.google.url(state, verifier, nonce), status_code=303)
            self.cookie(response, self.state_cookie, state, age=600)
            return response
        if path == "/api/auth/google/callback" and method == "GET":
            state = request.query_params.get("state", "")
            if not state or not secrets.compare_digest(
                state, request.cookies.get(self.state_cookie, "")
            ):
                raise ValueError("Sign-in session did not match; please retry")
            flow = self.store.consume_oauth(state)
            response = RedirectResponse(PREFIX + "/login?error=google", status_code=303)
            response.delete_cookie(
                self.state_cookie, path=PREFIX + "/", secure=self.settings.secure, httponly=True
            )
            code = request.query_params.get("code", "")
            if request.query_params.get("error") or not code or len(code) > 4096:
                return response
            info = await run_in_threadpool(self.google.identity, code, flow)
            email = email_address(info.get("email", ""))
            if not self.permits(email):
                return RedirectResponse(PREFIX + "/login?error=access", status_code=303)
            owner = await run_in_threadpool(self.store.google_user, info)
            response = RedirectResponse(PREFIX + "/", status_code=303)
            response.delete_cookie(
                self.state_cookie, path=PREFIX + "/", secure=self.settings.secure, httponly=True
            )
            return self.signed_in(request, owner, response)
        if method != "POST" or path not in {
            "/api/auth/login",
            "/api/auth/signup",
            "/api/auth/verify",
        }:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if not self.store.rate("auth-ip:" + ip, maximum=30):
            return JSONResponse(
                {"detail": "Too many attempts; try again in 15 minutes"}, status_code=429
            )
        model = (
            Registration
            if path.endswith("signup")
            else Verification
            if path.endswith("verify")
            else Credentials
        )
        values = await self.payload(request, model)
        email = email_address(values.email)
        if not self.permits(email):
            return JSONResponse(
                {"detail": "This email has not been granted access. Contact the app owner."},
                status_code=403,
            )
        maximum = 3 if path.endswith("signup") else 10
        if not self.store.rate(path + email, maximum=maximum):
            return JSONResponse(
                {"detail": "Too many attempts; try again in 15 minutes"}, status_code=429
            )
        if path.endswith("signup"):
            if not values.name.strip():
                raise ValueError("Enter your name")
            if self.settings.signup_mode == "invite":
                owner = await run_in_threadpool(
                    self.store.register_invited,
                    email,
                    values.name.strip(),
                    values.password,
                    values.invite_code,
                )
                return self.signed_in(request, owner)
            if not self.mailer.ready:
                return JSONResponse(
                    {
                        "detail": "Email signup awaits verification-mail configuration. "
                        "Use Google sign-in if available."
                    },
                    status_code=503,
                )
            code = await run_in_threadpool(
                self.store.register, email, values.name.strip(), values.password
            )
            if code:
                await run_in_threadpool(self.mailer.send, email, code)
            return JSONResponse(
                {
                    "ok": True,
                    "message": "If a new account can be created, a verification code has been "
                    "emailed. Existing users should sign in.",
                }
            )
        if path.endswith("verify"):
            if self.settings.signup_mode != "email":
                return JSONResponse({"detail": "Not found"}, status_code=404)
            owner = await run_in_threadpool(self.store.verify_registration, email, values.code)
        else:
            owner = await run_in_threadpool(self.store.login, email, values.password)
        return self.signed_in(request, owner)

    @staticmethod
    def private_headers(response):
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            }
        )
        return response

    async def device_auth(self, request, user):
        path, method = request.scope["path"], request.method
        ip = (request.scope.get("client") or ("unknown",))[0]
        if path.endswith("/begin") and method == "POST":
            if not self.store.rate("device-begin:" + ip, maximum=12, seconds=600):
                return JSONResponse({"detail": "Too many pairing attempts"}, status_code=429)
            payload = await self.payload(request, DeviceBegin)
            return JSONResponse(self.devices.begin(payload.token_hash, payload.label))
        if path.endswith("/poll") and method == "POST":
            payload = await self.payload(request, DevicePoll)
            result = self.devices.poll(payload.id, payload.token)
            if result.get("admin_workspace"):
                worker = self.devices.worker(payload.id, payload.token)
                if not worker or not secrets.compare_digest(
                    worker["binding"], self.admin_binding()
                ):
                    return JSONResponse(
                        {"detail": "Admin credentials changed. Pair this computer again."},
                        status_code=403,
                    )
            elif result.get("email") and not self.permits(result["email"]):
                return JSONResponse({"detail": "Access revoked"}, status_code=403)
            return JSONResponse(result)
        if self.admin(request):
            user = {"id": "admin:" + self.admin_binding()}
        if not user:
            return JSONResponse({"detail": "Sign in with an approved email first"}, status_code=401)
        if path.endswith("/current") and method == "GET":
            device = self.devices.browser(
                request.cookies.get(self.device_cookie_for(user)), user["id"]
            )
            return JSONResponse(
                {"paired": bool(device), "device": device, "required": self.device_mode}
            )
        if path.endswith(("/preview", "/approve")) and method == "POST":
            if not self.store.rate("device-code:" + user["id"], maximum=20, seconds=600):
                return JSONResponse({"detail": "Too many attempts"}, status_code=429)
            code = (await self.payload(request, DeviceCode)).code
            if path.endswith("/preview"):
                return JSONResponse(self.devices.preview(code))
            cookie = self.devices.approve(code, user["id"])
            response = JSONResponse({"ok": True})
            self.cookie(response, self.device_cookie_for(user), cookie, age=365 * 86400)
            return response
        return JSONResponse({"detail": "Not found"}, status_code=404)

    async def admin_endpoint(self, request):
        if not self.admin(request):
            return JSONResponse({"detail": "Administrator sign-in required"}, status_code=401)
        path, method = request.scope["path"], request.method
        if path == "/api/admin/overview" and method == "GET":
            return JSONResponse(
                await run_in_threadpool(self.operations.overview, self.settings.allowed_emails)
            )
        if method != "POST":
            return JSONResponse({"detail": "Not found"}, status_code=404)
        actor = self.owner_credentials[0]
        if not self.store.rate("admin-action:" + actor, maximum=40, seconds=600):
            return JSONResponse(
                {"detail": "Too many admin actions. Try again later."}, status_code=429
            )
        if path == "/api/admin/access":
            values = await self.payload(request, AdminAccessChange)
            email = email_address(values.email)
            if email not in self.settings.allowed_emails:
                return JSONResponse(
                    {
                        "detail": "This email is outside the owner-configured allowlist. "
                        "No access was granted."
                    },
                    status_code=403,
                )
            self.operations.access(email, values.enabled, actor)
            return JSONResponse({"ok": True})
        if path == "/api/admin/devices/revoke":
            values = await self.payload(request, AdminDeviceRevoke)
            self.operations.revoke_device(values.id, actor)
            return JSONResponse(
                {
                    "ok": True,
                    "message": "Device credentials revoked. "
                    "An already open browser must be closed on that computer.",
                }
            )
        if path == "/api/admin/search/cancel":
            values = await self.payload(request, AdminCancelSearch)
            self.operations.cancel_queued(values.scope, values.id, actor)
            return JSONResponse({"ok": True})
        if path == "/api/admin/backups":
            if not self.store.rate("admin-backup:" + actor, maximum=3, seconds=3600):
                return JSONResponse(
                    {"detail": "Snapshot limit reached: at most three snapshots per hour."},
                    status_code=429,
                )
            await self.payload(request, AdminBackup)
            return JSONResponse(await run_in_threadpool(self.operations.backup, actor))
        return JSONResponse({"detail": "Not found"}, status_code=404)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.legacy(scope, receive, send)
        request = Request(scope, receive)
        path = scope["path"]
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and not (self.cloud and path == "/api/cloud/notify")
            and (
                request.headers.get("sec-fetch-site") == "cross-site"
                or request.headers.get("origin", self.settings.origin) != self.settings.origin
                or not request.headers.get("content-type", "").startswith("application/json")
            )
        ):
            return await self.private_headers(
                JSONResponse({"detail": "Invalid request origin"}, status_code=403)
            )(scope, receive, send)
        try:
            if self.cloud and path.startswith("/api/cloud/"):
                response = await self.cloud.endpoint(request)
                return await self.private_headers(response)(scope, receive, send)
            if path == "/connect-computer" and request.method == "GET":
                if self.cloud or not self.device_mode:
                    return await self.private_headers(
                        RedirectResponse(PREFIX + "/", status_code=303)
                    )(scope, receive, send)
                from linkedin_automation.interfaces.device_ui import CONNECT_PAGE

                return await self.private_headers(HTMLResponse(CONNECT_PAGE))(scope, receive, send)
            if path == "/downloads/BluQQ-Worker.zip" and request.method == "GET":
                if self.cloud:
                    return await self.private_headers(
                        JSONResponse(
                            {"detail": "Cloud research does not use a local helper"},
                            status_code=404,
                        )
                    )(scope, receive, send)
                # The wheel installs under site-packages; downloads belong to the app root.
                package = Path(os.getenv("DESKTOP_WORKER_DOWNLOAD", "downloads/BluQQ-Worker.zip"))
                response = (
                    FileResponse(package, filename="BluQQ-Worker.zip")
                    if package.is_file()
                    else JSONResponse(
                        {"detail": "Windows helper download is not available yet"}, status_code=503
                    )
                )
                return await self.private_headers(response)(scope, receive, send)
            is_worker, worker_user = self.worker(request)
            if is_worker:
                if self.cloud:
                    return await self.private_headers(
                        JSONResponse(
                            {"detail": "Local workers are disabled in cloud mode"}, status_code=409
                        )
                    )(scope, receive, send)
                if not worker_user:
                    return await self.private_headers(
                        JSONResponse(
                            {"detail": "Worker credentials are invalid or revoked"}, status_code=401
                        )
                    )(scope, receive, send)
                if worker_user.get("device_id") and path.startswith("/api/outreach/"):
                    return await self.private_headers(
                        JSONResponse(
                            {"detail": "Desktop pairing permits research only"}, status_code=403
                        )
                    )(scope, receive, send)
                allowed = path in {
                    "/api/search/claim",
                    "/api/search/heartbeat",
                    "/api/search/template-definition",
                    "/api/outreach/claim",
                    "/api/import/research",
                    "/api/import/runs",
                } or re.fullmatch(r"/api/(?:search|outreach)/jobs/[^/]+/complete", path)
                if request.method != "POST" or not allowed:
                    return await self.private_headers(
                        JSONResponse(
                            {"detail": "Worker credentials cannot access this route"},
                            status_code=403,
                        )
                    )(scope, receive, send)
                if worker_user.get("admin_worker"):
                    return await self.delegate_admin(
                        scope, receive, send, device=worker_user["device_id"]
                    )
                return await self.delegate(worker_user, scope, receive, send, worker=True)
            if path.startswith("/api/admin/"):
                try:
                    response = await self.admin_endpoint(request)
                except (OSError, sqlite3.Error):
                    response = JSONResponse(
                        {
                            "detail": "Admin operation could not access server storage. "
                            "No restore or deletion was performed."
                        },
                        status_code=503,
                    )
                return await self.private_headers(response)(scope, receive, send)
            if path.startswith("/api/auth/"):
                response = await self.auth(request)
                return await self.private_headers(response)(scope, receive, send)
            if path == "/healthz":
                return await self.legacy(scope, receive, send)
            user = self.user(request)
            if path == "/admin/controls" and request.method == "GET":
                from linkedin_automation.interfaces.admin_ui import ADMIN_CONTROLS

                response = (
                    HTMLResponse(
                        ADMIN_CONTROLS
                        if self.device_mode
                        else ADMIN_CONTROLS.replace(
                            '<a href="/linkedin-automation/connect-computer">'
                            "Connect this computer</a>",
                            "",
                        )
                    )
                    if self.admin(request)
                    else RedirectResponse(PREFIX + "/admin", status_code=303)
                )
                return await self.private_headers(response)(scope, receive, send)
            if path == "/admin" and request.method == "GET":
                from linkedin_automation.interfaces.access_ui import auth_page

                response = (
                    RedirectResponse(PREFIX + "/", status_code=303)
                    if self.admin(request)
                    else HTMLResponse(auth_page("admin"))
                )
                return await self.private_headers(response)(scope, receive, send)
            if path in {"/login", "/signup", "/verify"} and request.method == "GET":
                from linkedin_automation.interfaces.access_ui import auth_page

                if path != "/login" and self.settings.signup_mode == "disabled":
                    return await self.private_headers(
                        RedirectResponse(PREFIX + "/login", status_code=303)
                    )(scope, receive, send)
                if path == "/verify" and self.settings.signup_mode == "invite":
                    return await self.private_headers(
                        RedirectResponse(PREFIX + "/signup", status_code=303)
                    )(scope, receive, send)
                response = (
                    RedirectResponse(PREFIX + "/", status_code=303)
                    if user
                    else HTMLResponse(
                        auth_page(
                            path[1:],
                            self.settings.google_ready,
                            self.mailer.ready,
                            self.settings.signup_mode,
                        )
                    )
                )
                return await self.private_headers(response)(scope, receive, send)
            if self.admin(request):
                if self.cloud:
                    response = await self.cloud.guard_search(request, self.cloud.owner(request))
                    if response:
                        return await self.private_headers(response)(scope, receive, send)
                if (
                    path in {"/api/search/claim", "/api/search/heartbeat", "/api/outreach/claim"}
                    or path.startswith("/api/import/")
                    or re.fullmatch(r"/api/(?:search|outreach)/jobs/[^/]+/complete", path)
                ):
                    return await self.private_headers(
                        JSONResponse({"detail": "Worker credentials required"}, status_code=403)
                    )(scope, receive, send)
                return await self.delegate_admin(scope, receive, send, browser=True)
            if not user:
                # Retain existing owner worker credentials. New user sessions never
                # inherit this shared owner's legacy dashboard or worker identity.
                if request.headers.get("authorization", "").startswith("Basic "):
                    return await self.legacy(scope, receive, send)
                response = (
                    RedirectResponse(PREFIX + "/login", status_code=303)
                    if path == "/"
                    else JSONResponse({"detail": "Sign in required"}, status_code=401)
                )
                return await self.private_headers(response)(scope, receive, send)
            if (
                path in {"/api/search/claim", "/api/search/heartbeat", "/api/outreach/claim"}
                or path.startswith(("/api/import/", "/api/cloud/"))
                or re.fullmatch(r"/api/(?:search|outreach)/jobs/[^/]+/complete", path)
            ):
                return await self.private_headers(
                    JSONResponse({"detail": "Not found"}, status_code=404)
                )(scope, receive, send)
            if self.cloud:
                response = await self.cloud.guard_search(request, user)
                if response:
                    return await self.private_headers(response)(scope, receive, send)
            return await self.delegate(user, scope, receive, send)
        except (ValidationError, json.JSONDecodeError):
            response = JSONResponse(
                {"detail": "Check the form fields and try again"}, status_code=422
            )
        except ValueError as error:
            response = JSONResponse({"detail": str(error)}, status_code=400)
        return await self.private_headers(response)(scope, receive, send)


def create_access_gateway(legacy, *, root=None, settings=None, google=None, mailer=None):
    settings = settings or AccessSettings.from_environment()
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, AuthLogFilter) for item in logger.filters):
        logger.addFilter(AuthLogFilter())
    gateway = AccessGateway(
        legacy,
        root or Path(os.getenv("HOSTED_DATA_DIR", "/data")),
        settings,
        google=google,
        mailer=mailer,
    )
    wrapped = PublicPathMiddleware(gateway)
    wrapped.gateway = gateway
    return wrapped
