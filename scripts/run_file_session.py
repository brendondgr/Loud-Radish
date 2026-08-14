#!/usr/bin/env python
"""Run a WAV file through the full pipeline with console output only.

This is the BE §20 M4 validation: the streaming engine driven end to end, **no UI**, against
recorded audio. Committed text is printed as it commits and the hypothesis tail is redrawn in place,
so the commit policy's behaviour is visible directly — including the 2–4 second latency, which is
inherent to the approach rather than a defect.

Usage::

    uv run python scripts/make_fixture_wav.py --out data/fixtures
    uv run python scripts/run_file_session.py data/fixtures/alternating-20s.wav

    # A real model, if the optional group is installed:
    uv run python scripts/run_file_session.py talk.wav --backend faster-whisper --model small

    # Faster than real time, for a long recording:
    uv run python scripts/run_file_session.py talk.wav --speed 10
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "web" / "backend"))

from app.config.schema import AsrConfig, StreamingConfig, VadConfig  # noqa: E402
from app.services.asr import PromptBuilder, build_backend  # noqa: E402
from app.services.asr.contract import AsrLoadError  # noqa: E402
from app.services.asr.mock import MockScript, positional_audio  # noqa: E402
from app.services.audio.sources import WavFileSource  # noqa: E402
from app.services.streaming import CommittedSegment, EngineNotice, HypothesisUpdate  # noqa: E402
from app.services.streaming.passthrough import build_engine  # noqa: E402
from app.services.vad import build_gate  # noqa: E402

#: Placeholder transcript the mock backend "recognises", so the pipeline can be exercised without
#: a real model installed.
MOCK_TALK = (
    "the central claim is that these two operators commute only on the dense subspace "
    "where both are essentially self adjoint. outside it the bracket simply is not defined. "
    "that sounds like a technicality but it is not. almost every physical argument you have "
    "seen for the uncertainty relation quietly assumes the bracket exists everywhere. "
    "so let me set the counterexample up properly. take the position operator on the half "
    "line and take the generator of dilations alongside it. both are symmetric on smooth "
    "compactly supported functions. neither is self adjoint there and the deficiency indices "
    "differ which is the whole point of this example."
)


def format_time(seconds: float) -> str:
    """Render session-relative seconds as ``HH:MM:SS``."""
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class ConsoleView:
    """Prints committed segments as lines and redraws the hypothesis tail in place."""

    def __init__(self, show_hypothesis: bool = True) -> None:
        self._show_hypothesis = show_hypothesis
        self._tail_width = 0

    def handle(self, event: object) -> None:
        if isinstance(event, CommittedSegment):
            self._clear_tail()
            segment = event.segment
            print(f"[{format_time(segment.start)}] {segment.text}", flush=True)
        elif isinstance(event, HypothesisUpdate) and self._show_hypothesis:
            self._draw_tail(event.text)
        elif isinstance(event, EngineNotice):
            self._clear_tail()
            print(f"  ! {event.severity}: {event.message}", flush=True)

    def _draw_tail(self, text: str) -> None:
        line = f"           {text}" if text else ""
        padding = " " * max(0, self._tail_width - len(line))
        print(f"\r{line}{padding}", end="", flush=True)
        self._tail_width = len(line)

    def _clear_tail(self) -> None:
        if self._tail_width:
            print("\r" + " " * self._tail_width + "\r", end="", flush=True)
            self._tail_width = 0


def build_asr(args: argparse.Namespace):  # noqa: ANN201 - returns an AsrBackend
    """Construct and load the requested backend."""
    config = AsrConfig(
        backend=args.backend,
        model=args.model,
        device=args.device,
        precision=args.precision,
        session_prompt=args.prompt,
    )
    backend = build_backend(config)

    if args.backend == "mock":
        backend.set_script(MockScript(words=MOCK_TALK.split(), words_per_second=2.6))  # type: ignore[attr-defined]

    print(f"Loading {backend.model_id} …", flush=True)
    backend.load()
    backend.warm_up()
    print(f"Ready: {backend.model_id}\n", flush=True)
    return backend, config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, help="WAV file to replay through the pipeline")
    parser.add_argument("--backend", default="mock", help="mock | faster-whisper")
    parser.add_argument("--model", default="small")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--precision", default="int8", choices=["int8", "float16", "float32"])
    parser.add_argument("--speed", type=float, default=1.0, help="0 replays as fast as possible")
    parser.add_argument("--step", type=float, default=0.75, help="seconds between inference passes")
    parser.add_argument("--agreement", type=int, default=2, help="passes that must agree")
    parser.add_argument("--prompt", default="", help="session biasing prompt")
    parser.add_argument("--no-vad", action="store_true", help="transcribe every frame")
    parser.add_argument("--no-hypothesis", action="store_true", help="committed text only")
    args = parser.parse_args(argv)

    try:
        backend, asr_config = build_asr(args)
    except AsrLoadError as exc:
        print(f"Could not load the model:\n  {exc}", file=sys.stderr)
        return 2

    streaming = StreamingConfig(step_s=args.step, agreement_count=args.agreement)
    engine = build_engine(
        config=streaming,
        transcribe=backend.transcribe,
        capabilities=backend.capabilities,
        model_id=backend.model_id,
        prompt_builder=PromptBuilder(asr_config),
    )
    gate = build_gate(VadConfig(enabled=not args.no_vad), frame_ms=32)
    view = ConsoleView(show_hypothesis=not args.no_hypothesis)
    finished = threading.Event()

    # The mock does not listen to audio, it replays a script — so it is handed frames encoding
    # their position on the script's own timeline, while the VAD hears the real recording. That
    # timeline advances only while the recording contains speech, so the script stays lined up with
    # the fixture's speech and silence rather than talking through the gaps. With a real backend
    # both see the same audio; this demo exercises the engine, not recognition.
    use_script_timeline = args.backend == "mock"
    script_clock = 0.0

    def on_frame(frame: np.ndarray) -> None:
        nonlocal script_clock
        result = gate.process(frame)

        seconds = frame.size / 16_000
        if use_script_timeline:
            chunk = positional_audio(script_clock, seconds)
            if result.speaking:
                script_clock += seconds
        else:
            chunk = frame

        for event in engine.add_audio(chunk, speaking=result.speaking, pause=result.pause_event):
            view.handle(event)

    source = WavFileSource(args.wav, frame_ms=32, speed=args.speed)
    try:
        source.start(on_frame, lambda error: finished.set())
    except (FileNotFoundError, ValueError) as exc:
        print(f"Could not read the audio:\n  {exc}", file=sys.stderr)
        return 2

    print(f"Replaying {args.wav.name} ({source.duration_seconds:.1f} s) …\n", flush=True)
    try:
        source.wait()
    except KeyboardInterrupt:
        source.stop()

    for event in engine.flush():
        view.handle(event)

    metrics = engine.metrics
    print("\n" + "─" * 72)
    print(f"  audio            {metrics.audio_seconds:.1f} s")
    print(f"  inference passes {metrics.inference_passes} ({metrics.skipped_silence} skipped)")
    print(f"  realtime factor  {metrics.real_time_factor:.2f}×  (must stay above 1.0)")
    print(
        f"  commit latency   {metrics.median_commit_latency:.2f} s median, "
        f"{metrics.p95_commit_latency:.2f} s p95"
    )
    print(f"  forced commits   {metrics.forced_commit_rate:.0%} of all commits")
    print(f"  corrections      {metrics.correction_rate:.0%} of passes")
    print(f"  words committed  {metrics.committed_words}")
    if metrics.repetition_truncations:
        print(f"  repetition loops {metrics.repetition_truncations} truncated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
