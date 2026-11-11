"""Explicit owner-approved transfer of one verified sender into shared admin."""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from linkedin_automation.domain.services import canonicalize_linkedin_profile_url
from linkedin_automation.infrastructure.cloud_accounts import fingerprint


def public_profile(account):
    im = account.get("connection_params", {}).get("im", {})
    slug = im.get("publicIdentifier") or im.get("public_identifier")
    if not isinstance(slug, str) or not slug:
        raise ValueError("Provider did not verify a public LinkedIn profile")
    return canonicalize_linkedin_profile_url("https://www.linkedin.com/in/" + slug + "/")


class AdminAccountTransfer:
    def __init__(self, cloud):
        self.cloud = cloud

    def plan(self, owner, email, profile_url):
        cloud = self.cloud
        if owner["id"] != "admin:" + cloud.gateway.admin_binding():
            raise ValueError("Shared administrator access is required")
        if not cloud.client or cloud.client.settings.version != 1:
            raise ValueError("This transfer requires the configured Unipile V1 connector")
        email = email.strip().casefold()
        if email and not cloud.gateway.permits(email):
            raise ValueError("Source workspace is not approved")
        target_url = canonicalize_linkedin_profile_url(profile_url)
        target = cloud.record(owner)
        if target["account_id"]:
            raise ValueError("Admin already has a sender. No existing admin account was replaced")
        data = cloud.client.request("/api/v1/accounts?limit=100")
        accounts = data.get("items", [])
        if not isinstance(accounts, list):
            raise ValueError("Provider account list could not be verified")
        candidates = []
        for account in accounts:
            if not isinstance(account, dict) or account.get("type") != "LINKEDIN":
                continue
            im = account.get("connection_params", {}).get("im", {})
            sources = account.get("sources")
            if (
                not isinstance(im.get("id"), str)
                or not 1 <= len(im["id"]) <= 255
                or not isinstance(sources, list)
                or not sources
                or any(not isinstance(s, dict) or s.get("status") != "OK" for s in sources)
            ):
                continue
            try:
                if public_profile(account) == target_url:
                    candidates.append(account)
            except ValueError:
                continue
        if not candidates:
            raise ValueError("No active verified session exists for the approved profile")
        members = {a["connection_params"]["im"]["id"] for a in candidates}
        if len(members) != 1:
            raise ValueError("Provider identities are ambiguous; no account was changed")
        member = next(iter(members))
        selected = max(candidates, key=lambda a: str(a.get("created_at", "")))
        verified = cloud.client.account(selected["id"])
        if (
            verified.get("connection_params", {}).get("im", {}).get("id") != member
            or public_profile(verified) != target_url
        ):
            raise ValueError("Provider did not verify the exact approved sender")
        with cloud.accounts.db() as db:
            row = db.execute("SELECT * FROM cloud_users WHERE member_id=?", (member,)).fetchone()
        source = dict(row) if row else None
        source_kind, source_email, source_root = "unbound_provider_session", None, None
        if source:
            subject = source["subject"].removeprefix("website:")
            if subject.startswith("admin:"):
                source_kind = "previous_admin_workspace"
                source_root = Path(cloud.gateway.operations.shared_root)
            else:
                with cloud.gateway.store.db() as db:
                    user = db.execute("SELECT * FROM users WHERE id=?", (subject,)).fetchone()
                if not user or not cloud.gateway.permits(user["email"]):
                    raise ValueError("The actual source workspace is not approved")
                source_kind, source_email = "private_workspace", user["email"]
                source_root = cloud.gateway.store.workspace(user["id"])
        if email and email != source_email:
            raise ValueError("Source workspace is not bound to the exact approved profile")
        return {
            "source": source,
            "target": target,
            "verified": verified,
            "source_email": source_email,
            "source_kind": source_kind,
            "member": member,
            "profile_url": target_url,
            "source_root": source_root,
            "target_root": Path(cloud.gateway.operations.shared_root),
        }

    @staticmethod
    def preview(plan):
        return {
            "ready": True,
            "source_email": plan["source_email"],
            "source_kind": plan["source_kind"],
            "profile_url": plan["profile_url"],
            "destination": "Shared admin workspace",
            "research_data_moved": False,
            "old_delivery_approvals_transferred": False,
        }

    def commit(self, plan):
        source, target, verified = (plan[k] for k in ("source", "target", "verified"))
        paths = list(
            dict.fromkeys(
                (Path(plan[k]) / "search-jobs.sqlite3").resolve()
                for k in ("source_root", "target_root")
                if plan[k] is not None
            )
        )
        if not paths or not all(p.is_file() for p in paths):
            raise ValueError("Workspace queues could not be verified")
        aliases = tuple("queue_" + str(i) for i in range(len(paths)))
        # Attach fixed server-derived paths before BEGIN: binding and approval revocation
        # commit together; runners cannot claim a queued delivery during the transfer.
        with closing(sqlite3.connect(self.cloud.accounts.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            for alias, path in zip(aliases, paths, strict=True):
                db.execute("ATTACH DATABASE ? AS " + alias, (str(path),))
            with db:
                db.execute("BEGIN IMMEDIATE")
                current = (
                    db.execute("SELECT * FROM cloud_users WHERE id=?", (source["id"],)).fetchone()
                    if source
                    else None
                )
                destination = db.execute(
                    "SELECT * FROM cloud_users WHERE id=?", (target["id"],)
                ).fetchone()
                if (
                    not destination
                    or (
                        source is not None
                        and (
                            not current
                            or current["account_id"] != source["account_id"]
                            or current["member_id"] != plan["member"]
                        )
                    )
                    or destination["account_id"]
                    or destination["member_id"]
                ):
                    raise ValueError("Workspace binding changed. No transfer was performed")
                if db.execute(
                    "SELECT 1 FROM cloud_users WHERE member_id=? AND id!=?",
                    (plan["member"], source["id"] if source else ""),
                ).fetchone():
                    raise ValueError("Sender is owned by an unexpected workspace")
                revoked = 0
                for alias in aliases:
                    if (
                        db.execute(
                            "SELECT 1 FROM " + alias + ".delivery_jobs "
                            "WHERE status IN ('running','uncertain')"
                        ).fetchone()
                        or db.execute(
                            "SELECT 1 FROM " + alias + ".search_jobs "
                            "WHERE status IN ('queued','running')"
                        ).fetchone()
                    ):
                        raise ValueError(
                            "Finish active or uncertain work before transferring the sender"
                        )
                    rows = db.execute(
                        "SELECT id,payload,authorized FROM "
                        + alias
                        + ".delivery_jobs WHERE status='queued'"
                    ).fetchall()
                    for row in rows:
                        payload = json.loads(row["payload"])
                        revoked += bool(row["authorized"])
                        if payload.get("follow_up_authorized"):
                            payload["follow_up_authorized"] = False
                        db.execute(
                            "UPDATE "
                            + alias
                            + ".delivery_jobs SET authorized=0,payload=? WHERE id=?",
                            (json.dumps(payload), row["id"]),
                        )
                if source:
                    db.execute(
                        "UPDATE cloud_users SET account_id=NULL,member_id=NULL,"
                        "status='not_connected' WHERE id=?",
                        (source["id"],),
                    )
                db.execute(
                    "UPDATE cloud_users SET account_id=?,member_id=?,status='connected' WHERE id=?",
                    (verified["id"], plan["member"], target["id"]),
                )
                db.execute(
                    "UPDATE cloud_flows SET consumed=1 WHERE user_id IN (?,?)",
                    (source["id"] if source else target["id"], target["id"]),
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS cloud_account_transfers "
                    "(id TEXT PRIMARY KEY,source_id TEXT,target_id TEXT,member_hash TEXT,"
                    "profile_url TEXT,created REAL)"
                )
                transfer = str(uuid4())
                db.execute(
                    "INSERT INTO cloud_account_transfers VALUES (?,?,?,?,?,?)",
                    (
                        transfer,
                        source["id"] if source else None,
                        target["id"],
                        fingerprint(plan["member"]),
                        plan["profile_url"],
                        time.time(),
                    ),
                )
        self.cloud.connection_checks.clear()
        return {
            **self.preview(plan),
            "transferred": True,
            "transfer_id": transfer,
            "pending_approvals_revoked": revoked,
        }
