"""Environment-backed local runtime paths and integration settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


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
        # Read local secrets without mutating process-wide state. Explicit shell variables win.
        file_values = dotenv_values(root / ".env")

        def setting(name: str) -> str | None:
            return os.getenv(name) or file_values.get(name)

        credentials = setting("GOOGLE_OAUTH_CLIENT_FILE")
        token = setting("GOOGLE_OAUTH_TOKEN_FILE")
        shell_database_path = os.getenv("LINKEDIN_AUTOMATION_DATABASE")
        shell_database_url = os.getenv("LINKEDIN_AUTOMATION_DATABASE_URL")
        database_path = Path(
            shell_database_path
            or file_values.get("LINKEDIN_AUTOMATION_DATABASE")
            or root / "data" / "research.sqlite3"
        ).resolve()
        database_url = (
            shell_database_url
            or (
                None
                if shell_database_path
                else file_values.get("LINKEDIN_AUTOMATION_DATABASE_URL")
            )
            or f"sqlite:///{database_path.as_posix()}"
        )
        return cls(
            database_path=database_path,
            database_url=database_url,
            artifact_dir=Path(
                setting("LINKEDIN_AUTOMATION_ARTIFACT_DIR") or root / "artifacts"
            ).resolve(),
            browser_data_dir=Path(
                setting("LINKEDIN_AUTOMATION_BROWSER_DATA") or root / "browser-data"
            ).resolve(),
            screenshot_dir=Path(
                setting("LINKEDIN_AUTOMATION_SCREENSHOT_DIR") or root / "screenshots"
            ).resolve(),
            google_credentials_path=Path(credentials).resolve() if credentials else None,
            google_token_path=Path(token).resolve() if token else None,
        )
