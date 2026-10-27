"""Isolated, authenticated Railway review dashboard; no remote browser actions."""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
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


def create_hosted_app() -> FastAPI:
    """Fail closed without credentials and use only this service's volume database."""
    username = os.environ.get("DASHBOARD_USERNAME", "").strip()
    password = os.environ.get("DASHBOARD_PASSWORD", "")
    if not username or ":" in username or len(password) < 24:
        raise RuntimeError(
            "Set DASHBOARD_USERNAME and a DASHBOARD_PASSWORD of at least 24 characters"
        )

    root = Path(os.environ.get("HOSTED_DATA_DIR", "/data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    database = root / "research.sqlite3"
    # Never inherit or connect to an existing application's database or browser profile.
    os.environ["LINKEDIN_AUTOMATION_DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    os.environ["LINKEDIN_AUTOMATION_ARTIFACT_DIR"] = str(root / "artifacts")
    os.environ["LINKEDIN_AUTOMATION_BROWSER_DATA"] = str(root / "browser-data")
    os.environ["LINKEDIN_AUTOMATION_SCREENSHOT_DIR"] = str(root / "screenshots")
    app = create_review_app(database, review_only=True)
    from linkedin_automation.interfaces.search_jobs import install_hosted_search

    install_hosted_search(app, root, Path("config"))

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
                run, rows, observations = prepare_research_transfer(payload)
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
            if (
                request.headers.get("sec-fetch-site") == "cross-site"
                or (origin and urlsplit(origin).netloc != request.url.netloc)
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

            body = body.replace("<main>", "<main>" + SEARCH_PANEL, 1)
            body = body.replace("</body>", SEARCH_SCRIPT + "</body>")
            body = body.replace("Neon connected", "Hosted dashboard")
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
            response = HTMLResponse(
                body,
                headers={
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() not in {"content-length", "content-type"}
                },
            )
        return response

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_hosted_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
