"""Invite-only website identities; independent of LinkedIn/provider credentials."""

import hashlib
import hmac
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.id_token import verify_oauth2_token

from linkedin_automation.infrastructure.cloud_connector import json_request

PREFIX = "/linkedin-automation"
ITERATIONS = 600_000


def email_address(value: str) -> str:
    value = value.strip().lower()
    if "*" in value:
        raise ValueError("Use an exact email address; wildcards are not supported")
    if len(value) > 254 or not re.fullmatch(
        r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", value
    ):
        raise ValueError("Enter a valid email address")
    return value


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str, salt: str | None = None) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Use a password between 12 and 128 characters")
    salt = salt or secrets.token_hex(24)
    result = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${result.hex()}"


def password_matches(password: str, encoded: str | None) -> bool:
    # Do the same expensive work for unknown identities and Google-only accounts.
    dummy = f"pbkdf2_sha256${ITERATIONS}${'00' * 24}${'00' * 32}"
    algorithm, rounds, salt, expected = (encoded or dummy).split("$")
    if algorithm != "pbkdf2_sha256" or int(rounds) != ITERATIONS:
        return False
    result = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
    return hmac.compare_digest(result.hex(), expected) and encoded is not None


@dataclass(frozen=True)
class AccessSettings:
    origin: str
    allowed_emails: frozenset[str]
    google_client_id: str = ""
    google_client_secret: str = field(default="", repr=False)
    signup_mode: str = "invite"

    def __post_init__(self):
        if self.signup_mode not in {"invite", "email", "disabled"}:
            raise ValueError("AUTH_SIGNUP_MODE must be invite, email or disabled")
        parsed = urlsplit(self.origin)
        local = parsed.hostname in {"localhost", "127.0.0.1"} and parsed.scheme == "http"
        if (
            not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or (parsed.scheme != "https" and not local)
        ):
            raise ValueError(
                "AUTH_PUBLIC_ORIGIN must be an exact HTTPS origin (HTTP only on localhost)"
            )
        if any(email_address(value) != value for value in self.allowed_emails):
            raise ValueError("Allowed emails must be normalized exact addresses")

    @classmethod
    def from_environment(cls):
        allowed = os.getenv("AUTH_EMAIL_ALLOWLIST", "")
        return cls(
            os.getenv("AUTH_PUBLIC_ORIGIN", "").strip().rstrip("/"),
            frozenset(email_address(item) for item in re.split(r"[,;\s]+", allowed) if item),
            os.getenv("GOOGLE_LOGIN_CLIENT_ID", "").strip(),
            os.getenv("GOOGLE_LOGIN_CLIENT_SECRET", "").strip(),
            os.getenv("AUTH_SIGNUP_MODE", "invite").strip(),
        )

    @property
    def secure(self):
        return self.origin.startswith("https://")

    @property
    def callback(self):
        return self.origin + PREFIX + "/api/auth/google/callback"

    @property
    def google_ready(self):
        return bool(self.google_client_id and self.google_client_secret)

    def permits(self, email):
        return email in self.allowed_emails


class AccessStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "web-access.sqlite3"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                  password_hash TEXT, google_sub TEXT UNIQUE, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS registrations (
                  email TEXT PRIMARY KEY, name TEXT NOT NULL, password_hash TEXT NOT NULL,
                  code_hash TEXT NOT NULL, expires REAL NOT NULL, attempts INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS oauth_flows (
                  state_hash TEXT PRIMARY KEY, verifier TEXT NOT NULL, nonce TEXT NOT NULL,
                  expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS rate_limits (
                  key_hash TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS invitations (
                  email TEXT PRIMARY KEY, code_hash TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS worker_bindings (
                  user_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS admin_sessions (
                  token_hash TEXT PRIMARY KEY, username TEXT NOT NULL,
                  credential_hash TEXT NOT NULL, expires REAL NOT NULL);
            """)

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db

    def rate(self, key: str, *, maximum=10, seconds=900):
        with self.db() as db:
            db.execute("DELETE FROM rate_limits WHERE expires<?", (time.time(),))
            row = db.execute(
                "SELECT count FROM rate_limits WHERE key_hash=?", (digest(key),)
            ).fetchone()
            if row and row["count"] >= maximum:
                return False
            if not row and db.execute("SELECT COUNT(*) FROM rate_limits").fetchone()[0] >= 10000:
                return False
            db.execute(
                "INSERT INTO rate_limits VALUES (?,1,?) ON CONFLICT(key_hash) "
                "DO UPDATE SET count=count+1",
                (digest(key), time.time() + seconds),
            )
            return True

    def register(self, email: str, name: str, password: str) -> str | None:
        hashed = password_hash(password)
        code = f"{secrets.randbelow(100_000_000):08d}"
        # Codes are never stored in plaintext and expire after five wrong guesses.
        code_hash = digest(email + hashed + code)
        with self.db() as db:
            db.execute("DELETE FROM registrations WHERE expires<?", (time.time(),))
            if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                return None
            db.execute(
                "INSERT INTO registrations VALUES (?,?,?,?,?,0) ON CONFLICT(email) "
                "DO UPDATE SET name=excluded.name,password_hash=excluded.password_hash,"
                "code_hash=excluded.code_hash,expires=excluded.expires,attempts=0",
                (email, name, hashed, code_hash, time.time() + 600),
            )
        return code

    def invite(self, email: str) -> str:
        """Owner-only provisioning; never exposed as a public website endpoint."""
        email = email_address(email)
        code = secrets.token_urlsafe(24)
        with self.db() as db:
            if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise ValueError("This account already exists; invitation not issued")
            db.execute(
                "INSERT INTO invitations VALUES (?,?,?) ON CONFLICT(email) "
                "DO UPDATE SET code_hash=excluded.code_hash,expires=excluded.expires",
                (email, digest(email + code), time.time() + 7 * 86400),
            )
        return code

    def register_invited(self, email: str, name: str, password: str, code: str):
        hashed = password_hash(password)
        with self.db() as db:
            row = db.execute("SELECT * FROM invitations WHERE email=?", (email,)).fetchone()
            if (
                not row
                or row["expires"] < time.time()
                or not hmac.compare_digest(row["code_hash"], digest(email + code))
            ):
                raise ValueError("Invalid or expired access code. Ask the app owner for a code.")
            if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise ValueError("This account already exists; please sign in")
            identifier = str(uuid4())
            db.execute(
                "INSERT INTO users VALUES (?,?,?,?,NULL,?)",
                (identifier, email, name, hashed, time.time()),
            )
            db.execute("DELETE FROM invitations WHERE email=?", (email,))
            return dict(db.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone())

    def verify_registration(self, email: str, code: str):
        user = None
        with self.db() as db:
            row = db.execute("SELECT * FROM registrations WHERE email=?", (email,)).fetchone()
            if row and row["expires"] > time.time() and row["attempts"] < 5:
                db.execute("UPDATE registrations SET attempts=attempts+1 WHERE email=?", (email,))
                if hmac.compare_digest(
                    row["code_hash"], digest(email + row["password_hash"] + code)
                ):
                    identifier = str(uuid4())
                    db.execute(
                        "INSERT OR IGNORE INTO users VALUES (?,?,?,?,NULL,?)",
                        (identifier, email, row["name"], row["password_hash"], time.time()),
                    )
                    db.execute("DELETE FROM registrations WHERE email=?", (email,))
                    user = db.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone()
        if not user:
            raise ValueError("Invalid or expired verification code")
        return dict(user)

    def login(self, email: str, password: str):
        with self.db() as db:
            row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not password_matches(password, row["password_hash"] if row else None):
            raise ValueError("Email or password is incorrect")
        return dict(row)

    def google_user(self, info: dict):
        email, subject = email_address(info.get("email", "")), info.get("sub")
        if (
            info.get("email_verified") is not True
            or not isinstance(subject, str)
            or not 1 <= len(subject) <= 255
        ):
            raise ValueError("Google did not verify this email")
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM users WHERE email=? OR google_sub=?", (email, subject)
            ).fetchall()
            if any(
                item["email"] != email or item["google_sub"] not in {None, subject} for item in row
            ):
                raise ValueError("Google identity does not match the registered account")
            db.execute(
                "INSERT INTO users VALUES (?,?,?,NULL,?,?) ON CONFLICT(email) "
                "DO UPDATE SET google_sub=excluded.google_sub",
                (str(uuid4()), email, str(info.get("name") or email)[:100], subject, time.time()),
            )
            db.execute("DELETE FROM registrations WHERE email=?", (email,))
            return dict(db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone())

    def session(self, user_id: str):
        token = secrets.token_urlsafe(48)
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            if not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                raise ValueError("Unknown account")
            db.execute(
                "DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN "
                "(SELECT token_hash FROM sessions WHERE user_id=? ORDER BY expires DESC LIMIT 9)",
                (user_id, user_id),
            )
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?)",
                (digest(token), user_id, time.time() + 7 * 86400),
            )
        return token

    def user(self, token):
        if not isinstance(token, str) or len(token) != 64:
            return None
        with self.db() as db:
            row = db.execute(
                "SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id "
                "WHERE s.token_hash=? AND s.expires>?",
                (digest(token), time.time()),
            ).fetchone()
            return {key: row[key] for key in ("id", "email", "name")} if row else None

    def revoke(self, token):
        if token:
            with self.db() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(token),))
                db.execute("DELETE FROM admin_sessions WHERE token_hash=?", (digest(token),))

    def admin_session(self, username, credential_hash):
        token = secrets.token_urlsafe(48)
        with self.db() as db:
            db.execute("DELETE FROM admin_sessions WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT INTO admin_sessions VALUES (?,?,?,?)",
                (digest(token), username, credential_hash, time.time() + 8 * 3600),
            )
        return token

    def admin(self, token, username, credential_hash):
        if not isinstance(token, str) or len(token) != 64:
            return False
        with self.db() as db:
            row = db.execute(
                "SELECT 1 FROM admin_sessions WHERE token_hash=? AND username=? "
                "AND credential_hash=? AND expires>?",
                (digest(token), username, credential_hash, time.time()),
            ).fetchone()
        return bool(row)

    def oauth_flow(self):
        state, verifier, nonce = (secrets.token_urlsafe(48) for _ in range(3))
        with self.db() as db:
            db.execute("DELETE FROM oauth_flows WHERE expires<?", (time.time(),))
            if db.execute("SELECT COUNT(*) FROM oauth_flows").fetchone()[0] >= 1000:
                raise ValueError("Sign-in is busy; try again later")
            db.execute(
                "INSERT INTO oauth_flows VALUES (?,?,?,?)",
                (digest(state), verifier, nonce, time.time() + 600),
            )
        return state, verifier, nonce

    def consume_oauth(self, state):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM oauth_flows WHERE state_hash=? AND expires>?",
                (digest(state), time.time()),
            ).fetchone()
            db.execute("DELETE FROM oauth_flows WHERE state_hash=?", (digest(state),))
        if not row:
            raise ValueError("Expired or previously used sign-in link")
        return dict(row)

    def workspace(self, user_id):
        return self.root / "workspaces" / UUID(user_id).hex

    def bind_worker(self, user_id: str, token_hash: str):
        """Owner provisioning only. Workers never receive a website session or owner password."""
        identifier = str(UUID(user_id))
        if not re.fullmatch(r"[a-f0-9]{64}", token_hash):
            raise ValueError("Invalid worker token hash")
        with self.db() as db:
            if not db.execute("SELECT 1 FROM users WHERE id=?", (identifier,)).fetchone():
                raise ValueError("Unknown worker workspace")
            db.execute(
                "INSERT INTO worker_bindings VALUES (?,?,?) ON CONFLICT(user_id) "
                "DO UPDATE SET token_hash=excluded.token_hash,created=excluded.created",
                (identifier, token_hash, time.time()),
            )

    def worker_user(self, username: str, token: str):
        if not re.fullmatch(r"worker-[a-f0-9]{32}", username) or not 40 <= len(token) <= 128:
            return None
        identifier = str(UUID(username[7:]))
        with self.db() as db:
            row = db.execute(
                "SELECT u.id,u.email,u.name,w.token_hash FROM users u "
                "JOIN worker_bindings w ON w.user_id=u.id WHERE u.id=?",
                (identifier,),
            ).fetchone()
        if not row or not hmac.compare_digest(row["token_hash"], digest(token)):
            return None
        return {key: row[key] for key in ("id", "email", "name")}


class GoogleLogin:
    def __init__(self, settings: AccessSettings):
        self.settings = settings

    def url(self, state, verifier, nonce):
        import base64

        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": self.settings.google_client_id,
                "redirect_uri": self.settings.callback,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )

    def identity(self, code, flow):
        data = json_request(
            "https://oauth2.googleapis.com/token",
            form=True,
            payload={
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "redirect_uri": self.settings.callback,
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": flow["verifier"],
            },
        )
        try:
            info = verify_oauth2_token(
                data["id_token"], GoogleRequest(), self.settings.google_client_id
            )
            if not hmac.compare_digest(info.get("nonce", ""), flow["nonce"]):
                raise ValueError("Invalid nonce")
            if info.get("email_verified") is not True:
                raise ValueError("Unverified email")
            return info
        except Exception:
            raise ValueError("Google sign-in could not be verified") from None


class VerificationMail:
    @property
    def ready(self):
        return all(
            os.getenv(key)
            for key in (
                "AUTH_SMTP_HOST",
                "AUTH_SMTP_USERNAME",
                "AUTH_SMTP_PASSWORD",
                "AUTH_MAIL_FROM",
            )
        )

    def send(self, email, code):
        if not self.ready:
            raise ValueError("Email verification is not configured")
        message = EmailMessage()
        message["From"], message["To"] = os.environ["AUTH_MAIL_FROM"], email
        message["Subject"] = "Your BluQQ verification code"
        message.set_content(
            f"Your BluQQ verification code is {code}.\nIt expires in 10 minutes.\n"
            "If you did not request it, ignore this email."
        )
        try:
            with smtplib.SMTP(
                os.environ["AUTH_SMTP_HOST"], int(os.getenv("AUTH_SMTP_PORT", "587")), timeout=15
            ) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(os.environ["AUTH_SMTP_USERNAME"], os.environ["AUTH_SMTP_PASSWORD"])
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException):
            raise ValueError("Verification email could not be sent; please retry later") from None
