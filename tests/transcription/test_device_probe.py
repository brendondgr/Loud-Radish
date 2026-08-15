"""Device enumeration for a picker, and the "does this microphone actually work" probe.

The probe exists because enumerating a device proves only that the host knows about it. Every test
below is a case where the device list looks fine and the recording would not be.
"""

from __future__ import annotations

import numpy as np
import pytest
from app.services.audio.devices import (
    _classify,
    _default_input_index,
    _is_plumbing,
    list_devices,
)
from app.services.audio.probe import (
    HOT_PEAK_DBFS,
    QUIET_PEAK_DBFS,
    ProbeResult,
    probe_device,
)


class FakeSource:
    """An ``AudioSource`` stand-in that emits a fixed signal, or fails."""

    def __init__(self, device_id=None, frame_ms=32, signal=None, error=None, open_error=None):
        self.device_id = device_id
        self._signal = signal
        self._error = error
        self._open_error = open_error
        self.stopped = False

    class _Info:
        name = "Fake microphone"

    info = _Info()

    def start(self, on_frame, on_error=None):
        if self._open_error:
            raise self._open_error
        if self._signal is not None:
            for start in range(0, self._signal.size, 512):
                on_frame(self._signal[start : start + 512])
        if self._error and on_error:
            on_error(self._error)

    def stop(self):
        self.stopped = True


def source_of(signal=None, **kwargs):
    """A factory the probe can call, closing over what the fake should do."""

    def build(device_id=None, frame_ms=32):
        return FakeSource(device_id=device_id, frame_ms=frame_ms, signal=signal, **kwargs)

    return build


def tone(peak: float, seconds: float = 1.0, rate: int = 16_000) -> np.ndarray:
    t = np.arange(int(seconds * rate), dtype=np.float32) / rate
    return (peak * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def probe(signal=None, **kwargs) -> ProbeResult:
    return probe_device(seconds=0.05, source_factory=source_of(signal, **kwargs))


# -- verdicts ----------------------------------------------------------------------------


def test_a_healthy_level_passes() -> None:
    result = probe(tone(0.3))

    assert result.result == "ok"
    assert result.usable is True
    assert "-10" in result.message or "-11" in result.message


def test_silence_is_the_verdict_that_matters_most() -> None:
    """A device that opens and delivers nothing looks identical to one that works."""
    result = probe(np.zeros(16_000, dtype=np.float32))

    assert result.result == "silent"
    assert result.usable is False
    assert "muted" in result.message


def test_clipping_says_no_model_setting_will_fix_it() -> None:
    result = probe(tone(1.0))

    assert result.result == "clipping"
    assert result.usable is False
    assert "gain" in result.message


def test_a_quiet_device_still_works_but_is_flagged() -> None:
    """Recoverable, and worth saying before the talk rather than after."""
    result = probe(tone(0.005))

    assert result.result == "quiet"
    assert result.usable is True
    assert result.peak_dbfs < QUIET_PEAK_DBFS


def test_a_hot_device_is_warned_about_before_it_clips() -> None:
    result = probe(tone(0.85))

    assert result.result == "hot"
    assert result.usable is True
    assert result.peak_dbfs > HOT_PEAK_DBFS


def test_a_loud_signal_that_never_crosses_zero_is_not_audio() -> None:
    """The failure this check exists for, seen on a real machine.

    The ALSA ``default`` device returned samples peaking above digital full scale with no zero
    crossings at all. Loudness alone reads as "close to clipping", so the user is told to lower a
    gain that is not the problem — while the VAD correctly gates every frame and the transcript
    stays empty with nothing to explain it.
    """
    dc = np.full(16_000, 0.7, dtype=np.float32)
    result = probe(dc)

    assert result.result == "invalid"
    assert result.usable is False
    assert "never crosses zero" in result.message


def test_samples_above_full_scale_are_not_audio_either() -> None:
    """Normalised float capture cannot exceed 1.0; anything that does did not convert correctly."""
    impossible = (tone(0.5) + 1.2).astype(np.float32)
    result = probe(impossible)

    assert result.result == "invalid"


def test_a_real_waveform_is_not_mistaken_for_invalid() -> None:
    """The check must not fire on ordinary loud speech."""
    result = probe(tone(0.9))

    assert result.result in ("hot", "clipping")
    assert result.result != "invalid"


# -- failures ------------------------------------------------------------------------------


def test_a_device_that_will_not_open_is_a_result_not_an_exception() -> None:
    """The caller is a button in a settings dialog; it needs a reason, not a traceback."""
    result = probe(open_error=OSError("Device unavailable"))

    assert result.result == "failed"
    assert result.usable is False
    assert "in use by another application" in result.message


def test_a_device_that_opens_and_produces_nothing_names_the_likely_cause() -> None:
    result = probe(None)

    assert result.result == "failed"
    assert "no audio at all" in result.message


def test_a_device_that_dies_mid_probe_reports_the_hosts_own_words() -> None:
    result = probe(tone(0.3), error=RuntimeError("stream overflowed"))

    assert result.result == "failed"
    assert "stream overflowed" in result.message


def test_the_device_is_always_closed() -> None:
    """Leaving it open would block the session that the user starts next."""
    built: list[FakeSource] = []

    def build(device_id=None, frame_ms=32):
        source = FakeSource(device_id=device_id, signal=tone(0.3))
        built.append(source)
        return source

    probe_device(seconds=0.05, source_factory=build)
    assert built[0].stopped is True


# -- enumeration ----------------------------------------------------------------------------


class FakeDefault:
    """``sounddevice.default.device`` is subscriptable but is not a list or a tuple."""

    def __getitem__(self, index):
        return (7, 7)[index]


class FakeSoundDevice:
    def __init__(self, devices, default=None):
        self._devices = devices
        chosen = FakeDefault() if default is None else default
        self.default = type("D", (), {"device": chosen})()

    def query_devices(self):
        return self._devices


def entry(name, inputs=2, rate=48_000):
    return {"name": name, "max_input_channels": inputs, "default_samplerate": rate}


def test_the_system_default_is_detected_through_a_subscriptable_pair() -> None:
    """This returned None on every real machine until it was fixed.

    Nothing was marked as the default and device selection silently fell back to the first
    microphone in enumeration order rather than the one chosen in the system's sound settings.
    """
    assert _default_input_index(FakeSoundDevice([])) == 7


@pytest.mark.parametrize("value", [3, [3, 3], (3, 3)])
def test_other_shapes_of_default_still_resolve(value) -> None:
    assert _default_input_index(FakeSoundDevice([], default=value)) == 3


def test_a_host_with_no_default_reports_none() -> None:
    assert _default_input_index(FakeSoundDevice([], default=-1)) is None


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Samson GoMic: USB Audio (hw:3,0)", "microphone"),
        ("Monitor of Built-in Audio", "loopback"),
        ("Speakers (Realtek) (loopback)", "loopback"),
        ("Stereo Mix", "loopback"),
        # Both from PipeWire's JACK bridge, which presents every output as a capture source.
        ("Radeon High Definition Audio Controller Digital Stereo (HDMI)", "loopback"),
        ("Spotify/spotify", "loopback"),
    ],
)
def test_playback_fed_back_is_not_labelled_a_microphone(name: str, kind: str) -> None:
    assert _classify({"name": name}) == kind


@pytest.mark.parametrize("name", ["sysdefault", "pipewire", "spdif", "pulse"])
def test_alsa_plumbing_is_recognised(name: str) -> None:
    assert _is_plumbing(name) is True


def test_default_is_never_treated_as_plumbing() -> None:
    """It is the one entry that keeps working when hardware is unplugged."""
    assert _is_plumbing("default") is False


def test_plumbing_is_hidden_from_the_picker(monkeypatch) -> None:
    """Fourteen entries of which three are real microphones makes the picker hard to use."""
    devices = [
        entry("ThinkPad Dock Audio: Audio (hw:0,0)"),
        entry("sysdefault", inputs=128),
        entry("pipewire", inputs=128),
        entry("spdif", inputs=1),
        entry("Samson GoMic: USB Audio (hw:3,0)"),
    ]
    monkeypatch.setattr(
        "app.services.audio.devices._sounddevice",
        lambda: FakeSoundDevice(devices, default=0),
    )

    names = [device.name for device in list_devices(include_file=False)]
    assert names == ["ThinkPad Dock Audio: Audio (hw:0,0)", "Samson GoMic: USB Audio (hw:3,0)"]


def test_plumbing_that_is_the_default_is_kept_and_renamed(monkeypatch) -> None:
    """Hiding it would remove the entry the picker is currently pointing at."""
    monkeypatch.setattr(
        "app.services.audio.devices._sounddevice",
        lambda: FakeSoundDevice([entry("default", inputs=128)], default=0),
    )

    devices = list_devices(include_file=False)
    assert [d.name for d in devices] == ["System default input"]
    assert devices[0].is_default is True
