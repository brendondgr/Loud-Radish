"""Taking a video's sound out for the batch pass (D-076).

The pass reads one format, a 16 kHz mono 16-bit WAV, and these check that a video's sound comes out
in exactly that format and length — and that a video with no sound is an answer, not an exception.
"""

from __future__ import annotations

import wave

import numpy as np
import pytest
from app.services.recording.batch import read_wav
from app.services.recording.extract import extract_audio, has_sound

av = pytest.importorskip("av")


def write_video(path, *, sound: bool, seconds: int = 2, rate: int = 48_000) -> None:
    with av.open(str(path), "w", format="matroska") as container:
        video = container.add_stream("mpeg4", rate=10)
        video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
        audio = container.add_stream("pcm_s16le", rate=rate) if sound else None
        for _ in range(10 * seconds):
            frame = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), np.uint8), format="rgb24")
            for packet in video.encode(frame):
                container.mux(packet)
        for packet in video.encode():
            container.mux(packet)
        if audio is not None:
            t = np.arange(rate * seconds) / rate
            samples = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16).reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
            frame.sample_rate = rate
            for packet in audio.encode(frame):
                container.mux(packet)
            for packet in audio.encode():
                container.mux(packet)


def test_a_videos_sound_becomes_the_wav_the_pass_reads(tmp_path) -> None:
    video, wav = tmp_path / "video.mkv", tmp_path / "audio.wav"
    write_video(video, sound=True, seconds=2, rate=48_000)

    result = extract_audio(video, wav)

    assert result.ok, result.reason
    assert result.seconds == pytest.approx(2.0, abs=0.05)
    with wave.open(str(wav), "rb") as handle:
        assert (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) == (
            1,
            2,
            16_000,
        )
    samples, seconds = read_wav(wav)
    assert seconds == pytest.approx(2.0, abs=0.05)
    assert float(np.abs(samples).max()) > 0.1
    assert not (tmp_path / "audio.wav.part").exists()


def test_a_video_with_no_sound_is_refused_with_a_reason(tmp_path) -> None:
    video, wav = tmp_path / "video.mkv", tmp_path / "audio.wav"
    write_video(video, sound=False)

    result = extract_audio(video, wav)

    assert result.ok is False
    assert result.reason == "This video has no sound to transcribe."
    assert not wav.exists()
    assert has_sound(video) is False


def test_an_unreadable_file_is_an_answer_not_an_exception(tmp_path) -> None:
    video = tmp_path / "video.webm"
    video.write_bytes(b"not a video at all")

    assert has_sound(video) is False
    result = extract_audio(video, tmp_path / "audio.wav")
    assert result.ok is False
    assert "could not be read" in result.reason
