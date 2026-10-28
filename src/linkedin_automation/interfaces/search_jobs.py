"""Persistent search requests for the hosted dashboard and local browser worker."""

import json
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from linkedin_automation.application.campaigns import CampaignPolicy, definition_hash
from linkedin_automation.application.errors import ConfigurationError
from linkedin_automation.infrastructure.campaign_catalog import CampaignCatalog
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign: str = Field(min_length=1, max_length=150)
    max_results: int = Field(default=5, ge=1, le=25)
    max_pages: int = Field(default=3, ge=1, le=5)


class SearchCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=48, max_length=48)
    status: str = Field(pattern="^(completed|failed)$")
    run_id: UUID | None = None
    observed: int = Field(default=0, ge=0, le=25)
    accepted: int = Field(default=0, ge=0, le=25)
    rejected: int = Field(default=0, ge=0, le=25)
    pages_visited: int = Field(default=0, ge=0, le=5)
    hidden_result_cards: int = Field(default=0, ge=0, le=1000)
    rejection_counts: dict[str, int] = Field(default_factory=dict, max_length=10)
    message: str = Field(default="", max_length=500)
    resume_available: bool = False


def configured_campaigns(root: Path) -> dict:
    result = {}
    loader = YamlSearchDefinitionLoader()
    root = root.resolve()
    for path in sorted(root.glob("*.yaml")):
        if path.resolve().parent != root or path.name.endswith(".local.yaml"):
            continue
        try:
            definition = loader.load(path)
            if definition.version == 2:
                result[path.name] = definition
        except ConfigurationError:
            continue
    return result


def campaign_options(root: Path) -> list[dict]:
    options = []
    for key, definition in configured_campaigns(root).items():
        search = definition.search
        keywords = ", ".join(search.keywords.include[:3]) or "Campaign keywords"
        locations = ", ".join(search.locations) or "Any location"
        titles = ", ".join(search.titles.include[:3]) or "Any title"
        options.append(
            {
                "id": key,
                "name": definition.name,
                "criteria": f"Search: {keywords} · Location: {locations} · Titles: {titles}",
            }
        )
    return options


class SearchJobs:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS search_jobs (
                id TEXT PRIMARY KEY, campaign TEXT NOT NULL, name TEXT NOT NULL,
                definition TEXT NOT NULL, base_hash TEXT NOT NULL,
                status TEXT NOT NULL, token TEXT, created REAL NOT NULL,
                started REAL, finished REAL, result TEXT)""")
            db.execute(
                "CREATE TABLE IF NOT EXISTS browser_worker (id INTEGER PRIMARY KEY, seen REAL)"
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(search_jobs)")}
            if "policy" not in columns:
                db.execute("ALTER TABLE search_jobs ADD COLUMN policy TEXT")
            if "completion_hash" not in columns:
                db.execute("ALTER TABLE search_jobs ADD COLUMN completion_hash TEXT")
            if "checkpoint_id" not in columns:
                db.execute("ALTER TABLE search_jobs ADD COLUMN checkpoint_id TEXT")

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def enqueue(
        self, payload: SearchRequest, campaigns: dict, *, policy: CampaignPolicy | None = None
    ) -> dict:
        original = campaigns.get(payload.campaign)
        if original is None:
            raise ValueError("Choose a configured campaign")
        if (
            payload.max_results > original.limits.max_results
            or payload.max_pages > original.limits.max_pages
        ):
            raise ValueError("Requested search exceeds this campaign's limits")
        definition = original.model_copy(
            update={
                "limits": original.limits.model_copy(
                    update={
                        "max_results": payload.max_results,
                        "max_pages": payload.max_pages,
                    }
                )
            }
        )
        identifier = str(uuid4())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.expire(db)
            if db.execute(
                "SELECT 1 FROM search_jobs WHERE status IN ('queued','running')"
            ).fetchone():
                raise ValueError("A search is already queued or running; wait for it to finish")
            db.execute(
                "INSERT INTO search_jobs "
                "(id,campaign,name,definition,base_hash,status,created,policy) "
                "VALUES (?,?,?,?,?,'queued',?,?)",
                (
                    identifier,
                    payload.campaign,
                    original.name,
                    definition.model_dump_json(),
                    policy.template_hash if policy else definition_hash(original),
                    time.time(),
                    policy.model_dump_json() if policy else None,
                ),
            )
        return {
            "id": identifier,
            "status": "queued",
            "campaign": original.name,
            "requested": {"profiles": payload.max_results, "pages": payload.max_pages},
        }

    def expire(self, db):
        cutoff = time.time() - 1800
        db.execute(
            "UPDATE search_jobs SET status='failed', finished=?, token=NULL, result=? "
            "WHERE status='running' AND started < ?",
            (
                time.time(),
                json.dumps(
                    {
                        "message": "Worker search expired. Resume on the original "
                        "worker computer if a checkpoint exists.",
                        "resume_available": True,
                    }
                ),
                cutoff,
            ),
        )
        db.execute(
            "UPDATE search_jobs SET status='failed', finished=?, result=? "
            "WHERE status IN ('queued','running') AND COALESCE(started,created) < ?",
            (
                time.time(),
                json.dumps(
                    {"message": "Search expired. Start the local browser worker and try again."}
                ),
                cutoff,
            ),
        )

    def claim(self) -> dict | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR REPLACE INTO browser_worker (id,seen) VALUES (1,?)", (time.time(),)
            )
            self.expire(db)
            if db.execute("SELECT 1 FROM search_jobs WHERE status='running'").fetchone():
                return None
            row = db.execute(
                "SELECT * FROM search_jobs WHERE status='queued' ORDER BY created LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            token = secrets.token_hex(24)
            db.execute(
                "UPDATE search_jobs SET status='running',token=?,started=? WHERE id=?",
                (token, time.time(), row["id"]),
            )
            return {
                "id": row["id"],
                "campaign": row["campaign"],
                "base_hash": row["base_hash"],
                "definition": json.loads(row["definition"]),
                "token": token,
                "policy": json.loads(row["policy"]) if row["policy"] else None,
                "checkpoint_id": row["checkpoint_id"] or row["id"],
            }

    def resume(self, identifier: UUID) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.expire(db)
            row = db.execute("SELECT * FROM search_jobs WHERE id=?", (str(identifier),)).fetchone()
            if (
                row is None
                or row["status"] != "failed"
                or not json.loads(row["result"] or "{}").get("resume_available")
            ):
                raise ValueError("This search has no resumable interruption")
            if db.execute(
                "SELECT 1 FROM search_jobs WHERE status IN ('queued','running')"
            ).fetchone():
                raise ValueError("Another search is queued or running")
            new_id = str(uuid4())
            previous_result = json.loads(row["result"] or "{}")
            previous_result["resume_available"] = False
            db.execute(
                "UPDATE search_jobs SET result=? WHERE id=?",
                (json.dumps(previous_result), str(identifier)),
            )
            db.execute(
                "INSERT INTO search_jobs "
                "(id,campaign,name,definition,base_hash,status,created,policy,checkpoint_id) "
                "VALUES (?,?,?,?,?,'queued',?,?,?)",
                (
                    new_id,
                    row["campaign"],
                    row["name"],
                    row["definition"],
                    row["base_hash"],
                    time.time(),
                    row["policy"],
                    row["checkpoint_id"] or row["id"],
                ),
            )
            limits = json.loads(row["definition"])["limits"]
            return {
                "id": new_id,
                "status": "queued",
                "campaign": row["name"],
                "requested": {"profiles": limits["max_results"], "pages": limits["max_pages"]},
            }

    def complete(self, identifier: UUID, result: SearchCompletion) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM search_jobs WHERE id=?", (str(identifier),)).fetchone()
            values = result.model_dump(mode="json", exclude={"token"})
            fingerprint = sha256(result.model_dump_json().encode()).hexdigest()
            if (
                row is not None
                and row["status"] in {"completed", "failed"}
                and secrets.compare_digest(row["completion_hash"] or "", fingerprint)
            ):
                # A response can be lost after commit. Only an identical authenticated
                # completion may be acknowledged again; historical reviews stay intact.
                return
            if (
                row is None
                or row["status"] != "running"
                or not secrets.compare_digest(row["token"] or "", result.token)
            ):
                raise ValueError("Search is no longer running or claim token is invalid")
            limits = json.loads(row["definition"])["limits"]
            if (
                result.observed > limits["max_results"]
                or result.accepted + result.rejected > result.observed
                or result.pages_visited > limits["max_pages"]
                or any(
                    len(key) > 80 or value < 0 or value > 25
                    for key, value in result.rejection_counts.items()
                )
            ):
                raise ValueError("Search counters exceed the requested limits")
            db.execute(
                "UPDATE search_jobs SET status=?,finished=?,result=?,token=NULL,"
                "completion_hash=? WHERE id=?",
                (result.status, time.time(), json.dumps(values), fingerprint, str(identifier)),
            )

    def status(self) -> dict:
        with self.connection() as db:
            self.expire(db)
            worker = db.execute("SELECT seen FROM browser_worker WHERE id=1").fetchone()
            rows = db.execute(
                "SELECT id,name,status,created,definition,result FROM search_jobs "
                "ORDER BY created DESC LIMIT 10"
            ).fetchall()
            return {
                "worker_online": bool(worker and time.time() - worker["seen"] < 45),
                "jobs": [
                    {
                        "id": row["id"],
                        "campaign": row["name"],
                        "status": row["status"],
                        "created": row["created"],
                        "result": json.loads(row["result"]) if row["result"] else None,
                        "requested": {
                            "profiles": json.loads(row["definition"])["limits"]["max_results"],
                            "pages": json.loads(row["definition"])["limits"]["max_pages"],
                        },
                    }
                    for row in rows
                ],
            }


def install_hosted_search(app: FastAPI, root: Path, config_dir: Path) -> None:
    store = SearchJobs(root / "search-jobs.sqlite3")
    catalog = CampaignCatalog(root / "campaigns.sqlite3", configured_campaigns(config_dir))
    from linkedin_automation.interfaces.campaign_editor import install_campaign_routes

    install_campaign_routes(app, catalog)

    @app.get("/api/search/settings")
    def settings():
        return {
            "mode": "hosted",
            "campaigns": catalog.options(),
            **store.status(),
        }

    @app.post("/api/search/jobs")
    def enqueue(payload: SearchRequest):
        try:
            definition, policy = catalog.resolve(payload.campaign)
            return store.enqueue(payload, {payload.campaign: definition}, policy=policy)
        except KeyError:
            raise HTTPException(422, "Choose a saved campaign") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/search/claim")
    def claim():
        return {"job": store.claim()}

    @app.post("/api/search/heartbeat")
    def heartbeat():
        with store.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO browser_worker (id,seen) VALUES (1,?)", (time.time(),)
            )
        return {"ok": True}

    @app.post("/api/search/jobs/{identifier}/complete")
    def complete(identifier: UUID, payload: SearchCompletion):
        try:
            store.complete(identifier, payload)
            return {"ok": True}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/search/jobs/{identifier}/resume")
    def resume(identifier: UUID):
        try:
            return store.resume(identifier)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
