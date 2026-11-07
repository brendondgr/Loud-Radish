"""Deciding what a session listens to, and proving that it is listening (D-028, D-030).

Split out of ``manager.py`` when that file passed twice its 800-line cap. **Mixin methods on**
:class:`~.manager.SessionManager` — see the note in ``frames.py`` for why that shape was chosen.

A microphone, a monitor of the machine's output, a file, or a tap linked into one application's
playback nodes. The tap is the elaborate one, and every piece of that elaboration is a measurement
some earlier version got wrong: that a tap can be linked, active and silent; that one node name is
not one node; and that an unresolved monitor target records the microphone while looking like it
worked.
"""

from __future__ import annotations

import logging

from ...config import AppConfig
from ..audio import WavFileSource
from ..audio.monitor import MonitorUnavailable, default_sink
from ..audio.sources import (
    AudioSource,
    DeviceSource,
    MixInput,
    MonitorSource,
    SyntheticSource,
    probe_peak,
    silence,
)
from ..audio.tap import ApplicationTap, TapError
from ..audio.tap import playback_streams as tap_streams
from ..audio.tap import score as tap_score
from . import degradation, modes
from .shapes import TAP_PROBE_S, SessionError

logger = logging.getLogger(__name__)


class AudioSourceMixin:
    """See the module docstring: these are methods of ``SessionManager``."""

    def _audio_choice(self, config: AppConfig) -> str:
        """Which audio this run should capture.

        The per-run choice wins. It is the one in front of the user at the moment they press
        record, and the whole reason the record sheet asks it is that this decision changes from
        recording to recording — a talk playing in a window one minute, narration over it the next.
        """
        options = self._options
        if options is not None and options.audio_source:
            return options.audio_source
        return config.capture.audio_source

    def _input_names(self, config: AppConfig, source: str) -> list[str]:
        """The inputs this run mixes, in order (D-081). Settled by `plan.resolve`, except for a
        window request that named none, which has always deferred to `capture.audio_source`."""
        options = self._options
        inputs = list(options.audio_inputs) if options is not None else []
        if inputs:
            return inputs
        if source == modes.MICROPHONE:
            return [modes.INPUT_MICROPHONE]
        if source == modes.SYSTEM:
            return [modes.INPUT_SYSTEM]
        return [modes.LEGACY_AUDIO_SOURCE.get(self._audio_choice(config), modes.INPUT_SYSTEM)]

    def _open_source(self, config: AppConfig, source: str = modes.MICROPHONE) -> AudioSource:
        """Build the audio source this session should capture from: a mix of its inputs (D-081).

        Decided by what the record sheet said to record (D-075), not by the mode. **Every run goes
        through the mixer**, so volume and mute work for every run; one input at 100 % passes
        through it sample for sample, so a single-input recording is exactly what it always was.

        **The file source still wins outright**: it is the reproducible input the whole pipeline is
        developed against, and a session that silently ignored it would make every mode
        untestable. It stands in for the *first* input; any second one is silence, so a two-input
        run on a test server still exercises the whole mixing path.
        """
        names = self._input_names(config, source)
        if config.audio.source_type == "file":
            if not config.audio.file_path:
                raise SessionError(
                    "The file source is selected but no file is set. "
                    "Choose a WAV file, or switch to a microphone."
                )
            inputs = [
                MixInput(
                    names[0],
                    WavFileSource(
                        config.audio.file_path,
                        frame_ms=config.audio.frame_ms,
                        speed=config.audio.file_speed,
                    ),
                    "file",
                )
            ]
            inputs += [
                MixInput(
                    name,
                    # Paced at real time: unpaced, a looping generator spins as fast as it can and
                    # starves the file it is meant to accompany of the interpreter.
                    SyntheticSource(
                        [silence(1.0)], frame_ms=config.audio.frame_ms, speed=1.0, loop=True
                    ),
                    "tap",
                )
                for name in names[1:]
            ]
        else:
            inputs = []
            try:
                for name in names:
                    inputs.append(self._open_input(config, name))
            except Exception:
                # A tap built for the first input is a null sink in the user's output picker until
                # something unloads it; the second input failing must not leak it.
                tap, self._tap = self._tap, None
                if tap is not None:
                    tap.close()
                raise
        return self._build_mix(config, inputs)

    def _open_input(self, config: AppConfig, name: str) -> MixInput:
        """One named input, opened but not started.

        **This computer's sound is a tap, never the microphone**, and so is a window's: the portal
        carries video only (D-022), and recording the person watching instead of the window was the
        reported fault. The microphone is only ever what was asked for by name.
        """
        if name == modes.INPUT_WINDOW:
            return MixInput(name, self._open_application_tap(config), "tap")
        if name == modes.INPUT_SYSTEM:
            # **Also through a tap.** Recording the default sink's monitor directly is the obvious
            # implementation and it does not work here: `pw-record --target=<sink>.monitor` failed
            # to resolve against this machine's Bluetooth sink and, because an unresolved target
            # falls back to the *default source*, silently recorded the microphone instead —
            # measured at 0.97 correlation with it. Tapping every playing stream reaches the same
            # audio by the route that demonstrably works, and one mechanism is easier to keep
            # correct than two.
            try:
                return MixInput(name, self._open_application_tap(config, match=False), "tap")
            except MonitorUnavailable as exc:
                # The microphone is not a silent substitute here — it records the wrong thing, and
                # the whole point of the choice is that it does not.
                raise SessionError(str(exc)) from exc
        return MixInput(
            name,
            DeviceSource(device_id=config.audio.device_id, frame_ms=config.audio.frame_ms),
            "microphone",
        )

    def _open_application_tap(self, config: AppConfig, *, match: bool = True) -> AudioSource:
        """Tap the chosen window's own audio, leaving it playing on the user's speakers.

        Used for both audio choices. `match=False` links every stream that is playing — the
        "system output" case — and `match=True` narrows to the ones that look like the window's.
        Both go through a tap because tapping is the route that works on this machine: targeting a
        sink's monitor directly silently recorded the microphone instead.

        Raises:
            MonitorUnavailable: when there is nothing to tap or the tap cannot be built. **Not
                downgraded to the microphone**, ever. Falling back to a device that records the
                person watching, in a mode whose purpose is recording the window, is the fault this
                whole path exists to fix — and it is the one that produced a transcript of the
                user's own speech over a video they were trying to record.
        """
        streams = self._tap_candidates(match)
        if not streams:
            raise MonitorUnavailable(
                "Nothing is playing any audio, so there is no window sound to record. Start the "
                "video first, or choose 'My microphone' if you meant to narrate."
            )

        tap = ApplicationTap()
        try:
            tap.open()
            # The *set*, not the best one: a browser owns a playback node per media element, and
            # linking only the top-ranked node records only whichever tab happened to be first.
            if tap.link_all(streams) == 0:
                raise TapError("no audio ports could be linked into the capture sink")
        except TapError as exc:
            tap.close()
            raise MonitorUnavailable(
                f"The window's audio could not be captured ({exc}). Choose 'My microphone' if you "
                "meant to record yourself."
            ) from exc

        self._verify_tap(tap)

        wider = self._whole_output_if_the_tap_is_dead(tap, config)
        if wider is not None:
            return wider

        self._tap = tap
        self._tap_match = match
        return MonitorSource(frame_ms=config.audio.frame_ms, tap=tap)

    def _whole_output_if_the_tap_is_dead(
        self, tap: ApplicationTap, config: AppConfig
    ) -> AudioSource | None:
        """Record the machine's whole output when the tap is built correctly and carries nothing.

        **The third question, after two that could not answer this on their own.** Listening to the
        tap alone cannot tell a dead graph from a paused video — both are bit-exact zeros, and
        refusing on that basis rejected working recordings within a day. Counting links cannot tell
        a link that carries audio from one that merely exists — which is this fault exactly: the
        sink created, the browser's ports linked, every link ``active``, every node ``running``,
        every gain 1.0, and zeros for the length of a talk.

        Asking both at once separates them, because the machine's own output is the ground truth
        neither question had:

        =============  =============  ==================================  ==================
        tap            whole output   what that means                     what happens
        =============  =============  ==================================  ==================
        silent         silent         nothing is playing yet              keep the tap
        anything       —              the tap is delivering               keep the tap
        silent         audible        the tap cannot carry this stream    record the output
        unknown        anything       the probe did not run               keep the tap
        =============  =============  ==================================  ==================

        The last row is not a formality. A probe that cannot run knows nothing, and must never be
        the thing that changes what a recording captures.

        Widening rather than refusing is the deliberate trade. What the user asked for is a
        transcript of the thing they are watching; a wider recording still contains it, and an empty
        one contains nothing. The cost — every other sound on the machine lands in the transcript —
        is real, so it is said out loud rather than absorbed silently.
        """
        sink = tap.monitor.removesuffix(".monitor")
        if probe_peak(sink, capture_sink=True, seconds=TAP_PROBE_S) != 0.0:
            # Delivering, or unmeasurable. Either way, not the fault this exists for.
            return None

        try:
            whole_output = default_sink()
        except MonitorUnavailable:
            # No output to widen to. The tap is the only route there is, so it stays.
            return None

        # **`capture_sink=True` here too, and it is load-bearing.** Widening through
        # `<name>.monitor` was this repair's own worst bug: that target does not resolve on either
        # sink on this machine, and an unresolved target falls back to the *default source* — so
        # the widened capture recorded the **microphone**, correlating with it at +1.000. It went
        # unnoticed because a microphone hears the speakers, which makes the level look right.
        # A window recording that transcribes the room is the exact fault D-028 exists to prevent,
        # and widening must not be the thing that reintroduces it.
        if (
            probe_peak(whole_output, capture_sink=True, device_sink=True, seconds=TAP_PROBE_S)
            <= 0.0
        ):
            # The machine is not playing anything, so the tap's silence is the ordinary kind —
            # someone who pressed record before pressing play. Leave it alone; it will fill.
            return None

        logger.warning(
            "The tap on %s is linked (%d ports) and delivering digital silence while the machine "
            "is playing audio. Recording the whole output instead.",
            sink,
            tap.live_links,
        )
        tap.close()
        self._tap = None
        self._emit_failure(degradation.window_audio_not_delivering())
        return MonitorSource(node=whole_output, frame_ms=config.audio.frame_ms, capture_sink=True)

    def _tap_candidates(self, match: bool) -> list:
        """The playback streams this run should tap.

        **`match` used to be documented and ignored.** Both audio choices linked every stream that
        was playing, so "record this window's audio" quietly recorded the machine's, and a second
        application making noise landed in the transcript of the first. It now narrows using the
        same `rank`/`score` heuristic the interface already presents — keeping *every* stream that
        scores rather than the single best one, because a browser owns a playback node per media
        element and picking one records whichever tab happened to be first.

        A window that matches nothing falls back to everything rather than to nothing. The match is
        a heuristic over properties PipeWire's own documentation warns are not authoritative
        (D-027), so it must not be the thing that decides a recording captures no audio at all.
        """
        streams = tap_streams()
        if not match or not streams:
            return streams

        options = self._options
        app_id = getattr(options, "window_app_id", "") or ""
        title = getattr(options, "window_title", "") or ""
        if not app_id and not title:
            return streams

        scored = [s for s in streams if tap_score(s, app_id=app_id, title=title) > 0]
        if not scored:
            logger.info("No playing stream matched the chosen window; tapping everything instead.")
            return streams
        logger.info(
            "Tapping %d of %d playing streams matched to the window", len(scored), len(streams)
        )
        return scored

    def _verify_tap(self, tap: ApplicationTap) -> None:
        """Confirm the graph is routing something into the tap, before the session commits.

        **This asked the wrong question for a day, and refused working recordings for it.** The
        first version listened to the tap and treated a run of bit-exact zeros as proof the graph
        was not delivering. That premise holds for a microphone, which always carries a noise floor,
        and is false for an application — a media player between sounds, a paused video whose stream
        is still open, or a clip with a silent lead-in all write literal zeros. Someone who pressed
        record a moment before the audio started was told the capture would be silent, and it would
        not have been.

        Whether anything is *linked* cannot be confused with whether anything is *audible*, so that
        is what is asked. It still catches the fault the check exists for — a tap nothing is routed
        into records silence for the length of a talk — and it cannot fire on a quiet moment.
        """
        if tap.live_links > 0:
            return

        tap.close()
        raise MonitorUnavailable(
            "The window's audio could not be routed into the capture — nothing is connected to it, "
            "so the recording would be silent throughout. Try starting the recording again, or "
            "choose 'My microphone' if you meant to record yourself."
        )

    def _relink_tap(self) -> None:
        """Join playback nodes that appeared after the recording started.

        **Linking once is linking too early.** `tap.py`'s own module docstring says the set has to
        be watched, because "an application creates and destroys playback nodes as the user opens
        tabs and starts media" — and the session linked once, at open, and never again. So pressing
        record and *then* pressing play produced a recording of nothing: the node carrying the video
        did not exist at the moment the tap was built. Runs from the status tick, which already
        fires once a second for the monitor pane.
        """
        tap = self._tap
        if tap is None or not tap.is_open:
            return
        try:
            added = tap.link_all(self._tap_candidates(self._tap_match))
        except TapError as exc:
            logger.debug("Could not refresh the audio tap: %s", exc)
            return
        if added:
            logger.info("Linked %d newly playing port(s) into the audio tap", added)

        # **Reported, never fatal.** If everything the tap was carrying goes away mid-recording —
        # the browser tab closed, the player quit — the rest of the session records silence, and the
        # user should hear that from the application rather than from an empty transcript
        # afterwards. Said once: repeating it every second would bury the notices that matter.
        if tap.live_links == 0 and not self._warned_tap_silent:
            self._warned_tap_silent = True
            self._emit_failure(degradation.window_audio_stopped())
