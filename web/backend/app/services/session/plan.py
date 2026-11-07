"""What one run needs, decided once from its options (D-075).

Before the record sheet, `SessionManager._start_locked` branched on the mode: `recorded` opened a
sink and built no engine, `window` read three switches, and `live` built an engine and nothing else.
The window branch was already the general case — the other two were fixed combinations of its
switches — so this module makes it the only case. Every run now has options, from the sheet or from
its mode's preset in :data:`.modes.MODE_PRESETS`, and these functions read them.

Pure functions with no I/O, so the whole table from `docs/design-system.md` § Capture Modes can be
tested without opening a device.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import modes
from .shapes import CaptureOptions


class OptionsConflict(ValueError):
    """A request whose mode and options cannot both be true."""


@dataclass(frozen=True)
class RunPlan:
    """Which of the three pieces of a session this run starts."""

    #: Transcribe as audio arrives.
    engine: bool
    #: Write the audio to a file, which is what the post-capture pass reads.
    sink: bool
    #: Start the window recorder and its portal.
    video: bool


def preset(mode: str) -> CaptureOptions:
    """The options a request naming only `mode` stands for."""
    if not modes.is_valid_mode(mode):
        raise OptionsConflict(f"Unknown capture mode: {mode!r}")
    return CaptureOptions(**modes.MODE_PRESETS[mode])  # type: ignore[arg-type]


def resolve(mode: str | None, options: CaptureOptions | None) -> tuple[str, CaptureOptions]:
    """Settle a start request into one mode and one complete set of options.

    - Neither given: the original behaviour, ``live``.
    - A mode alone: that mode's preset, so shortcuts and older clients keep doing what they did.
    - Options without a source: the source the mode implies, so a client that predates the record
      sheet and sends ``mode=window`` with its three switches still records a window.
    - Options with a source: the mode is derived. A mode sent alongside that disagrees is refused
      rather than guessed at — one of the two is a mistake, and the caller knows which.

    Video is dropped for an audio source rather than refused: a microphone has no picture, and
    "record video" left ticked from a window run is not worth a refusal the user has to read.

    Raises:
        OptionsConflict: on an unknown mode, or a mode that contradicts the options' source.
    """
    if options is None:
        chosen = mode or modes.LIVE
        return chosen, preset(chosen)

    source = options.source or (modes.WINDOW_SOURCE if mode == modes.WINDOW else modes.MICROPHONE)
    if source != modes.WINDOW_SOURCE:
        options = replace(options, video=False)
    options = replace(options, source=source)

    options = replace(options, audio_inputs=_inputs(source, options))
    options = replace(options, levels=_levels(options))

    derived = modes.derive_mode(source, post_transcription=options.post_transcription)
    if mode is not None and mode != derived:
        raise OptionsConflict(
            f"A '{mode}' recording cannot be started with these options: recording from "
            f"{_SOURCE_NAMES.get(source, source)} and transcribing "
            f"{_timing(options)} is a '{derived}' recording."
        )
    return derived, options


def _inputs(source: str, options: CaptureOptions) -> tuple[str, ...]:
    """What this run's sound is made of, settled and checked (D-081).

    Unstated, it is what the source always meant: the microphone, the computer's sound, or — for a
    window — whatever `audio_source` chose, falling back to configuration when that is empty too
    (`_open_input` reads `capture.audio_source` for that case, as a window preset always has).

    Raises:
        OptionsConflict: for a combination that cannot be recorded as asked.
    """
    inputs = tuple(options.audio_inputs)
    if not inputs:
        if source == modes.MICROPHONE:
            return (modes.INPUT_MICROPHONE,)
        if source == modes.SYSTEM:
            return (modes.INPUT_SYSTEM,)
        legacy = modes.LEGACY_AUDIO_SOURCE.get(options.audio_source)
        return (legacy,) if legacy else ()

    unknown = [name for name in inputs if name not in modes.AUDIO_INPUTS]
    if unknown:
        raise OptionsConflict(f"Unknown audio input: {', '.join(unknown)}.")
    if len(set(inputs)) != len(inputs):
        raise OptionsConflict("The same sound was chosen twice.")
    if len(inputs) > modes.MAX_AUDIO_INPUTS:
        raise OptionsConflict(f"At most {modes.MAX_AUDIO_INPUTS} sounds can be recorded together.")
    # Both are taps on the same playback: the window's sound is part of the computer's, so the two
    # together would record it twice, an echo of itself a few milliseconds apart.
    if modes.INPUT_WINDOW in inputs and modes.INPUT_SYSTEM in inputs:
        raise OptionsConflict(
            "A window's sound is already part of this computer's sound; choose one of them."
        )
    if modes.INPUT_WINDOW in inputs and source != modes.WINDOW_SOURCE:
        raise OptionsConflict("A window's sound can only be recorded with a window.")
    own = {modes.MICROPHONE: modes.INPUT_MICROPHONE, modes.SYSTEM: modes.INPUT_SYSTEM}.get(source)
    if own and own not in inputs:
        raise OptionsConflict(f"Recording {_SOURCE_NAMES[source]} has to include its sound.")
    return inputs


def _levels(options: CaptureOptions) -> dict[str, int]:
    """Starting volumes for this run's inputs, each 0–100. A level for an absent input is refused:
    it is a sound the caller thinks is being recorded and is not."""
    levels = dict(options.levels)
    stray = [name for name in levels if name not in options.audio_inputs]
    if stray:
        raise OptionsConflict(f"A volume was given for a sound not being recorded: {stray[0]}.")
    for name, volume in levels.items():
        if not 0 <= int(volume) <= 100:
            raise OptionsConflict(f"The {name} volume must be between 0 and 100 %.")
    return {name: int(volume) for name, volume in levels.items()}


def plan_run(options: CaptureOptions) -> RunPlan:
    """Which pieces a run with `options` starts. None of them implies another."""
    return RunPlan(
        engine=options.live_transcription,
        sink=options.post_transcription,
        video=options.video and options.source == modes.WINDOW_SOURCE,
    )


_SOURCE_NAMES = {
    modes.MICROPHONE: "the microphone",
    modes.SYSTEM: "this computer's sound",
    modes.WINDOW_SOURCE: "a window",
}


def _timing(options: CaptureOptions) -> str:
    if options.live_transcription and options.post_transcription:
        return "both while recording and afterwards"
    if options.post_transcription:
        return "afterwards"
    if options.live_transcription:
        return "while recording"
    return "not at all"
