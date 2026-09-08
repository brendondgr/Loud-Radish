"""Typed configuration schema for every area of the pipeline.

One model per configuration area of the backend architecture (BE §13.2). These models are the
validation boundary: anything that reaches the pipeline has already been through them, so no stage
needs to defend against a missing key or an out-of-range value.

Credentials never appear here. They live in the OS credential store — see ``credentials.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SourceType = Literal["microphone", "loopback", "file"]
VadDetector = Literal["energy", "silero"]
ComputeDevice = Literal["auto", "cpu", "cuda"]
Precision = Literal["int8", "float16", "float32"]
LlmMode = Literal["local", "api"]
ExportFormat = Literal["text", "markdown", "srt", "vtt", "json"]


class _Base(BaseModel):
    """Shared model behaviour: reject unknown keys, validate on assignment."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AudioConfig(_Base):
    """Capture device selection and the optional preprocessing chain (BE §4)."""

    source_type: SourceType = "microphone"
    device_id: str | None = None
    file_path: str | None = None
    #: Replay speed for the file source. 1.0 is real time; above 1.0 drives the accelerated soak
    #: run and lets a long recording be reprocessed quickly. 0 emits as fast as the source can,
    #: which will overrun the bounded capture queue and drop most of the audio — useful only for
    #: deliberately exercising that backpressure path.
    file_speed: float = Field(default=1.0, ge=0.0, le=100.0)
    frame_ms: int = Field(default=32, ge=10, le=200)
    normalise_gain: bool = False
    gain_db: float = Field(default=0.0, ge=-24.0, le=24.0)
    high_pass: bool = True
    high_pass_hz: float = Field(default=80.0, ge=20.0, le=300.0)


class VadConfig(_Base):
    """Voice activity detection, its hysteresis, and its pause threshold (BE §5.2)."""

    enabled: bool = True
    #: **Silero, chosen from a measurement rather than left at the safe option.** Both detectors
    #: were run over the same clips at the same sensitivity: on real speech Silero found 83% of
    #: frames against energy's 62%, and on a tone fixture that contains no speech at all it fired
    #: on 1% of frames against energy's 62%. Better on both axes, which is unusual enough to be
    #: worth writing down. It costs nothing to ship: the model comes with `faster-whisper`.
    detector: VadDetector = "silero"
    #: Path to ``silero_vad.onnx``. Only consulted when ``detector`` is ``silero``.
    model_path: str | None = None
    sensitivity: float = Field(default=0.6, ge=0.0, le=1.0)
    enter_frames: int = Field(default=3, ge=1, le=20)
    leave_frames: int = Field(default=10, ge=1, le=100)
    pause_ms: int = Field(default=500, ge=100, le=5000)


class HallucinationConfig(_Base):
    """Thresholds for discarding text the speech model invented rather than heard.

    Every one of these can in principle delete something real, so the defaults are conservative and
    each test is switchable on its own. Raising a threshold keeps more invented text; lowering one
    risks deleting quiet or accented speech, which is the speech least well served by being lost.
    """

    enabled: bool = True
    #: Drop a pass the model itself scored as this likely to contain no speech. Whisper's
    #: ``no_speech_prob`` sits near 1.0 on exactly the passes that invent text.
    no_speech_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    #: Corroboration for the above: the mean token log-probability must also be at or below this.
    #: Strongly negative means the model was guessing. Ignored when the backend reports none.
    logprob_threshold: float = Field(default=-1.0, ge=-10.0, le=0.0)
    #: Above this, the model's no-speech estimate needs no corroboration at all. Measured against
    #: `tiny` on a non-speech fixture: it invented "Oh" at 0.901 with a log-probability of -0.99,
    #: which the corroborated rule missed by a hundredth. At this level the model is not hedging.
    no_speech_certain: float = Field(default=0.85, ge=0.0, le=1.0)
    #: Drop a pass whose words averaged less confidence than this. Zero switches it off, which is
    #: the default: it is a blunt instrument and it punishes quiet speakers.
    min_word_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    #: Drop a short pass whose *entire* output is a phrase the model emits over silence.
    drop_phrases_enabled: bool = True
    #: How short. The same two words over twenty seconds of audio came from a speaker.
    max_phrase_seconds: float = Field(default=3.0, ge=0.5, le=30.0)


class AsrConfig(_Base):
    """Speech model selection and term biasing (BE §6)."""

    backend: str = "mock"
    model: str = "small"
    device: ComputeDevice = "auto"
    precision: Precision = "int8"
    language: str | None = "en"
    beam_size: int = Field(default=1, ge=1, le=10)
    session_prompt: str = ""
    use_session_prompt: bool = True
    use_rolling_prompt: bool = True
    rolling_prompt_chars: int = Field(default=220, ge=0, le=2000)
    #: Whisper's built-in Silero filter, which strips non-speech from the buffer before decoding.
    #: The first line of defence: text that is never decoded cannot be invented.
    vad_filter: bool = True
    hallucination: HallucinationConfig = Field(default_factory=HallucinationConfig)


class StreamingConfig(_Base):
    """Buffer bounds, commit policy, and trim behaviour (BE §7)."""

    agreement_count: int = Field(default=2, ge=1, le=5)
    step_s: float = Field(default=0.75, ge=0.1, le=5.0)
    min_buffer_s: float = Field(default=1.0, ge=0.2, le=10.0)
    max_buffer_s: float = Field(default=25.0, ge=5.0, le=30.0)
    commit_timeout_s: float = Field(default=15.0, ge=2.0, le=120.0)
    retained_context_s: float = Field(default=0.75, ge=0.0, le=5.0)
    max_segment_s: float = Field(default=30.0, ge=5.0, le=120.0)
    repetition_limit: int = Field(default=3, ge=2, le=20)


class LlmLocalConfig(_Base):
    """Configuration for an OpenAI-compatible local server (BE §10.3)."""

    endpoint: str = "http://localhost:11434/v1"
    model: str = ""
    context_window: int = Field(default=8192, ge=512, le=1_000_000)


class LlmApiConfig(_Base):
    """Configuration for a hosted provider. The credential is never stored here (BE §10.6)."""

    provider: str = "anthropic"
    model: str = ""
    context_window: int = Field(default=200_000, ge=512, le=1_000_000)
    base_url: str | None = None


class LlmGenerationConfig(_Base):
    """Generation parameters shared by both modes."""

    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=1024, ge=16, le=32_000)


class LlmConfig(_Base):
    """Top-level local/API mode with both nested configurations retained (BE §10.4).

    Both blocks persist regardless of the active mode so the user can switch back without
    re-entering anything (FE §7.3).
    """

    mode: LlmMode = "local"
    local: LlmLocalConfig = Field(default_factory=LlmLocalConfig)
    api: LlmApiConfig = Field(default_factory=LlmApiConfig)
    generation: LlmGenerationConfig = Field(default_factory=LlmGenerationConfig)


class ContextConfig(_Base):
    """Rolling summarisation, glossary extraction, and the chat token budget (BE §9)."""

    summary_enabled: bool = True
    summary_interval_s: float = Field(default=300.0, ge=30.0, le=3600.0)
    glossary_enabled: bool = True
    token_budget: int = Field(default=8000, ge=512, le=1_000_000)
    retrieval_enabled: bool = True
    chunk_words: int = Field(default=300, ge=50, le=2000)
    recent_verbatim_s: float = Field(default=240.0, ge=30.0, le=3600.0)


class PolishConfig(_Base):
    """The minute-by-minute pass that tidies the transcript for reading.

    Distinct from :class:`ContextConfig`'s rolling summaries, which compress. This one must not:
    it rewrites punctuation, sentence flow, and paragraphing while keeping every claim the speaker
    made. See Decision D-018.
    """

    enabled: bool = True
    #: How much committed transcript accumulates before a cut is looked for.
    chunk_seconds: float = Field(default=60.0, ge=15.0, le=600.0)
    #: How long the speaker must stop for before the accumulated chunk is cut at that break.
    pause_seconds: float = Field(default=2.0, ge=0.5, le=15.0)
    #: The ceiling. A speaker who never pauses would otherwise never produce a chunk at all, and
    #: the page would stay raw for the whole talk.
    max_chunk_seconds: float = Field(default=150.0, ge=30.0, le=1800.0)
    #: How often a ``[MM:SS]`` marker is placed in the text handed to the model, which is how often
    #: one can appear in the result. Markers are what make a polished stretch traceable back to the
    #: moment it was said; too many turn readable prose into a table of contents.
    timestamp_interval_s: float = Field(default=15.0, ge=5.0, le=300.0)
    #: Ask the provider not to think before answering. There is no portable field for this, so
    #: several are sent; a server that rejects unknown fields is retried without them.
    disable_reasoning: bool = True
    #: The floor on ``returned words ÷ source words``. Below it the result is a summary rather than
    #: a tidy-up, and is discarded in favour of the raw text.
    min_retained_ratio: float = Field(default=0.6, ge=0.0, le=1.0)


class StorageConfig(_Base):
    """Where sessions are written, and what is retained (BE §17)."""

    session_dir: str = "./data/sessions"
    #: Reopen the last finished transcript when the application starts, so the talk that just
    #: happened is still on the page after a restart (D-041). D-031 keeps a finished session
    #: readable until the next one begins, but that lives in a process, and a restart ends it.
    #: Off means the page comes up empty and the talk is reached through Recordings, which is
    #: where it always was.
    reopen_last_session: bool = True
    retain_audio: bool = False
    autosave_interval_s: float = Field(default=5.0, ge=0.5, le=120.0)
    default_export_format: ExportFormat = "markdown"


class RecordingConfig(_Base):
    """Capturing audio to a file, and transcribing it once it is whole (D-021).

    Read by `recorded` mode and by `window` mode. Not by `live`, which writes no file.
    """

    #: Where recordings are written. Separate from ``audio.file_path``'s library, which holds
    #: recordings the *file source replays* — mixing the machine's own captures into the list a
    #: user picks fixtures from is a way to delete the wrong thing.
    recording_dir: str = "./data/recordings"
    #: Stop after this long. A toggle is easy to forget, and an unattended microphone will fill a
    #: disk overnight. Reaching it stops cleanly and says so rather than truncating in silence.
    max_minutes: float = Field(default=240.0, ge=1.0, le=1440.0)
    #: Seconds of audio handed to the model per pass in the batch transcription. Long enough that
    #: the model has real context, short enough that progress moves and a failure loses little.
    batch_window_s: float = Field(default=30.0, ge=5.0, le=300.0)
    #: Overlap between consecutive windows, so a word split across a boundary is seen whole once.
    batch_overlap_s: float = Field(default=1.0, ge=0.0, le=10.0)


class CaptureConfig(_Base):
    """Recording a window's video (D-022). Read only by `window` mode."""

    #: Whether the mouse pointer is drawn into the recording.
    cursor_mode: Literal["hidden", "embedded"] = "hidden"
    #: Frames per second. Fifteen is ample for a talk — slides change every thirty seconds — and
    #: doubling it doubles the work of a software encoder sharing a CPU with the speech model.
    frame_rate: int = Field(default=15, ge=1, le=60)
    #: Ceiling on the encoded height. Only ever scales down.
    max_height: int = Field(default=720, ge=240, le=2160)
    #: Draw a low-rate preview for the monitor pane. Costs one small JPEG a second.
    preview: bool = True
    #: Reuse the desktop's remembered consent so a second recording skips the picker. The token
    #: itself lives in the OS credential store, never in this file (D-017).
    reuse_consent: bool = True
    #: Combine the video and the session's audio into one file once both are closed.
    mux_audio: bool = True
    #: Which audio a `window` session records. **Defaults to the machine's output**, because the
    #: point of the mode is the window's sound and not the person watching it — a microphone
    #: recorded when it was not wanted is the fault this setting exists to fix. The portal carries
    #: video only (D-022), so this is a separate capture either way; `system` reads the default
    #: sink's monitor, which contains no microphone at all.
    #: `application` taps only the chosen window's own audio, additively — the application keeps
    #: playing to the user's speakers. It needs a match between the window and a PipeWire playback
    #: stream, which is a heuristic, so `system` remains the default and the fallback.
    audio_source: Literal["system", "microphone", "application"] = "system"
    #: Which video encoder to use. **`software` by default, deliberately.** Hardware AV1 was
    #: measured four times cheaper on CPU and was the default until a real capture produced
    #: `amdgpu: The CS has cancelled because the context is lost. This context is guilty of a hard
    #: recovery.` — a GPU context loss that kills the recording and can take the desktop session,
    #: and therefore the window being recorded, with it. A tool for recording talks that happen once
    #: does not trade that for CPU. `hardware` remains available for anyone whose driver is happier.
    encoder: Literal["software", "hardware"] = "software"
    #: How much the encoder is allowed to spend on the picture. **`balanced` by default, and that
    #: default is a repair.** `vp8enc` was run with no rate control named at all, leaving
    #: `target-bitrate` at its factory value of 256 kbps; recordings measured 345–357 kbps at
    #: 1080x1064 and looked it. Measured on fifteen seconds of a real capture, Y-PSNR against the
    #: source and the size an hour would take:
    #:
    #:     before      487 kbps   35.44 dB    208 MB/h
    #:     efficient  1405 kbps   42.84 dB    603 MB/h
    #:     balanced   2161 kbps   44.80 dB    927 MB/h
    #:     high       3031 kbps   45.89 dB   1194 MB/h
    #:
    #: These are constant-quality settings with a bitrate ceiling rather than targets, so a small
    #: window still produces a small file — the same setting measured 4909 kbps at 1080x1064 and
    #: 1038 kbps at 640x360.
    quality: Literal["efficient", "balanced", "high"] = "balanced"


class DictationConfig(_Base):
    """Press a key, speak, press it again, and the words arrive where you were typing (D-049).

    **The timings here come from measurement, not preference.** Transcribing fifteen seconds on a
    warm model is a fraction of a second; running the result through the local language model was
    measured at 8-10 seconds warm and 44 seconds cold. That gap is the whole design: the cleanup is
    bounded and falls back to the raw transcript, and the text is pasted exactly once either way.
    """

    enabled: bool = True

    #: ``off`` pastes what was said; ``llm`` tidies it first. Tidying costs the seconds above.
    cleanup: Literal["off", "llm"] = "llm"

    #: How long to wait for the tidy before giving up and pasting the raw transcript. Generous
    #: against the 8-10 s measured here, tight enough that a stalled model does not eat the words.
    cleanup_timeout_s: float = Field(default=20.0, ge=1.0, le=300.0)

    #: Whether to press the paste chord afterwards. Off still leaves the text on the clipboard,
    #: which is the right behaviour for anyone who wants to choose where it lands.
    paste: bool = True

    #: **A setting, not a detection.** In a terminal `Ctrl+V` quotes the next character and paste
    #: is `Ctrl+Shift+V`; nothing can ask the compositor what kind of window has focus.
    paste_chord: str = "ctrl+v"

    #: A stop nobody pressed. Dictation is push-to-talk, so a recording still running after this
    #: long is a key that was pressed once and forgotten. Reaching it *finishes* the dictation
    #: rather than silently dropping everything said afterwards: what was captured is delivered.
    #:
    #: Thirty minutes, up from five. Five was chosen when a dictation was a sentence; it is now
    #: used for thinking aloud at length, and the chunked pass below means a long one costs
    #: nothing at a boundary (D-061).
    max_seconds: float = Field(default=1800.0, ge=5.0, le=7200.0)

    #: The most audio handed to the speech model — and then to the tidy — at once. The recording
    #: is cut into pieces no longer than this, each ending where the speaker paused, so no piece
    #: ever begins or ends mid-word (D-061). Two minutes keeps a chunk's tidy well inside
    #: `cleanup_timeout_s` on the model measured here, and gives progress something to report.
    chunk_seconds: float = Field(default=120.0, ge=10.0, le=600.0)

    #: The shortest silence that counts as somewhere to cut. Three hundred milliseconds is a
    #: breath between clauses; the gap between two words in a phrase is shorter than that.
    min_pause_ms: int = Field(default=300, ge=100, le=3000)

    #: Kept out of `data/recordings/`, which is for recordings someone means to keep. This
    #: repository deleted 679 empty session databases one plan ago; fifty dictations a day would
    #: rebuild that pile from the other end within a fortnight.
    directory: str = "./data/dictations"

    #: Audio is discarded once the words are delivered. Speech nobody asked to keep should not
    #: accumulate on disk.
    keep_audio: bool = False

    #: How many past dictations to keep the text of, so one that pastes into the wrong window is
    #: recoverable. Oldest are pruned on each new dictation.
    keep_transcripts: int = Field(default=50, ge=0, le=1000)


class ShortcutsConfig(_Base):
    """Global keyboard shortcuts, registered by the companion process (Plan 5).

    **A web page cannot register these.** Under Wayland an application cannot grab keys at all, and
    reading `/dev/input` directly would mean a keylogger running as the user — not a reasonable
    thing for a transcription tool to install. They go through the desktop's own shortcut service,
    which is the sanctioned route and the one that can be revoked from System Settings.

    The distinction the brief asks for lives in the action names rather than in a branch: `toggle_*`
    starts or stops immediately, and `arm_window` opens the options sheet first, because window
    capture has three switches that must be answered before anything is captured.
    """

    enabled: bool = True
    # **Checked against a real Plasma desktop, which the previous defaults were not.** Three of
    # them collided with shortcuts KDE ships: `Meta+Alt+L` is the keyboard layout switcher,
    # `Meta+Alt+R` is Spectacle's screen recorder, and `Meta+Alt+S` toggles the screen reader. A
    # colliding default is not merely unavailable — it is registered against, refused, and reported
    # as a conflict on every start, which is a fault report for something nobody chose.
    #:
    #: The letters are mnemonic where a free key allowed it: V for voice, C for capture, W for
    #: window, D for dictate.
    toggle_live: str = "Meta+Alt+V"
    toggle_recorded: str = "Meta+Alt+C"
    arm_window: str = "Meta+Alt+W"
    stop: str = "Meta+Alt+X"
    open_app: str = "Meta+Alt+T"
    dictate: str = "Meta+Alt+D"


class QuickAction(_Base):
    """A parameterised prompt template surfaced as a one-tap button (BE §11.3)."""

    id: str
    label: str
    hint: str = ""
    prompt: str
    range_minutes: float | None = None
    since_last_read: bool = False


class AppConfig(_Base):
    """The complete resolved configuration handed to the pipeline."""

    audio: AudioConfig = Field(default_factory=AudioConfig)
    vad: VadConfig = Field(default_factory=VadConfig)
    asr: AsrConfig = Field(default_factory=AsrConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    polish: PolishConfig = Field(default_factory=PolishConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    shortcuts: ShortcutsConfig = Field(default_factory=ShortcutsConfig)
    dictation: DictationConfig = Field(default_factory=DictationConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    quick_actions: list[QuickAction] = Field(default_factory=list)
