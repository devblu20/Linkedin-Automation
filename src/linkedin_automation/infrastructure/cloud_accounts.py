"""Private identities, expiring sessions and one-use account-linking transactions."""

import hashlib
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import UUID, uuid4


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class CloudAccounts:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "cloud-accounts.sqlite3"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cloud_users (
                  id TEXT PRIMARY KEY, subject TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                  account_id TEXT UNIQUE, member_id TEXT UNIQUE,
                  status TEXT NOT NULL DEFAULT 'not_connected',
                  created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cloud_sessions (
                  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cloud_flows (
                  state_hash TEXT PRIMARY KEY, user_id TEXT, kind TEXT NOT NULL,
                  expires REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS cloud_account_changes (
                  state_hash TEXT PRIMARY KEY, old_account_id TEXT NOT NULL,
                  old_member_id TEXT NOT NULL, target_profile_url TEXT NOT NULL);
            """)

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db

    def flow(self, kind: str, user_id: str | None = None) -> str:
        if kind not in {"identity", "connection"}:
            raise ValueError("Invalid authentication flow")
        state = secrets.token_urlsafe(48)
        with self.db() as db:
            db.execute("DELETE FROM cloud_flows WHERE expires<?", (time.time(),))
            db.execute(
                "DELETE FROM cloud_account_changes WHERE state_hash NOT IN "
                "(SELECT state_hash FROM cloud_flows)"
            )
            if (
                user_id is not None
                and not db.execute("SELECT 1 FROM cloud_users WHERE id=?", (user_id,)).fetchone()
            ):
                raise ValueError("Unknown workspace")
            if db.execute("SELECT COUNT(*) FROM cloud_flows").fetchone()[0] >= 10000:
                raise ValueError("Sign-in is busy; please try again later")
            if (
                user_id
                and db.execute(
                    "SELECT COUNT(*) FROM cloud_flows WHERE user_id=? AND consumed=0", (user_id,)
                ).fetchone()[0]
                >= 10
            ):
                raise ValueError("Too many open connection requests; retry in ten minutes")
            db.execute(
                "INSERT INTO cloud_flows VALUES (?,?,?,?,0)",
                (fingerprint(state), user_id, kind, time.time() + 600),
            )
        return state

    def replacement_flow(self, user_id, account_id, target_profile_url):
        """Explicit, expiring replacement intent; leave the current binding untouched."""
        from linkedin_automation.domain.services import canonicalize_linkedin_profile_url

        target = canonicalize_linkedin_profile_url(target_profile_url)
        state = self.flow("connection", user_id)
        with self.db() as db:
            user = db.execute("SELECT * FROM cloud_users WHERE id=?", (user_id,)).fetchone()
            if (
                not user
                or not account_id
                or user["account_id"] != account_id
                or not user["member_id"]
            ):
                raise ValueError("LinkedIn connection changed. Refresh before switching accounts.")
            db.execute(
                "INSERT INTO cloud_account_changes VALUES (?,?,?,?)",
                (fingerprint(state), account_id, user["member_id"], target),
            )
        return state

    def replacement_owner(self, state):
        with self.db() as db:
            row = db.execute(
                "SELECT f.user_id FROM cloud_flows f JOIN cloud_account_changes c "
                "ON f.state_hash=c.state_hash WHERE f.state_hash=? AND f.consumed=0",
                (fingerprint(state),),
            ).fetchone()
            return row["user_id"] if row else None

    def consume(self, state: str, kind: str, *, user_id: str | None = None):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM cloud_flows WHERE state_hash=?", (fingerprint(state),)
            ).fetchone()
            if (
                not row
                or row["kind"] != kind
                or row["consumed"]
                or row["expires"] < time.time()
                or (user_id is not None and row["user_id"] != user_id)
            ):
                raise ValueError(
                    "Authentication link is expired, already used or belongs to another user"
                )
            db.execute(
                "UPDATE cloud_flows SET consumed=1 WHERE state_hash=?", (fingerprint(state),)
            )
            return dict(row)

    def identity(self, subject: str, name: str) -> dict:
        if not subject or len(subject) > 255:
            raise ValueError("Verified LinkedIn identity is missing")
        with self.db() as db:
            db.execute(
                "INSERT INTO cloud_users(id,subject,name,created) VALUES (?,?,?,?) "
                "ON CONFLICT(subject) DO UPDATE SET name=excluded.name",
                (str(uuid4()), subject, name[:255], time.time()),
            )
            return dict(
                db.execute("SELECT * FROM cloud_users WHERE subject=?", (subject,)).fetchone()
            )

    def session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(48)
        with self.db() as db:
            db.execute("DELETE FROM cloud_sessions WHERE expires<?", (time.time(),))
            if not db.execute("SELECT 1 FROM cloud_users WHERE id=?", (user_id,)).fetchone():
                raise ValueError("Unknown workspace")
            db.execute(
                "DELETE FROM cloud_sessions WHERE user_id=? AND token_hash NOT IN "
                "(SELECT token_hash FROM cloud_sessions WHERE user_id=? "
                "ORDER BY expires DESC LIMIT 19)",
                (user_id, user_id),
            )
            db.execute(
                "INSERT INTO cloud_sessions VALUES (?,?,?)",
                (fingerprint(token), user_id, time.time() + 30 * 86400),
            )
        return token

    def user(self, token: str | None) -> dict | None:
        if not token or len(token) != 64:
            return None
        with self.db() as db:
            row = db.execute(
                "SELECT u.* FROM cloud_users u JOIN cloud_sessions s ON u.id=s.user_id "
                "WHERE s.token_hash=? AND s.expires>?",
                (fingerprint(token), time.time()),
            ).fetchone()
            return dict(row) if row else None

    def revoke(self, token: str):
        with self.db() as db:
            db.execute("DELETE FROM cloud_sessions WHERE token_hash=?", (fingerprint(token),))

    def connection(self, state: str, account: dict) -> dict:
        """Call only after authenticating the provider's server-to-server callback."""
        account_id = account.get("id")
        member = account.get("connection_params", {}).get("im", {}).get("id")
        if (
            account.get("type") != "LINKEDIN"
            or not isinstance(account_id, str)
            or not account_id
            or len(account_id) > 150
            or not isinstance(member, str)
            or not member
            or len(member) > 255
        ):
            raise ValueError("Provider did not verify a LinkedIn account identity")
        with self.db() as db:
            flow = db.execute(
                "SELECT * FROM cloud_flows WHERE state_hash=?", (fingerprint(state),)
            ).fetchone()
            if not flow or flow["kind"] != "connection" or flow["expires"] < time.time():
                raise ValueError("Connection link expired")
            user = db.execute("SELECT * FROM cloud_users WHERE id=?", (flow["user_id"],)).fetchone()
            if not user:
                raise ValueError("Unknown workspace")
            if flow["consumed"]:
                if user["account_id"] == account_id and user["member_id"] == member:
                    return dict(user)
                raise ValueError("Connection link already used")
            change = db.execute(
                "SELECT * FROM cloud_account_changes WHERE state_hash=?", (fingerprint(state),)
            ).fetchone()
            if change:
                from linkedin_automation.domain.services import canonicalize_linkedin_profile_url

                if (
                    user["account_id"] != change["old_account_id"]
                    or user["member_id"] != change["old_member_id"]
                ):
                    raise ValueError("LinkedIn sender changed during authentication. Start again.")
                im = account.get("connection_params", {}).get("im", {})
                slug = im.get("publicIdentifier") or im.get("public_identifier")
                if not isinstance(slug, str) or not slug:
                    raise ValueError("Provider did not verify the selected public profile")
                actual = canonicalize_linkedin_profile_url(
                    "https://www.linkedin.com/in/" + slug.strip("/") + "/"
                )
                if actual != change["target_profile_url"]:
                    raise ValueError(
                        "Sign in with the exact LinkedIn profile selected for this change"
                    )
            elif user["member_id"] and user["member_id"] != member:
                raise ValueError("Reconnect the original LinkedIn account")
            other = db.execute(
                "SELECT id FROM cloud_users WHERE (account_id=? OR member_id=?) AND id!=?",
                (account_id, member, user["id"]),
            ).fetchone()
            if other:
                raise ValueError("This LinkedIn account belongs to another private workspace")
            db.execute(
                "UPDATE cloud_users SET account_id=?,member_id=?,status='connected' WHERE id=?",
                (account_id, member, user["id"]),
            )
            db.execute(
                "UPDATE cloud_flows SET consumed=1 WHERE state_hash=?", (fingerprint(state),)
            )
            if change:
                # Older hosted-login callbacks must never roll the new sender back.
                db.execute("UPDATE cloud_flows SET consumed=1 WHERE user_id=?", (user["id"],))
            return dict(
                db.execute("SELECT * FROM cloud_users WHERE id=?", (user["id"],)).fetchone()
            )

    def workspace(self, user_id: str) -> Path:
        identifier = UUID(user_id)
        directory = self.root / "workspaces" / identifier.hex
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        return directory

    def account_for(self, user_id: str) -> str:
        with self.db() as db:
            row = db.execute(
                "SELECT account_id,status FROM cloud_users WHERE id=?", (str(UUID(user_id)),)
            ).fetchone()
            if not row or not row["account_id"] or row["status"] != "connected":
                raise ValueError("Connect your LinkedIn account first")
            return row["account_id"]

    def connected_users(self) -> list[dict]:
        with self.db() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM cloud_users WHERE account_id IS NOT NULL")
            ]
