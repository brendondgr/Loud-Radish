# Plan 4 — Window Recording and Transcription

*Created: 2026-08-15 · Status: **complete** (7 / 7 steps), with one measurement outstanding*

Part four of the five-plan expansion. Depends on [Plan 2](multi-mode-ui-implementation.md) and
[Plan 3](recorded-transcription.md).

## 1. Introduction

`window` mode captures a window the user picks, records the audio that goes with it, and applies any
combination of three options chosen before capture starts: live transcription while it runs,
post-process transcription when it ends, and video capture. All three off is refused; any other
combination is valid, including video with no transcription at all and transcription with no video.
A monitor pane shows the capture in progress, and the transcript and the assistant keep working
throughout — being able to ask a question about what was just said, while it is still being
recorded, is the point of the mode rather than a bonus.

**This plan is shaped almost entirely by one environment fact, established by inspection rather than
assumption: this machine runs Wayland under KDE Plasma.** On Wayland an application cannot enumerate
windows, cannot read another window's pixels, and cannot grab the screen. The only sanctioned route
is `org.freedesktop.portal.ScreenCast` over D-Bus — the compositor shows *its own* window picker, the
user consents, and the application receives a PipeWire node it may read. Everything below follows
from that: the picker is not ours to design, capture is a negotiated stream rather than a screen
grab, and the whole feature has a permission dialog in the middle of it that can be declined.

### What was measured on this machine

| Fact | Value | Consequence |
|---|---|---|
| Session type | `wayland`, KDE Plasma | X11 grabbing is unavailable; the portal is the only route |
| `org.freedesktop.portal.ScreenCast` | present, version 5 | `CreateSession`, `SelectSources`, `Start`, `OpenPipeWireRemote` |
| `AvailableSourceTypes` | `7` — monitor, window, and virtual | Window capture is genuinely offered, not just screen |
| `AvailableCursorModes` | `7` — hidden, embedded, metadata | The cursor can be drawn into the recording or omitted |
| `ffmpeg` | 8.1.2, **no `pipewiregrab` filter** | ffmpeg cannot consume the portal's stream on this build |
| GStreamer `pipewiresrc` | present | GStreamer is the capture path, driven as a subprocess |
| Video encoders | `openh264enc`, `vp8enc`, `vp9enc` present; **`x264enc` and all VAAPI encoders missing** | VP8-in-WebM is the default; H.264 via OpenH264 is the alternative; there is no hardware encoder |
| Muxers | `webmmux`, `matroskamux`, `mp4mux` present | WebM by default, Matroska for the raw-ish path |
| `jpegenc` | present | A low-frame-rate preview branch is achievable |

---

## 2. Gaps & Unanswered Questions

- **Who picks the window?** *Assumption*: the desktop's own portal dialog, because on Wayland
  nothing else is permitted to. The pre-flight sheet from Plan 2 collects the three options, and the
  portal picker opens after it is confirmed. This means the application cannot show a thumbnail grid
  of windows, cannot pre-select a window, and cannot name the window until the portal tells it —
  and the interface must not imply otherwise.

- **Does the user re-pick every time?** *Assumption*: no, when the portal allows persistence. The
  ScreenCast portal returns a `restore_token` under `persist_mode`; storing it lets a second
  recording of the same window start without a dialog. The token is a capability granted to this
  application and belongs in the OS credential store alongside API keys under the existing D-017
  rule, not in the config file. The user must be able to clear it from settings, because a stored
  consent nobody remembers granting is the wrong kind of convenience.

- **Where does the audio come from?** *Assumption*: the existing audio capture path, unchanged. The
  KDE ScreenCast portal carries **video only** — it does not offer application audio — so the mode
  reuses whatever source the Audio settings already select, which for capturing a remote talk means
  a loopback/monitor source. This is honest about a real limitation: audio is *the machine's*, not
  *that window's*. The interface must say so at the moment of arming, because a user who assumes
  per-window audio and records the wrong thing has lost the recording.

- **How are audio and video aligned?** *Assumption*: by a single session start timestamp taken
  before either is started, with the small skew accepted and documented. Sample-accurate
  synchronisation would require both streams through one GStreamer pipeline, which would mean moving
  audio capture off the existing tested path for a benefit nobody watching a seminar recording will
  notice.

- **If both live and post-process transcription run, which transcript wins?** *Assumption*:
  **neither — both are kept.** The post-process pass is stored as a second *revision* of the
  session's transcript rather than overwriting the first. The live transcript is what the user
  watched, what the assistant answered from, and what any chat citation points at; silently replacing
  it would invalidate a conversation that already happened. The pane offers a Live/Final switch and
  defaults to Final once it exists. This follows the additive principle D-018 already established
  for polished blocks.

- **Can the machine encode video and run speech inference at once?** *Human intervention is needed
  to answer this question.* There is no hardware encoder on this machine, so video encoding is
  software VP8 or OpenH264 on the same CPU that runs Whisper, whose real-time factor was previously
  measured at ≈1.5 for `small`/`int8`. Whether 1080p30 software encoding plus live transcription fits
  is a measurement, not a guess. The plan therefore makes resolution, frame rate, and encoder
  configurable, defaults conservatively (720p at 15 fps, VP8), and **step 7 measures it and records
  the result**. If it does not fit, the documented remedy is to turn live transcription off and use
  the post-process pass, which is precisely why both toggles exist.

  **Partly answered, and the rest is recorded as outstanding rather than guessed.** Encoding alone
  runs at ≈13× real time at 720p15 with VP8, so the encoder has ample headroom by itself. The
  contention figure is *not* answered: the measurement script's synthetic fixture cannot produce it,
  because Whisper emits its no-speech token after a handful of steps on anything that is not speech
  and therefore decodes a tone in a fraction of the time a talk would — the resulting 30–150× is
  the short-circuit, not the contention. The script now **refuses to conclude** from a run that
  produced no transcript and directs the user to `--audio path/to/a/talk.wav`. Answering it needs a
  real recording, which is verification debt in `docs/checklist.md`, not a gap in the design.

- **What happens when the captured window closes mid-recording?** *Assumption*: the PipeWire stream
  ends, the recorder is torn down cleanly, the video file is finalised at whatever length it reached,
  and any audio-side transcription continues to the user's stop. A closed window must not end the
  session, because the audio is very often still worth having.

- **Is there a preview, and what if there is not?** *Assumption*: a 1–2 fps JPEG preview from a
  branch of the same pipeline, served as a still image endpoint rather than pushed over the socket —
  the socket carries transcript, and a video preview must never be able to delay a committed segment.
  If `jpegenc` is missing or the branch fails, the monitor shows the static card Plan 1 specified.

- **Does this work on X11, GNOME, or a machine with no portal?** *Assumption*: the portal path is the
  only one implemented. GNOME and any other compositor implementing the same portal will work
  unchanged; X11-only setups and headless machines will report the mode unavailable with the reason.
  Writing a second X11 capture path for a machine that is not this one is speculative work.

---

## 3. Hierarchical Step-by-Step Instructions

### Step 1: Capability detection, the optional dependency group, and honest unavailability

- **Locations**: New package `web/backend/app/services/capture/` with `__init__.py` and
  `probe.py` — checks in order: a Wayland or X11 session, the `org.freedesktop.portal.ScreenCast`
  interface and its version, `AvailableSourceTypes` including `WINDOW`, the `gst-launch-1.0` binary,
  and each required GStreamer element by name; returns a structured verdict with the *specific*
  missing piece and its remedy. `pyproject.toml` gains a pure-Python D-Bus client (`jeepney` — no compilation, works inside the
  `uv` virtualenv, and supports the Unix file-descriptor passing `OpenPipeWireRemote` requires) as
  an ordinary dependency, not an extra (D-023). `web/backend/app/routes/health.py`
  reports the verdict. `docs/workflow.md` documents the system
  packages GStreamer needs. Tests: `tests/transcription/test_capture_probe.py` against a faked D-Bus
  and a faked element list.
- **Rationale**: this mode has five distinct ways to be unavailable and they need five distinct
  messages — "install the Python extra", "install GStreamer", "install a VP8 encoder", "your desktop
  has no screen-cast portal", and "your portal does not offer window capture" are different problems
  with different fixes. One "window capture unavailable" for all five is the failure mode that
  generates support requests. Plan 2 already renders a disabled mode with a reason; this fills it in.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/transcription/test_capture_probe.py` plus a manual `GET /api/health` on this machine
  confirming the verdict matches the table in section 1. Once validated, commit stating: `Window
  Recording (1 / 7) Complete: The application detects exactly which part of the window-capture stack
  is missing and names the remedy for each.`

### Step 2: The portal ScreenCast session

- **Locations**: `web/backend/app/services/capture/portal.py` — `CreateSession`, `SelectSources`
  (`types=WINDOW`, cursor mode from configuration, `persist_mode=2`), `Start`, then
  `OpenPipeWireRemote` for the file descriptor; handles the portal's asynchronous `Request`/`Response`
  object pattern, the user declining, and the dialog timing out. Returns the PipeWire node id, the fd,
  the stream's reported size, and any `restore_token`.
  `web/backend/app/config/credentials.py` stores and clears the restore token.
  `web/backend/app/config/schema.py` gains `CaptureConfig` (`cursor_mode`, `encoder`, `container`,
  `frame_rate`, `max_height`, `reuse_consent`). Tests: `tests/transcription/test_portal_session.py`
  against a scripted D-Bus double.
- **Rationale**: this is the step with no alternative implementation — every other part of the
  feature has a fallback and this one does not, so it gets its own step, its own test double, and its
  own failure taxonomy. The portal's request/response pattern (a method call returns an object path
  and the real answer arrives as a signal on it) is the specific thing that is easy to get subtly
  wrong and hard to debug afterwards.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/transcription/test_portal_session.py`, plus a **manual run on this machine** confirming the
  KDE picker appears, a window can be chosen, a node id comes back, and declining the dialog produces
  a clean refusal rather than a hang. Once validated, commit stating: `Window Recording (2 / 7)
  Complete: A window is chosen through the desktop's own portal, with consent optionally remembered
  and revocable.`

### Step 3: The GStreamer recorder and the preview branch

- **Locations**: `web/backend/app/services/capture/recorder.py` — builds and supervises a
  `gst-launch-1.0` subprocess: `pipewiresrc` on the portal's fd and node, `videorate` and
  `videoscale` to the configured ceiling, a `tee` into (a) `videoconvert` → the configured encoder →
  the configured muxer → `filesink` writing `data/recordings/<session_id>.webm`, and (b) `videorate`
  at 1–2 fps → `jpegenc` → `multifilesink` writing a single preview file. Owns process lifetime,
  graceful `EOS` on stop so the container is finalised, a hard kill after a timeout, stderr capture
  into `logs/`, and detection of the stream ending because the window closed.
  `web/backend/app/services/capture/pipeline.py` builds the launch string from `CaptureConfig` and
  the probe's verdict, so the encoder choice is data rather than branching.
  Tests: `tests/transcription/test_capture_pipeline.py` (the launch string for each encoder and
  container combination, and that an unavailable encoder is never selected) and
  `tests/transcription/test_recorder_lifecycle.py` (against a stub subprocess).
- **Rationale**: driving GStreamer as a subprocess rather than through PyGObject keeps the `uv`
  virtualenv free of a dependency that needs system GObject introspection to build — the same
  reasoning that already keeps this project's frontend free of a toolchain. A finalised container
  matters more than it sounds: killing the process instead of sending EOS produces a WebM with no
  duration index that many players refuse to seek.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/transcription/test_capture_pipeline.py tests/transcription/test_recorder_lifecycle.py`, plus
  a **manual capture on this machine** producing a playable file and a preview image that updates.
  Once validated, commit stating: `Window Recording (3 / 7) Complete: A chosen window records to a
  finalised video file with a low-rate preview, encoder and container chosen from what is installed.`

### Step 4: Wire `window` mode into the session manager

- **Locations**: `web/backend/app/services/session/manager.py` — a `window` branch taking the three
  pre-flight options: it always starts audio capture and Plan 3's `WavSink`; starts the streaming
  engine only when live transcription was requested; starts the recorder only when video was
  requested; and records the session start timestamp both use. Teardown reverses it, and a recorder
  that dies does not stop the audio session.
  `web/backend/app/schemas/api.py` — `StartSessionRequest.options` carrying the three booleans.
  `web/backend/app/routes/session.py` — drops the `mode-unavailable` rejection for `window`, refuses
  the all-off combination server-side as well as in the UI, and surfaces a declined portal dialog as
  a distinct, non-alarming error code.
  `web/backend/app/transport/events.py` — `CAPTURE_STATE` (critical) and `CAPTURE_STATS`
  (coalescing). Tests: `tests/transcription/test_window_session.py` with the recorder stubbed.
- **Rationale**: the manager is already the one place that answers "what runs during a session", and
  this mode's whole substance is that the answer is now three independent switches. Refusing the
  invalid combination on the server as well as the client is not redundancy — the client check is a
  courtesy and the server check is the rule.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/transcription/test_window_session.py`, exercising all seven valid option combinations and the
  refused eighth, plus a recorder failure mid-session that leaves audio running. Once validated,
  commit stating: `Window Recording (4 / 7) Complete: Window sessions run any valid combination of
  live transcription, post-process transcription, and video, and survive the recorder dying.`

### Step 5: Transcript revisions, so both passes can be kept

- **Locations**: `web/backend/app/services/transcript/schema.sql` — a `revision` column on
  `segments`, defaulting to `0`; the FTS5 triggers reviewed so the index still tracks inserts
  correctly. `web/backend/app/services/transcript/store.py` — a guarded `ALTER TABLE ... ADD COLUMN`
  migration for session databases written before this column existed, and a `revision` argument on
  the four queries and on search, defaulting to the newest revision present.
  `web/backend/app/services/recording/batch.py` (Plan 3) — writes at `revision=1`.
  `web/backend/app/routes/transcript.py` — a `revision` query parameter and a
  `GET /api/transcript/revisions` listing what exists.
  `web/backend/app/services/transcript/archive.py` — reads a session file that predates the column.
  Tests: `tests/data/test_transcript_revisions.py`, including opening a fixture database created
  without the column.
- **Rationale**: two transcription passes over one session produce two answers, and both are worth
  something — the live one is what the assistant's citations point into, and the final one is more
  accurate. Overwriting is the only option that destroys information, so it is the one option not
  taken. The migration must be guarded because `archive.py` explicitly opens databases written by
  older versions, which is a constraint `docs/checklist.md` already records.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/data`, including search over both revisions and an export of each. Once validated, commit
  stating: `Window Recording (5 / 7) Complete: A session can hold a live transcript and a
  post-processed one at the same time, and old session files still open.`

### Step 6: The monitor pane and the Live/Final switch

- **Locations**: `web/frontend/templates/partials/monitor/pane.html` (filled in from Plan 2's shell);
  new `web/frontend/static/js/components/recording-monitor.js` (preview refresh on a timer that
  pauses when the pane is hidden or the tab is backgrounded, elapsed clock, output path and size,
  which options are active, the closed-window notice);
  new `web/backend/app/routes/capture.py` serving `GET /api/capture/preview.jpg` with no-cache
  headers, plus `GET /api/capture/state`;
  new `web/frontend/static/js/stores/capture.js`;
  `web/frontend/templates/partials/transcript/toolbar.html` and
  `web/frontend/static/js/components/transcript-pane.js` (the Live/Final revision switch, shown only
  when more than one revision exists); `web/frontend/static/css/components/monitor.css`;
  `web/frontend/static/js/main.js` wiring.
- **Rationale**: pulling the preview as an image on a timer rather than pushing frames over the
  WebSocket is the decision that protects the transcript — the socket has a documented backpressure
  policy in which transcript events are critical and must never be dropped, and video frames sharing
  that channel would be the one thing capable of delaying them. Pausing the timer when the pane is
  not visible keeps a backgrounded tab from encoding JPEGs nobody is looking at.
- **Action**: Undergo the verification/tests/validation process for this phase — manual browser QA
  during a real capture: the preview updates, the assistant answers a question about the live
  transcript *while recording*, the Live/Final switch appears only after the second pass, plus a
  320 px and a keyboard-only pass over the third tab. Once validated, commit stating: `Window
  Recording (6 / 7) Complete: A monitor pane shows the capture live without competing with the
  transcript stream, and finished sessions can be read at either revision.`

### Step 7: Muxing, the CPU-cost measurement, failure paths, and documentation

- **Locations**: `web/backend/app/services/capture/mux.py` — optionally combines the WebM and the
  session's WAV into one file with `ffmpeg`, after both are closed, keeping the originals until it
  succeeds; skipped when video was not captured. `web/backend/app/services/session/degradation.py` —
  entries for a declined portal, a dead recorder, a closed window, and a full disk.
  `scripts/measure_capture_cost.py` — runs a fixed-length capture with and without live transcription
  and reports real-time factor and dropped frames, so the open question in section 2 is answered with
  a number. Documentation: `docs/documentation.md` (Decision **D-022**), `docs/architecture.md`,
  `docs/data-flow.md`, `docs/api-contract.md`, `docs/routes.md`, `docs/structure.md`,
  `docs/component-map.md`, `docs/design-system.md`, `docs/deployment.md`, `docs/workflow.md`,
  `docs/checklist.md`, `.env.example`; `web/shared/contracts/` regenerated.
- **Rationale**: the measurement is the point of this step. Everything else in the plan is designed
  around not knowing whether software encoding and live inference fit on one CPU, and shipping
  without measuring would leave the central performance question of the mode unanswered in the one
  place it can actually be answered. D-022 records both the Wayland constraint and whatever the
  number turns out to be, because the next person to wonder why this is a portal session and not a
  screen grab deserves the reason.
- **Action**: Undergo the verification/tests/validation process for this phase — the full suite,
  `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, plus `uv run python
  scripts/measure_capture_cost.py` with its output pasted into `docs/deployment.md`. Report the
  measurement honestly, including if it shows the combination does not fit. Once validated, commit
  stating: `Window Recording (7 / 7) Complete: Audio and video are muxed into one file, every failure
  path is named, and the CPU cost of capturing while transcribing is measured rather than assumed.`

---

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Capability probe | Five distinct unavailability verdicts, each with its remedy | `web/backend/app/services/capture/probe.py` |
| Portal session | `CreateSession`/`SelectSources`/`Start`/`OpenPipeWireRemote`, consent persistence | `web/backend/app/services/capture/portal.py` |
| Pipeline builder | Launch string from configuration and installed elements | `web/backend/app/services/capture/pipeline.py` |
| Recorder supervisor | Subprocess lifetime, EOS finalisation, stream-ended detection | `web/backend/app/services/capture/recorder.py` |
| Muxer | Combines video and session audio once both are closed | `web/backend/app/services/capture/mux.py` |
| Window mode wiring | Three independent option switches in the session manager | `web/backend/app/services/session/manager.py` |
| Transcript revisions | `revision` column, guarded migration, revision-aware queries | `web/backend/app/services/transcript/{schema.sql,store.py}` |
| Capture routes | `GET /api/capture/preview.jpg`, `GET /api/capture/state` | `web/backend/app/routes/capture.py` |
| Monitor component | Preview, stats, active options, closed-window notice | `web/frontend/static/js/components/recording-monitor.js` |
| Revision switch | Live/Final toggle in the transcript toolbar | `web/frontend/static/js/components/transcript-pane.js` |
| Optional group | `capture-window` with a pure-Python D-Bus client | `pyproject.toml`, `docs/workflow.md` |
| Probe tests | Each missing-piece verdict, against faked D-Bus and element lists | `tests/transcription/test_capture_probe.py` |
| Portal tests | Request/response pattern, decline, timeout, restore token | `tests/transcription/test_portal_session.py` |
| Pipeline tests | Launch string per encoder/container; never selects a missing element | `tests/transcription/test_capture_pipeline.py` |
| Recorder tests | Start, EOS finalisation, hard kill, window-closed teardown | `tests/transcription/test_recorder_lifecycle.py` |
| Session tests | All seven valid option combinations, the refused eighth, recorder death | `tests/transcription/test_window_session.py` |
| Revision tests | Two revisions in one session, search and export of each, old-file migration | `tests/data/test_transcript_revisions.py` |
| Cost measurement script | Real-time factor and dropped frames, with and without live transcription | `scripts/measure_capture_cost.py` |
| Decision D-022 | Why the portal, why GStreamer, why two revisions, and the measured cost | `docs/documentation.md` |
