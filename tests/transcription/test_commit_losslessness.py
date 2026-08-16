"""Nothing said may be dropped, whatever the guards decide.

Reported: "when there is a lack of audio or a gap in signal, the engine triggers a forced commit,
and part of the speech is lost in the process", with a log line naming the `commit-timeout` guard.
The requirement attached to it is unconditional — *every word said over the course of the recording
needs to be retained rather than discarded when a commit is forced*.

**This file is the judge, and it was written before the fix.** A harness authored afterwards proves
only that a change is self-consistent with itself; this one has to be able to say which words went
missing and under which guard, so that the repair is aimed rather than guessed. The same discipline
caught a leaky-queue regression under D-027 that every existing test had passed.

**How a drop is detected.** The mock backend is positional: script word `i` occupies
`[i / words_per_second, (i + 1) / words_per_second)` of absolute session time, and a pass returns
the words actually inside the submitted audio. So the committed transcript, mapped back to script
indices, must be a **contiguous run starting at zero**. A gap in that run is a word that was spoken
into the engine and never came out — which is the fault, stated as an assertion. The tail may be
uncommitted when the run ends, so the property checked is "no gaps", not "everything"; `flush`
closes the session first so that the tail is only ever short.

The words are numbered rather than prose so that a failure names the missing index instead of
leaving a reader to diff two paragraphs.
"""

from __future__ import annotations

import re

import numpy as np
import pytest
from app.config.schema import StreamingConfig
from app.services.asr import MockAsrBackend, MockScript, positional_audio
from app.services.audio.formats import SAMPLE_RATE
from app.services.streaming import CommittedSegment, StreamingEngine

FRAME_SECONDS = 0.25
WORDS_PER_SECOND = 2.5
SILENT_FRAME = np.zeros(int(FRAME_SECONDS * SAMPLE_RATE), dtype=np.float32)

#: Two hundred distinguishable words — eighty seconds of speech at the scripted rate.
SCRIPT = [f"w{i:03d}" for i in range(200)]

#: The mock marks a revised word with `'` and an unstable one with `-<pass>`. Neither changes which
#: word was said, and this test is about *which*, not about how it was spelled on the way out.
DECORATION = re.compile(r"(?:-\d+)?'*$")


def undecorate(word: str) -> str:
    return DECORATION.sub("", word)


def build(**overrides) -> tuple[StreamingEngine, MockAsrBackend]:
    """An engine over the numbered script, with an unstable tail unless told otherwise.

    The tail is unstable by default because a perfectly stable model never trips a guard, and a
    guard that never fires is a guard whose losslessness has not been tested.
    """
    streaming = {
        "agreement_count": 2,
        "step_s": 0.5,
        "min_buffer_s": 0.4,
        "max_buffer_s": 25.0,
        "commit_timeout_s": 15.0,
        "retained_context_s": 0.5,
        "max_segment_s": 30.0,
    }
    script = {"words": SCRIPT, "words_per_second": WORDS_PER_SECOND, "unstable_tail": 4}
    for key, value in overrides.items():
        (streaming if key in streaming else script)[key] = value

    backend = MockAsrBackend(MockScript(**script))  # type: ignore[arg-type]
    backend.load()
    engine = StreamingEngine(
        config=StreamingConfig(**streaming),
        transcribe=backend.transcribe,
        capabilities=backend.capabilities,
        model_id=backend.model_id,
    )
    return engine, backend


def speak(engine, frames: int, speaking: bool = True, silent: bool = False) -> list:
    """Feed frames of position-encoded speech and collect everything the engine emitted."""
    events: list = []
    for _ in range(frames):
        chunk = SILENT_FRAME if silent else positional_audio(engine.session_seconds, FRAME_SECONDS)
        events.extend(engine.add_audio(chunk, speaking=speaking))
    return events


def committed_words(events: list) -> list[str]:
    """Every word the engine committed, in order, stripped of the mock's decorations."""
    words: list[str] = []
    for event in events:
        if isinstance(event, CommittedSegment):
            words.extend(undecorate(w) for w in event.segment.text.split())
    return words


def missing_indices(words: list[str]) -> list[int]:
    """Script indices that were spoken into the engine and never came out.

    Only positions *inside* the committed run count. A word after the last one committed has not
    been dropped, it has not been reached — and the difference matters, or every run would report
    its own tail as a loss.
    """
    seen = sorted({int(word[1:]) for word in words if re.fullmatch(r"w\d{3}", word)})
    if not seen:
        return []
    return [index for index in range(seen[0], seen[-1] + 1) if index not in set(seen)]


def report(words: list[str]) -> str:
    """A failure message that names the gap rather than describing one."""
    gaps = missing_indices(words)
    named = [f"w{index:03d}" for index in gaps[:12]]
    return (
        f"{len(gaps)} spoken word(s) never reached the transcript: "
        f"{named}{' ...' if len(gaps) > 12 else ''}"
    )


# -- the property, under each guard that can force a commit -------------------------------------


def test_a_stable_model_loses_nothing() -> None:
    """The control. If this fails the harness is wrong, not the engine."""
    engine, _ = build(unstable_tail=0)
    events = speak(engine, frames=200)
    events.extend(engine.flush())

    words = committed_words(events)
    assert words, "the control run committed nothing at all"
    assert missing_indices(words) == [], report(words)


def test_the_commit_timeout_guard_loses_nothing() -> None:
    """**The reported fault.**

    An unstable tail can never reach agreement, so nothing commits naturally and the guard fires
    after fifteen seconds of continuous speech — exactly the log line in the report:
    `No commit for 15 s of continuous speech; committing so the transcript keeps moving.`
    """
    engine, _ = build(unstable_tail=6)
    events = speak(engine, frames=280)  # 70 s: the guard fires several times over
    events.extend(engine.flush())

    words = committed_words(events)
    assert words, "nothing was committed at all"
    assert missing_indices(words) == [], report(words)


def test_the_maximum_buffer_guard_loses_nothing() -> None:
    """The guard whose own comment says it discards audio.

    `Discard it rather than resubmitting audio that already failed` is a defensible trade against a
    model that produced nothing usable, and it is not defensible against a requirement that no word
    may be lost. If those two cannot both hold, this test is where that shows.
    """
    engine, _ = build(unstable_tail=8, max_buffer_s=6.0, commit_timeout_s=60.0)
    events = speak(engine, frames=200)
    events.extend(engine.flush())

    words = committed_words(events)
    assert words, "nothing was committed at all"
    assert missing_indices(words) == [], report(words)


def test_a_pause_in_the_middle_loses_nothing() -> None:
    """The silence gate discards the whole buffer on the detector's word.

    A gap in signal is the other half of the report. When the gate closes over speech the detector
    misjudged, whatever was buffered goes with it — so the run below speaks, pauses, and speaks
    again, and the words either side of the pause must all survive.
    """
    engine, _ = build()
    events = speak(engine, frames=80)
    events.extend(speak(engine, frames=12, speaking=False, silent=True))
    events.extend(speak(engine, frames=80))
    events.extend(engine.flush())

    words = committed_words(events)
    assert words, "nothing was committed at all"
    assert missing_indices(words) == [], report(words)


def test_speech_the_detector_calls_silence_is_still_transcribed() -> None:
    """**The harshest case, and the one the report describes as "a gap in signal".**

    The VAD is a detector, not an oracle. Here it is wrong: real position-encoded speech arrives
    while `speaking=False`. The buffer is discarded on the gate's say-so, and every word inside it
    goes with it. Nothing about "no spoken content may be dropped" admits an exception for a
    detector that guessed wrong.
    """
    engine, _ = build()
    events = speak(engine, frames=60)
    events.extend(speak(engine, frames=40, speaking=False))  # real speech, misjudged
    events.extend(speak(engine, frames=60))
    events.extend(engine.flush())

    words = committed_words(events)
    assert words, "nothing was committed at all"
    assert missing_indices(words) == [], report(words)


@pytest.mark.parametrize("tail", [2, 6, 10])
def test_losslessness_does_not_depend_on_how_unstable_the_model_is(tail: int) -> None:
    """How far a model rewrites its tail changes *when* guards fire, never whether words survive."""
    engine, _ = build(unstable_tail=tail)
    events = speak(engine, frames=240)
    events.extend(engine.flush())

    words = committed_words(events)
    assert missing_indices(words) == [], report(words)
