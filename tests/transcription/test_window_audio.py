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
