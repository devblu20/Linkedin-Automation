"""Idempotent Google Drive upload using narrow OAuth file scope."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from linkedin_automation.application.exceptions import StorageError
from linkedin_automation.domain.models import StoredArtifact

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"


class GoogleDriveStorage:
    """Upload reports idempotently using Drive app properties."""

    def __init__(self, service: Any, *, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._service = service
        self._max_attempts = max_attempts

    @classmethod
    def from_oauth(cls, credentials_path: Path, token_path: Path) -> GoogleDriveStorage:
        """Authorize locally and keep refresh tokens outside the repository."""
        if not credentials_path.is_file():
            raise StorageError(f"Google OAuth client file not found: {credentials_path}")
        credentials: Credentials | None = None
        if token_path.is_file():
            credentials = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
                str(token_path), [DRIVE_FILE_SCOPE]
            )
        if credentials is not None and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())  # type: ignore[no-untyped-call]
        if credentials is None or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), [DRIVE_FILE_SCOPE]
            )
            credentials = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
        return cls(build("drive", "v3", credentials=credentials, cache_discovery=False))

    def upload(self, path: Path, *, folder_id: str, idempotency_key: str) -> StoredArtifact:
        if not path.is_file():
            raise StorageError(f"report file not found: {path}")
        if not folder_id.strip():
            raise StorageError("Google Drive folder ID is empty")
        existing = self._find_existing(folder_id, idempotency_key)
        if existing is not None:
            return self._artifact(existing)
        metadata = {
            "name": path.name,
            "parents": [folder_id],
            "appProperties": {"researchRunId": idempotency_key},
        }
        media = MediaFileUpload(
            str(path),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            resumable=True,
        )
        response = self._execute_with_retry(
            lambda: (
                self._service.files()
                .create(body=metadata, media_body=media, fields="id,webViewLink")
                .execute()
            )
        )
        return self._artifact(response)

    def authorized_email(self) -> str:
        """Return the Google account associated with the active OAuth credentials."""
        response = self._execute_with_retry(
            lambda: self._service.about().get(fields="user(emailAddress)").execute()
        )
        user = response.get("user", {})
        email = str(user.get("emailAddress", "")).strip()
        if not email:
            raise StorageError("Google Drive did not return the authorized account email")
        return email

    def _find_existing(self, folder_id: str, idempotency_key: str) -> dict[str, Any] | None:
        safe_key = idempotency_key.replace("'", "\\'")
        safe_folder = folder_id.replace("'", "\\'")
        query = (
            f"'{safe_folder}' in parents and trashed = false and "
            f"appProperties has {{ key='researchRunId' and value='{safe_key}' }}"
        )
        response = self._execute_with_retry(
            lambda: (
                self._service.files()
                .list(q=query, spaces="drive", fields="files(id,webViewLink)", pageSize=1)
                .execute()
            )
        )
        files = response.get("files", [])
        return files[0] if files else None

    def _execute_with_retry(self, operation: Any) -> dict[str, Any]:
        for attempt in range(1, self._max_attempts + 1):
            try:
                result: dict[str, Any] = operation()
                return result
            except HttpError as error:
                status = getattr(error.resp, "status", 0)
                if status not in {429, 500, 502, 503, 504} or attempt == self._max_attempts:
                    raise StorageError(
                        f"Google Drive request failed with status {status}"
                    ) from error
                time.sleep(2 ** (attempt - 1))
        raise StorageError("Google Drive request failed")

    @staticmethod
    def _artifact(response: dict[str, Any]) -> StoredArtifact:
        file_id = str(response.get("id", ""))
        if not file_id:
            raise StorageError("Google Drive response did not include a file ID")
        return StoredArtifact(
            file_id=file_id,
            web_url=str(
                response.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")
            ),
        )
