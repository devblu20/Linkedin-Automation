"""Private Unipile connections for the existing allowlisted Google/admin login."""

import json
import secrets
import time
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, RedirectResponse

from linkedin_automation.infrastructure.cloud_accounts import CloudAccounts, fingerprint
from linkedin_automation.infrastructure.cloud_delivery import cloud_sending_enabled
from linkedin_automation.infrastructure.cloud_logging import protect_cloud_access_logs
from linkedin_automation.infrastructure.cloud_search_runner import CloudSearchRunner
from linkedin_automation.infrastructure.unipile_client import (
    UnipileAccountUnavailable,
    UnipileClient,
    UnipileSettings,
)


class LinkedInAccountChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: StrictBool
    profile_url: str = Field(min_length=1, max_length=2048)


class AdminLinkedInTransfer(LinkedInAccountChange):
    source_email: str = Field(default="", max_length=255)


class WebsiteCloud:
    def __init__(self, gateway, root):
        self.gateway = gateway
        self.accounts = CloudAccounts(Path(root) / "website-cloud")
        self.runners = {}
        self.lock = Lock()
        self.connection_checks = {}
        self.cookie = gateway.cookie_name + "_unipile"
        try:
            self.client = UnipileClient(UnipileSettings.environment(gateway.settings.origin))
        except ValueError:
            self.client = None
        with self.accounts.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS website_scopes "
                "(user_id TEXT PRIMARY KEY,scope_id TEXT NOT NULL)"
            )
        protect_cloud_access_logs()

    def owner(self, request):
        user = self.gateway.user(request)
        if user:
            return user
        if self.gateway.admin(request):
            return {
                "id": "admin:" + self.gateway.admin_binding(),
                "name": "Administrator",
                "email": None,
            }
        return None

    def record(self, owner):
        return self.accounts.identity("website:" + owner["id"], owner["name"])

    def connection(self, identifier):
        with self.accounts.db() as db:
            row = db.execute("SELECT * FROM cloud_users WHERE id=?", (identifier,)).fetchone()
        return dict(row) if row else None

    def scope(self, identifier):
        with self.accounts.db() as db:
            row = db.execute(
                "SELECT scope_id FROM website_scopes WHERE user_id=?", (identifier,)
            ).fetchone()
        return row["scope_id"] if row else None

    def private_scope(self, identifier):
        with self.lock:
            value = self.scope(identifier)
            if not value:
                value = self.client.create_scope("bluqq-" + identifier)
                with self.accounts.db() as db:
                    db.execute("INSERT INTO website_scopes VALUES (?,?)", (identifier, value))
            return value

    def allowed(self, owner, identifier):
        if owner["id"].startswith("admin:"):
            valid = secrets.compare_digest(owner["id"][6:], self.gateway.admin_binding())
        else:
            valid = self.gateway.permits(owner["email"])
        record = self.connection(identifier)
        return bool(valid and record and record["status"] == "connected")

    def ensure(self, owner):
        record = self.record(owner)
        if not self.client or record["status"] != "connected":
            return
        with self.lock:
            if record["id"] not in self.runners:
                if owner["id"].startswith("admin:"):
                    app = self.gateway.legacy
                    root = Path(self.gateway.operations.shared_root)
                else:
                    app, _ = self.gateway.tenant(owner)
                    root = self.gateway.store.workspace(owner["id"])
                self.runners[record["id"]] = CloudSearchRunner(
                    self.client,
                    lambda: self.connection(record["id"]),
                    lambda: self.allowed(owner, record["id"]),
                    app,
                    root,
                    scope=self.scope(record["id"]),
                )
            self.runners[record["id"]].ensure()

    def status(self, owner):
        record = self.record(owner)
        return {
            "configured": bool(self.client),
            "connected": record["status"] == "connected",
            "name": record["name"],
            "execution": "cloud",
            "sending_enabled": cloud_sending_enabled(self.client),
            "has_linked_account": bool(record["account_id"]),
            "can_change_account": bool(
                record["account_id"] and self.client and self.client.settings.version == 1
            ),
            "message": "Cloud connector is not configured by the app owner."
            if not self.client
            else "LinkedIn connected. Searches run in the cloud."
            if record["status"] == "connected"
            else "Saved LinkedIn account needs reconnection. "
            "Reconnect it, or choose a different account."
            if record["account_id"]
            else "Connect your own LinkedIn account to start cloud research.",
        }

    def verify_connection(self, record, *, cached=False):
        """Recheck the exact sender; outages do not rewrite a valid account binding."""
        if not record["account_id"] or not self.client:
            return
        key = (record["id"], record["account_id"], record["member_id"])
        if cached and time.monotonic() - self.connection_checks.get(key, -60) < 30:
            return
        try:
            account = self.client.account(record["account_id"], scope_id=self.scope(record["id"]))
            member = account.get("connection_params", {}).get("im", {}).get("id")
            if member != record["member_id"]:
                raise UnipileAccountUnavailable(
                    "The LinkedIn sender changed. Reconnect the intended account before searching."
                )
        except UnipileAccountUnavailable:
            self.connection_checks.pop(key, None)
            with self.accounts.db() as db:
                db.execute(
                    "UPDATE cloud_users SET status='needs_reconnect' "
                    "WHERE id=? AND account_id=? AND member_id=?",
                    key,
                )
            raise
        with self.accounts.db() as db:
            db.execute(
                "UPDATE cloud_users SET status='connected' "
                "WHERE id=? AND account_id=? AND member_id=?",
                key,
            )
        self.connection_checks[key] = time.monotonic()

    def guard_account_change(self, identifier):
        record = self.connection(identifier)
        subject = record["subject"].removeprefix("website:") if record else ""
        if subject.startswith("admin:"):
            valid = secrets.compare_digest(subject[6:], self.gateway.admin_binding())
        else:
            with self.gateway.store.db() as db:
                user = db.execute("SELECT email FROM users WHERE id=?", (subject,)).fetchone()
            valid = bool(user and self.gateway.permits(user["email"]))
        if not valid:
            raise ValueError("Workspace access changed; LinkedIn account was not replaced")
        runner = self.runners.get(identifier)
        if not runner:
            return
        with runner.app.state.delivery_queue.db() as db:
            if db.execute("SELECT 1 FROM delivery_jobs WHERE status='running'").fetchone():
                raise ValueError("Wait for the active delivery to finish before changing LinkedIn")
        with runner.app.state.search_store.connection() as db:
            if db.execute("SELECT 1 FROM search_jobs WHERE status='running'").fetchone():
                raise ValueError("Wait for the active search to finish before changing LinkedIn")

    async def endpoint(self, request):
        path = request.scope["path"]
        owner = self.owner(request)
        if path == "/api/cloud/notify" and request.method == "POST":
            if not self.client or self.client.settings.version != 1:
                return JSONResponse({"detail": "Callback unavailable"}, status_code=503)
            if not secrets.compare_digest(
                request.query_params.get("secret", ""), self.client.settings.callback_secret
            ):
                return JSONResponse({"detail": "Invalid callback"}, status_code=403)
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 65536:
                    return JSONResponse({"detail": "Callback too large"}, status_code=413)
            data = json.loads(raw)
            if data.get("status") not in {"CREATION_SUCCESS", "RECONNECTED"}:
                return JSONResponse({"ok": True})
            state, account_id = data.get("name"), data.get("account_id")
            if (
                not isinstance(state, str)
                or len(state) != 64
                or not isinstance(account_id, str)
                or not 1 <= len(account_id) <= 150
            ):
                raise ValueError("Invalid connection callback")
            account = await run_in_threadpool(self.client.account, account_id)
            change_owner = self.accounts.replacement_owner(state)
            if change_owner:
                self.guard_account_change(change_owner)
            record = self.accounts.connection(state, account)
            # Private owner must still pass the application allowlist on its next request.
            return JSONResponse({"ok": bool(record)})
        if not owner:
            return JSONResponse(
                {"detail": "Sign in with an approved app account first"}, status_code=401
            )
        record = self.record(owner)
        if path == "/api/cloud/account" and request.method == "GET":
            try:
                await run_in_threadpool(self.verify_connection, record, cached=True)
            except UnipileAccountUnavailable:
                return JSONResponse(self.status(owner))
            self.ensure(owner)
            return JSONResponse(self.status(owner))
        if not self.client:
            return JSONResponse(
                {
                    "detail": "The app owner must configure Unipile before cloud connection "
                    "or research can run."
                },
                status_code=503,
            )
        if path in {"/api/cloud/admin-transfer/preview", "/api/cloud/admin-transfer"}:
            if not self.gateway.admin(request) or not owner["id"].startswith("admin:"):
                return JSONResponse(
                    {"detail": "Shared administrator access required"}, status_code=403
                )
            if request.method != "POST":
                return JSONResponse({"detail": "Method not allowed"}, status_code=405)
            values = await self.gateway.payload(request, AdminLinkedInTransfer)
            if not values.confirmed:
                raise ValueError("Explicit confirmation of this sender transfer is required")
            from linkedin_automation.infrastructure.admin_account_transfer import (
                AdminAccountTransfer,
            )

            service = AdminAccountTransfer(self)
            plan = await run_in_threadpool(
                service.plan, owner, values.source_email, values.profile_url
            )
            if path.endswith("/preview"):
                return JSONResponse(service.preview(plan))
            if not self.gateway.store.rate(
                "admin-cloud-transfer:" + owner["id"], maximum=3, seconds=600
            ):
                return JSONResponse(
                    {"detail": "Wait before retrying this transfer"}, status_code=429
                )
            result = await run_in_threadpool(service.commit, plan)
            # Do not start delivery or search as a side effect of an owner transfer.
            return JSONResponse(result)
        if path in {"/api/cloud/connect", "/api/cloud/change-account"} and request.method == "POST":
            if not self.gateway.store.rate("cloud-connect:" + owner["id"], maximum=6, seconds=600):
                return JSONResponse(
                    {"detail": "Too many connection requests. Retry in ten minutes."},
                    status_code=429,
                )
            changing = path.endswith("/change-account")
            if changing and self.client.settings.version != 1:
                return JSONResponse(
                    {"detail": "Account replacement currently requires Unipile V1"}, status_code=409
                )
            scope = (
                await run_in_threadpool(self.private_scope, record["id"])
                if self.client.settings.version == 2
                else None
            )
            if changing:
                values = await self.gateway.payload(request, LinkedInAccountChange)
                if not values.confirmed:
                    raise ValueError(
                        "Confirm the new sender and preservation of old authorizations"
                    )
                self.guard_account_change(record["id"])
                state = self.accounts.replacement_flow(
                    record["id"], record["account_id"], values.profile_url
                )
            else:
                state = self.accounts.flow("connection", record["id"])
            link = await run_in_threadpool(
                self.client.hosted_link,
                state,
                None if changing else record["account_id"],
                scope_id=scope,
            )
            response = JSONResponse({"url": link})
            self.gateway.cookie(response, self.cookie, state, age=600)
            return response
        if path == "/api/cloud/return" and request.method == "GET":
            if self.client.settings.version == 2:
                state = request.query_params.get("flow", "")
                if len(state) != 64 or not secrets.compare_digest(
                    state, request.cookies.get(self.cookie, "")
                ):
                    raise ValueError("LinkedIn connection did not match this signed-in browser")
                with self.accounts.db() as db:
                    flow = db.execute(
                        "SELECT * FROM cloud_flows WHERE state_hash=?", (fingerprint(state),)
                    ).fetchone()
                if (
                    not flow
                    or flow["user_id"] != record["id"]
                    or flow["consumed"]
                    or flow["expires"] < time.time()
                ):
                    raise ValueError("LinkedIn connection expired or belongs to another workspace")
                if request.query_params.get("error_type"):
                    raise ValueError(
                        "LinkedIn connection was not completed. Return and connect again."
                    )
                account_id = request.query_params.get("account_id", "")
                if not 1 <= len(account_id) <= 150:
                    raise ValueError("LinkedIn did not return a connection")
                account = await run_in_threadpool(
                    self.client.account, account_id, scope_id=self.scope(record["id"])
                )
                self.accounts.connection(state, account)
            self.ensure(owner)
            response = RedirectResponse("/linkedin-automation/", status_code=303)
            response.delete_cookie(self.cookie, path="/linkedin-automation/")
            return response
        if path == "/api/cloud/refresh" and request.method == "POST":
            if not self.gateway.store.rate("cloud-refresh:" + owner["id"], maximum=12, seconds=600):
                return JSONResponse(
                    {"detail": "Please wait before checking again"}, status_code=429
                )
            if record["account_id"]:
                try:
                    await run_in_threadpool(self.verify_connection, record)
                except UnipileAccountUnavailable:
                    return JSONResponse(self.status(owner))
            self.ensure(owner)
            return JSONResponse(self.status(owner))
        return JSONResponse({"detail": "Not found"}, status_code=404)

    async def guard_search(self, request, owner):
        if request.method == "POST" and request.scope["path"] in {"/api/search/jobs"}:
            if not self.client:
                return JSONResponse(
                    {"detail": "Unipile is not configured by the app owner."}, status_code=503
                )
            if self.record(owner)["status"] != "connected":
                return JSONResponse(
                    {"detail": "Connect your LinkedIn account first."}, status_code=409
                )
            # Verify provider access before queueing, not just stale local callback state.
            record = self.record(owner)
            try:
                await run_in_threadpool(self.verify_connection, record)
            except UnipileAccountUnavailable as error:
                return JSONResponse({"detail": str(error)}, status_code=409)
            self.ensure(owner)
        path = request.scope["path"]
        if path.startswith(("/api/outreach/", "/api/enhancements/")):
            if request.method == "POST" and path.endswith("/authorize"):
                if not cloud_sending_enabled(self.client):
                    return JSONResponse(
                        {"detail": "Cloud sending is not enabled by the app owner."},
                        status_code=503,
                    )
                record = self.record(owner)
                if record["status"] != "connected":
                    return JSONResponse(
                        {"detail": "Connect LinkedIn before enabling delivery."}, status_code=409
                    )
                try:
                    await run_in_threadpool(self.verify_connection, record)
                except UnipileAccountUnavailable as error:
                    return JSONResponse({"detail": str(error)}, status_code=409)
            self.ensure(owner)
        return None

    def restore(self):
        """Restore only connected, still-authorized workspaces after a server restart."""
        if not self.client:
            return
        with self.accounts.db() as db:
            records = db.execute(
                "SELECT subject FROM cloud_users WHERE status='connected'"
            ).fetchall()
        for record in records:
            identifier = record["subject"].removeprefix("website:")
            if identifier == "admin:" + self.gateway.admin_binding():
                owner = {"id": identifier, "name": "Administrator", "email": None}
            else:
                with self.gateway.store.db() as db:
                    row = db.execute(
                        "SELECT id,name,email FROM users WHERE id=?", (identifier,)
                    ).fetchone()
                if not row or not self.gateway.permits(row["email"]):
                    continue
                owner = dict(row)
            self.ensure(owner)
