"""Press a key, speak, press it again, and the words arrive where you were typing (D-049).

Every dependency is faked here — the microphone, the speech model, the language model, the
clipboard and the keystroke — because the interesting part is the **order of operations and what
happens when one of them refuses**, and none of that needs a real desktop.

The case worth reading first is `test_a_slow_tidy_falls_back_to_what_was_said`. Tidying through the
local model was measured at 8-10 seconds warm and 44 cold, which is why the tidy is bounded at all;
and `test_the_text_is_pasted_exactly_once` is why it falls back rather than pasting twice — a
second paste lands wherever the caret has moved to in the meantime, so the user ends up with both
versions, in two places, one of them mid-word.
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest
from app.config import ConfigStore
from app.services.asr.contract import AsrResult, WordToken
from app.services.audio.formats import SAMPLE_RATE
from app.services.dictation import DictationError, DictationService
from app.services.dictation.service import DONE, ERROR, RECORDING
from app.services.vad.base import VoiceActivityDetector


def said(text: str) -> AsrResult:
    """What a speech model returns for ``text``: one token per word, timed at 2.5 a second."""
    words = [
        WordToken(text=word, start=index / 2.5, end=(index + 1) / 2.5)
        for index, word in enumerate(text.split())
    ]
    return AsrResult(words=words, model_id="fake")


def tone(seconds: float, amplitude: float = 0.3) -> np.ndarray:
    """Something a loudness detector calls speech."""
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


class Loudness(VoiceActivityDetector):
    """Speech is anything louder than a whisper. Deterministic, so a cut lands where a test says."""

    def is_speech(self, frame: np.ndarray) -> bool:
        return bool(np.sqrt(np.mean(np.square(frame, dtype=np.float64))) > 0.01)

    def set_sensitivity(self, sensitivity: float) -> None:
        pass

    def reset(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "loudness"


class FakeSource:
    """A microphone that writes a fixed clip the moment it is started."""

    def __init__(
        self, *, fail: bool = False, samples: int = 16_000, audio: np.ndarray | None = None
    ) -> None:
        self.fail = fail
        self.audio = audio if audio is not None else np.zeros(samples, dtype=np.float32)
        self.started = False
        self.stopped = False

    def start(self, on_frame, _on_error=None) -> None:
        if self.fail:
            raise OSError("no such device")
        self.started = True
        on_frame(self.audio)

    def stop(self) -> None:
        self.stopped = True


class FakeAsr:
    """Answers each pass with the next scripted text, and remembers how much audio it was given."""

    is_ready = True

    def __init__(self, text: str = "so i was thinking um we should move the meeting", *texts):
        self.texts = [text, *texts]
        self.calls = 0
        self.seconds_seen: list[float] = []
        self.audio_seen: list[np.ndarray] = []

    def transcribe(self, audio, prompt=None):  # noqa: ANN001, ARG002
        self.calls += 1
        self.seconds_seen.append(len(audio) / SAMPLE_RATE if audio is not None else 0.0)
        self.audio_seen.append(audio)
        return said(self.texts[min(self.calls - 1, len(self.texts) - 1)])


class FakeLlm:
    """Answers `complete`, optionally slowly or badly — or differently on each call."""

    def __init__(
        self,
        answer: str = "So I was thinking we should move the meeting.",
        delay=0.0,
        *,
        answers: list[str] | None = None,
    ):
        self.answer = answer
        self.answers = answers
        self.delay = delay
        self.calls = 0

    async def complete(self, _messages, _options=None) -> str:  # noqa: ANN001
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.answers:
            return self.answers[min(self.calls - 1, len(self.answers) - 1)]
        return self.answer


class Desk:
    """The clipboard and the keystroke, recording what they were asked to do."""

    def __init__(self, *, copy_ok: bool = True, paste_ok: bool = True) -> None:
        self.copy_ok, self.paste_ok = copy_ok, paste_ok
        self.copied: list[str] = []
        self.pastes: list[str] = []
        self.notices: list[tuple[str, str]] = []


@pytest.fixture
def desk(monkeypatch):
    from app.desktop import clipboard, keystroke
    from app.desktop.outcome import Outcome
    from app.services.dictation import service as module

    board = Desk()

    def fake_copy(text: str) -> Outcome:
        if not board.copy_ok:
            return Outcome(False, detail="refused")
        board.copied.append(text)
        return Outcome(True, "fake-clipboard")

    def fake_paste(chord: str = "ctrl+v") -> Outcome:
        if not board.paste_ok:
            return Outcome(False, detail="nothing can type")
        board.pastes.append(chord)
        return Outcome(True, "fake-keystroke", chord)

    monkeypatch.setattr(module.clipboard, "copy", fake_copy)
    monkeypatch.setattr(
        module.clipboard, "read_back", lambda: board.copied[-1] if board.copied else ""
    )
    monkeypatch.setattr(module.keystroke, "paste", fake_paste)
    assert clipboard and keystroke  # imported for their real names, faked above
    return board


@pytest.fixture
def build(tmp_path, desk):
    """A service with everything faked, and a handle on each fake."""

    def make(*, asr=None, llm=None, source=None, busy=False, **settings):
        store = ConfigStore(config_path=tmp_path / "config.json")
        store.update(
            {"dictation.directory": str(tmp_path / "dictations"), **settings}, layer="user"
        )
        pieces = {
            "asr": asr or FakeAsr(),
            "llm": llm or FakeLlm(),
            "source": source or FakeSource(),
            "desk": desk,
        }
        service = DictationService(
            config_provider=store.resolve,
            asr_provider=lambda: pieces["asr"],
            backend_factory=lambda: pieces["llm"],
            source_factory=lambda _config: pieces["source"],
            detector_factory=lambda _config: Loudness(),
            session_busy=lambda: busy,
            notifier=lambda summary, body="", **_k: desk.notices.append((summary, body)) or _Ok(),
        )
        pieces["service"] = service
        return pieces

    return make


class _Ok:
    ok = False
    detail = ""


def _settle(service, timeout: float = 5.0) -> None:
    """Wait for the background delivery thread to finish."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not service.state().busy:
            return
        time.sleep(0.02)
    raise AssertionError(f"the dictation never settled; stuck at {service.state().state}")


# -- the happy path --------------------------------------------------------------------------


def test_a_dictation_records_transcribes_tidies_and_pastes(build) -> None:
    parts = build()
    service = parts["service"]

    service.start()
    assert service.state().state == RECORDING
    service.finish()
    _settle(service)

    final = service.state()
    assert final.state == DONE
    assert final.text == "So I was thinking we should move the meeting."
    assert final.tidied is True
    assert final.delivered is True
    assert parts["desk"].copied == [final.text]
    assert parts["desk"].pastes == ["ctrl+v"]


def test_one_key_does_both_halves(build) -> None:
    """A push-to-talk key is one key: press, speak, press."""
    service = build()["service"]

    assert service.toggle().state == RECORDING
    service.toggle()
    _settle(service)

    assert service.state().state == DONE


def test_the_text_is_pasted_exactly_once(build) -> None:
    """**The rule the whole timing design exists to keep.** Pasting the raw text and then the
    tidied one would put both into the document, the second wherever the caret had moved to."""
    parts = build()
    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    assert len(parts["desk"].pastes) == 1
    assert len(parts["desk"].copied) == 1


# -- the tidy, and its deadline --------------------------------------------------------------


def test_a_slow_tidy_falls_back_to_what_was_said(build) -> None:
    """Measured: 8-10 s warm, 44 s cold. A dictation must not wait for the worse case."""
    parts = build(llm=FakeLlm(delay=5.0), **{"dictation.cleanup_timeout_s": 1.0})

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"], timeout=10.0)

    final = parts["service"].state()
    assert final.tidied is False
    assert final.text == "so i was thinking um we should move the meeting"
    assert final.delivered is True, "a slow tidy must not cost the user their words"


def test_cleanup_off_pastes_what_was_said(build) -> None:
    parts = build(**{"dictation.cleanup": "off"})

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    assert parts["service"].state().tidied is False
    assert parts["llm"].calls == 0


def test_a_model_that_writes_an_essay_is_ignored(build) -> None:
    """**The worst outcome this feature has** is pasting a model's own paragraph into someone's
    document. A punctuation pass that returns four times the words did not punctuate anything."""
    essay = " ".join(["the model had opinions"] * 40)
    parts = build(llm=FakeLlm(answer=essay))

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    final = parts["service"].state()
    assert final.tidied is False
    assert final.text == "so i was thinking um we should move the meeting"


def test_a_model_that_swallows_the_text_is_ignored(build) -> None:
    parts = build(llm=FakeLlm(answer="ok"))

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    assert parts["service"].state().tidied is False


def test_a_model_that_refuses_costs_the_tidy_and_not_the_words(build) -> None:
    class Broken:
        async def complete(self, *_a, **_k):
            raise ConnectionError("no server")

    parts = build(llm=Broken())
    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    assert parts["service"].state().delivered is True
    assert parts["service"].state().tidied is False


# -- refusing rather than pasting the wrong thing ---------------------------------------------


def test_a_silent_recording_pastes_nothing(build) -> None:
    parts = build(asr=FakeAsr(text=""))

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    assert parts["service"].state().text == ""
    assert parts["desk"].copied == []
    assert parts["desk"].pastes == []


def test_a_clipboard_that_refuses_does_not_press_paste(build, desk) -> None:
    """Pressing paste over a clipboard that still holds the previous thing puts someone else's
    text into the document, and looks like it worked."""
    desk.copy_ok = False
    parts = build()

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    assert parts["service"].state().state == ERROR
    assert desk.pastes == []


def test_a_desktop_that_cannot_type_still_leaves_the_words_on_the_clipboard(build, desk) -> None:
    desk.paste_ok = False
    parts = build()

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    final = parts["service"].state()
    assert final.state == DONE
    assert final.delivered is False
    assert desk.copied, "the words must still be recoverable"


def test_paste_can_be_turned_off_and_the_words_still_arrive_on_the_clipboard(build, desk) -> None:
    parts = build(**{"dictation.paste": False})

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    assert desk.copied
    assert desk.pastes == []
    assert parts["service"].state().delivered is False


def test_the_paste_chord_is_configurable(build, desk) -> None:
    """A terminal pastes with Ctrl+Shift+V, and nothing can ask what kind of window has focus."""
    parts = build(**{"dictation.paste_chord": "ctrl+shift+v"})

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    assert desk.pastes == ["ctrl+shift+v"]


# -- refusing to start -------------------------------------------------------------------------


def test_a_running_recording_blocks_a_dictation(build) -> None:
    """They share the microphone. Starting one over the other would take it away mid-talk."""
    parts = build(busy=True)

    with pytest.raises(DictationError, match="recording is running"):
        parts["service"].start()


def test_a_second_dictation_is_refused_while_one_is_running(build) -> None:
    service = build()["service"]
    service.start()

    with pytest.raises(DictationError, match="already"):
        service.start()


def test_a_cold_model_is_loaded_while_the_user_is_already_talking(build) -> None:
    """**Refusing was the bug.** A key that works without opening the browser cannot then require
    that the browser has been opened to load the model. The load takes about as long as a sentence,
    so it happens during the recording and is waited for at the end."""

    class Cold:
        def __init__(self) -> None:
            self.is_ready = False
            self.loads = 0

        async def load(self, config=None) -> None:  # noqa: ANN001
            # The config is passed explicitly: the lifecycle is built before the config file is
            # read, so a bare `load()` would use the built-in default — the mock backend.
            assert config is not None, "the model must be loaded with the resolved settings"
            self.loads += 1
            self.is_ready = True

        def transcribe(self, _audio, prompt=None):  # noqa: ANN001, ARG002
            assert self.is_ready, "transcribed before the model finished loading"
            return said("it loaded in time")

    cold = Cold()
    parts = build(asr=cold)

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    assert cold.loads == 1
    assert parts["service"].state().state == DONE
    assert parts["desk"].copied, "the words arrived despite the model starting cold"


def test_a_model_that_will_not_load_keeps_the_recording(build, tmp_path) -> None:
    """The words are gone either way; the audio is the only chance of getting them back."""

    class Broken:
        is_ready = False

        async def load(self, config=None) -> None:  # noqa: ANN001, ARG002
            raise RuntimeError("no such model")

        def transcribe(self, _audio, prompt=None):  # noqa: ANN001, ARG002
            raise AssertionError("must not transcribe with no model")

    parts = build(asr=Broken())
    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"], timeout=10.0)

    assert parts["service"].state().state == ERROR
    assert len(list((tmp_path / "dictations").glob("*.wav"))) == 1, "the recording was thrown away"


def test_dictation_can_be_switched_off(build) -> None:
    with pytest.raises(DictationError, match="switched off"):
        build(**{"dictation.enabled": False})["service"].start()


def test_a_microphone_that_will_not_open_says_which_and_leaves_no_file(build, tmp_path) -> None:
    parts = build(source=FakeSource(fail=True))

    with pytest.raises(DictationError, match="microphone could not be opened"):
        parts["service"].start()

    assert list((tmp_path / "dictations").glob("*.wav")) == []


def test_finishing_when_nothing_is_running_says_so(build) -> None:
    with pytest.raises(DictationError, match="Nothing is being dictated"):
        build()["service"].finish()


# -- cancelling --------------------------------------------------------------------------------


def test_a_cancelled_dictation_transcribes_nothing_and_pastes_nothing(build) -> None:
    parts = build()
    parts["service"].start()

    parts["service"].cancel()

    assert parts["asr"].calls == 0
    assert parts["desk"].copied == []
    assert parts["service"].state().state == "idle"


# -- what is left on disk ------------------------------------------------------------------------


def test_the_audio_is_discarded_once_the_words_are_delivered(build, tmp_path) -> None:
    """Speech nobody asked to keep should not accumulate."""
    parts = build()
    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    assert list((tmp_path / "dictations").glob("*.wav")) == []


def test_the_words_are_kept_so_a_misplaced_paste_is_recoverable(build, tmp_path) -> None:
    parts = build()
    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    import json

    [sidecar] = list((tmp_path / "dictations").glob("*.json"))
    saved = json.loads(sidecar.read_text())
    assert saved["text"] == "So I was thinking we should move the meeting."
    assert saved["raw"] == "so i was thinking um we should move the meeting"


def test_the_audio_can_be_kept_when_asked(build, tmp_path) -> None:
    parts = build(**{"dictation.keep_audio": True})
    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    assert len(list((tmp_path / "dictations").glob("*.wav"))) == 1


def test_old_dictations_are_pruned(build, tmp_path) -> None:
    """**679 empty session databases were deleted one plan ago.** Dictation writes a file every
    time a key is pressed, which is the fastest way to rebuild that pile."""
    directory = tmp_path / "dictations"
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(6):
        (directory / f"20250101-0000{index}.json").write_text("{}")

    parts = build(**{"dictation.keep_transcripts": 3})
    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    assert len(list(directory.glob("*.json"))) == 3


# -- long dictations: cut where the speaker paused, never on a clock (D-061) -----------------


def _long_clip() -> np.ndarray:
    """Four seconds of speech, a breath, four more, a breath, four more: 13.2 s in all."""
    return np.concatenate([tone(4.0), silence(0.6), tone(4.0), silence(0.6), tone(4.0)])


def test_a_long_dictation_is_cut_where_the_speaker_paused(build) -> None:
    """**The fault this exists for**: a real two-and-a-half minute dictation came back with "..."
    where a thirty-second window had cut a phrase in half. A chunk ends inside a pause, so there
    is no half-word for the model to see and nothing for anything to merge."""
    asr = FakeAsr("the first part", "the second part")
    parts = build(
        asr=asr,
        source=FakeSource(audio=_long_clip()),
        **{"dictation.chunk_seconds": 10.0, "dictation.cleanup": "off"},
    )

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    final = parts["service"].state()
    assert final.state == DONE
    assert final.chunks == 2
    assert final.text == "the first part the second part"

    # The first chunk must end by 10 s. The longest pause in its back half (5-10 s) is the breath
    # at 8.6-9.2 s, so the cut lands in the middle of it, and the second chunk is the rest.
    assert asr.seconds_seen == pytest.approx([8.9, 4.3], abs=0.01)
    assert sum(asr.seconds_seen) == pytest.approx(13.2, abs=0.01), "nothing skipped, nothing twice"

    first, second = asr.audio_seen
    quarter = int(0.25 * SAMPLE_RATE)
    assert np.max(np.abs(first[-quarter:])) == 0, "the first chunk ends in silence"
    assert np.max(np.abs(second[:quarter])) == 0, "the second chunk begins in silence"


def test_each_chunk_is_tidied_on_its_own_and_a_mangled_one_falls_back_alone(build) -> None:
    """The tidy's word-count guard is the model's opinion of *one* chunk. The chunk it mangles
    keeps its raw text; the others keep their tidy."""
    llm = FakeLlm(answers=["The first part.", " ".join(["the model had opinions"] * 40)])
    parts = build(
        asr=FakeAsr("the first part", "the second part"),
        llm=llm,
        source=FakeSource(audio=_long_clip()),
        **{"dictation.chunk_seconds": 10.0},
    )

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    final = parts["service"].state()
    assert llm.calls == 2
    assert final.text == "The first part. the second part"
    assert final.raw_text == "the first part the second part"
    assert final.tidied is False


def test_a_stalled_tidy_is_not_waited_for_once_per_chunk(build) -> None:
    """A timeout is a property of the server, not of the chunk. Waiting the full timeout for each
    of ten chunks would turn a keystroke into the coffee break the timeout exists to prevent."""
    llm = FakeLlm(delay=5.0)
    parts = build(
        asr=FakeAsr("the first part", "the second part"),
        llm=llm,
        source=FakeSource(audio=_long_clip()),
        **{"dictation.chunk_seconds": 10.0, "dictation.cleanup_timeout_s": 1.0},
    )

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"], timeout=10.0)

    final = parts["service"].state()
    assert llm.calls == 1, "a server that stalled on the first chunk is not asked about the rest"
    assert final.text == "the first part the second part"
    assert final.tidied is False
    assert final.delivered is True


def test_reaching_the_length_limit_delivers_what_was_said(build) -> None:
    """A key pressed once and forgotten used to leave the dictation *recording* over a file that
    had stopped growing, and everything said after the cap was lost without a word. Now the cap
    ends the dictation and delivers what was captured."""
    parts = build(
        source=FakeSource(audio=tone(7.0)),
        **{"dictation.max_seconds": 5.0, "dictation.cleanup": "off"},
    )

    parts["service"].start()
    # Nobody presses the key again.
    _settle(parts["service"], timeout=10.0)

    final = parts["service"].state()
    assert final.state == DONE
    assert final.seconds == pytest.approx(5.0, abs=0.01)
    assert final.delivered is True
    assert any("limit" in summary for summary, _ in parts["desk"].notices)
    assert parts["source"].stopped is True


# -- the tidy runs while the next chunk transcribes (D-063) --------------------------------


class SlowAsr(FakeAsr):
    """A speech model that takes a measurable time per chunk."""

    def __init__(self, delay: float, *texts: str) -> None:
        super().__init__(*texts)
        self.delay = delay

    def transcribe(self, audio, prompt=None):  # noqa: ANN001
        time.sleep(self.delay)
        return super().transcribe(audio, prompt)


def _three_chunk_clip() -> np.ndarray:
    """Eight seconds, a breath, eight, a breath, eight: three chunks at the ten-second cap."""
    return np.concatenate([tone(8.0), silence(0.6), tone(8.0), silence(0.6), tone(8.0)])


def test_the_tidy_of_one_chunk_runs_while_the_next_transcribes(build) -> None:
    """Three chunks, each 0.3 s to transcribe and 0.3 s to tidy: in sequence that is 1.8 s, and
    overlapped it is three transcriptions plus the *last* tidy — about 1.2 s. The margin below is
    wide, because the point is the shape of the wait and not its exact figure."""
    llm = FakeLlm(delay=0.3, answers=["One.", "Two.", "Three."])
    parts = build(
        asr=SlowAsr(0.3, "one", "two", "three"),
        llm=llm,
        source=FakeSource(audio=_three_chunk_clip()),
        **{"dictation.chunk_seconds": 10.0},
    )

    parts["service"].start()
    started = time.monotonic()
    parts["service"].finish()
    _settle(parts["service"], timeout=10.0)
    elapsed = time.monotonic() - started

    final = parts["service"].state()
    assert final.chunks == 3
    assert final.text == "One. Two. Three."
    assert final.tidied is True
    assert llm.calls == 3
    assert elapsed < 1.65, f"the two halves ran in sequence: {elapsed:.2f} s"
    # The transcription took its three turns; the tidy the user waited for was the last one only.
    assert final.timings["transcribe"] == pytest.approx(0.9, abs=0.25)
    assert final.timings["tidy"] < 0.5


def test_the_texts_come_back_in_the_order_they_were_spoken(build) -> None:
    """The worker consumes the queue in order, so a fast tidy of chunk two cannot overtake a slow
    tidy of chunk one."""
    llm = FakeLlm(answers=["First.", "Second.", "Third."])
    parts = build(
        asr=FakeAsr("first", "second", "third"),
        llm=llm,
        source=FakeSource(audio=_three_chunk_clip()),
        **{"dictation.chunk_seconds": 10.0},
    )

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"])

    final = parts["service"].state()
    assert final.text == "First. Second. Third."
    assert final.raw_text == "first second third"


def test_a_stalled_tidy_still_stops_the_chunks_after_it_when_overlapped(build) -> None:
    """The give-up rule survives the move onto a worker: one timeout, and nothing after it is
    asked for."""
    llm = FakeLlm(delay=5.0)
    parts = build(
        asr=FakeAsr("one", "two", "three"),
        llm=llm,
        source=FakeSource(audio=_three_chunk_clip()),
        **{"dictation.chunk_seconds": 10.0, "dictation.cleanup_timeout_s": 1.0},
    )

    parts["service"].start()
    parts["service"].finish()
    _settle(parts["service"], timeout=10.0)

    final = parts["service"].state()
    assert llm.calls == 1
    assert final.text == "one two three"
    assert final.tidied is False
    assert final.delivered is True


def test_the_state_says_tidying_only_once_the_last_chunk_is_decoded(build) -> None:
    """Until then the user is waiting on the speech model, whatever the worker is doing."""
    seen: list[str] = []
    parts = build(
        asr=SlowAsr(0.15, "one", "two", "three"),
        llm=FakeLlm(delay=0.4),
        source=FakeSource(audio=_three_chunk_clip()),
        **{"dictation.chunk_seconds": 10.0},
    )
    service = parts["service"]
    original = service._set_state  # noqa: SLF001 - the transitions are the thing under test

    def spy(state: str) -> None:
        seen.append(state)
        original(state)

    service._set_state = spy  # noqa: SLF001
    service.start()
    service.finish()
    _settle(service, timeout=10.0)

    assert seen.index("tidying") < seen.index("delivering")
    assert seen.count("tidying") == 1
