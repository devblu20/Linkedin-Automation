"""Owner operations over access and run metadata; never return tokens or lead content."""

import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4


def read_database(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)


class AdminOperations:
    def __init__(self, store, shared_root):
        self.store, self.shared_root = store, Path(shared_root).resolve()
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS admin_access (
                  email TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS admin_events (
                  id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
                  target TEXT NOT NULL, created REAL NOT NULL);
            """)

    def enabled(self, email):
        with self.store.db() as db:
            row = db.execute("SELECT enabled FROM admin_access WHERE email=?", (email,)).fetchone()
        return row is None or bool(row["enabled"])

    @staticmethod
    def event(db, actor, action, target):
        db.execute(
            "INSERT INTO admin_events(actor,action,target,created) VALUES (?,?,?,?)",
            (actor, action, target, time.time()),
        )

    def access(self, email, enabled, actor):
        with self.store.db() as db:
            db.execute(
                "INSERT INTO admin_access VALUES (?,?) ON CONFLICT(email) "
                "DO UPDATE SET enabled=excluded.enabled",
                (email, int(enabled)),
            )
            if not enabled:
                db.execute(
                    "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email=?)",
                    (email,),
                )
            self.event(db, actor, "access_enabled" if enabled else "access_blocked", email)

    def revoke_device(self, identifier, actor):
        identifier = UUID(str(identifier)).hex
        with self.store.db() as db:
            if not db.execute(
                "SELECT 1 FROM desktop_devices WHERE id=? AND user_id IS NOT NULL", (identifier,)
            ).fetchone():
                raise ValueError("Paired device not found")
            db.execute(
                "UPDATE desktop_devices SET revoked=1,browser_hash=NULL WHERE id=?", (identifier,)
            )
            self.event(db, actor, "device_revoked", identifier)

    def scopes(self):
        with self.store.db() as db:
            users = [
                dict(row) for row in db.execute("SELECT id,email,name FROM users ORDER BY email")
            ]
        return [
            ("shared", "Shared administrator workspace", self.shared_root),
            *[(user["id"], user["email"], self.store.workspace(user["id"])) for user in users],
        ]

    def run_metadata(self):
        jobs, heartbeats = [], {}
        for scope, email, root in self.scopes():
            path = root / "search-jobs.sqlite3"
            if not path.is_file():
                continue
            with closing(read_database(path)) as db:
                db.row_factory = sqlite3.Row
                tables = {
                    r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if "desktop_heartbeats" in tables:
                    heartbeats.update(
                        {
                            r["device"]: r["seen"]
                            for r in db.execute("SELECT device,seen FROM desktop_heartbeats")
                        }
                    )
                if "search_jobs" not in tables:
                    continue
                for row in db.execute(
                    "SELECT id,name,status,created,result FROM search_jobs "
                    "ORDER BY created DESC LIMIT 30"
                ):
                    try:
                        result = json.loads(row["result"] or "{}")
                    except (TypeError, ValueError):
                        result = {}
                    jobs.append(
                        {
                            "id": row["id"],
                            "scope": scope,
                            "email": email,
                            "campaign": row["name"],
                            "status": row["status"],
                            "created": row["created"],
                            "message": str(result.get("message", ""))[:500],
                            "observed": result.get("observed", 0),
                            "saved": result.get("accepted", 0),
                        }
                    )
        return sorted(jobs, key=lambda j: j["created"], reverse=True)[:60], heartbeats

    def cancel_queued(self, scope, identifier, actor):
        identifier = str(UUID(str(identifier)))
        roots = {item[0]: item[2] for item in self.scopes()}
        if scope not in roots or not (roots[scope] / "search-jobs.sqlite3").is_file():
            raise ValueError("Search scope not found")
        with closing(sqlite3.connect(roots[scope] / "search-jobs.sqlite3", timeout=15)) as db, db:
            result = db.execute(
                "UPDATE search_jobs SET status='failed',finished=?,token=NULL,result=? "
                "WHERE id=? AND status='queued'",
                (
                    time.time(),
                    json.dumps({"message": "Queued search cancelled by administrator."}),
                    identifier,
                ),
            )
            if result.rowcount != 1:
                raise ValueError(
                    "Only a queued search can be cancelled. "
                    "Running browsers must be stopped on their PC."
                )
        with self.store.db() as db:
            self.event(db, actor, "queued_search_cancelled", scope + ":" + identifier)

    def backups(self):
        root = self.shared_root / "backups"
        result = []
        if root.is_dir():
            for path in sorted(root.glob("admin-*/manifest.json"), reverse=True)[:10]:
                try:
                    item = json.loads(path.read_text())
                    result.append(
                        {key: item[key] for key in ("id", "created", "files", "complete")}
                    )
                except (ValueError, KeyError, OSError):
                    continue
        return result

    def backup(self, actor):
        identifier = "admin-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        destination = self.shared_root / "backups" / identifier
        destination.mkdir(parents=True, exist_ok=False)
        roots = [self.shared_root, self.store.root]
        cloud_root = self.store.root.parent / "website-cloud"
        if cloud_root.is_dir():
            roots.append(cloud_root)
        sources = sorted(
            {
                path.resolve()
                for root in roots
                for path in root.rglob("*.sqlite3")
                if "backups" not in path.parts
                and not path.is_symlink()
                and path.resolve().is_relative_to(root.resolve())
            }
        )
        manifest = {"id": identifier, "created": time.time(), "files": 0, "complete": False}
        # The private restore map stays on the server; it is never returned by the API.
        restore_map = {}
        try:
            for index, path in enumerate(sources):
                deadline = time.monotonic() + 30

                def progress(status, remaining, total, deadline=deadline):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Database snapshot timed out")

                with (
                    closing(read_database(path)) as source,
                    closing(sqlite3.connect(destination / f"{index}.sqlite3")) as target,
                ):
                    source.backup(target, pages=256, progress=progress)
                restore_map[f"{index}.sqlite3"] = str(path)
                manifest["files"] += 1
            manifest["complete"] = True
        finally:
            (destination / "manifest.json").write_text(json.dumps(manifest))
            (destination / "restore-map.json").write_text(json.dumps(restore_map))
            with self.store.db() as db:
                self.event(
                    db,
                    actor,
                    "backup_created" if manifest["complete"] else "backup_failed",
                    identifier,
                )
        return manifest

    def overview(self, allowed):
        jobs, seen = self.run_metadata()
        with self.store.db() as db:
            known = {r["email"]: dict(r) for r in db.execute("SELECT id,email,name FROM users")}
            controls = {
                r["email"]: bool(r["enabled"])
                for r in db.execute("SELECT email,enabled FROM admin_access")
            }
            rows = db.execute(
                "SELECT d.id,d.label,d.revoked,"
                "CASE WHEN d.user_id LIKE 'admin:%' THEN 'Shared administrator workspace' "
                "ELSE u.email END AS email,"
                "CASE WHEN d.user_id LIKE 'admin:%' THEN 1 ELSE 0 END AS admin_workspace "
                "FROM desktop_devices d LEFT JOIN users u ON u.id=d.user_id "
                "WHERE u.id IS NOT NULL OR d.user_id LIKE 'admin:%' ORDER BY email,d.label"
            ).fetchall()
            devices = [
                {
                    **dict(r),
                    "last_seen": seen.get(r["id"]),
                    "online": bool(
                        not r["revoked"]
                        and (
                            r["admin_workspace"]
                            or (controls.get(r["email"], True) and r["email"] in allowed)
                        )
                        and time.time() - seen.get(r["id"], 0) < 45
                    ),
                }
                for r in rows
            ]
            events = [
                dict(r)
                for r in db.execute(
                    "SELECT actor,action,target,created FROM admin_events ORDER BY id DESC LIMIT 50"
                )
            ]
        users = [
            {
                "email": email,
                "name": known.get(email, {}).get("name", "Not signed in yet"),
                "registered": email in known,
                "enabled": controls.get(email, True),
                "devices": sum(d["email"] == email and not d["revoked"] for d in devices),
            }
            for email in sorted(allowed)
        ]
        configuration = [
            {
                "name": label,
                "configured": all(bool(os.getenv(key, "")) for key in keys),
                "note": note,
            }
            for label, keys, note in [
                (
                    "Google login",
                    ["GOOGLE_LOGIN_CLIENT_ID", "GOOGLE_LOGIN_CLIENT_SECRET"],
                    "Required for approved user login.",
                ),
                (
                    "Unipile",
                    ["UNIPILE_API_URL", "UNIPILE_API_KEY"],
                    "Required for cloud research and authorized cloud delivery. "
                    "Desktop research does not need this.",
                ),
                (
                    "Brave search",
                    ["BRAVE_SEARCH_API_KEY"],
                    "Public discovery uses temporary previews by default. "
                    "Set storage permission to 1 only when "
                    "the selected API plan permits storing/exporting search results.",
                ),
                (
                    "Drive uploads",
                    [
                        "GOOGLE_OAUTH_CLIENT_FILE",
                        "GOOGLE_OAUTH_TOKEN_FILE",
                        "GOOGLE_DRIVE_FOLDER_ID",
                    ],
                    "Optional. CSV/Excel downloads work without Drive.",
                ),
                (
                    "Email verification",
                    [
                        "AUTH_SMTP_HOST",
                        "AUTH_SMTP_USERNAME",
                        "AUTH_SMTP_PASSWORD",
                        "AUTH_MAIL_FROM",
                    ],
                    "Optional. Signup is currently disabled.",
                ),
            ]
        ]
        return {
            "users": users,
            "devices": devices,
            "jobs": jobs,
            "events": events,
            "configuration": configuration,
            "backups": self.backups(),
            "signup_mode": os.getenv("AUTH_SIGNUP_MODE", "disabled"),
            "cloud_enabled": (
                os.getenv("CLOUD_ACCOUNT_MODE") == "1" or os.getenv("WEBSITE_CLOUD_MODE") == "1"
            ),
            "cloud_sending_enabled": os.getenv("CLOUD_OUTREACH_ENABLED") == "1",
            "release_blocker": (
                "Cloud mode requires a valid Unipile key, per-user LinkedIn connection "
                "and live provider testing. Local helper installation is not used."
                if os.getenv("WEBSITE_CLOUD_MODE") == "1"
                else "Windows Smart App Control blocks the downloaded launcher on protected PCs. "
                "Trusted distribution/signing and clean-PC validation are pending."
            ),
            "backup_note": (
                "Snapshots remain on this Railway volume. "
                "Offsite copies and restore testing are still required."
            ),
        }
