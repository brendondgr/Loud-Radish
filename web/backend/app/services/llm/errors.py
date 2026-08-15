"""Why a language model request failed, in terms the user can act on (BE §10.7).

A connection either works or it fails in one of **four** ways, and each one has a different remedy.
"Connection failed" tells the user nothing; "no server responded at http://localhost:11434/v1 —
check that Ollama is running" tells them exactly what to do next. That distinction is the entire
reason this module exists rather than a bare ``RuntimeError``.

The four results are fixed by ``docs/api-contract.md``. Anything more specific — a missing model, a
request that timed out, a malformed response — is a *message* within one of them, never a new
result, because the frontend renders the result and reads the message aloud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: The four outcomes of a connection test. Not extensible: the frontend switches on this.
TestResult = Literal["connected", "no_server", "auth_rejected", "server_error"]


@dataclass(frozen=True)
class ConnectionTest:
    """The outcome of probing a provider, as returned by ``POST /api/llm/test``."""

    result: TestResult
    #: Names the specific remedy. Never generic failure text.
    message: str
    #: Model identifiers the endpoint reported. Empty unless ``result`` is ``connected``.
    models: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the provider is usable."""
        return self.result == "connected"

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload for ``POST /api/llm/test``."""
        return {"result": self.result, "message": self.message, "models": list(self.models)}


class LlmError(RuntimeError):
    """A language model failure that carries its own test result and remedy.

    Every subclass fixes :attr:`result`. Route handlers surface :attr:`message` directly, so the
    message must always be safe to display and must always name the next action.
    """

    #: The connection-test result this failure corresponds to.
    result: TestResult = "server_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def as_test(self) -> ConnectionTest:
        """Express this failure as a connection-test outcome."""
        return ConnectionTest(result=self.result, message=self.message)


class LlmUnreachableError(LlmError):
    """Nothing answered at the endpoint — the server is not running, or the address is wrong."""

    result: TestResult = "no_server"


class LlmAuthError(LlmError):
    """The provider rejected the credential, or none was supplied."""

    result: TestResult = "auth_rejected"


class LlmServerError(LlmError):
    """The server answered, but not usefully — a missing model, a timeout, a 500."""

    result: TestResult = "server_error"


class LlmConfigError(LlmError):
    """The configuration itself is incomplete, so no request was attempted.

    Classified as ``server_error`` because from the user's point of view the outcome is the same:
    the assistant cannot answer. The message says which field is missing.
    """

    result: TestResult = "server_error"


def unreachable(endpoint: str, detail: str = "") -> LlmUnreachableError:
    """Build the "nothing is listening" failure, naming the address that was tried."""
    suffix = f" ({detail})" if detail else ""
    return LlmUnreachableError(
        f"No server responded at {endpoint}{suffix}. Check that it is running, "
        f"and that the address in settings matches the port it is listening on."
    )


def timed_out(endpoint: str, seconds: float) -> LlmServerError:
    """Build the "answered too slowly" failure.

    Distinct from :func:`unreachable` on purpose: a timeout means something *is* there, so the
    remedy is a smaller model or a longer timeout, not starting the server.
    """
    return LlmServerError(
        f"{endpoint} did not respond within {seconds:.0f} seconds. "
        f"The model may still be loading, or the request may be too large for it."
    )


def auth_rejected(provider: str, has_credential: bool) -> LlmAuthError:
    """Build the credential failure, distinguishing "wrong key" from "no key"."""
    if not has_credential:
        return LlmAuthError(
            f"{provider} requires an API key and none is stored. Add one in Settings → Assistant."
        )
    return LlmAuthError(
        f"{provider} rejected the stored API key. Replace it in Settings → Assistant."
    )


def model_missing(model: str, endpoint: str, available: list[str]) -> LlmServerError:
    """Build the "server is fine, that model is not there" failure.

    Lists what *is* available, because the usual cause is a name that differs by a suffix.
    """
    if available:
        listed = ", ".join(available[:8])
        more = f", and {len(available) - 8} more" if len(available) > 8 else ""
        return LlmServerError(
            f"{endpoint} has no model named {model!r}. It offers: {listed}{more}."
        )
    return LlmServerError(
        f"{endpoint} has no model named {model!r}, and reports no models at all. "
        f"Check that a model is loaded on the server."
    )


def short_message(text: str, limit: int = 160) -> str:
    """Collapse and trim a server's own error text to something a banner can hold.

    Server errors are frequently a page of HTML or a stack trace. Quoting the first line is useful;
    quoting all of it makes the banner unreadable and buries the remedy.
    """
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else f"{collapsed[:limit]}…"


def bad_response(endpoint: str, detail: str) -> LlmServerError:
    """Build the "answered, but not in a shape we understand" failure."""
    return LlmServerError(
        f"{endpoint} returned a response this application could not read ({detail}). "
        f"Check that the address points at an OpenAI-compatible API path."
    )
