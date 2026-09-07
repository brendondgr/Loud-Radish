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
from app.models.segment import Segment
from app.services.dictation import DictationError, DictationService
from app.services.dictation.service import DONE, ERROR, RECORDING


class FakeSource:
    """A microphone that writes a fixed tone for as long as it is running."""

    def __init__(self, *, fail: bool = False, samples: int = 16_000) -> None:
        self.fail = fail
        self.samples = samples
        self.started = False
        self.stopped = False

    def start(self, on_frame, _on_error=None) -> None:
        if self.fail:
            raise OSError("no such device")
        self.started = True
        on_frame(np.zeros(self.samples, dtype=np.float32))

    def stop(self) -> None:
        self.stopped = True


class FakeAsr:
    is_ready = True

    def __init__(self, text: str = "so i was thinking um we should move the meeting") -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, _audio, prompt=None):  # noqa: ANN001, ARG002
        self.calls += 1
        return self.text


class FakeLlm:
    """Answers `complete`, optionally slowly or badly."""

    def __init__(self, answer: str = "So I was thinking we should move the meeting.", delay=0.0):
        self.answer = answer
        self.delay = delay
        self.calls = 0

    async def complete(self, _messages, _options=None) -> str:  # noqa: ANN001
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
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


def _fake_segments(text: str) -> list[Segment]:
    return [Segment(id=1, text=text, start=0.0, end=1.0)]


@pytest.fixture(autouse=True)
def _transcribe_without_a_model(monkeypatch):
    """`transcribe_file` reads a real wav through a real segmenter; the text is what matters."""
    from app.services.recording import batch

    monkeypatch.setattr(
        batch,
        "transcribe_file",
        lambda _path, *, transcribe, **_k: _fake_segments(transcribe(None)),
    )


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
            return "it loaded in time"

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
