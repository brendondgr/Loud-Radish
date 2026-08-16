"""Assertions that live in the recorder rather than in a test suite.

Research §9.1, and the most important paragraph in that document: *tests that check what your code
does cannot catch what the environment does.* A 1291-test suite passed while window capture was
broken in every user-visible respect, because every test asserted the shape of a launch line and
none opened a file. The specific faults it missed:

* a pipeline that wrote **480x16** — valid command, exit code 0, no warning;
* `pw-record` emitting an **AU container header** whose bytes decode to `NaN` — a capture that ran at
  the right rate, for the right duration, and transcribed silence, because one NaN poisons every
  downstream mean, peak and RMS.

Neither is detectable by a test double. Both are detectable in three lines by the running capture,
which is where those checks now are. **The tests in this file exercise the guards** — they are the
tests of the assertions, not a replacement for them.
"""

from __future__ import annotations

import numpy as np
import pytest
from app.services.audio.monitor import MonitorUnavailable
from app.services.audio.sources import monitor as monitor_module
from app.services.capture.geometry import GeometryError, resolve


class FakePipe:
    """Stands in for `pw-record`'s stdout, handing back bytes the test chose."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class FakeProcess:
    def __init__(self, payload: bytes) -> None:
        self.stdout = FakePipe(payload)
        self.stderr = FakePipe(b"")


def drive(source: monitor_module.MonitorSource, payload: bytes) -> list[Exception | None]:
    """Run the reader loop over a chosen byte stream and collect what it reported."""
    errors: list[Exception | None] = []
    source._process = FakeProcess(payload)  # type: ignore[assignment]
    source._on_frame = lambda _frame: None
    source._on_error = errors.append
    source._running.set()
    source._read()
    return errors


def test_a_frame_that_is_not_finite_is_refused_at_runtime() -> None:
    """The exact fault, stated as its signature rather than as one header's bytes.

    `pw-record` without `--container=raw` writes an AU header ahead of the samples, and reading it
    as float32 produced NaN. That capture ran for the right duration at the right rate, so nothing
    about its *shape* was wrong — only its values, which only the running capture can see. Whatever
    the source, a frame containing a value that is not finite must not reach the pipeline: one NaN
    poisons every downstream mean, peak and RMS, so the level meter reads nothing, the speech gate
    never opens, and the session transcribes silence.
    """
    source = monitor_module.MonitorSource(node="test", frame_ms=32)
    poisoned = np.full(source.frame_samples, np.nan, dtype=np.float32)

    errors = drive(source, poisoned.tobytes())

    assert errors, "a non-finite frame was delivered without complaint"
    assert isinstance(errors[0], MonitorUnavailable)
    assert "not finite" in str(errors[0])


def test_an_infinity_is_refused_too() -> None:
    """NaN is the observed case; an infinity breaks the same arithmetic just as thoroughly."""
    source = monitor_module.MonitorSource(node="test", frame_ms=32)
    poisoned = np.full(source.frame_samples, np.inf, dtype=np.float32)

    errors = drive(source, poisoned.tobytes())

    assert any("not finite" in str(error) for error in errors)


def test_ordinary_audio_passes_the_guard() -> None:
    """The guard must not cost a working capture anything.

    The stream running out *is* reported — a capture ending while the session still wants it is a
    real failure — so what this asserts is that nothing was reported about the *values*.
    """
    source = monitor_module.MonitorSource(node="test", frame_ms=32)
    audio = (np.sin(np.linspace(0, 40, source.frame_samples * 6)) * 0.4).astype(np.float32)

    errors = drive(source, audio.tobytes())

    assert not any("not finite" in str(error) for error in errors)


def test_the_guard_stops_after_the_first_second() -> None:
    """It exists to catch a mis-specified format, which is wrong from the first byte.

    Checking every frame for the length of a talk would spend real CPU on a question that was
    answered in the first second.
    """
    assert monitor_module.GUARD_SAMPLES == 16_000


def test_a_rate_mismatch_is_reported(caplog) -> None:
    """A capture asked for the wrong sample format still produces bytes at a plausible rate.

    Duration cannot tell the two apart. The sample *count* can, which is why it is checked against
    the wall clock — logged rather than raised, because by the time it is known the recording
    exists and discarding a talk over a rate mismatch is the worse outcome.
    """
    source = monitor_module.MonitorSource(node="test", frame_ms=32)

    with caplog.at_level("WARNING"):
        # Far fewer samples than 2 seconds at 16 kHz would be.
        source._check_rate(delivered=1000, elapsed=2.0)

    assert any("off the" in record.message for record in caplog.records)


def test_a_correct_rate_is_silent(caplog) -> None:
    source = monitor_module.MonitorSource(node="test", frame_ms=32)

    with caplog.at_level("WARNING"):
        source._check_rate(delivered=32_000, elapsed=2.0)

    assert not caplog.records


# -- geometry, at the other end of the same discipline -------------------------------------------


def test_a_degenerate_negotiated_size_refuses_before_it_reaches_an_encoder() -> None:
    """The 480x16 file's ancestor: a number that produced a plausible file nobody could watch."""
    with pytest.raises(GeometryError, match="Refusing"):
        resolve(1920, 32767, max_height=720)


def test_the_refusal_names_the_value_that_was_wrong() -> None:
    """A guard that says only "invalid" leaves the next person to bisect it."""
    with pytest.raises(GeometryError) as excinfo:
        resolve(0, 1080, max_height=720)

    assert "width is 0" in str(excinfo.value)
