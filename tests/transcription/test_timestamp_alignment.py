"""Do the transcript's timestamps point at the moment a thing was actually said?

The reported fault is that the assistant's citations do not line up with the recording — ask it
about something and the timestamp it gives lands somewhere else. Three causes were possible and
they have opposite fixes, so this file measures before anything is changed:

1. **the stored times are wrong** — a word's offset within an inference pass leaking out as though
   it were an offset within the session;
2. **the two passes disagree** — the live pass (revision 0) and the post-capture pass (revision 1)
   computing different origins, so switching revision moves every citation;
3. **the model invents them** — copying the shape of the `[MM:SS]` markers it is shown rather than
   the values, which no amount of correct storage would fix.

The tests here settle (1) and (2). A word's time comes back from the model relative to *the array
that was submitted*, and a recording is transcribed in overlapping windows — so every stored time
is a sum, and a sum done in the wrong place is invisible until someone clicks a citation. The
scripted transcriber below returns words at times it chooses, which is what makes the arithmetic
checkable rather than merely plausible: the expected absolute time is known exactly.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
from app.services.asr.contract import AsrResult, WordToken
from app.services.audio.formats import SAMPLE_RATE
from app.services.recording.batch import plan_windows, transcribe_file

#: Long enough to need several windows at the size used below, so the rebasing is exercised rather
#: than trivially correct on a single pass.
RECORDING_S = 95.0

WINDOW_S = 30.0
OVERLAP_S = 1.0


#: Second *n* of the recording is a pure tone at this frequency. Spaced widely enough that a
#: one-second slice identifies its own second unambiguously from a single FFT peak.
def tone_hz(second: int) -> float:
    return 300.0 + 20.0 * second


def write_wav(path: Path, seconds: float) -> Path:
    """Audio that **says what time it is**, one distinct tone per second.

    This is what makes the check independent. A transcriber that reads the frequency of the audio
    it was handed knows the absolute second that audio came from without being told, and without
    reproducing any of the arithmetic under test. A stored timestamp can then be compared against
    the recording itself rather than against a second implementation of the same sum.
    """
    total = int(seconds)
    chunks = [
        np.sin(
            2 * np.pi * tone_hz(second) * (np.arange(SAMPLE_RATE, dtype=np.float64) / SAMPLE_RATE)
        )
        * 0.6
        for second in range(total)
    ]
    signal = np.concatenate(chunks)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((signal * 32767).astype(np.int16).tobytes())
    return path


def second_of(slice_: np.ndarray) -> int:
    """Which second of the recording this one-second slice came from, read off its frequency."""
    spectrum = np.abs(np.fft.rfft(slice_ * np.hanning(len(slice_))))
    peak_hz = float(np.fft.rfftfreq(len(slice_), 1 / SAMPLE_RATE)[int(np.argmax(spectrum))])
    return int(round((peak_hz - 300.0) / 20.0))


class MarkerTranscriber:
    """Returns one word per second, each naming the absolute second it was actually taken from.

    It derives that second from the audio, not from a counter — so it cannot drift in step with a
    bug in the code under test. A stored word whose text and whose `start` disagree is a rebasing
    fault, and the failure message names the second that went astray.
    """

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, samples: np.ndarray, prompt: str | None = None) -> AsrResult:
        self.calls.append(len(samples) / SAMPLE_RATE)

        words = []
        for offset in range(len(samples) // SAMPLE_RATE):
            chunk = samples[offset * SAMPLE_RATE : (offset + 1) * SAMPLE_RATE]
            words.append(
                WordToken(
                    text=f"t{second_of(chunk)}",
                    # Relative to the submitted array, which is what a real model returns.
                    start=float(offset),
                    end=float(offset) + 0.4,
                    confidence=0.9,
                )
            )
        return AsrResult(words=words, model_id="marker:test")


def transcribe(tmp_path: Path, **overrides):
    """Run a full pass over a synthetic recording and return its segments."""
    path = write_wav(tmp_path / "talk.wav", RECORDING_S)
    return transcribe_file(
        path,
        transcribe=MarkerTranscriber(),
        window_s=WINDOW_S,
        overlap_s=OVERLAP_S,
        **overrides,
    )


# -- the arithmetic ---------------------------------------------------------------------------


def test_a_word_is_stored_at_the_second_it_was_spoken(tmp_path: Path) -> None:
    """Every word names its own absolute time, so every stored time is checkable exactly.

    A word's offset is relative to the buffer submitted to the model. A recording is transcribed in
    windows. If the window's own start is added anywhere other than exactly once, timestamps drift
    by a whole window and every citation past the first thirty seconds points at the wrong moment —
    which is precisely the reported symptom.
    """
    segments = transcribe(tmp_path)
    assert segments, "the pass produced nothing to check"

    for segment in segments:
        for word in segment.words or []:
            expected = int(word.text.removeprefix("t"))
            assert word.start == pytest.approx(expected, abs=0.05), (
                f"{word.text!r} was spoken at {expected}s and stored at {word.start:.2f}s"
            )


def test_no_word_is_stored_beyond_the_end_of_the_recording(tmp_path: Path) -> None:
    """The cheapest possible check on the sum, and the one that catches double-counting.

    Adding the window offset twice puts the tail of a 95-second talk past three minutes. Nothing
    downstream validates that, so it surfaces as a citation that seeks past the end of the audio.
    """
    segments = transcribe(tmp_path)

    for segment in segments:
        assert segment.start <= RECORDING_S + 1.0
        assert segment.end <= RECORDING_S + 1.0


def test_segments_run_forwards(tmp_path: Path) -> None:
    """Out-of-order starts mean a window was rebased against the wrong origin."""
    starts = [segment.start for segment in transcribe(tmp_path)]

    assert starts == sorted(starts)


def test_the_two_passes_agree_on_when_things_were_said(tmp_path: Path) -> None:
    """The fault that would move every citation when the revision switch is used.

    A window session can hold a live transcript and a post-capture one at the same time (D-022), and
    a chat citation points into whichever is on screen. If the two passes computed different
    origins, switching revision would silently move every timestamp — and each pass would look
    perfectly self-consistent when examined alone.
    """
    live = transcribe(tmp_path, revision=0)
    second = transcribe(tmp_path, revision=1)

    assert [segment.revision for segment in live] == [0] * len(live)
    assert [segment.revision for segment in second] == [1] * len(second)
    assert [round(s.start, 2) for s in live] == [round(s.start, 2) for s in second]


def test_the_overlap_is_context_and_not_extra_transcript(tmp_path: Path) -> None:
    """A word seen by two windows must be stored once, at one time.

    The overlap exists so a word split across a boundary is seen whole by someone. Emitting it from
    both windows would put the same word at two different seconds, which reads downstream as the
    speaker having said it twice.
    """
    segments = transcribe(tmp_path)
    spoken = [word.text for segment in segments for word in segment.words or []]

    assert len(spoken) == len(set(spoken)), "a word in the overlap was emitted by both windows"


# -- the windows themselves --------------------------------------------------------------------


def test_windows_tile_the_recording_without_a_gap() -> None:
    """A gap is a stretch of talk nobody transcribes, and it is silent when it happens."""
    total = int(RECORDING_S * SAMPLE_RATE)
    windows = list(
        plan_windows(np.zeros(total, dtype=np.float32), window_s=WINDOW_S, overlap_s=OVERLAP_S)
    )

    assert windows[0].start_s == 0.0
    for earlier, later in zip(windows, windows[1:], strict=False):
        assert later.start_s <= earlier.end_s, "a stretch of the recording is in no window at all"
    assert windows[-1].end_s == pytest.approx(RECORDING_S, abs=0.01)


# -- citations the model produced ---------------------------------------------------------------
#
# The measurements above rule out the two storage causes: stored times are correct, and both passes
# agree. What is left is the model naming a number of the right *shape* rather than the right
# value — most often by interpolating between two lines, or by reading one off the summary block,
# whose ranges are not moments at all. A wrong timestamp shown confidently is worse than none: the
# reader clicks it, lands somewhere unrelated, and stops trusting the correct ones too.


def test_a_cited_moment_that_exists_survives() -> None:
    from app.services.context.assembler import resolve_citations

    answer, dropped = resolve_citations("The proof begins at [12:30].", {750.0})

    assert answer == "The proof begins at [12:30]."
    assert dropped == []


def test_an_invented_moment_is_removed_and_the_sentence_kept() -> None:
    """The claim is usually right; it was the number that was made up."""
    from app.services.context.assembler import resolve_citations

    answer, dropped = resolve_citations("The proof begins at [40:00].", {750.0})

    assert "[40:00]" not in answer
    assert answer == "The proof begins at."
    assert dropped == ["[40:00]"]


def test_rounding_to_the_nearest_second_is_not_an_invention() -> None:
    """A model that names 12:35 for a line at 12:34.6 found the right line."""
    from app.services.context.assembler import resolve_citations

    _answer, dropped = resolve_citations("As noted at [12:35].", {754.6})

    assert dropped == []


def test_hours_are_read_as_hours() -> None:
    """`[1:02:03]` is an hour in, `[1:02]` a minute in. Confusing them moves a citation."""
    from app.services.context.assembler import resolve_citations

    _kept, dropped = resolve_citations("At [1:02:03].", {3723.0})
    assert dropped == []

    _kept, dropped = resolve_citations("At [1:02].", {3723.0})
    assert dropped == ["[1:02]"]


def test_nothing_is_dropped_when_nothing_was_offered() -> None:
    """No context means no basis to judge, and silently stripping every citation would be worse."""
    from app.services.context.assembler import resolve_citations

    answer, dropped = resolve_citations("At [12:30].", set())

    assert answer == "At [12:30]."
    assert dropped == []


def test_the_offered_set_comes_from_the_segments_the_model_was_shown() -> None:
    from app.models.segment import Segment
    from app.services.context.assembler import offered_seconds

    segments = [
        Segment(id=1, start=10.0, end=12.0, text="one"),
        Segment(id=2, start=30.5, end=33.0, text="two"),
    ]

    assert offered_seconds(segments) == {10.0, 30.5}
