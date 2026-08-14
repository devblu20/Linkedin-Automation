"""Typed workflow failures exposed to user interfaces."""


class WorkflowError(Exception):
    """Base class for an actionable workflow failure."""

    def __init__(self, message: str, *, run_id: str | None = None) -> None:
        super().__init__(message)
        self.run_id = run_id

    def __str__(self) -> str:
        message = super().__str__()
        return f"{message} (run ID: {self.run_id})" if self.run_id else message


class RunNotFoundError(WorkflowError):
    """A requested run ID does not exist."""


class CollectionError(WorkflowError):
    """Collection could not continue safely."""

    def __init__(self, message: str, *, checkpoint_page: int = 0) -> None:
        super().__init__(message)
        self.checkpoint_page = checkpoint_page


class AuthenticationRequiredError(CollectionError):
    """The user must authenticate in the supervised browser."""


class SecurityChallengeError(CollectionError):
    """A checkpoint or CAPTCHA requires user action."""


class LayoutChangedError(CollectionError):
    """Expected page semantics are no longer available."""


class RateLimitedError(CollectionError):
    """The source requested that collection stop."""


class CollectionLimitError(CollectionError):
    """A configured local collection limit has been reached."""


class ReportError(WorkflowError):
    """A report could not be generated or verified."""


class StorageError(WorkflowError):
    """A report could not be uploaded safely."""
