"""Seam A — the ASR abstraction (BE §6).

One of the two interfaces the architecture rests on. It makes the choice of speech model a
configuration value rather than an architectural commitment, and keeps the streaming engine from
ever learning which model it is driving.

* ``contract``       — the interface, word tokens, and capability declaration
* ``registry``       — backend registration and construction from configuration
* ``mock``           — a scripted backend, which is what makes the commit policy testable
* ``faster_whisper`` — the real default, behind an optional dependency
* ``prompting``      — session and rolling-context term biasing
* ``lifecycle``      — asynchronous load, warm-up, swap, and unload

**The boundary that matters:** backends return timestamps relative to the audio array they were
handed. Converting to session-absolute time is the streaming engine's job, and nothing here should
attempt it.
"""

from .contract import (
    EMPTY_RESULT,
    AsrBackend,
    AsrCapabilities,
    AsrError,
    AsrLoadError,
    AsrResult,
    AsrUnavailableError,
    WordToken,
)
from .lifecycle import AsrLifecycle, LoadProgress, LoadState
from .mock import MockAsrBackend, MockScript, scripted
from .prompting import PromptBuilder
from .registry import (
    BackendInfo,
    available_backends,
    build_backend,
    register,
    registered_ids,
)

__all__ = [
    "EMPTY_RESULT",
    "AsrBackend",
    "AsrCapabilities",
    "AsrError",
    "AsrLifecycle",
    "AsrLoadError",
    "AsrResult",
    "AsrUnavailableError",
    "BackendInfo",
    "LoadProgress",
    "LoadState",
    "MockAsrBackend",
    "MockScript",
    "PromptBuilder",
    "WordToken",
    "available_backends",
    "build_backend",
    "register",
    "registered_ids",
    "scripted",
]
