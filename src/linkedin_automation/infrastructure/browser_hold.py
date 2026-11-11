"""Durable, profile-scoped pause after LinkedIn requests human intervention."""

import json
from datetime import UTC, datetime
from pathlib import Path

from linkedin_automation.application.exceptions import (
    AuthenticationRequiredError,
    RateLimitedError,
    SecurityChallengeError,
)


def requires_manual_intervention(error: BaseException) -> bool:
    """Browser delivery wraps preflight errors; preserve their original safety signal."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(
            error, (AuthenticationRequiredError, SecurityChallengeError, RateLimitedError)
        ):
            return True
        error = error.__cause__ or error.__context__
    return False


class BrowserHold:
    def __init__(self, profile_dir: Path):
        self.path = Path(profile_dir).resolve() / ".bluqq-account-hold.json"

    @property
    def active(self) -> bool:
        return self.path.exists()

    def pause(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # No credentials, cookies, page contents or identity documents are recorded.
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"paused_at": datetime.now(UTC).isoformat(), "manual_login_required": True}),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def clear_after_manual_login(self) -> None:
        self.path.unlink(missing_ok=True)
