"""The streaming engine (BE §7).

The core of the system, split so that each hard part can be unit-tested on its own:

* ``agreement``   — LocalAgreement-*n*, the commit policy
* ``buffer``      — the growing buffer, trimming, and timestamp rebasing
* ``guards``      — the six conditions that override the normal path
* ``segmenter``   — grouping committed words into readable segments
* ``events``      — the committed / hypothesis output contract
* ``engine``      — the orchestrator for offline models
* ``passthrough`` — the bypass path for streaming-native models

Both engines produce identical events, so nothing downstream can tell which one is running.
"""

from .agreement import LocalAgreement, longest_common_prefix, normalise
from .buffer import StreamBuffer
from .engine import EngineMetrics, StreamingEngine
from .events import CommittedSegment, EngineEvent, EngineNotice, HypothesisUpdate
from .guards import (
    GuardAction,
    GuardDecision,
    GuardWarning,
    Severity,
    StreamGuards,
    find_repetition,
)
from .passthrough import PassthroughEngine, build_engine
from .segmenter import Segmenter, ends_sentence

__all__ = [
    "CommittedSegment",
    "EngineEvent",
    "EngineMetrics",
    "EngineNotice",
    "GuardAction",
    "GuardDecision",
    "GuardWarning",
    "HypothesisUpdate",
    "LocalAgreement",
    "PassthroughEngine",
    "Segmenter",
    "Severity",
    "StreamBuffer",
    "StreamGuards",
    "StreamingEngine",
    "build_engine",
    "ends_sentence",
    "find_repetition",
    "longest_common_prefix",
    "normalise",
]
