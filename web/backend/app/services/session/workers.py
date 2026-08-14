"""Queues and worker threads (BE §14).

Four workers connected by queues. This structure is what satisfies constraint **C5** — capture never
blocks on transcription.

=================== ========== ================ ==================================================
Worker              Priority   May block?       Queue behaviour
=================== ========== ================ ==================================================
Capture             Highest    **Never**        Writes into a bounded queue; drop-oldest, counted
ASR / engine        High       Yes              Reads that queue; this is the expensive one
Context pipeline    Low        Yes, interruptibly  Must never delay a chat request
Transport           Responsive Never on the path   Hypothesis coalesces; committed never drops
=================== ========== ================ ==================================================

The queue below is deliberately not :class:`queue.Queue`. That class blocks the producer when full,
which is precisely the behaviour this design forbids: a full queue is a health signal, not a reason
to stop capturing audio.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class QueueStats:
    """A snapshot of queue health, safe to serialise into a status event."""

    depth: int
    capacity: int
    dropped: int
    enqueued: int

    @property
    def fill(self) -> float:
        """How full the queue is, 0.0–1.0. Sustained high fill is backpressure."""
        return self.depth / self.capacity if self.capacity else 0.0


class DropOldestQueue(Generic[T]):
    """A bounded queue whose producer never waits.

    When full, the oldest item is discarded and counted. For audio this is the right trade: the
    words just spoken matter more than the words from four seconds ago, and a producer that stalls
    loses audio it can never recover.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("Queue capacity must be at least 1")
        self._capacity = capacity
        self._items: deque[T] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._dropped = 0
        self._enqueued = 0
        self._closed = False

    def put(self, item: T) -> bool:
        """Enqueue without ever blocking. Returns whether something had to be dropped."""
        with self._not_empty:
            if self._closed:
                return False
            self._enqueued += 1
            dropped = False
            if len(self._items) >= self._capacity:
                self._items.popleft()
                self._dropped += 1
                dropped = True
            self._items.append(item)
            self._not_empty.notify()
            return dropped

    def get(self, timeout: float | None = 0.1) -> T | None:
        """Dequeue the oldest item, waiting up to ``timeout``. ``None`` when nothing arrived."""
        with self._not_empty:
            if not self._items and not self._closed:
                self._not_empty.wait(timeout)
            if not self._items:
                return None
            return self._items.popleft()

    def drain(self) -> list[T]:
        """Remove and return everything queued."""
        with self._lock:
            items = list(self._items)
            self._items.clear()
            return items

    def close(self) -> None:
        """Wake any waiting consumer and refuse further writes."""
        with self._not_empty:
            self._closed = True
            self._not_empty.notify_all()

    def clear(self) -> None:
        """Discard queued items without touching the counters."""
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def dropped(self) -> int:
        """How many items have been discarded. Non-zero means lost audio."""
        with self._lock:
            return self._dropped

    def stats(self) -> QueueStats:
        """A consistent snapshot."""
        with self._lock:
            return QueueStats(
                depth=len(self._items),
                capacity=self._capacity,
                dropped=self._dropped,
                enqueued=self._enqueued,
            )


class Worker:
    """A named daemon thread that drains a queue until stopped.

    Exceptions in the callback are logged and swallowed. A worker that dies silently is the failure
    this whole pipeline is built to avoid — transcription would simply stop with the UI still
    showing "Recording".
    """

    def __init__(
        self, name: str, queue: DropOldestQueue, handler: Callable[[object], None]
    ) -> None:
        self._name = name
        self._queue = queue
        self._handler = handler
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._errors = 0

    @property
    def name(self) -> str:
        """The worker's thread name."""
        return self._name

    @property
    def is_running(self) -> bool:
        """Whether the thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def error_count(self) -> int:
        """How many handler exceptions have been swallowed. Non-zero deserves investigation."""
        return self._errors

    def start(self) -> None:
        """Begin draining the queue."""
        if self.is_running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Stop draining and join the thread."""
        self._stop.set()
        self._queue.close()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning("Worker %s did not stop within %.1f s", self._name, timeout)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get(timeout=0.1)
            if item is None:
                continue
            try:
                self._handler(item)
            except Exception:  # noqa: BLE001 - a dead worker is worse than a dropped item
                self._errors += 1
                logger.exception("Worker %s failed handling an item; continuing", self._name)
