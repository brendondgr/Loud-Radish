"""Session wiring, workers, metrics, and degradation (BE §14, §15, §16).

* ``workers``      — the drop-oldest queue and the worker threads that drain it
* ``metrics``      — pipeline health, gathered in one place
* ``degradation``  — what each failure means and what to do about it
* ``manager``      — capture → VAD → engine → store → transport

The manager is event-loop agnostic: it emits by calling a plain function, and the transport layer
marshals onto the loop. asyncio stays out of the audio path.
"""

from . import degradation
from .manager import CapturedFrame, EmitFn, SessionError, SessionManager
from .metrics import PipelineMetrics
from .workers import DropOldestQueue, QueueStats, Worker

__all__ = [
    "CapturedFrame",
    "DropOldestQueue",
    "EmitFn",
    "PipelineMetrics",
    "QueueStats",
    "SessionError",
    "SessionManager",
    "Worker",
    "degradation",
]
