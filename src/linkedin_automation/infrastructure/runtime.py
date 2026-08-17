"""Environment-backed local runtime paths and integration settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Validated local paths used to compose concrete adapters."""

    database_path: Path
    database_url: str
    artifact_dir: Path
    browser_data_dir: Path
    screenshot_dir: Path
    google_credentials_path: Path | None
    google_token_path: Path | None

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        root = Path.cwd().resolve()
        # Local secrets stay in the gitignored .env file. Explicit shell variables win.
        load_dotenv(root / ".env", override=False)
        credentials = os.getenv("GOOGLE_OAUTH_CLIENT_FILE")
        token = os.getenv("GOOGLE_OAUTH_TOKEN_FILE")
        database_path = Path(
            os.getenv("LINKEDIN_AUTOMATION_DATABASE", root / "data" / "research.sqlite3")
        ).resolve()
        database_url = os.getenv(
            "LINKEDIN_AUTOMATION_DATABASE_URL", f"sqlite:///{database_path.as_posix()}"
        )
        return cls(
            database_path=database_path,
            database_url=database_url,
            artifact_dir=Path(
                os.getenv("LINKEDIN_AUTOMATION_ARTIFACT_DIR", root / "artifacts")
            ).resolve(),
            browser_data_dir=Path(
                os.getenv("LINKEDIN_AUTOMATION_BROWSER_DATA", root / "browser-data")
            ).resolve(),
            screenshot_dir=Path(
                os.getenv("LINKEDIN_AUTOMATION_SCREENSHOT_DIR", root / "screenshots")
            ).resolve(),
            google_credentials_path=Path(credentials).resolve() if credentials else None,
            google_token_path=Path(token).resolve() if token else None,
        )
