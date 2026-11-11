"""Expiring, user-approved desktop pairing with per-browser device selection."""

import secrets
import time
from uuid import UUID, uuid4

from linkedin_automation.infrastructure.access_auth import digest


class DevicePairings:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS desktop_devices (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                code_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
                expires REAL NOT NULL, user_id TEXT, browser_hash TEXT,
                revoked INTEGER NOT NULL DEFAULT 0)""")

    def begin(self, token_hash, label):
        identifier = uuid4().hex
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(10))
        with self.store.db() as db:
            db.execute(
                "DELETE FROM desktop_devices WHERE user_id IS NULL AND expires < ?", (time.time(),)
            )
            db.execute(
                "INSERT INTO desktop_devices "
                "(id,token_hash,code_hash,label,expires) VALUES (?,?,?,?,?)",
                (identifier, token_hash, digest(code), label, time.time() + 600),
            )
        return {"id": identifier, "code": code, "expires_in": 600}

    def preview(self, code):
        with self.store.db() as db:
            row = db.execute(
                "SELECT label,expires,user_id FROM desktop_devices WHERE code_hash=?",
                (digest(code),),
            ).fetchone()
        if not row or row["user_id"] or row["expires"] < time.time():
            raise ValueError("Pairing code expired or already used. Open the helper again.")
        return {"label": row["label"]}

    def approve(self, code, user_id):
        cookie = secrets.token_urlsafe(48)
        with self.store.db() as db:
            row = db.execute(
                "SELECT * FROM desktop_devices WHERE code_hash=?", (digest(code),)
            ).fetchone()
            if not row or row["user_id"] or row["expires"] < time.time():
                raise ValueError("Pairing code expired or already used")
            db.execute(
                "UPDATE desktop_devices SET user_id=?,browser_hash=? WHERE id=?",
                (user_id, digest(cookie), row["id"]),
            )
        return cookie

    def poll(self, identifier, token):
        with self.store.db() as db:
            row = db.execute(
                "SELECT d.*,u.email FROM desktop_devices d "
                "LEFT JOIN users u ON u.id=d.user_id WHERE d.id=?",
                (UUID(identifier).hex,),
            ).fetchone()
        if (
            not row
            or row["revoked"]
            or not secrets.compare_digest(row["token_hash"], digest(token))
        ):
            raise ValueError("Invalid device credentials")
        if not row["user_id"]:
            if row["expires"] < time.time():
                raise ValueError("Pairing expired. Open the helper again.")
            return {"status": "pending"}
        admin = row["user_id"].startswith("admin:")
        return {
            "status": "paired",
            "username": "worker-device-" + row["id"],
            "email": "Shared administrator workspace" if admin else row["email"],
            **({"admin_workspace": True} if admin else {}),
        }

    def worker(self, identifier, token):
        try:
            if self.poll(identifier, token)["status"] != "paired":
                return None
        except ValueError:
            return None
        with self.store.db() as db:
            device = db.execute(
                "SELECT id,user_id FROM desktop_devices WHERE id=? AND revoked=0",
                (UUID(identifier).hex,),
            ).fetchone()
            if device and device["user_id"] and device["user_id"].startswith("admin:"):
                return {
                    "id": "shared",
                    "name": "Administrator",
                    "email": None,
                    "device_id": device["id"],
                    "admin_worker": True,
                    "binding": device["user_id"][6:],
                }
            row = db.execute(
                "SELECT u.id,u.name,u.email,d.id AS device_id FROM desktop_devices d "
                "JOIN users u ON u.id=d.user_id WHERE d.id=? AND d.revoked=0",
                (UUID(identifier).hex,),
            ).fetchone()
        return dict(row) if row else None

    def browser(self, cookie, user_id):
        if not cookie:
            return None
        with self.store.db() as db:
            row = db.execute(
                "SELECT id,label FROM desktop_devices WHERE user_id=? "
                "AND browser_hash=? AND revoked=0",
                (user_id, digest(cookie)),
            ).fetchone()
        return dict(row) if row else None

    def configured(self, user_id):
        """Reuse a sole authorized workspace worker without browser pairing.

        Never pick between several computers or inherit another workspace's worker.
        """
        with self.store.db() as db:
            rows = db.execute(
                "SELECT id,label FROM desktop_devices WHERE user_id=? AND revoked=0",
                (user_id,),
            ).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None
