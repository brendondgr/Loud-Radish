"""Audio sources — the WAV file source, the synthetic source, and device enumeration.

The file source is the most important thing here. It is what makes the streaming engine testable at
all: a saved recording is reproducible, a live microphone is not (BE §19.1).
"""

from __future__ import annotations

import sys
import threading
import time
import types
import wave
from pathlib import Path

import numpy as np
import pytest
from app.services.audio.devices import (
    FILE_DEVICE_ID,
    AudioDevice,
    DeviceEnumerationError,
    _classify,
    find_device,
    list_devices,
)
from app.services.audio.formats import SAMPLE_RATE, frame_samples
from app.services.audio.sources import DeviceSource, SyntheticSource, WavFileSource
from app.services.audio.sources.device import DeviceUnavailableError
from app.services.audio.sources.synthetic import silence, speech


def write_wav(
    path: Path, seconds: float = 1.0, rate: int = SAMPLE_RATE, channels: int = 1, width: int = 2
) -> Path:
    """Write a short tone as a PCM WAV file."""
    count = int(seconds * rate)
    t = np.arange(count, dtype=np.float64) / rate
    mono = (0.4 * np.sin(2 * np.pi * 300 * t) * 32767).astype(np.int16)
    data = np.repeat(mono[:, None], channels, axis=1).ravel() if channels > 1 else mono

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(data.astype(np.int16).tobytes())
    return path


class Collector:
    """Captures frames from a source, the way the ring buffer will."""

    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []
        self.error: BaseException | None = None
        self.finished = threading.Event()
        self._lock = threading.Lock()

    def on_frame(self, frame: np.ndarray) -> None:
        with self._lock:
            self.frames.append(frame.copy())

    def on_error(self, error: Exception | None) -> None:
        self.error = error
        self.finished.set()

    @property
    def samples(self) -> int:
        with self._lock:
            return sum(frame.size for frame in self.frames)


class TestWavFileSource:
    def test_it_emits_the_whole_file_in_uniform_frames(self, tmp_path: Path) -> None:
        source = WavFileSource(write_wav(tmp_path / "a.wav", seconds=1.0), frame_ms=32, speed=0)
        collector = Collector()
        source.start(collector.on_frame, collector.on_error)
        assert source.wait(timeout=5.0)

        expected = frame_samples(32)
        assert all(frame.size == expected for frame in collector.frames)
        assert collector.samples >= SAMPLE_RATE
        assert collector.error is None

    def test_frames_are_in_canonical_format(self, tmp_path: Path) -> None:
        source = WavFileSource(write_wav(tmp_path / "a.wav", seconds=0.3), speed=0)
        collector = Collector()
        source.start(collector.on_frame, collector.on_error)
        source.wait(timeout=5.0)

        frame = collector.frames[0]
        assert frame.dtype == np.float32
        assert frame.ndim == 1
        assert float(np.max(np.abs(frame))) <= 1.0

    def test_a_44100_hz_stereo_file_is_converted_on_the_way_in(self, tmp_path: Path) -> None:
        path = write_wav(tmp_path / "stereo.wav", seconds=1.0, rate=44_100, channels=2)
        source = WavFileSource(path, speed=0)
        collector = Collector()
        source.start(collector.on_frame, collector.on_error)
        source.wait(timeout=5.0)

        assert source.duration_seconds == pytest.approx(1.0, rel=0.02)
        assert collector.samples == pytest.approx(SAMPLE_RATE, rel=0.05)

    def test_real_time_playback_takes_roughly_real_time(self, tmp_path: Path) -> None:
        """Timing behaviour, backpressure, and the commit timeout all depend on this."""
        source = WavFileSource(write_wav(tmp_path / "a.wav", seconds=0.5), frame_ms=32, speed=1.0)
        collector = Collector()

        started = time.monotonic()
        source.start(collector.on_frame, collector.on_error)
        source.wait(timeout=5.0)
        elapsed = time.monotonic() - started

        assert 0.35 <= elapsed <= 1.2, f"expected roughly 0.5 s of playback, took {elapsed:.2f} s"

    def test_faster_than_real_time_playback_is_faster(self, tmp_path: Path) -> None:
        source = WavFileSource(write_wav(tmp_path / "a.wav", seconds=2.0), speed=20.0)
        collector = Collector()

        started = time.monotonic()
        source.start(collector.on_frame, collector.on_error)
        source.wait(timeout=5.0)

        assert time.monotonic() - started < 1.0

    def test_the_final_short_frame_is_padded(self, tmp_path: Path) -> None:
        """A uniform frame size means no consumer needs a short-frame branch."""
        path = write_wav(tmp_path / "odd.wav", seconds=0.05)
        source = WavFileSource(path, frame_ms=32, speed=0)
        collector = Collector()
        source.start(collector.on_frame, collector.on_error)
        source.wait(timeout=5.0)

        assert {frame.size for frame in collector.frames} == {frame_samples(32)}

    def test_stopping_early_halts_emission(self, tmp_path: Path) -> None:
        source = WavFileSource(write_wav(tmp_path / "a.wav", seconds=5.0), speed=1.0)
        collector = Collector()
        source.start(collector.on_frame, collector.on_error)
        time.sleep(0.15)
        source.stop()

        assert not source.is_running
        assert collector.samples < 5 * SAMPLE_RATE

    def test_a_missing_file_reports_what_to_fix(self, tmp_path: Path) -> None:
        source = WavFileSource(tmp_path / "nope.wav")
        with pytest.raises(FileNotFoundError, match="file_path"):
            source.start(lambda frame: None)

    def test_an_unsupported_sample_width_says_how_to_convert(self, tmp_path: Path) -> None:
        path = tmp_path / "wide.wav"
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(3)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(b"\x00" * 300)

        with pytest.raises(ValueError, match="16-bit PCM"):
            WavFileSource(path).start(lambda frame: None)

    def test_starting_twice_is_rejected(self, tmp_path: Path) -> None:
        source = WavFileSource(write_wav(tmp_path / "a.wav", seconds=2.0), speed=1.0)
        source.start(lambda frame: None)
        try:
            with pytest.raises(RuntimeError):
                source.start(lambda frame: None)
        finally:
            source.stop()

    def test_a_raising_consumer_does_not_kill_capture(self, tmp_path: Path) -> None:
        """Losing capture is far worse than losing one frame."""
        source = WavFileSource(write_wav(tmp_path / "a.wav", seconds=0.3), speed=0)
        seen = []

        def explode(frame: np.ndarray) -> None:
            seen.append(frame)
            raise RuntimeError("consumer failure")

        finished = threading.Event()
        source.start(explode, lambda error: finished.set())
        assert source.wait(timeout=5.0)
        assert len(seen) > 3

    def test_it_works_as_a_context_manager(self, tmp_path: Path) -> None:
        with WavFileSource(write_wav(tmp_path / "a.wav", seconds=2.0), speed=1.0) as source:
            source.start(lambda frame: None)
        assert not source.is_running


class TestSyntheticSource:
    def test_it_renders_the_scripted_spans(self) -> None:
        source = SyntheticSource([speech(1.0), silence(0.5)], speed=0)
        rendered = source.render()
        assert rendered.size == int(1.5 * SAMPLE_RATE)
        assert float(np.max(np.abs(rendered[:SAMPLE_RATE]))) > 0.1
        assert float(np.max(np.abs(rendered[SAMPLE_RATE:]))) == 0.0

    def test_it_emits_frames_like_any_other_source(self) -> None:
        source = SyntheticSource([speech(0.5)], frame_ms=32, speed=0)
        collector = Collector()
        source.start(collector.on_frame, collector.on_error)
        source.wait(timeout=5.0)

        assert collector.samples >= int(0.5 * SAMPLE_RATE)
        assert all(frame.size == frame_samples(32) for frame in collector.frames)

    def test_it_describes_itself(self) -> None:
        info = SyntheticSource([speech(0.1)]).info
        assert info.sample_rate == SAMPLE_RATE
        assert "16 kHz" in info.describe()


class TestDeviceEnumeration:
    def test_the_file_source_is_always_listed(self) -> None:
        """The reproducible input the pipeline is developed against; hiding it helps nobody."""
        devices = list_devices()
        assert any(device.id == FILE_DEVICE_ID for device in devices)

    def test_the_file_source_can_be_excluded(self) -> None:
        assert all(device.id != FILE_DEVICE_ID for device in list_devices(include_file=False))

    def test_devices_serialise_to_json_safe_payloads(self) -> None:
        payload = list_devices()[0].as_dict()
        assert set(payload) >= {"id", "name", "kind", "channels", "is_default"}

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Built-in Microphone", "microphone"),
            ("Speakers (loopback)", "loopback"),
            ("Monitor of Built-in Audio", "loopback"),
            ("alsa_output.pci-0000_00_1f.3.analog-stereo.monitor", "loopback"),
            ("Stereo Mix", "loopback"),
            ("USB Audio Device", "microphone"),
        ],
    )
    def test_loopback_devices_are_recognised_by_platform_naming(
        self, name: str, expected: str
    ) -> None:
        """Loopback is enumerated separately by the host; a merged list must still tag it."""
        assert _classify({"name": name}) == expected

    def test_microphones_and_loopbacks_appear_in_one_merged_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _fake_sounddevice(
            [
                {
                    "name": "Built-in Microphone",
                    "max_input_channels": 2,
                    "default_samplerate": 48000,
                },
                {
                    "name": "Speakers (loopback)",
                    "max_input_channels": 2,
                    "default_samplerate": 48000,
                },
                {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000},
            ]
        )
        monkeypatch.setitem(sys.modules, "sounddevice", fake)

        devices = [device for device in list_devices() if device.kind != "file"]
        assert [device.kind for device in devices] == ["microphone", "loopback"]
        assert devices[0].is_default

    def test_output_only_devices_are_omitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _fake_sounddevice([{"name": "Speakers", "max_input_channels": 0}])
        monkeypatch.setitem(sys.modules, "sounddevice", fake)
        assert [device for device in list_devices() if device.kind != "file"] == []

    def test_a_host_failure_is_reported_with_a_remedy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = types.ModuleType("sounddevice")

        def boom() -> None:
            raise OSError("PortAudio not initialised")

        fake.query_devices = boom  # type: ignore[attr-defined]
        fake.default = types.SimpleNamespace(device=None)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "sounddevice", fake)

        with pytest.raises(DeviceEnumerationError, match="audio service"):
            list_devices()

    def test_find_device_returns_the_default_when_none_is_named(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _fake_sounddevice(
            [
                {"name": "Cheap Mic", "max_input_channels": 1},
                {"name": "Good Mic", "max_input_channels": 1},
            ],
            default_index=1,
        )
        monkeypatch.setitem(sys.modules, "sounddevice", fake)
        found = find_device(None)
        assert found is not None and found.name == "Good Mic"

    def test_find_device_returns_none_for_an_unknown_id(self) -> None:
        assert find_device("no-such-device") is None


class TestDeviceSource:
    def test_it_explains_how_to_install_the_optional_backend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "sounddevice", None)
        with pytest.raises(DeviceUnavailableError, match="audio-device"):
            DeviceSource(device_id="0").start(lambda frame: None)

    def test_selecting_the_file_device_points_at_the_right_class(self) -> None:
        fake = _fake_sounddevice([])
        with pytest.raises(DeviceUnavailableError, match="WavFileSource"):
            DeviceSource(device_id=FILE_DEVICE_ID, sounddevice_module=fake).start(
                lambda frame: None
            )

    def test_an_unknown_device_suggests_re_opening_the_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _fake_sounddevice([{"name": "Mic", "max_input_channels": 1}])
        monkeypatch.setitem(sys.modules, "sounddevice", fake)
        with pytest.raises(DeviceUnavailableError, match="device list"):
            DeviceSource(device_id="99", sounddevice_module=fake).start(lambda frame: None)

    def test_it_reports_no_device_before_starting(self) -> None:
        assert DeviceSource().info.name == "No device"

    def test_device_audio_is_converted_and_reframed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The host delivers its own block size at its own rate; downstream sees canonical
        frames."""
        fake = _fake_sounddevice(
            [{"name": "Mic", "max_input_channels": 1, "default_samplerate": 48000}]
        )
        monkeypatch.setitem(sys.modules, "sounddevice", fake)

        source = DeviceSource(device_id="0", frame_ms=32, sounddevice_module=fake)
        collector = Collector()
        source.start(collector.on_frame, collector.on_error)

        # One second of 48 kHz audio, delivered in the host's own blocks.
        block = np.zeros(1536, dtype=np.float32)
        for _ in range(48_000 // 1536):
            fake.last_stream.push(block)
        source.stop()

        assert all(frame.size == frame_samples(32) for frame in collector.frames)
        assert collector.samples == pytest.approx(SAMPLE_RATE, rel=0.05)


def _fake_sounddevice(devices: list[dict], default_index: int = 0) -> types.ModuleType:
    """A stand-in for ``sounddevice``, since no audio device exists in this environment."""
    module = types.ModuleType("sounddevice")

    class FakeStream:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.started = False
            module.last_stream = self  # type: ignore[attr-defined]

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.started = False

        def close(self) -> None:
            pass

        def push(self, block: np.ndarray) -> None:
            callback = self.kwargs["callback"]
            callback(block, block.size, None, None)  # type: ignore[operator]

    module.query_devices = lambda: [  # type: ignore[attr-defined]
        {"name": "", "max_input_channels": 0, "default_samplerate": 48000, **device}
        for device in devices
    ]
    module.default = types.SimpleNamespace(device=(default_index, None))  # type: ignore[attr-defined]
    module.InputStream = FakeStream  # type: ignore[attr-defined]
    return module


def test_audio_device_dataclass_is_immutable() -> None:
    device = AudioDevice(id="1", name="Mic", kind="microphone")
    with pytest.raises(AttributeError):
        device.name = "Other"  # type: ignore[misc]
