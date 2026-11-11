"""Isolated, authenticated Railway review dashboard; no remote browser actions."""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from linkedin_automation.application.research_transfer import prepare_research_transfer
from linkedin_automation.application.run_transfer import (
    MAX_TRANSFER_BYTES,
    RunTransfer,
    prepare_transfer,
)
from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository
from linkedin_automation.interfaces.review_site import create_review_app

_BROWSER_ACTIONS = {
    "/api/actions/send-connections",
    "/api/actions/check-connections",
    "/api/actions/send-messages",
    "/api/actions/queue",
}


def create_hosted_app(
    *,
    data_root: Path | None = None,
    access_credentials: tuple[str, str] | None = None,
    worker_mode: str = "hosted",
) -> FastAPI:
    """Fail closed without credentials and use only this service's volume database."""
    username, password = access_credentials or (
        os.environ.get("DASHBOARD_USERNAME", "").strip(),
        os.environ.get("DASHBOARD_PASSWORD", ""),
    )
    if not username or ":" in username or len(password) < 24:
        raise RuntimeError(
            "Set DASHBOARD_USERNAME and a DASHBOARD_PASSWORD of at least 24 characters"
        )

    root = (data_root or Path(os.environ.get("HOSTED_DATA_DIR", "/data"))).resolve()
    public_origin = os.getenv("DASHBOARD_PUBLIC_ORIGIN", "").rstrip("/")
    if public_origin:
        parsed = urlsplit(public_origin)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise RuntimeError("DASHBOARD_PUBLIC_ORIGIN must be an HTTPS origin without a path")
    root.mkdir(parents=True, exist_ok=True)
    database = root / "research.sqlite3"
    # Per-instance paths must not mutate process-wide environment in multi-user mode.
    from linkedin_automation.infrastructure.runtime import RuntimeSettings

    settings = replace(
        RuntimeSettings.from_environment(),
        database_path=database,
        database_url=f"sqlite:///{database.as_posix()}",
        artifact_dir=root / "artifacts",
        browser_data_dir=root / "browser-data",
        screenshot_dir=root / "screenshots",
    )
    app = create_review_app(database, review_only=True, runtime_settings=settings)
    from linkedin_automation.interfaces.search_jobs import install_hosted_search

    install_hosted_search(app, root, Path("config"), mode=worker_mode)
    from linkedin_automation.interfaces.outreach_delivery import install_delivery

    delivery_repository = DatabaseResearchRepository(database)
    delivery = install_delivery(app, root, delivery_repository)
    from linkedin_automation.interfaces.engagement import install_engagement

    install_engagement(app, delivery_repository, delivery.engagement)
    if (
        os.getenv("PERSONAL_BROWSER_MODE") == "1"
        and os.getenv("WEBSITE_CLOUD_MODE") != "1"
        and data_root is None
    ):
        from linkedin_automation.interfaces.browser_engagement import install_browser_engagement

        install_browser_engagement(app, delivery, delivery_repository, delivery.engagement)

    @app.post("/api/import/runs")
    @app.post("/api/import/research")
    async def import_run(request: Request, dry_run: bool = False):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_TRANSFER_BYTES:
                return JSONResponse({"detail": "Import exceeds the 2 MiB limit"}, status_code=413)
        try:
            payload = RunTransfer.model_validate_json(bytes(body))
            research = request.url.path == "/api/import/research"
            if research:
                run, rows, observations = prepare_research_transfer(
                    payload, automatic=os.getenv("AUTOMATIC_QUALIFICATION", "0") == "1"
                )
            else:
                run, rows = prepare_transfer(payload)
                observations = None
            if dry_run:
                return {
                    "run_id": str(run.id),
                    "validated": len(observations) if research else len(rows),
                    "dry_run": True,
                }
            repository = DatabaseResearchRepository(database)
            try:
                inserted, skipped = repository.import_review_run(
                    run, rows, observations=observations
                )
                if research and os.getenv("AUTOMATIC_QUALIFICATION", "0") == "1":
                    repository.enrich_unreviewed_profiles(rows)
                if os.getenv("AUTOMATIC_QUEUE", "0") == "1":
                    delivery.prepare()
            finally:
                repository.close()
            result = {"run_id": str(run.id), "inserted": inserted, "skipped": skipped}
            if research:
                result.update(
                    observed=len(observations),
                    accepted=len(rows),
                    rejected=len(observations) - len(rows),
                )
            return result
        except ValidationError as error:
            return JSONResponse(
                {"detail": json.loads(error.json(include_input=False, include_context=False))},
                status_code=422,
            )
        except (ValueError, KeyError):
            return JSONResponse(
                {"detail": "Invalid profiles or campaign settings"}, status_code=422
            )
        except IntegrityError:
            return JSONResponse(
                {"detail": "Conflicting import; retry the same run"}, status_code=409
            )

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "review-dashboard"}

    @app.middleware("http")
    async def hosted_boundary(request: Request, call_next):
        if request.url.path == "/healthz" and request.method == "GET":
            return await call_next(request)
        authenticated = False
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Basic "):
            try:
                decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
                supplied_user, separator, supplied_password = decoded.partition(":")
                user_ok = secrets.compare_digest(supplied_user.encode(), username.encode())
                password_ok = secrets.compare_digest(supplied_password.encode(), password.encode())
                authenticated = bool(separator) and user_ok and password_ok
            except (ValueError, UnicodeError, binascii.Error):
                pass
        if not authenticated:
            return JSONResponse(
                {"detail": "Sign in to LinkedIn Automation"},
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="LinkedIn Automation", charset="UTF-8"',
                    "Cache-Control": "no-store",
                },
            )
        if request.url.path in _BROWSER_ACTIONS:
            return JSONResponse(
                {"detail": "Browser outreach is unavailable on the hosted review dashboard."},
                status_code=409,
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            public_request = bool(request.scope.get("state", {}).get("dashboard_prefix"))
            trusted_public_origin = public_request and public_origin and origin == public_origin
            if (
                request.headers.get("sec-fetch-site") == "cross-site"
                or (
                    origin
                    and urlsplit(origin).netloc != request.url.netloc
                    and not trusted_public_origin
                )
                or not request.headers.get("content-type", "").startswith("application/json")
            ):
                return JSONResponse(
                    {"detail": "Invalid request origin or content type"}, status_code=403
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path == "/" and response.status_code == 200:
            body = b"".join([chunk async for chunk in response.body_iterator]).decode("utf-8")
            # Keep the local dashboard intact; hosted mode has no collection/send controls.
            start = body.index('<button class="btn" onclick="checkConnections()">')
            end = body.index('</div><div class="table-wrap">', start)
            body = body[:start] + body[end:]
            from linkedin_automation.interfaces.search_ui import SEARCH_PANEL, SEARCH_SCRIPT

            search_panel, search_script = SEARCH_PANEL, SEARCH_SCRIPT
            from linkedin_automation.interfaces.public_discovery_ui import PANEL as PUBLIC_PANEL
            from linkedin_automation.interfaces.public_discovery_ui import SCRIPT as PUBLIC_SCRIPT

            search_panel += PUBLIC_PANEL
            search_script += PUBLIC_SCRIPT
            website_cloud = request.scope.get("state", {}).get("website_cloud", False)
            if website_cloud:
                from linkedin_automation.interfaces.website_cloud_ui import PANEL, SCRIPT
                from linkedin_automation.interfaces.worker_controls import (
                    WORKER_PANEL,
                    WORKER_SCRIPT,
                )

                search_panel = PANEL + search_panel.replace(WORKER_PANEL, "")
                search_script = search_script.replace(WORKER_SCRIPT, "") + SCRIPT
            body = body.replace("<main>", "<main>" + search_panel, 1)
            body = body.replace("</body>", search_script + "</body>")
            from linkedin_automation.interfaces.delivery_ui import DELIVERY_SCRIPT

            body = body.replace("</body>", DELIVERY_SCRIPT + "</body>")
            from linkedin_automation.interfaces.engagement_ui import ENGAGEMENT_SCRIPT

            body = body.replace("</body>", ENGAGEMENT_SCRIPT + "</body>")
            body = body.replace("Neon connected", "Hosted dashboard")
            if request.scope.get("state", {}).get("website_admin"):
                from linkedin_automation.interfaces.access_ui import ADMIN_PANEL, ADMIN_SCRIPT

                admin_panel = ADMIN_PANEL
                if website_cloud:
                    admin_panel = admin_panel.replace(
                        'For shared admin searches, '
                        '<a href="/linkedin-automation/connect-computer">'
                        "connect this computer</a>.",
                        "Connect LinkedIn below for cloud research in the shared admin workspace.",
                    )
                if not request.scope.get("state", {}).get("device_required", False):
                    admin_panel = admin_panel.replace(
                        "For shared admin searches, "
                        '<a href="/linkedin-automation/connect-computer">'
                        "connect this computer</a>.",
                        "Searches use the existing configured admin worker. "
                        "Keep that computer awake and its worker running.",
                    )
                body = body.replace("<main>", "<main>" + admin_panel, 1)
                body = body.replace("</body>", ADMIN_SCRIPT + "</body>")
            if getattr(app.state, "website_user", None):
                from linkedin_automation.interfaces.access_ui import ACCOUNT_PANEL, ACCOUNT_SCRIPT

                body = body.replace("<main>", "<main>" + ACCOUNT_PANEL, 1)
                body = body.replace("</body>", ACCOUNT_SCRIPT + "</body>")
                body = body.replace("Hosted dashboard", "Private workspace")
            if worker_mode == "cloud" and not website_cloud:
                from linkedin_automation.interfaces.cloud_ui import ACCOUNT_PANEL, ACCOUNT_SCRIPT

                body = body.replace("<main>", "<main>" + ACCOUNT_PANEL, 1)
                body = body.replace("</body>", ACCOUNT_SCRIPT + "</body>")
                body = body.replace("Hosted dashboard", "Private cloud workspace")
            body = body.replace(
                "LinkedIn sending remains manual.",
                "Automatic qualification. Review and enable queued drafts for delivery.",
            )
            body = body.replace(
                "Review evidence, edit message drafts and copy approved drafts for manual sending. "
                "Approval does not send a message.",
                "Review profile qualification and drafts. Eligible profiles can be auto-approved "
                "and queued; enable the named delivery below to start sending.",
            )
            body = body.replace("London client pipeline", "LinkedIn research pipeline")
            body = body.replace("All Neon campaigns", "Hosted review · separate database")
            body = body.replace("Loading Neon leads", "Loading leads")
            body = body.replace(
                "No qualified clients found in Neon.",
                "No matching leads saved yet. Choose a campaign and start a search.",
            )
            body = body.replace("Changes are saved to Neon", "Changes are saved to this dashboard")
            body = body.replace("Client updated in Neon", "Client updated")
            body = body.replace("Could not load Neon data", "Could not load dashboard data")
            body = body.replace(
                "<script>",
                """<script>
function escapeHtml(v){
const entities={'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'};
return String(v??'').replace(/[&<>\"']/g,c=>entities[c]);
}
""",
            )
            for old, new in {
                "${initials(l.full_name)}": "${escapeHtml(initials(l.full_name))}",
                "${l.full_name}": "${escapeHtml(l.full_name)}",
                "${l.profile_url}": "${escapeHtml(l.profile_url)}",
                "${l.current_title||l.headline||'Title unavailable'}": (
                    "${escapeHtml(l.current_title||l.headline||'Title unavailable')}"
                ),
                "${l.company||'Unknown company'}": "${escapeHtml(l.company||'Unknown company')}",
                "${l.location||'Location unavailable'}": (
                    "${escapeHtml(l.location||'Location unavailable')}"
                ),
            }.items():
                body = body.replace(old, new)
            prefix = request.scope.get("state", {}).get("dashboard_prefix", "")
            if prefix:
                # All hosted API links, fetches and downloads share this path. Do not
                # claim the main site's /api routes or rewrite external profile URLs.
                body = body.replace("/api/", prefix + "/api/")
            response = HTMLResponse(
                body,
                headers={
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() not in {"content-length", "content-type"}
                },
            )
        return response

    from linkedin_automation.interfaces.public_path import PublicPathMiddleware

    app.add_middleware(PublicPathMiddleware)
    return app


def main() -> None:
    import uvicorn

    app = create_hosted_app()
    if os.getenv("WEBSITE_AUTH_MODE") == "1":
        from linkedin_automation.interfaces.access_gateway import create_access_gateway

        app = create_access_gateway(app)
    elif os.getenv("CLOUD_ACCOUNT_MODE") == "1":
        from linkedin_automation.interfaces.cloud_gateway import create_cloud_gateway

        app = create_cloud_gateway(app)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
