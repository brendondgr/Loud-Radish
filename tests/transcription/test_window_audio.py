"""Where a window session's audio comes from.

The reported fault: recording a window also recorded the person watching it. The screen-sharing
portal carries video only (D-022), so the audio was always a separate capture — it was simply
capturing the wrong thing, because the only audio source the session manager knew about was a
capture device.

**PortAudio cannot reach what is needed.** This machine lists fifteen inputs through `sounddevice`
and not one is the `.monitor` of a sink, while ``pactl list short sources`` shows them plainly; the
`loopback` kind the device list reports is a name heuristic that labels an HDMI *output* as a
loopback. So window mode reads the machine's output through ``pw-record``, the same subprocess shape
the video recorder uses, and these tests cover the two things that shape gets wrong: what is asked
of the tool, and what is done with the bytes it returns.
"""

from __future__ import annotations

import shutil
import subprocess
import time

import numpy as np
import pytest
from app.config.schema import AppConfig
from app.services.audio.formats import SAMPLE_RATE, frame_samples
from app.services.audio.monitor import MonitorUnavailable, default_monitor, tools_present
from app.services.audio.sources.monitor import MonitorSource
from app.services.session import modes
from app.services.session.manager import SessionManager

pipewire = pytest.mark.skipif(
    not tools_present(), reason="PipeWire's command-line tools are a system package"
)


# -- what is asked of pw-record ----------------------------------------------------------------


@pipewire
def test_the_container_is_raw() -> None:
    """The flag whose absence produced a capture that ran perfectly and transcribed silence.

    Writing to stdout without `--container=raw`, `pw-record` emits an **AU container** — a 24-byte
    `.snd` header ahead of the samples. Read as float32 those bytes decode to NaN, and one NaN
    propagates through every downstream mean, peak and RMS: the level meter reads nothing, the
    speech gate never opens, and the session produces an empty transcript with nothing to say why.
    Observed directly — a three-second capture came back with the right frame count, the right
    duration, and a peak amplitude of `nan`.
    """
    node = default_monitor()
    # `pw-record` captures until it is stopped, so a bounded read and a terminate — not a wait for
    # an exit that never comes.
    process = subprocess.Popen(
        [
            "pw-record",
            f"--target={node}",
            "--rate=16000",
            "--channels=1",
            "--format=f32",
            "--container=raw",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        raw = process.stdout.read(16_000 * 4)  # one second
    finally:
        process.terminate()
        process.wait(timeout=5)

    samples = np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.float32)
    assert samples.size > 0
    assert not np.isnan(samples).any(), "a header is being read as audio"
    # The AU header's first words decode to values far outside audio range even when not NaN.
    assert np.abs(samples).max() <= 1.0


@pipewire
def test_a_capture_produces_finite_audio_at_the_canonical_rate() -> None:
    """End to end against the real daemon: frames arrive, framed correctly, and are usable."""
    frames: list[np.ndarray] = []
    errors: list[Exception | None] = []

    source = MonitorSource()
    source.start(frames.append, errors.append)
    time.sleep(1.5)
    source.stop()

    assert not errors
    assert frames, "no audio arrived from the system's output"

    audio = np.concatenate(frames)
    assert audio.dtype == np.float32
    assert not np.isnan(audio).any()
    # 16 kHz: a second and a half of audio, within the slack of process start-up.
    assert 0.7 <= audio.size / 16_000 <= 2.5
    assert np.abs(audio).max() <= 1.0


@pipewire
def test_every_frame_is_the_size_the_pipeline_expects() -> None:
    """A short or ragged frame desynchronises the VAD's hysteresis from the audio clock."""
    frames: list[np.ndarray] = []
    source = MonitorSource(frame_ms=32)
    source.start(frames.append)
    time.sleep(1.0)
    source.stop()

    assert frames
    assert {frame.size for frame in frames} == {source.frame_samples}


@pipewire
def test_stopping_releases_the_process() -> None:
    """A `pw-record` left running holds a PipeWire stream open for the life of the server."""
    source = MonitorSource()
    source.start(lambda _frame: None)
    assert source.is_running

    source.stop()

    assert not source.is_running
    assert source._process is None


def test_a_machine_with_no_tools_says_so_rather_than_recording_nothing(monkeypatch) -> None:
    """Silence with no explanation is the worst outcome; naming the missing package is the fix."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(MonitorUnavailable, match="pactl"):
        default_monitor()


@pipewire
def test_the_resolved_node_is_a_monitor() -> None:
    """A sink is an output. Recording it rather than its monitor captures nothing."""
    assert default_monitor().endswith(".monitor")


# -- what the session manager opens ------------------------------------------------------------


def _manager(tmp_path, **overrides) -> tuple[SessionManager, AppConfig]:
    from app.config import ConfigStore

    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update({"audio.source_type": "microphone", **overrides})
    return SessionManager(store, emit=lambda *_a: None, session_dir=tmp_path), store.resolve()


def test_window_mode_opens_the_machines_output_not_a_microphone(tmp_path, monkeypatch) -> None:
    """The fault, stated as the property that fixes it."""
    opened: list[str] = []
    monkeypatch.setattr(
        "app.services.session.manager.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.manager.DeviceSource",
        lambda **_kwargs: opened.append("device") or object(),
    )
    manager, config = _manager(tmp_path)

    manager._open_source(config, modes.WINDOW)

    assert opened == ["monitor"]


def test_the_other_modes_still_open_a_microphone(tmp_path, monkeypatch) -> None:
    """Live and recorded sessions are someone speaking into a microphone. Nothing changes there."""
    opened: list[str] = []
    monkeypatch.setattr(
        "app.services.session.manager.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.manager.DeviceSource",
        lambda **_kwargs: opened.append("device") or object(),
    )
    manager, config = _manager(tmp_path)

    manager._open_source(config, modes.LIVE)
    manager._open_source(config, modes.RECORDED)

    assert opened == ["device", "device"]


def test_a_window_session_can_be_asked_for_the_microphone_instead(tmp_path, monkeypatch) -> None:
    """Someone recording a window *and* their own commentary is a legitimate thing to want."""
    opened: list[str] = []
    monkeypatch.setattr(
        "app.services.session.manager.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.manager.DeviceSource",
        lambda **_kwargs: opened.append("device") or object(),
    )
    manager, config = _manager(tmp_path, **{"capture.audio_source": "microphone"})

    manager._open_source(config, modes.WINDOW)

    assert opened == ["device"]


def test_the_file_source_still_wins_in_window_mode(tmp_path, monkeypatch) -> None:
    """It is the reproducible input the pipeline is developed against.

    A window session that silently ignored it in favour of the machine's output would make the mode
    untestable against a fixture, which is how every other mode is tested.
    """
    opened: list[str] = []
    monkeypatch.setattr(
        "app.services.session.manager.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.manager.WavFileSource",
        lambda *_a, **_k: opened.append("file") or object(),
    )
    wav = tmp_path / "talk.wav"
    wav.write_bytes(b"")
    manager, config = _manager(
        tmp_path, **{"audio.source_type": "file", "audio.file_path": str(wav)}
    )

    manager._open_source(config, modes.WINDOW)

    assert opened == ["file"]


def test_an_unavailable_monitor_is_named_rather_than_swapped_for_a_microphone(
    tmp_path, monkeypatch
) -> None:
    """Falling back would record the wrong thing — which is the fault, not the remedy."""
    from app.services.session.manager import SessionError

    def unavailable(**_kwargs):
        raise MonitorUnavailable("This machine exposes no audio monitor.")

    monkeypatch.setattr("app.services.session.manager.MonitorSource", unavailable)
    manager, config = _manager(tmp_path)

    with pytest.raises(SessionError, match="no audio monitor"):
        manager._open_source(config, modes.WINDOW)


def test_the_default_is_the_machines_output() -> None:
    """A microphone recorded when it was not wanted is the fault this default exists to fix."""
    assert AppConfig().capture.audio_source == "system"


# -- the choice made at record time --------------------------------------------------------------
#
# Reported: a window recording transcribed the person watching rather than the window. The default
# was already the machine's output, so the fault was that the decision lived in a settings tab —
# two screens away, changed once, and surprising thereafter. It belongs in the sheet that appears
# when you press record, because it genuinely changes from recording to recording: a talk playing
# in a window one minute, narration over it the next.


def _with_options(tmp_path, choice, monkeypatch):
    from app.services.session.manager import CaptureOptions

    opened: list[str] = []
    monkeypatch.setattr(
        "app.services.session.manager.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.manager.DeviceSource",
        lambda **_kwargs: opened.append("device") or object(),
    )
    manager, config = _manager(tmp_path)
    manager._options = CaptureOptions(audio_source=choice)
    manager._open_source(config, modes.WINDOW)
    return opened


def test_the_per_run_choice_selects_the_microphone(tmp_path, monkeypatch) -> None:
    """Narrating over a window is a legitimate thing to want, and it is chosen here."""
    assert _with_options(tmp_path, "microphone", monkeypatch) == ["device"]


def test_the_per_run_choice_selects_the_windows_sound(tmp_path, monkeypatch) -> None:
    assert _with_options(tmp_path, "system", monkeypatch) == ["monitor"]


def test_the_per_run_choice_overrides_the_configured_one(tmp_path, monkeypatch) -> None:
    """**The sheet wins.** It is what the user is looking at when they press record.

    A configured default they set once and forgot is not more authoritative than the choice in
    front of them, and treating it as such is how a window recording ends up capturing a
    microphone nobody asked for.
    """
    from app.services.session.manager import CaptureOptions

    opened: list[str] = []
    monkeypatch.setattr(
        "app.services.session.manager.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.manager.DeviceSource",
        lambda **_kwargs: opened.append("device") or object(),
    )
    # Configuration says microphone; the run says the window's sound.
    manager, config = _manager(tmp_path, **{"capture.audio_source": "microphone"})
    manager._options = CaptureOptions(audio_source="system")

    manager._open_source(config, modes.WINDOW)

    assert opened == ["monitor"]


def test_without_a_per_run_choice_the_configured_one_applies(tmp_path, monkeypatch) -> None:
    """`live` and `recorded` sessions carry no options at all, and must not break on that."""
    opened: list[str] = []
    monkeypatch.setattr(
        "app.services.session.manager.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.manager.DeviceSource",
        lambda **_kwargs: opened.append("device") or object(),
    )
    manager, config = _manager(tmp_path, **{"capture.audio_source": "microphone"})
    manager._options = None

    manager._open_source(config, modes.WINDOW)

    assert opened == ["device"]


def test_the_request_schema_carries_the_choice() -> None:
    """It has to survive the trip from the sheet to the manager."""
    from app.schemas.api import CaptureOptions as CaptureOptionsRequest

    assert CaptureOptionsRequest().audio_source == "system"
    assert CaptureOptionsRequest(audio_source="microphone").audio_source == "microphone"


# -- the gate that closed on everything ----------------------------------------------------------


def test_speech_opens_the_loopback_gate_and_ambience_does_not() -> None:
    """The threshold, held to the measurements it was chosen from.

    Two faults sit either side of this number and both were shipped. Gating a monitor on the
    *adaptive* energy floor closed it permanently — system output has no gaps for a floor to settle
    into, so 8% of frames passed against 61% for microphone speech, and window recordings committed
    nothing. Removing the gate entirely was worse: Whisper **invents** text on non-speech, and a
    gaming video's background music produced a transcript of disjointed fragments — "my other
    children", "yeah it happens father" — which is far worse than an empty one, because it reads as
    real.

    An absolute threshold works where an adaptive one cannot, because a digital output has *true*
    silence where a room only has a noise floor. Measured per 32 ms frame: seminar speech runs
    −57 to −21 dBFS, a 35 dB range; the video's ambience sat between −42.6 and −37.4, a 5 dB band.
    Speech has dynamics and ambience at conversational volume does not.
    """
    from app.services.session.manager import LOOPBACK_SPEECH_RMS

    size = frame_samples(32)
    t = np.arange(size * 120) / SAMPLE_RATE

    # Ambience: flat, quiet, no dynamics — around -40 dBFS, as measured.
    ambience = (0.010 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    # Speech: loud syllables over near-silent gaps, which is what dynamics means.
    envelope = (0.5 + 0.5 * np.sin(2 * np.pi * 3.5 * t)) ** 3
    speech = (0.12 * np.sin(2 * np.pi * 300 * t) * envelope).astype(np.float32)

    def passing(audio: np.ndarray) -> float:
        frames = audio[: audio.size // size * size].reshape(-1, size)
        return float((np.sqrt((frames**2).mean(axis=1)) >= LOOPBACK_SPEECH_RMS).mean())

    assert passing(ambience) < 0.05, "ambience opens the gate; Whisper will invent text on it"
    assert passing(speech) > 0.20, "speech cannot open the gate; the transcript will stay empty"


def test_a_microphone_is_not_subject_to_the_absolute_gate(tmp_path) -> None:
    """It keeps the adaptive detector, which is the right tool for a room."""
    from app.services.audio.sources.base import SourceInfo

    manager, _config = _manager(tmp_path)
    manager._source_info = SourceInfo(id="mic", name="GoMic", kind="microphone")

    loud = np.full(frame_samples(32), 0.5, dtype=np.float32)
    assert manager._loopback_has_content(loud) is False


def test_a_loud_loopback_frame_passes(tmp_path) -> None:
    from app.services.audio.sources.base import SourceInfo

    manager, _config = _manager(tmp_path)
    manager._source_info = SourceInfo(id="tap", name="System output", kind="loopback")

    assert manager._loopback_has_content(np.full(frame_samples(32), 0.2, dtype=np.float32)) is True
    assert (
        manager._loopback_has_content(np.full(frame_samples(32), 0.001, dtype=np.float32)) is False
    )


def test_the_energy_gate_rejects_system_audio_it_should_pass() -> None:
    """The measurement behind the bypass, kept so the reasoning survives the code.

    `EnergyVad` judges a frame against an **adaptive noise floor**, which is exactly right for a
    microphone: speech spikes above a floor that settles into the gaps between phrases. System
    output has no gaps — the floor rises to meet continuous content and nothing ever clears it.

    Measured on this machine with the same detector and settings: **61% of frames from real
    microphone speech pass, against 8% from the machine's own output.** With three-consecutive-
    frame hysteresis, 8% scattered frames essentially never open the gate, so a window recording
    consumed audio for minutes and committed nothing — indistinguishable, from outside, from "it
    only listens to my microphone".
    """
    from app.services.audio.formats import frame_samples
    from app.services.vad.energy import EnergyVad

    size = frame_samples(32)
    rng = np.random.default_rng(7)

    # Continuous content, like a video playing: no silent troughs for the floor to settle into.
    t = np.arange(size * 200) / SAMPLE_RATE
    continuous = (
        0.25 * np.sin(2 * np.pi * 220 * t)
        + 0.2 * np.sin(2 * np.pi * 1400 * t)
        + 0.05 * rng.standard_normal(t.size)
    ).astype(np.float32)

    vad = EnergyVad(sensitivity=0.6)
    passed = sum(
        vad.is_speech(continuous[i : i + size]) for i in range(0, continuous.size - size, size)
    )
    total = continuous.size // size

    # The point is not the exact figure — it is that a level-based detector cannot see content in
    # audio that never goes quiet, so the gate must not be what decides for such a source.
    assert passed / total < 0.5, (
        "if continuous audio now passes the energy gate, the bypass may no longer be needed — "
        "measure a real monitor capture before removing it"
    )


def test_a_loopback_source_is_not_gated_on_energy(tmp_path, monkeypatch) -> None:
    """The fix, as the property that matters: frames reach the engine regardless of the verdict."""
    from app.services.audio.sources.base import SourceInfo

    manager, _config = _manager(tmp_path)
    manager._source_info = SourceInfo(id="tap", name="System output", kind="loopback")

    assert manager._source_is_loopback is True


def test_a_microphone_source_is_still_gated(tmp_path) -> None:
    """The gate earns its place on a microphone: it stops inference running on an empty room."""
    from app.services.audio.sources.base import SourceInfo

    manager, _config = _manager(tmp_path)
    manager._source_info = SourceInfo(id="mic", name="GoMic", kind="microphone")

    assert manager._source_is_loopback is False


def test_no_source_yet_is_not_treated_as_loopback(tmp_path) -> None:
    """`_source_info` is read after the source opens, and is None before that."""
    manager, _config = _manager(tmp_path)
    manager._source_info = None

    assert manager._source_is_loopback is False
