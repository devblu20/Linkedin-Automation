"""Persistent search requests for the hosted dashboard and local browser worker."""

import json
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from linkedin_automation.application.campaigns import CampaignPolicy, definition_hash
from linkedin_automation.application.errors import ConfigurationError
from linkedin_automation.infrastructure.campaign_catalog import CampaignCatalog
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign: str = Field(min_length=1, max_length=150)
    max_results: int = Field(default=5, ge=1, le=100)
    max_pages: int = Field(default=3, ge=1, le=5)
    profile_url: str | None = Field(default=None, max_length=2048)


class WorkerHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    managed: bool = False
    autostart: bool = False
    linkedin_paused: bool = False


class SearchCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=48, max_length=48)
    status: str = Field(pattern="^(completed|failed)$")
    run_id: UUID | None = None
    observed: int = Field(default=0, ge=0, le=100)
    accepted: int = Field(default=0, ge=0, le=100)
    rejected: int = Field(default=0, ge=0, le=100)
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
            worker_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(browser_worker)")
            }
            if "metadata" not in worker_columns:
                db.execute(
                    "ALTER TABLE browser_worker ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
                )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(search_jobs)")}
            if "policy" not in columns:
                db.execute("ALTER TABLE search_jobs ADD COLUMN policy TEXT")
            if "completion_hash" not in columns:
                db.execute("ALTER TABLE search_jobs ADD COLUMN completion_hash TEXT")
            if "checkpoint_id" not in columns:
                db.execute("ALTER TABLE search_jobs ADD COLUMN checkpoint_id TEXT")
            if "worker_device" not in columns:
                db.execute("ALTER TABLE search_jobs ADD COLUMN worker_device TEXT")
            if "source_profile_url" not in columns:
                db.execute("ALTER TABLE search_jobs ADD COLUMN source_profile_url TEXT")
            db.execute(
                "CREATE TABLE IF NOT EXISTS desktop_heartbeats "
                "(device TEXT PRIMARY KEY,seen REAL,metadata TEXT)"
            )

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def enqueue(
        self,
        payload: SearchRequest,
        campaigns: dict,
        *,
        policy: CampaignPolicy | None = None,
        device: str | None = None,
    ) -> dict:
        original = campaigns.get(payload.campaign)
        if original is None:
            raise ValueError("Choose a configured campaign")
        source_profile_url = None
        if payload.profile_url:
            if not policy or policy.spec.template_id != "zz-controlled-queue-test.yaml":
                raise ValueError(
                    "Known-profile lookup is available only for a saved controlled-test campaign"
                )
            from linkedin_automation.domain.services import canonicalize_linkedin_profile_url

            source_profile_url = canonicalize_linkedin_profile_url(payload.profile_url)
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
                "(id,campaign,name,definition,base_hash,status,created,policy,"
                "worker_device,source_profile_url) "
                "VALUES (?,?,?,?,?,'queued',?,?,?,?)",
                (
                    identifier,
                    payload.campaign,
                    original.name,
                    definition.model_dump_json(),
                    policy.template_hash if policy else definition_hash(original),
                    time.time(),
                    policy.model_dump_json() if policy else None,
                    device,
                    source_profile_url,
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

    def claim(self, heartbeat: WorkerHeartbeat | None = None, *, device=None) -> dict | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.record_heartbeat(db, heartbeat, device=device)
            self.expire(db)
            if db.execute("SELECT 1 FROM search_jobs WHERE status='running'").fetchone():
                return None
            if (
                db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='delivery_jobs'"
                ).fetchone()
                and db.execute("SELECT 1 FROM delivery_jobs WHERE status='running'").fetchone()
            ):
                return None
            row = db.execute(
                "SELECT * FROM search_jobs WHERE status='queued' AND worker_device IS ? "
                "ORDER BY created LIMIT 1",
                (device,),
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
                "source_profile_url": row["source_profile_url"],
            }

    def resume(self, identifier: UUID, *, device=None) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.expire(db)
            row = db.execute("SELECT * FROM search_jobs WHERE id=?", (str(identifier),)).fetchone()
            if (
                row is None
                or row["worker_device"] != device
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
                "(id,campaign,name,definition,base_hash,status,created,policy,"
                "checkpoint_id,worker_device,source_profile_url) "
                "VALUES (?,?,?,?,?,'queued',?,?,?,?,?)",
                (
                    new_id,
                    row["campaign"],
                    row["name"],
                    row["definition"],
                    row["base_hash"],
                    time.time(),
                    row["policy"],
                    row["checkpoint_id"] or row["id"],
                    device,
                    row["source_profile_url"],
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
                    len(key) > 80 or value < 0 or value > limits["max_results"]
                    for key, value in result.rejection_counts.items()
                )
            ):
                raise ValueError("Search counters exceed the requested limits")
            db.execute(
                "UPDATE search_jobs SET status=?,finished=?,result=?,token=NULL,"
                "completion_hash=? WHERE id=?",
                (result.status, time.time(), json.dumps(values), fingerprint, str(identifier)),
            )

    @staticmethod
    def record_heartbeat(db, heartbeat: WorkerHeartbeat | None = None, *, device=None):
        if device:
            db.execute(
                "INSERT INTO desktop_heartbeats VALUES (?,?,?) ON CONFLICT(device) "
                "DO UPDATE SET seen=excluded.seen,metadata=excluded.metadata",
                (device, time.time(), (heartbeat or WorkerHeartbeat()).model_dump_json()),
            )
            return
        if heartbeat is None:
            db.execute(
                "INSERT INTO browser_worker (id,seen) VALUES (1,?) "
                "ON CONFLICT(id) DO UPDATE SET seen=excluded.seen",
                (time.time(),),
            )
        else:
            db.execute(
                "INSERT INTO browser_worker (id,seen,metadata) VALUES (1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET seen=excluded.seen,metadata=excluded.metadata",
                (time.time(), heartbeat.model_dump_json()),
            )

    def status(self, *, device=None) -> dict:
        with self.connection() as db:
            self.expire(db)
            worker = (
                db.execute(
                    "SELECT seen,metadata FROM desktop_heartbeats WHERE device=?", (device,)
                ).fetchone()
                if device
                else db.execute("SELECT seen,metadata FROM browser_worker WHERE id=1").fetchone()
            )
            metadata = json.loads(worker["metadata"]) if worker else {}
            rows = db.execute(
                "SELECT id,name,status,created,definition,result FROM search_jobs "
                "WHERE worker_device IS ? ORDER BY created DESC LIMIT 10",
                (device,),
            ).fetchall()
            fresh = bool(worker and time.time() - worker["seen"] < 45)
            paused = fresh and bool(metadata.get("linkedin_paused"))
            return {
                "worker_online": fresh and not paused,
                "worker_linkedin_paused": paused,
                "worker_last_seen": worker["seen"] if worker else None,
                "worker_managed": bool(metadata.get("managed")),
                "worker_autostart": bool(metadata.get("autostart")),
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


def install_hosted_search(app: FastAPI, root: Path, config_dir: Path, *, mode="hosted") -> None:
    store = SearchJobs(root / "search-jobs.sqlite3")
    catalog = CampaignCatalog(root / "campaigns.sqlite3", configured_campaigns(config_dir))
    app.state.search_store, app.state.campaign_catalog = store, catalog
    from linkedin_automation.interfaces.campaign_editor import install_campaign_routes

    install_campaign_routes(app, catalog)
    from linkedin_automation.interfaces.public_discovery import install_public_discovery

    install_public_discovery(app, root, catalog)

    @app.get("/api/search/settings")
    def settings(request: Request):
        device = getattr(request.state, "worker_device", None)
        required = getattr(request.state, "device_required", False)
        status = store.status(device=device)
        if required and not device:
            status.update(
                worker_online=False, worker_linkedin_paused=False,
                worker_managed=False, worker_autostart=False, jobs=[]
            )
        return {
            "mode": "cloud" if getattr(request.state, "website_cloud", False) else mode,
            "campaigns": catalog.options(),
            **status,
            "device_required": required,
            "device_paired": bool(device),
            "cloud_connected": getattr(request.state, "cloud_connected", False),
        }

    @app.post("/api/search/jobs")
    def enqueue(payload: SearchRequest, request: Request):
        if payload.profile_url and not getattr(request.state, "website_cloud", False):
            raise HTTPException(409, "Known-profile tests require cloud mode.")
        device = getattr(request.state, "worker_device", None)
        if getattr(request.state, "device_required", False) and not device:
            raise HTTPException(
                409, "Connect this computer first. No search was queued on another PC."
            )
        try:
            definition, policy = catalog.resolve(payload.campaign)
            return store.enqueue(
                payload, {payload.campaign: definition}, policy=policy, device=device
            )
        except KeyError:
            raise HTTPException(422, "Choose a saved campaign") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/search/claim")
    def claim(request: Request, payload: WorkerHeartbeat | None = None):
        return {"job": store.claim(payload, device=getattr(request.state, "worker_device", None))}

    @app.post("/api/search/heartbeat")
    def heartbeat(request: Request, payload: WorkerHeartbeat | None = None):
        with store.connection() as db:
            store.record_heartbeat(
                db, payload, device=getattr(request.state, "worker_device", None)
            )
        return {"ok": True}

    @app.post("/api/search/jobs/{identifier}/complete")
    def complete(identifier: UUID, payload: SearchCompletion, request: Request):
        try:
            device = getattr(request.state, "worker_device", None)
            with store.connection() as db:
                row = db.execute(
                    "SELECT worker_device FROM search_jobs WHERE id=?", (str(identifier),)
                ).fetchone()
            if row and row["worker_device"] != device:
                raise ValueError("Search belongs to a different computer")
            store.complete(identifier, payload)
            return {"ok": True}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/search/jobs/{identifier}/resume")
    def resume(identifier: UUID, request: Request):
        try:
            return store.resume(identifier, device=getattr(request.state, "worker_device", None))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
