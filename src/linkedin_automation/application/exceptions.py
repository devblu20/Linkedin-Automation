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

    def __init__(
        self,
        message: str,
        *,
        checkpoint_page: int = 0,
        pages_visited: int = 0,
        hidden_result_cards: int = 0,
    ) -> None:
        super().__init__(message)
        self.checkpoint_page = checkpoint_page
        self.pages_visited = pages_visited
        self.hidden_result_cards = hidden_result_cards


class AuthenticationRequiredError(CollectionError):
    """The user must authenticate in the supervised browser."""


class BrowserClosedError(CollectionError):
    """The supervised browser was closed while collection was active."""


class SecurityChallengeError(CollectionError):
    """A checkpoint or CAPTCHA requires user action."""


class LayoutChangedError(CollectionError):
    """Expected page semantics are no longer available."""


class LimitedVisibilityError(CollectionError):
    """LinkedIn did not expose profile identities or canonical profile URLs."""


class RateLimitedError(CollectionError):
    """The source requested that collection stop."""


class SearchQuotaExceededError(RateLimitedError):
    """LinkedIn explicitly reported an exhausted people-search allowance."""


class CollectionLimitError(CollectionError):
    """A configured local collection limit has been reached."""


class ReportError(WorkflowError):
    """A report could not be generated or verified."""


class StorageError(WorkflowError):
    """A report could not be uploaded safely."""
