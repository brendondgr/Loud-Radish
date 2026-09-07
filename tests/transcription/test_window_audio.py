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

import select
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


def _read_until(stream, want: int, *, deadline_s: float) -> bytes:
    """Read up to ``want`` bytes, giving up at the deadline instead of blocking forever.

    ``stream.read(n)`` on a pipe blocks until it has **exactly** n bytes or the writer closes. If
    the monitor node produces nothing — no default sink, a suspended node, `pw-record` dying with
    its stderr discarded — that read never returns, and the `finally` that would kill the process
    is never reached. The suite then hangs on a machine whose only fault is that it is quiet.
    """
    chunks: list[bytes] = []
    have = 0
    end = time.monotonic() + deadline_s
    while have < want and time.monotonic() < end:
        ready, _, _ = select.select([stream], [], [], 0.25)
        if not ready:
            continue
        chunk = stream.read1(min(8192, want - have))
        if not chunk:  # the writer closed; nothing more is coming
            break
        chunks.append(chunk)
        have += len(chunk)
    return b"".join(chunks)


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
        raw = _read_until(process.stdout, 16_000 * 4, deadline_s=10.0)  # one second of audio
    finally:
        process.terminate()
        process.wait(timeout=5)

    samples = np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.float32)
    assert samples.size > 0
    assert not np.isnan(samples).any(), "a header is being read as audio"
    # **Not an amplitude assertion.** This test used to also require `max() <= 1.0`, and that is a
    # claim about whatever the developer happens to be playing, not about the flag under test — it
    # was watched failing and then passing on consecutive runs with no code change, because
    # something clipped above unity in between. NaN is the fault this test was written for; NaN is
    # what it checks. The values a `.snd` header decodes to are NaN long before they are loud.
    assert np.isfinite(samples).all(), "a header is being read as audio"


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
    # Finite, not quiet: see the note in `test_the_container_is_raw`.
    assert np.isfinite(audio).all()


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
#
# These assert the **routing decision** — which kind of source a mode and a choice end up with —
# rather than how any of them is built.
#
# `MonitorSource` is still the end of the "window's sound" path, but since D-030 it is no longer the
# start of it: targeting a sink's monitor directly was measured to record the *microphone* whenever
# the target failed to resolve, so that choice now goes through the application tap as well and
# arrives at a `MonitorSource` reading the tap. What sits in front of it — the query for what is
# playing, the tap itself, the delivery probe — is answered suite-wide by `tests/conftest.py`, which
# is what stops these tests depending on whether the developer happens to have a video open.


def _manager(tmp_path, **overrides) -> tuple[SessionManager, AppConfig]:
    from app.config import ConfigStore

    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update({"audio.source_type": "microphone", **overrides})
    return SessionManager(store, emit=lambda *_a: None, session_dir=tmp_path), store.resolve()


def test_window_mode_opens_the_machines_output_not_a_microphone(tmp_path, monkeypatch) -> None:
    """The fault, stated as the property that fixes it."""
    opened: list[str] = []
    monkeypatch.setattr(
        "app.services.session.sources.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.sources.DeviceSource",
        lambda **_kwargs: opened.append("device") or object(),
    )
    manager, config = _manager(tmp_path)

    manager._open_source(config, modes.WINDOW)

    assert opened == ["monitor"]


def test_the_other_modes_still_open_a_microphone(tmp_path, monkeypatch) -> None:
    """Live and recorded sessions are someone speaking into a microphone. Nothing changes there."""
    opened: list[str] = []
    monkeypatch.setattr(
        "app.services.session.sources.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.sources.DeviceSource",
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
        "app.services.session.sources.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.sources.DeviceSource",
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
        "app.services.session.sources.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.sources.WavFileSource",
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

    monkeypatch.setattr("app.services.session.sources.MonitorSource", unavailable)
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
        "app.services.session.sources.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.sources.DeviceSource",
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
        "app.services.session.sources.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.sources.DeviceSource",
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
        "app.services.session.sources.MonitorSource",
        lambda **_kwargs: opened.append("monitor") or object(),
    )
    monkeypatch.setattr(
        "app.services.session.sources.DeviceSource",
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
    from app.services.session.shapes import LOOPBACK_SPEECH_RMS

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


# -- the tap is verified against the graph, not against silence ----------------------------------
#
# The first version of this check listened to the tap and treated a run of bit-exact zeros as proof
# the graph was not delivering. That premise holds for a microphone, which always carries a noise
# floor, and is **false for an application** — a media player between sounds, a paused video whose
# stream is still open, or a clip with a silent lead-in all write literal zeros. Working recordings
# were refused for being quiet, within a day of the check shipping.
#
# Whether anything is *linked* cannot be confused with whether anything is *audible*, so that is
# what is asked now. It still catches the fault the check exists for.


class _Tap:
    """A tap double whose link count is whatever the test says it is."""

    def __init__(self, links: int) -> None:
        self.sink_name = "loud-radish-tap-double"
        self.live_links = links
        self.closed = False
        self.is_open = True

    @property
    def monitor(self) -> str:
        return f"{self.sink_name}.monitor"

    def open(self) -> None:
        self.is_open = True

    def link_all(self, streams) -> int:
        return 2 * len(list(streams))

    def close(self) -> None:
        self.closed = True
        self.is_open = False


def _stream(state: str = "running"):
    from app.services.audio.tap import PlaybackStream

    return PlaybackStream(
        node_id=1,
        serial=1,
        node_name="LibreWolf",
        application="LibreWolf",
        binary="librewolf",
        media_name="A Lecture",
        state=state,
    )


def _manager_with_tap(tmp_path, monkeypatch, links: int):
    tap = _Tap(links)
    monkeypatch.setattr("app.services.session.sources.ApplicationTap", lambda *_a, **_k: tap)
    monkeypatch.setattr("app.services.session.sources.tap_streams", lambda: [_stream()])
    manager, config = _manager(tmp_path)
    return manager, config, tap


def test_a_tap_nothing_is_routed_into_refuses_to_start(tmp_path, monkeypatch) -> None:
    """The fault the check exists for: a sink no audio reaches records silence for a whole talk."""
    from app.services.session.manager import SessionError

    manager, config, tap = _manager_with_tap(tmp_path, monkeypatch, links=0)

    with pytest.raises(SessionError, match="nothing is connected"):
        manager._open_source(config, modes.WINDOW)
    assert tap.closed, "a refused tap must not be left loaded in the graph"


def test_a_linked_tap_that_happens_to_be_silent_is_accepted(tmp_path, monkeypatch) -> None:
    """**The regression this replaces.**

    An application writes bit-exact zeros whenever it is between sounds. Refusing on that refused
    recordings that would have worked, which is what the user reported the day the old check
    shipped. Linked is linked, whether or not anyone is talking into it.
    """
    manager, config, _tap = _manager_with_tap(tmp_path, monkeypatch, links=2)

    source = manager._open_source(config, modes.WINDOW)

    assert source.info.kind == "loopback"


@pipewire
def test_a_real_tap_reports_its_links_without_listening_to_them() -> None:
    """Against the real graph, and deliberately with nothing playing.

    That is the state the old check could not tell apart from a broken one, so it is the state this
    one has to be measured in.
    """
    from app.services.audio.tap import ApplicationTap, playback_streams

    bare = ApplicationTap()
    bare.open()
    try:
        assert bare.live_links == 0, "an unlinked tap must report no links"
        streams = playback_streams()
        if not streams:
            pytest.skip("no application is playing audio to link")
        # **Assert on what `link_all` reports, not on the developer's graph.** This used to link
        # whatever happened to be playing and then require `live_links > 0` regardless — so it
        # failed whenever `pw-dump` listed a node that could not actually be linked (one that died
        # between the dump and the link, or one already routed elsewhere). That is a fact about the
        # machine, not about the counter under test. The subject here is that `live_links` reports
        # the links that exist, which is only checkable once some exist.
        linked = bare.link_all(streams)
        if not linked:
            pytest.skip("nothing currently playing could be linked into a fresh tap")
        assert bare.live_links > 0, "a linked tap must report its links"
    finally:
        bare.close()


# -- the tap keeps linking while the recording runs ----------------------------------------------
#
# `tap.py`'s own module docstring says the set has to be watched, because "an application creates
# and destroys playback nodes as the user opens tabs and starts media". The session linked once, at
# open, and never again — so pressing record and *then* pressing play recorded nothing at all.


def test_new_playback_nodes_are_linked_after_recording_starts(tmp_path, monkeypatch) -> None:
    calls: list[int] = []

    class Growing(_Tap):
        def link_all(self, streams):
            calls.append(len(list(streams)))
            return 2

    tap = Growing(links=2)
    monkeypatch.setattr("app.services.session.sources.tap_streams", lambda: [_stream()])
    manager, _config = _manager(tmp_path)
    manager._tap = tap
    manager._tap_match = False

    manager._relink_tap()

    assert calls == [1], "the status tick must re-link the streams playing now"


def test_relinking_a_closed_tap_is_harmless(tmp_path, monkeypatch) -> None:
    """The tick runs once a second in every mode, including after teardown has released the tap."""
    manager, _config = _manager(tmp_path)
    manager._tap = None

    manager._relink_tap()  # must not raise


# -- 'application' narrows to the window; 'system' does not --------------------------------------


def test_the_application_choice_narrows_to_the_matching_streams(tmp_path, monkeypatch) -> None:
    """`match` was documented and ignored: both choices linked everything that was playing."""
    from app.services.audio.tap import PlaybackStream

    wanted = _stream()
    other = PlaybackStream(
        node_id=2,
        serial=2,
        node_name="Music",
        application="Music",
        binary="music",
        media_name="Some Song",
        state="running",
    )
    monkeypatch.setattr("app.services.session.sources.tap_streams", lambda: [other, wanted])
    manager, _config = _manager(tmp_path)
    manager._options = _window_options("application", app_id="librewolf")

    assert [s.node_name for s in manager._tap_candidates(match=True)] == ["LibreWolf"]
    assert len(manager._tap_candidates(match=False)) == 2


def test_a_window_that_matches_nothing_taps_everything(tmp_path, monkeypatch) -> None:
    """The match is a heuristic over properties PipeWire warns are not authoritative (D-027).

    It must not be the thing that decides a recording captures no audio at all.
    """
    monkeypatch.setattr("app.services.session.sources.tap_streams", lambda: [_stream()])
    manager, _config = _manager(tmp_path)
    manager._options = _window_options("application", app_id="nothing-like-this")

    assert len(manager._tap_candidates(match=True)) == 1


def _window_options(choice: str, *, app_id: str = "", title: str = ""):
    from app.services.session.manager import CaptureOptions

    options = CaptureOptions(audio_source=choice)
    object.__setattr__(options, "window_app_id", app_id)
    object.__setattr__(options, "window_title", title)
    return options


# -- a tap that is built correctly and still carries nothing -------------------------------------


#: What :func:`~app.services.audio.monitor.default_sink` is made to return in these tests. A sink
#: name, deliberately without a ``.monitor`` suffix: that suffix does not resolve on this machine
#: and falls back to the microphone, which is the bug these doubles must not paper over.
THE_SPEAKERS = "the-speakers"


def _probes(monkeypatch, *, tap: float, whole_output: float = 0.0) -> list[str]:
    """Answer both probes by hand, and record which one was asked.

    **Keyed by node, not by ``capture_sink``.** Both probes pass ``capture_sink=True`` now — the
    only form that reads a sink here — so the flag no longer distinguishes them, and a double that
    switched on it would answer the wrong question while still passing.
    """
    asked: list[str] = []

    def fake_probe(
        node: str, *, capture_sink: bool, device_sink: bool = False, seconds: float = 1.0
    ) -> float:
        asked.append(node)
        assert capture_sink, "every probe must address a sink, never a `.monitor` that falls back"
        if node == THE_SPEAKERS:
            # The machine's own output is a **device** sink, and must be probed with the form that
            # a device sink accepts. Carrying `node.dont-fallback` there means no stream starts at
            # all, so the widening would silently decide the machine was silent and never fire.
            assert device_sink, "a device sink must not be probed with the tap's property set"
            return whole_output
        assert not device_sink, "the tap is a null sink and keeps the stricter property set"
        return tap

    monkeypatch.setattr("app.services.session.sources.probe_peak", fake_probe)
    monkeypatch.setattr("app.services.session.sources.default_sink", lambda: THE_SPEAKERS)
    return asked


def test_a_tap_that_delivers_nothing_widens_to_the_whole_output(tmp_path, monkeypatch) -> None:
    """The reported fault, stated as the property that catches it.

    Measured on the real graph: the sink created, the browser's ports linked, every link ``active``,
    every node ``running``, every gain 1.0 — and bit-exact zeros out of the tap while the same audio
    played through the speakers. Neither of the two questions asked before this could see it: the
    tap *is* linked, and its silence is indistinguishable from a paused video until the machine's
    own output is asked as well.
    """
    _probes(monkeypatch, tap=0.0, whole_output=0.4)
    manager, config, tap = _manager_with_tap(tmp_path, monkeypatch, links=2)

    source = manager._open_source(config, modes.WINDOW)

    assert source.info.id == THE_SPEAKERS, "the capture should have widened"
    assert tap.closed, "the tap that could not deliver must not be left loaded in the graph"
    assert manager._tap is None


def test_a_silent_tap_on_a_silent_machine_keeps_the_tap(tmp_path, monkeypatch) -> None:
    """**The regression that must not come back.**

    Nothing playing yet is the ordinary case — someone presses record and then presses play. Both
    sides are zero, which is exactly what a dead tap looks like, and the difference is the whole
    reason the machine's output is consulted at all. Widening here would silently record the wrong
    thing for everyone who arms a recording early.
    """
    _probes(monkeypatch, tap=0.0, whole_output=0.0)
    manager, config, tap = _manager_with_tap(tmp_path, monkeypatch, links=2)

    manager._open_source(config, modes.WINDOW)

    assert not tap.closed
    assert manager._tap is tap


def test_a_probe_that_cannot_run_never_changes_the_capture(tmp_path, monkeypatch) -> None:
    """``-1.0`` is *unknown*, and a measurement that did not happen must decide nothing."""
    _probes(monkeypatch, tap=-1.0, whole_output=0.4)
    manager, config, tap = _manager_with_tap(tmp_path, monkeypatch, links=2)

    manager._open_source(config, modes.WINDOW)

    assert not tap.closed
    assert manager._tap is tap


def test_a_delivering_tap_is_left_alone(tmp_path, monkeypatch) -> None:
    """The ordinary success, and it must not cost a second probe."""
    asked = _probes(monkeypatch, tap=0.3, whole_output=0.4)
    manager, config, tap = _manager_with_tap(tmp_path, monkeypatch, links=2)

    manager._open_source(config, modes.WINDOW)

    assert manager._tap is tap
    assert len(asked) == 1, "the machine's output is irrelevant once the tap is known to deliver"


def test_widening_says_so_rather_than_absorbing_it(tmp_path, monkeypatch) -> None:
    """A wider recording is a different recording, and the transcript will show it."""
    from app.config import ConfigStore

    _probes(monkeypatch, tap=0.0, whole_output=0.4)
    events: list[tuple[str, dict]] = []
    tap = _Tap(2)
    monkeypatch.setattr("app.services.session.sources.ApplicationTap", lambda *_a, **_k: tap)
    monkeypatch.setattr("app.services.session.sources.tap_streams", lambda: [_stream()])
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update({"audio.source_type": "microphone"})
    manager = SessionManager(
        store, emit=lambda name, payload: events.append((name, payload)), session_dir=tmp_path
    )

    manager._open_source(store.resolve(), modes.WINDOW)

    codes = [payload.get("code") for name, payload in events if name == "error"]
    assert "window-audio-not-delivering" in codes


@pipewire
def test_the_probe_reports_unknown_rather_than_silent_for_an_unresolvable_target() -> None:
    """The distinction the whole comparison rests on.

    A target that does not resolve cannot be measured, and reporting that as *silent* would let a
    broken probe widen every recording on the machine.
    """
    from app.services.audio.sources.monitor import probe_peak

    assert probe_peak("no-such-node-anywhere", capture_sink=True, seconds=0.2) == -1.0


# -- one name, several nodes ---------------------------------------------------------------------


def test_two_nodes_sharing_a_name_are_both_linked(monkeypatch) -> None:
    """A browser names every tab's playback node after itself, and both tabs must be captured.

    Keyed by name, the first tab's ``LibreWolf:FL`` marked the second tab's port already linked and
    it was skipped — so exactly one tab was ever recorded, which is the fault this module's own
    docstring warns about.
    """
    from app.services.audio import tap as tap_module

    commands: list[list[str]] = []
    monkeypatch.setattr(tap_module, "_output_ports", lambda node_id: {})
    monkeypatch.setattr(tap_module, "_succeeded", lambda command: commands.append(command) or True)

    tap = tap_module.ApplicationTap(sink_name="loud-radish-tap-test")
    tap._module = "42"  # opened, without touching the daemon

    def node(node_id: int):
        return tap_module.PlaybackStream(
            node_id=node_id,
            serial=node_id,
            node_name="LibreWolf",
            application="LibreWolf",
            binary="librewolf",
            media_name=f"tab {node_id}",
            state="running",
        )

    assert tap.link_all([node(101), node(117)]) == 4, "both tabs' stereo pairs must be linked"
    assert len(commands) == 4


def test_the_same_node_is_not_linked_twice(monkeypatch) -> None:
    """The re-link runs once a second; it must not pile duplicate links onto the same ports."""
    from app.services.audio import tap as tap_module

    monkeypatch.setattr(tap_module, "_output_ports", lambda node_id: {})
    monkeypatch.setattr(tap_module, "_succeeded", lambda command: True)

    tap = tap_module.ApplicationTap(sink_name="loud-radish-tap-test")
    tap._module = "42"
    stream = tap_module.PlaybackStream(
        node_id=101,
        serial=101,
        node_name="LibreWolf",
        application="LibreWolf",
        binary="librewolf",
        media_name="tab",
        state="running",
    )

    assert tap.link_all([stream]) == 2
    assert tap.link_all([stream]) == 0
