"""Request and response shapes — the runtime enforcement of ``docs/api-contract.md``.

Kept in one module because the surface is small and reading it in one place is worth more than the
symmetry of one file per route group. Split it if it passes ~400 lines.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..services.session.modes import CaptureMode


class _Model(BaseModel):
    """Reject unknown keys, so a typo in a request is an error rather than a silent no-op."""

    model_config = ConfigDict(extra="forbid")


# -- errors ----------------------------------------------------------------------------


class ErrorBody(_Model):
    """The error envelope. Never a bare string."""

    code: str
    #: Human-readable and safe to display. Says what happened *and what to do about it*.
    message: str
    severity: Literal["info", "warning", "critical"] = "warning"


class ErrorResponse(_Model):
    """The body of any non-2xx response."""

    error: ErrorBody


# -- session ---------------------------------------------------------------------------


class CaptureOptions(_Model):
    """Per-run switches collected before capture begins (D-020).

    Deliberately per-run rather than configuration: a user who recorded one window without video
    should not silently get no video the next time. Only ``window`` mode reads them; the other two
    have nothing to choose.

    All three false records nothing and is refused. The interface refuses it as a courtesy — this
    is the rule.
    """

    live_transcription: bool = True
    post_transcription: bool = True
    video: bool = True

    @property
    def records_nothing(self) -> bool:
        return not (self.live_transcription or self.post_transcription or self.video)


class StartSessionRequest(_Model):
    """Optional metadata for a new session. Everything else comes from configuration."""

    title: str = ""
    venue: str = ""
    speaker: str = ""
    #: Which capture mode to run (D-020). Defaults to the original behaviour, so a client that
    #: predates capture modes keeps working unchanged.
    mode: CaptureMode = "live"
    #: Only meaningful for ``window``. Absent means the defaults above.
    options: CaptureOptions | None = None


class SessionResponse(_Model):
    """The current session and pipeline state."""

    running: bool
    session: dict[str, Any] | None = None
    asr: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    #: The post-capture transcription pass, when one is running or has just finished (D-021).
    #: Read on page load, so a reload during a half-hour pass resumes showing its progress rather
    #: than an idle interface with no explanation for the missing transcript.
    transcription: dict[str, Any] | None = None
    #: How much audio the current recording has captured, when one is being written.
    recording: dict[str, Any] | None = None
    #: The window capture, when `window` mode is recording video (D-022).
    capture: dict[str, Any] | None = None


class SessionStoppedResponse(_Model):
    """Final statistics for a session that has just ended."""

    session_id: str
    stats: dict[str, Any]


# -- audio -----------------------------------------------------------------------------


class DeviceListResponse(_Model):
    """Microphones and loopback devices in one merged list, each tagged with its type."""

    devices: list[dict[str, Any]]
    #: False when the optional audio backend is not installed; the message says how to add it.
    device_support: bool
    note: str = ""


class SelectDeviceRequest(_Model):
    """Choose a capture source."""

    device_id: str | None = None
    source_type: Literal["microphone", "loopback", "file"] = "microphone"
    file_path: str | None = None


# -- speech recognition ------------------------------------------------------------------


class AsrModelsResponse(_Model):
    """Every backend, including ones that cannot run here, with the reason attached."""

    backends: list[dict[str, Any]]
    current: dict[str, Any]


class LoadModelRequest(_Model):
    """Load a specific model."""

    backend: str
    model: str
    device: Literal["auto", "cpu", "cuda"] | None = None
    precision: Literal["int8", "float16", "float32"] | None = None


class SessionPromptRequest(_Model):
    """The biasing prompt the user pastes before the talk (BE §6.5)."""

    prompt: str = ""
    use_session_prompt: bool = True
    use_rolling_prompt: bool = True


# -- configuration ---------------------------------------------------------------------


class ConfigResponse(_Model):
    """The full resolved configuration, plus what each area costs to change."""

    config: dict[str, Any]
    presets: list[dict[str, str]]


class ConfigPatchRequest(_Model):
    """Dotted-path changes, applied atomically."""

    changes: dict[str, Any]
    #: ``runtime`` by default; ``user`` also persists to the config file.
    layer: Literal["runtime", "session", "user"] = "runtime"


class ConfigPatchResponse(_Model):
    """What applying the change costs the running session (BE §13.3)."""

    applied: bool
    hot_swap: Literal["live", "restart-stage", "restart-session"]
    consequence: str
    config: dict[str, Any]


class PresetRequest(_Model):
    """Apply a named profile."""

    name: str


# -- transcript ------------------------------------------------------------------------


class SegmentListResponse(_Model):
    """A page of committed segments."""

    segments: list[dict[str, Any]]
    last_id: int


class SearchResponse(_Model):
    """Full-text search results."""

    query: str
    segments: list[dict[str, Any]]


class SummariesResponse(_Model):
    """The running outline."""

    summaries: list[dict[str, Any]]


class PolishedBlocksResponse(_Model):
    """The transcript rewritten for reading, one block per finished minute (D-018).

    Empty whenever no language model has been available — which is the ordinary state, and the
    signal to the frontend that it should keep showing raw segments.
    """

    blocks: list[dict[str, Any]]


class GlossaryResponse(_Model):
    """The session glossary."""

    terms: list[dict[str, Any]]


# -- language model --------------------------------------------------------------------


class LlmConfigResponse(_Model):
    """Both provider configurations, and whether a credential exists.

    **Never carries a credential value.** The frontend learns only whether one is present.
    """

    mode: Literal["local", "api"]
    local: dict[str, Any]
    api: dict[str, Any]
    generation: dict[str, Any]
    credential: dict[str, Any]


class LlmCredentialRequest(_Model):
    """Store a credential in the OS credential store."""

    provider: str
    #: Write-only. It is never echoed back in any response.
    value: str


class ConnectionTestResponse(_Model):
    """A specific result, never generic failure text (FE §7.3)."""

    result: Literal["connected", "no_server", "auth_rejected", "server_error"]
    message: str
    models: list[str] = Field(default_factory=list)


# -- chat ------------------------------------------------------------------------------


class ChatSendRequest(_Model):
    """Ask a question. The answer streams over the WebSocket."""

    message: str = ""
    #: A quick-action id, used instead of a typed message.
    action: str | None = None
    #: Text the user selected in the transcript, for "Ask about this".
    quote: str | None = None
    quote_start: float | None = None


class ChatSendResponse(_Model):
    """Acknowledgement. The answer itself arrives as ``chat.delta`` frames."""

    request_id: str
    context_timestamp: float


class ChatHistoryResponse(_Model):
    """The conversation so far."""

    messages: list[dict[str, Any]]


class QuickActionsResponse(_Model):
    """The configured one-tap prompts."""

    actions: list[dict[str, Any]]
