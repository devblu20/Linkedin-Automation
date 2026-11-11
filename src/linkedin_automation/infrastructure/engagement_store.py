"""Additive, workspace-local conversation evidence. Existing research rows are untouched."""

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from threading import RLock


class EngagementStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS engagement_messages (
                    account TEXT NOT NULL, id TEXT NOT NULL, profile_url TEXT NOT NULL,
                    chat TEXT NOT NULL, sent_at TEXT NOT NULL, outgoing INTEGER NOT NULL,
                    content TEXT NOT NULL, classification TEXT, classification_source TEXT,
                    PRIMARY KEY(account,id));
                CREATE INDEX IF NOT EXISTS engagement_recipient
                    ON engagement_messages(profile_url,sent_at);
                CREATE TABLE IF NOT EXISTS engagement_holds (
                    account TEXT NOT NULL, profile_url TEXT NOT NULL, reason TEXT NOT NULL,
                    PRIMARY KEY(account,profile_url));
                CREATE TABLE IF NOT EXISTS engagement_sync (
                    account TEXT PRIMARY KEY, cursor TEXT, after_at TEXT, newest TEXT,
                    last_success TEXT, error TEXT);
            """)

    @contextmanager
    def db(self):
        with self.lock, closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def observe(self, account, profile_url, message):
        """Persist a validated provider message and its reply hold in the SAME transaction."""
        from linkedin_automation.application.engagement import expresses_disinterest

        with self.db() as db:
            previous = db.execute(
                "SELECT content FROM engagement_messages WHERE account=? AND id=?",
                (account, message["id"]),
            ).fetchone()
            changed = previous is None or previous["content"] != message["content"]
            disinterest = not message["outgoing"] and expresses_disinterest(message["content"])
            label = "Not Interested" if disinterest else None
            source = "explicit opt-out rule" if disinterest else None
            db.execute(
                """INSERT INTO engagement_messages VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(account,id) DO UPDATE SET content=excluded.content,
                classification=CASE WHEN content!=excluded.content THEN excluded.classification
                    ELSE COALESCE(excluded.classification,classification) END,
                classification_source=CASE WHEN content!=excluded.content
                    THEN excluded.classification_source
                    ELSE COALESCE(excluded.classification_source,classification_source) END""",
                (
                    account,
                    message["id"],
                    profile_url,
                    message["chat"],
                    message["sent_at"],
                    int(message["outgoing"]),
                    message["content"],
                    label,
                    source,
                ),
            )
            if not message["outgoing"]:
                reason = (
                    "Recipient opted out / expressed disinterest"
                    if disinterest
                    else "Recipient replied"
                )
                db.execute(
                    """INSERT INTO engagement_holds VALUES (?,?,?)
                    ON CONFLICT(account,profile_url) DO UPDATE SET reason=
                    CASE WHEN reason='Recipient replied' THEN excluded.reason ELSE reason END""",
                    (account, profile_url, reason),
                )
            return changed

    def pause(self, account, profile_url, reason):
        """Persist a reply hold even when a visible attachment lacks a readable timestamp."""
        with self.db() as db:
            db.execute(
                "INSERT INTO engagement_holds VALUES (?,?,?) ON CONFLICT(account,profile_url) "
                "DO UPDATE SET reason=CASE WHEN reason='Recipient replied' THEN excluded.reason "
                "ELSE reason END",
                (account, profile_url, reason),
            )

    def reply_signature(self, profile_url, account):
        """Bind a reviewed response to the exact currently observed incoming history."""
        rows = [
            (x["id"], x["content"]) for x in self.history(profile_url, account) if not x["outgoing"]
        ]
        return hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest() if rows else None

    def observed_reply_after(self, profile_url, account, reply_id, sent_content):
        """Use verified order from the same browser snapshot, not invented timestamp seconds."""
        if not account or not account.startswith("browser:"):
            return False
        with self.db() as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='engagement_browser_order'"
            ).fetchone():
                return False
            reply = db.execute(
                "SELECT o.snapshot,o.position FROM engagement_browser_order o "
                "JOIN engagement_messages m ON m.account=o.account AND m.id=o.message_id "
                "WHERE o.account=? AND o.profile_url=? AND o.message_id=? AND m.outgoing=0",
                (account, profile_url, reply_id),
            ).fetchone()
            if not reply:
                return False
            matches = db.execute(
                "SELECT o.position FROM engagement_browser_order o "
                "JOIN engagement_messages m ON m.account=o.account AND m.id=o.message_id "
                "WHERE o.account=? AND o.profile_url=? AND o.snapshot=? "
                "AND m.outgoing=1 AND m.content=?",
                (account, profile_url, reply["snapshot"], sent_content),
            ).fetchall()
            return len(matches) == 1 and matches[0]["position"] < reply["position"]

    def hold(self, profile_url, account=None):
        with self.db() as db:
            row = db.execute(
                "SELECT reason FROM engagement_holds WHERE profile_url=?"
                + (" AND account=?" if account else "")
                + " ORDER BY reason DESC LIMIT 1",
                (profile_url, account) if account else (profile_url,),
            ).fetchone()
            return row["reason"] if row else None

    def classify(self, account, identifier, label, source, expected_content):
        from linkedin_automation.application.engagement import LABELS

        if label not in LABELS:
            raise ValueError("Unsupported reply intent")
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM engagement_messages WHERE account=? AND id=? AND outgoing=0",
                (account, identifier),
            ).fetchone()
            if row is None or row["content"] != expected_content:
                return
            if row["classification_source"] == "explicit opt-out rule":
                return
            db.execute(
                "UPDATE engagement_messages SET classification=?,classification_source=? "
                "WHERE account=? AND id=?",
                (label, source, account, identifier),
            )
            if label == "Not Interested":
                db.execute(
                    "UPDATE engagement_holds SET reason='Recipient expressed disinterest' "
                    "WHERE account=? AND profile_url=?",
                    (account, row["profile_url"]),
                )

    def history(self, profile_url=None, account=None):
        query, values = "SELECT * FROM engagement_messages WHERE 1=1", []
        for key, value in (("profile_url", profile_url), ("account", account)):
            if value is not None:
                query += " AND " + key + "=?"
                values.append(value)
        with self.db() as db:
            return [dict(r) for r in db.execute(query + " ORDER BY sent_at,id", values)]

    def sync_state(self, account):
        with self.db() as db:
            row = db.execute("SELECT * FROM engagement_sync WHERE account=?", (account,)).fetchone()
            return dict(row) if row else {}

    def save_sync(self, account, **values):
        allowed = {"cursor", "after_at", "newest", "last_success", "error"}
        if not values.keys() <= allowed:
            raise ValueError("Invalid sync state")
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO engagement_sync(account) VALUES (?)", (account,))
            db.execute(
                "UPDATE engagement_sync SET "
                + ",".join(k + "=?" for k in values)
                + " WHERE account=?",
                (*values.values(), account),
            )
