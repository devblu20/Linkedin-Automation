"""Mocked tests for idempotent Google Drive delivery."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from linkedin_automation.infrastructure.storage import GoogleDriveStorage


class FakeRequest:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def execute(self) -> dict[str, Any]:
        return self.response


class FakeFiles:
    def __init__(self, existing: list[dict[str, str]]) -> None:
        self.existing = existing
        self.created = 0
        self.create_body: dict[str, Any] | None = None

    def list(self, **_: Any) -> FakeRequest:
        return FakeRequest({"files": self.existing})

    def create(self, **kwargs: Any) -> FakeRequest:
        self.created += 1
        self.create_body = kwargs["body"]
        return FakeRequest({"id": "new-id", "webViewLink": "https://drive/new-id"})


class FakeService:
    def __init__(self, files: FakeFiles) -> None:
        self._files = files

    def files(self) -> FakeFiles:
        return self._files

    def about(self) -> FakeAbout:
        return FakeAbout()


class FakeAbout:
    def get(self, **_: Any) -> FakeRequest:
        return FakeRequest({"user": {"emailAddress": "marketingcodex77@gmail.com"}})


def test_returns_existing_idempotent_upload(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"xlsx")
    files = FakeFiles([{"id": "existing", "webViewLink": "https://drive/existing"}])
    artifact = GoogleDriveStorage(FakeService(files)).upload(
        report, folder_id="folder", idempotency_key="run-id"
    )
    assert artifact.file_id == "existing"
    assert files.created == 0


def test_creates_upload_with_run_id_property(tmp_path: Path) -> None:
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"xlsx")
    files = FakeFiles([])
    artifact = GoogleDriveStorage(FakeService(files)).upload(
        report, folder_id="folder", idempotency_key="run-id"
    )
    assert artifact.file_id == "new-id"
    assert files.created == 1
    assert files.create_body is not None
    assert files.create_body["appProperties"] == {"researchRunId": "run-id"}


def test_returns_authorized_google_account() -> None:
    storage = GoogleDriveStorage(FakeService(FakeFiles([])))
    assert storage.authorized_email() == "marketingcodex77@gmail.com"
