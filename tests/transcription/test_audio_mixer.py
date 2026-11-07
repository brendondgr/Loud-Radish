"""Mixing a window's sound and the microphone into one source (D-081).

The arithmetic is checked directly. The timing — which input keeps the clock, how far another may
drift, what happens when one is lost — is checked by driving the mixer's input callbacks by hand,
with a fake clock, so an hour of drift is simulated in milliseconds and nothing depends on how busy
the machine running the suite happens to be.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from app.services.audio.formats import SAMPLE_RATE, frame_samples
from app.services.audio.mixer import InputLevel, apply_gain, mix, percent_to_gain, rms
from app.services.audio.sources import SyntheticSource, speech
from app.services.audio.sources.base import AudioSource, SourceInfo
from app.services.audio.sources.mixed import (
    MAX_LAG_MS,
    PRIMARY_QUIET_S,
    MixedSource,
    MixInput,
)

FRAME = frame_samples(32)


def tone(value: float, size: int = FRAME) -> np.ndarray:
    return np.full(size, value, dtype=np.float32)


class Manual(AudioSource):
    """An input whose frames and failures the test delivers by hand."""

    def __init__(self, name: str = "manual") -> None:
        super().__init__(frame_ms=32)
        self._name = name

    def start(self, on_frame, on_error=None) -> None:
        self._on_frame = on_frame
        self._on_error = on_error
        self._running.set()

    def stop(self) -> None:
        self._running.clear()

    @property
    def info(self) -> SourceInfo:
        return SourceInfo(id=self._name, name=self._name.title(), kind="device")

    def push(self, frame: np.ndarray) -> None:
        self._emit(frame)

    def end(self, error: Exception | None = None) -> None:
        self._fail(error)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def build(*kinds: tuple[str, str], **kwargs):
    """A started mixer over manual inputs, and what it emitted."""
    sources = {name: Manual(name) for name, _ in kinds}
    out: list[np.ndarray] = []
    errors: list[object] = []
    mixer = MixedSource(
        [MixInput(name, sources[name], kind) for name, kind in kinds],
        watch=False,
        **kwargs,
    )
    mixer.start(out.append, errors.append)
    return mixer, sources, out, errors


# -- the arithmetic --------------------------------------------------------------------------------


def test_volume_is_a_percentage_clamped_to_0_100() -> None:
    assert percent_to_gain(100) == 1.0
    assert percent_to_gain(50) == 0.5
    assert percent_to_gain(-5) == 0.0
    assert percent_to_gain(250) == 1.0


def test_mute_silences_without_forgetting_the_volume() -> None:
    level = InputLevel(volume=70, muted=True)
    assert level.gain == 0.0
    assert InputLevel(volume=level.volume).gain == pytest.approx(0.7)


def test_unity_gain_passes_the_frame_through_untouched() -> None:
    frame = tone(0.3)
    assert apply_gain(frame, 1.0, 1.0) is frame


def test_a_gain_change_ramps_across_the_frame_rather_than_stepping() -> None:
    """A step between two samples is a click on every slider drag."""
    out = apply_gain(tone(1.0), 1.0, 0.0)
    assert out[0] == pytest.approx(1.0)
    assert out[-1] == pytest.approx(0.0, abs=0.01)
    assert np.all(np.diff(out) <= 0)
    assert np.max(np.abs(np.diff(out))) < 0.01


def test_inputs_sum_and_the_sum_is_clipped() -> None:
    assert mix([tone(0.2), tone(0.3)])[0] == pytest.approx(0.5)
    loud = mix([tone(0.8), tone(0.7)])
    assert loud.max() == pytest.approx(1.0)
    assert mix([tone(-0.8), tone(-0.7)]).min() == pytest.approx(-1.0)


def test_one_frame_is_not_mixed_at_all() -> None:
    frame = tone(0.9)
    assert mix([frame]) is frame


def test_rms_of_a_constant_is_its_magnitude() -> None:
    assert rms(tone(-0.5)) == pytest.approx(0.5)
    assert rms(np.zeros(0, dtype=np.float32)) == 0.0


# -- one input ------------------------------------------------------------------------------------


def test_a_single_input_mixer_is_a_pass_through() -> None:
    """Every recording goes through a mixer now, so one input must come out sample for sample."""
    mixer, sources, out, _ = build(("microphone", "microphone"))
    frames = [
        np.random.default_rng(seed).uniform(-1, 1, FRAME).astype(np.float32) for seed in (1, 2)
    ]
    for frame in frames:
        sources["microphone"].push(frame)
    assert len(out) == 2
    assert all(a is b for a, b in zip(out, frames, strict=True))


def test_a_single_input_reports_its_own_description() -> None:
    mixer, *_ = build(("microphone", "microphone"))
    assert mixer.info.name == "Microphone"


def test_a_single_input_can_still_be_muted() -> None:
    mixer, sources, out, _ = build(("microphone", "microphone"))
    mixer.set_level("microphone", muted=True)
    sources["microphone"].push(tone(0.5))  # ramps down
    sources["microphone"].push(tone(0.5))
    assert np.all(out[-1] == 0.0)


# -- two inputs -----------------------------------------------------------------------------------


def test_the_microphone_keeps_time_over_a_tap() -> None:
    """A hardware clock that never pauses beats a tap that may idle when nothing plays."""
    mixer, *_ = build(("window", "tap"), ("microphone", "microphone"))
    assert mixer.primary == "microphone"


def test_a_file_keeps_time_over_everything() -> None:
    mixer, *_ = build(("microphone", "microphone"), ("window", "file"))
    assert mixer.primary == "window"


def test_only_the_primary_produces_frames_and_each_includes_both_inputs() -> None:
    mixer, sources, out, _ = build(("window", "tap"), ("microphone", "microphone"))
    sources["window"].push(tone(0.25))
    assert out == []
    sources["microphone"].push(tone(0.5))
    assert len(out) == 1
    assert out[0][0] == pytest.approx(0.75)


def test_volume_and_mute_change_each_input_alone() -> None:
    mixer, sources, out, _ = build(("window", "tap"), ("microphone", "microphone"))
    mixer.set_level("window", volume=50)
    mixer.set_level("microphone", muted=True)
    for _ in range(2):  # the first frame ramps; the second is settled
        sources["window"].push(tone(0.4))
        sources["microphone"].push(tone(0.3))
    assert out[-1][0] == pytest.approx(0.2)


def test_each_inputs_track_receives_what_it_contributed() -> None:
    """Mute applies to everything kept: a muted input's own track is silent too (D-081)."""
    tracks: dict[str, list[np.ndarray]] = {"window": [], "microphone": []}
    mixer, sources, out, _ = build(
        ("window", "tap"),
        ("microphone", "microphone"),
        levels={"microphone": InputLevel(muted=True)},
        on_track=lambda name, frame: tracks[name].append(frame),
    )
    sources["window"].push(tone(0.4))
    sources["microphone"].push(tone(0.3))
    assert tracks["window"][0][0] == pytest.approx(0.4)
    assert np.all(tracks["microphone"][0] == 0.0)
    assert out[0][0] == pytest.approx(0.4)


def test_a_secondary_that_falls_behind_is_padded_with_silence() -> None:
    mixer, sources, out, _ = build(("window", "tap"), ("microphone", "microphone"))
    sources["microphone"].push(tone(0.5))
    assert out[0][0] == pytest.approx(0.5)
    assert mixer.padded_samples == FRAME


@pytest.mark.parametrize("rate", [1.03, 0.97])
def test_drift_over_an_hour_stays_inside_the_bound(rate: float) -> None:
    """A secondary 3 % fast or slow for an hour never queues more than MAX_LAG_MS."""
    mixer, sources, out, _ = build(("window", "tap"), ("microphone", "microphone"))
    bound = SAMPLE_RATE * MAX_LAG_MS // 1000
    owed = 0.0
    frames_per_hour = SAMPLE_RATE * 3600 // FRAME
    window = sources["window"]
    microphone = sources["microphone"]
    lane = mixer._lanes["window"]
    worst = 0
    for _ in range(frames_per_hour):
        owed += rate
        while owed >= 1.0:
            window.push(tone(0.1))
            owed -= 1.0
        microphone.push(tone(0.1))
        worst = max(worst, lane.queued)
    assert len(out) == frames_per_hour
    assert worst <= bound
    if rate > 1:
        assert mixer.dropped_samples > 0
    else:
        assert mixer.padded_samples > 0


# -- the primary and losing inputs ----------------------------------------------------------------


def test_a_quiet_primary_hands_the_clock_to_an_input_that_is_delivering() -> None:
    clock = Clock()
    mixer, sources, out, _ = build(("window", "tap"), ("microphone", "microphone"), clock=clock)
    clock.now += PRIMARY_QUIET_S + 0.1
    sources["window"].push(tone(0.2))
    assert mixer.check_primary() == "window"
    sources["window"].push(tone(0.2))
    assert len(out) == 1


def test_a_quiet_primary_is_kept_when_nothing_else_is_delivering_either() -> None:
    clock = Clock()
    mixer, *_ = build(("window", "tap"), ("microphone", "microphone"), clock=clock)
    clock.now += PRIMARY_QUIET_S + 0.1
    assert mixer.check_primary() == "microphone"


def test_losing_one_input_keeps_the_other_recording() -> None:
    lost: list[tuple[str, object]] = []
    mixer, sources, out, errors = build(
        ("window", "tap"),
        ("microphone", "microphone"),
        on_input_lost=lambda name, error: lost.append((name, error)),
    )
    failure = OSError("unplugged")
    sources["microphone"].end(failure)
    assert lost == [("microphone", failure)]
    assert errors == []
    assert mixer.primary == "window"
    sources["window"].push(tone(0.3))
    assert out[-1][0] == pytest.approx(0.3)
    assert mixer.levels()["microphone"]["alive"] is False


def test_losing_every_input_ends_the_source() -> None:
    mixer, sources, out, errors = build(("window", "tap"), ("microphone", "microphone"))
    sources["microphone"].end(OSError("unplugged"))
    final = OSError("tap gone")
    sources["window"].end(final)
    assert errors == [final]
    assert not mixer.is_running


def test_a_file_reaching_its_end_ends_the_mix() -> None:
    """The file is the talk being replayed; when it ends, so does the recording."""
    mixer, sources, out, errors = build(("window", "file"), ("microphone", "microphone"))
    sources["window"].end(None)
    assert errors == [None]


def test_an_unknown_input_cannot_be_set() -> None:
    mixer, *_ = build(("microphone", "microphone"))
    with pytest.raises(KeyError):
        mixer.set_level("window", volume=10)


def test_names_must_be_distinct_and_present() -> None:
    with pytest.raises(ValueError):
        MixedSource([])
    with pytest.raises(ValueError):
        MixedSource([MixInput("a", Manual()), MixInput("a", Manual())])


# -- with real threads ----------------------------------------------------------------------------


def test_two_threaded_sources_mix_for_real() -> None:
    """The same two-clock situation as a recording, with real generator threads."""
    window = SyntheticSource([speech(1.0, amplitude=0.2)], speed=1.0)
    microphone = SyntheticSource([speech(1.0, amplitude=0.3)], speed=1.0)
    out: list[np.ndarray] = []
    mixer = MixedSource(
        [MixInput("window", window, "tap"), MixInput("microphone", microphone, "microphone")]
    )
    mixer.start(out.append)
    deadline = time.monotonic() + 5
    while (window.is_running or microphone.is_running) and time.monotonic() < deadline:
        time.sleep(0.05)
    mixer.stop()
    # About one second of audio, every frame containing both tones once they overlap.
    assert 25 <= len(out) <= 35
    peak = max(float(np.max(np.abs(frame))) for frame in out)
    assert peak > 0.3
