# Mixed Audio and Steady Video

**Status:** In progress (3 / 7 phases). Branch: `feature/mixed-audio-steady-video`.

## 1. Introduction

A window recording can capture either the window's sound or the user's microphone, never both. A
session opens exactly one audio source (`_open_source` in `web/backend/app/services/session/sources.py`),
so a commentary over a talk, or a call where both sides matter, cannot be recorded. The user also
has no control over levels once recording starts. This plan adds a **mixer**: a session may open
two audio inputs (the window's sound and a microphone), each with its own **volume from 0 to 100 %**
and a **mute**, adjustable live while recording. One mix feeds the transcript and the recording,
and each input is also kept as its own track.

A second fault is fixed first, because it loses recordings today. **Resizing or moving a window
during a recording stalls or ends the video.** The logs give the cause. Every window recording on
this machine logged `Recording PipeWire node N, source 0x0`, because the portal reports no size. With
no size, `_record_scaler` in `web/backend/app/services/capture/pipeline.py` adds **no scaler at
all**. That defeats the fixed-size stage that research §6.3 (`docs/plans/capture-research-findings.md`)
prescribed: it puts a fixed-size step before the encoder, so a resize is absorbed instead of
reaching `vp8enc`/`webmmux`, which cannot change resolution mid-stream. The fix is the one the user
proposed. Every recording gets **one fixed canvas size**, chosen before it starts, and the window is
scaled to fit inside it, whatever the window does. A fixed canvas also means every segment of a
restarted capture has the same dimensions, which is what `stitch.py`'s stream-copy join requires.

### What this sets up for the next plan (switching windows)

The user wants to follow this with a plan for switching between windows and screens during one
recording. Two pieces built here are its foundations, and are shaped for it on purpose:

- **The fixed canvas.** Once every segment is the same size, "switch to another window" becomes
  "end this segment, ask the portal for a new window, start the next segment on the same canvas".
  That is the resume path D-036 already runs, with a different window. The existing stitcher then
  joins the pieces with no re-encode. A live compositor (two sources on one canvas at once) is the
  more ambitious version and would also start from the canvas.
- **Audio inputs as a list.** The mixer takes *N* named inputs, not "window + mic" as a special
  case. A later plan can add, remove or re-target an input mid-recording without changing its shape.

Neither switching nor adding inputs mid-recording is built here. Each input can be **set** while
recording; the set of inputs is fixed when recording starts.

## 2. Gaps & Unanswered Questions

- **Fit with bars, or crop to fill?** The user wrote "the video maybe just fills the screen".
  *Assumption:* **fit with bars.** The whole window is scaled to fit the canvas, keeping its aspect
  ratio, with black bars where the shapes differ (`videoscale add-borders=true` with a fixed
  `pixel-aspect-ratio=1/1`, the way the preview branch already works). Cropping to fill would cut
  the edges off slides and code. Under constant-quality encoding the bars cost almost nothing.
  "Crop to fill" can be added later as a setting.
- **Canvas size.** *Assumption:* a new `capture.canvas` setting, `720p` / `1080p` / `1440p`,
  default **1080p** (1920×1080), at 16:9 because that is the shape of the screens these recordings
  are watched on. Windows are scaled **up as well as down**, so the framing stays the same while a
  window shrinks. The content never jumps around inside the frame, which is the point of the
  change. `capture.max_height` has never taken effect on this desktop (the size is always unknown).
  It is kept in the schema so old config files still load, and documented as superseded.
- **Does the canvas alone stop the stall?** It removes the cause visible in the logs: a size change
  reaching the encoder. `pipewiresrc` can *also* fail its own renegotiation on AMD + KWin + DMA-BUF
  (research §6.3). That ends the process, which the existing resume path (D-036) already handles.
  Phase 1 adds naming that failure from the capture log. If a resize still stops `pipewiresrc`
  itself on the real machine, the next step is `pipewiresrc always-copy=true` (avoids DMA-BUF
  re-import), measured before it is adopted. **Human intervention is needed to answer this
  question**: only a real portal session on the user's desktop can show it, and the Claude sandbox
  has no GStreamer.
- **Echo when using speakers.** If the window plays through speakers, the microphone hears it a few
  milliseconds late, and the mix carries it twice. *Assumption:* no echo cancellation in this plan.
  The record sheet says "Use headphones to avoid echo" when both inputs are chosen. PipeWire's
  `module-echo-cancel` is logged as follow-up work in `docs/checklist.md`.
- **What does mute do?** *Assumption:* mute and volume apply to **everything that is kept**: the
  mix, the transcript and that input's own track. A user who mutes their microphone expects
  nothing from it to be recorded. Keeping a pre-mute track for re-mixing later would quietly break
  that, so it is not done.
- **Volume above 100 %.** The user asked for 0–100 %. *Assumption:* volume is attenuation only, and
  100 % means the input as captured. A boost for a quiet microphone is follow-up work.
- **Separate tracks.** *Assumption:* yes, on by default (`recording.keep_input_tracks`). Beside the
  mixed `audio.wav`, each input gets its own WAV (`audio.window.wav`, `audio.microphone.wav`) at
  the session's format, 16 kHz mono, about 115 MB/hour each. They allow a re-transcription of one
  side later and cost only disk space.
- **Computer sound + microphone.** It uses the same mechanism as window + microphone, so the record
  sheet offers it too. *Assumption:* in scope, for the same reason.
- **Clock drift between inputs.** The microphone (PortAudio, `DeviceSource`) and the tap
  (`pw-record`, `MonitorSource`) run on independent clocks. *Decision:* the mixer has **no clock of
  its own**. One input is the **primary**, and each frame it delivers produces one mixed frame,
  taking the same number of samples from every other input's queue. The primary is chosen as the
  file source if there is one, then the microphone (a hardware clock that never pauses), then the
  tap. When another input falls behind, its gap is filled with silence. When it runs ahead, its
  oldest samples are dropped once its queue passes 200 ms. This bounds drift at 200 ms and never
  lets a secondary input stall the mix. Following the primary rather than a wall-clock tick keeps
  the file source's faster-than-real-time playback (`audio.file_speed`) working. If the primary
  delivers nothing for 500 ms or fails, the next input is promoted.
- **No ffmpeg on the dev machine** (memory and `docs/checklist.md`). The mux and the stitch call
  ffmpeg, so on this machine a capture restarted after a resize leaves separate pieces. The fixed
  canvas makes restarts rare. Porting `mux.py`/`stitch.py` to PyAV is **out of scope** and is
  logged as discovered work.
- **Hardware verification.** The resize test, the dual-input recording and the live sliders all
  need the user's desktop: a portal dialog, real PipeWire and real GStreamer. *Human intervention
  is needed to answer this question* at the end of Phases 1 and 7. Everything else is verified in
  the sandbox with synthetic sources.

## 3. Hierarchical Step-by-Step Instructions

### Phase 1 — Steady video: one canvas for the whole recording

> **Status: ✅ Complete.** What happened, against what was planned:
> - **Reproduced first.** `utils/resize_capture_check.py` runs the app's own pipeline with its
>   source swapped for a test pattern resized on a timer. Without the canvas
>   (`--without-canvas`): one WebM holding **six frame sizes** under a header naming the first.
>   With it: 30 s, 12 resizes, 450 frames all 1920×1080, letterboxed with the shape kept
>   (frames checked by eye).
> - **A fake PipeWire window was tried and dropped.** `pipewiresink` does not renegotiate its
>   consumer (the recorder kept reading 800×600 while the frames changed size), so it garbled
>   every recording with or without the fix, and it fails outright near 500×500. The
>   GStreamer-level rig reproduces the part that broke; `pipewiresrc` against KWin itself stays a
>   real-desktop check (Phase 7).
> - **There is no Video settings tab.** No capture option had a control before this. The canvas
>   select went into a new "Window video" group in Settings → Storage, beside the other
>   recording settings.
> - **Tests that need GStreamer and ffmpeg now run here.** The host's tools are reachable from
>   this toolbox container through `flatpak-spawn --host` (see `docs/workflow.md`). All
>   26 real-file tests in `test_capture_output.py` pass, including a new end-to-end resize test.

- **Locations:**
  - `web/backend/app/services/capture/geometry.py`: new `CANVASES` table, and `canvas(name)`
    returning a fixed even `Geometry`.
  - `web/backend/app/services/capture/pipeline.py`:
    - `build()` takes `canvas_width`/`canvas_height` instead of `max_height`/`source_*`.
    - `_record_scaler` is replaced by `_canvas_stage`, which is **always** emitted: `videoscale
      add-borders=true`, then the fixed `video/x-raw,width=W,height=H,pixel-aspect-ratio=1/1`
      caps.
  - `web/backend/app/config/schema.py` (`CaptureConfig.canvas`; `max_height` documented as
    superseded).
  - `web/backend/app/services/session/window_capture.py`: `_start_window_capture` and
    `_resume_window_capture` pass the canvas.
  - `web/backend/app/services/capture/recorder.py`: new `_explain_exit()`, which names a
    renegotiation failure (`not-negotiated`, `unhandled format`, `no more input formats`) from the
    capture log in `RecorderState.error`.
  - `web/backend/app/services/session/degradation.py`: wording for that failure.
  - Frontend: the Video settings tab gets a canvas size select (`web/frontend/templates/partials/settings/`
    video partial and its `components/settings/` module).
- **Rationale:** this is the cause visible in the logs. Fixing it first protects every later
  window test in this plan from the stall, and the stitcher relies on equal-sized segments.
- **Tests:**
  - `tests/transcription/test_capture_pipeline.py`: the canvas stage is present with unknown
    *and* known source sizes, and its caps are fixed and even.
  - New `tests/utils/test_canvas_geometry.py`: canvas sizes; the even-dimension rule.
  - `tests/transcription/test_capture_stall.py`: a log containing `not-negotiated` produces the
    named error.
  - New utility `utils/check_video_frames.py` (PyAV): reads a recording and reports its duration
    and **every distinct frame size**. The resize test passes when it reports exactly one size.
- **Docs:**
  - D-080 in `docs/documentation.md` (fixed canvas; supersedes the "never scale when the size is
    unknown" rule in `_record_scaler`).
  - `docs/api-contract.md` (the config field).
  - The Video tab in `docs/design-system.md`.
  - `docs/workflow.md` (the check utility).
- **Validation:**
  - `uv run pytest tests/transcription/test_capture_pipeline.py tests/transcription/test_capture_stall.py tests/utils/test_canvas_geometry.py`.
  - `uv run ruff check` and `uv run ruff format --check` on the changed files.
  - Browser QA of the settings select on the isolated server (:8411).
  - **On the user's desktop:** record a window for 60 s while resizing and moving it repeatedly;
    `uv run python utils/check_video_frames.py <video>` reports about 60 s and one frame size.
- > *Action: Undergo the verification/tests/validation process for this phase. Once validated,
  > commit stating: Mixed Audio and Steady Video (1 / 7) Complete: every window recording is written
  > on one fixed canvas, so resizing or moving the window no longer stalls the video.*

### Phase 2 — The mixer, as a pure audio source

> **Status: ✅ Complete.** Built as planned. `MixedSource` also takes an `on_track` callback that
> receives each input's post-gain frame, which is how Phase 3 writes each input's own track, and
> `levels()` reports each input's level before gain, so a meter still moves while its input is
> turned down. 26 tests, including a simulated hour of ±3 % drift and two real generator threads.

- **Locations:**
  - New `web/backend/app/services/audio/mixer.py`, the pure math:
    - `InputLevel(gain, muted)`.
    - `mix(frames, levels, previous)` sums the inputs, ramps each gain change across one frame so
      a slider never clicks, and clips the sum to [−1, 1].
    - `percent_to_gain` (0–100 → 0.0–1.0).
  - New `web/backend/app/services/audio/sources/mixed.py`: `MixedSource(AudioSource)`.
    - It owns named child sources and a bounded queue per secondary input (cap 200 ms).
    - Each frame from the primary input emits one mixed frame, filling any secondary gap with
      silence (see the drift decision in §2).
    - A watchdog promotes the next input if the primary goes quiet for 500 ms.
    - With one input it is a pass-through that applies only that input's gain.
    - `set_level(name, percent, muted)` is thread-safe and takes effect on the next frame.
    - It reports per-input RMS through `levels()`.
    - A child that fails is dropped from the mix and reported through a new `on_input_lost`
      callback. The source only fails when **every** input has failed.
  - Export from `web/backend/app/services/audio/sources/__init__.py`.
- **Rationale:** the mixer is the risky part (timing, drift, one input dying). Built and tested as a
  standalone `AudioSource` against synthetic inputs, it plugs into the session without the session
  having to learn anything new about sources. The source surface stays the same, so the ASR, VAD,
  WAV sink and pause logic (`hold.py` drops frames at `_on_frame`) work unchanged.
- **Tests:** new `tests/transcription/test_audio_mixer.py`, using the existing `SyntheticSource`:
  - two tones sum;
  - 50 % halves an input and mute removes it;
  - a gain change ramps rather than steps;
  - the sum clips and never exceeds 1;
  - a secondary input producing 3 % too fast, or too slow, stays within the queue cap over a
    simulated hour (driven by feeding frames directly, not wall-clock time);
  - a one-input mixer is a pass-through, so its output equals its input at 100 %;
  - the primary going quiet promotes the secondary;
  - one input failing leaves the other running, with `on_input_lost` called;
  - both failing ends the source.
- **Docs:**
  - D-081 in `docs/documentation.md` (one mix, N named inputs, drift policy, mute applies to
    everything kept).
  - `docs/data-flow.md` (the capture stage now has an optional mixer).
  - `docs/structure.md` (the new modules).
- **Validation:**
  - `uv run pytest tests/transcription/test_audio_mixer.py tests/transcription/test_audio_sources.py`.
  - `uv run ruff check` and `uv run ruff format --check`.
- > *Action: Undergo the verification/tests/validation process for this phase. Once validated,
  > commit stating: Mixed Audio and Steady Video (2 / 7) Complete: a mixer source combines named
  > audio inputs with per-input volume and mute, tolerating drift and the loss of one input.*

### Phase 3 — A session with two audio inputs

> **Status: ✅ Complete.** Differences from the plan:
> - **Each input's own track follows `storage.retain_audio`** instead of a new
>   `recording.keep_input_tracks` setting. A user who chose not to keep audio must not find their
>   microphone kept on its own, and two settings for "keep my audio" would disagree.
> - **Two faults found by the tests and fixed in `MixedSource`.** When a file reached its end,
>   the silent second input took over the clock and kept writing frames. And the silent stand-in
>   was unpaced, so it starved the file of the interpreter. Now a file's end ends the whole mix,
>   and the stand-in runs at real time.
> - **`mix.changed` is registered as a never-dropped event now** (the plan had it in Phase 4),
>   because losing an input already emits it. The contracts are regenerated.
> - **Checked on the QA server (:8412):** a computer-sound + microphone run through
>   `POST /api/session/start` reported its `mix`, then wrote `audio.wav`, `audio.system.wav` and
>   `audio.microphone.wav`, all 5.02 s. With retention off it wrote neither track.

- **Locations:**
  - `web/backend/app/services/session/shapes.py`:
    - `CaptureOptions.audio_inputs: tuple[str, ...]`, holding the input kinds `window`,
      `system` and `microphone`. `window` means today's `application` tap and `system` means the
      whole-output tap.
    - `CaptureOptions.levels: dict[str, int]`, the starting percentages.
    - The old single `audio_source` still accepted and mapped to one input.
  - `web/backend/app/services/session/modes.py` and its mirror `web/frontend/static/js/core/modes.js`:
    the `AUDIO_INPUTS` vocabulary (D-020; `tests/utils/test_mode_vocabulary.py` guards the mirror).
  - `web/backend/app/services/session/plan.py`: `resolve()` refuses duplicate inputs, more than
    two, and a window input on a non-window run (422 `options-conflict`).
  - `web/backend/app/services/session/sources.py`:
    - `_open_source` becomes `_open_inputs`, which builds one child per input via
      `_open_input(config, kind)`. That is the existing branches, unchanged, and also the
      **test seam**.
    - Every run's inputs are wrapped in `MixedSource`, so volume and mute work for every run. One
      input is a pass-through at 100 %, so today's single-input recordings are unchanged sample
      for sample (asserted by a test).
    - The file source still wins outright (it stands in for the *window* input when two are asked
      for, and the microphone is then a `SyntheticSource` of silence, so QA on :8411 exercises the
      mixer path).
  - `web/backend/app/services/recording/`: a `WavSink` per input for the separate tracks, written
    from the mixer's post-gain frames, and `layout.input_track(name)`.
  - `web/backend/app/services/session/manager.py`:
    - `state()` and `session.started` carry `mix`.
    - `on_input_lost` emits a new degradation, `degradation.audio_input_lost(name)`.
    - Keep `manager.py` under 800 lines. If it would cross, the new wiring goes in a new
      `session/mixing.py` mixin, as `hold.py` did.
  - `web/backend/app/schemas/api.py` (`CaptureOptions.audio_inputs`, `levels`; `SessionResponse.mix`)
    and `web/backend/app/routes/session.py`.
- **Rationale:** connects the mixer to real sessions without any interface yet, so the backend
  contract can be tested on its own. Keeping the one-input path identical means every existing
  recording mode is untouched by construction.
- **Tests:**
  - `tests/api/test_session_modes.py`:
    - a two-input run starts, reports `mix`, writes `audio.wav` plus two input tracks, and its
      transcript comes from the mix;
    - a legacy `audio_source` request still works;
    - a single-input run's `audio.wav` is identical to what it was before the mixer, sample for
      sample;
    - the conflicts return 422.
  - `tests/utils/test_run_plan.py`: the new `resolve` rules.
  - `tests/utils/test_mode_vocabulary.py`: the mirrored list.
- **Docs:** `docs/api-contract.md` (start request and session response), `docs/data-flow.md`,
  `docs/component-map.md`.
- **Validation:**
  - `uv run pytest tests/api/test_session_modes.py tests/utils/test_run_plan.py tests/utils/test_mode_vocabulary.py`.
  - The full `uv run pytest`, where only the 4 known environmental failures may remain.
  - `uv run ruff check`.
- > *Action: Undergo the verification/tests/validation process for this phase. Once validated,
  > commit stating: Mixed Audio and Steady Video (3 / 7) Complete: a session can record a window's
  > sound and a microphone together, keeping the mix and each input's own track.*

### Phase 4 — Live sound controls in the API

- **Locations:**
  - `web/backend/app/routes/session.py`:
    - `GET /api/session/mix`.
    - `PATCH /api/session/mix`, with body `{inputs: {<name>: {volume: 0–100, muted: bool}}}`.
    - 409 `no-session` when nothing is recording, and 422 for an unknown input or an
      out-of-range volume.
  - `web/backend/app/schemas/api.py` (`MixLevelsRequest`, `MixResponse`).
  - The session manager (or the `session/mixing.py` mixin) gets `set_levels()`, which forwards to
    `MixedSource.set_level`. It works for single-input runs too, since every run has a mixer.
  - Transport events: `mix.changed` on every change, so two open tabs agree, and `mix.levels`,
    the per-input meters at `LEVEL_INTERVAL_S`.
  - `web/frontend/static/js/transport/api.js` (`setMix`).
  - A new `web/frontend/static/js/stores/mix.js`.
- **Rationale:** the controls are a server-side state change and must take effect in the capture
  thread, so the contract (including what happens with two tabs open) is fixed before any interface
  is drawn.
- **Tests:** new `tests/api/test_session_mix.py`:
  - a PATCH changes the mix's gain;
  - mute zeroes that input in the next written frames of `audio.wav` and of its own track;
  - the 409 and 422 cases;
  - `mix.changed` is emitted.
- **Docs:** `docs/api-contract.md` (endpoints and events), `docs/routes.md`.
- **Validation:**
  - `uv run pytest tests/api/test_session_mix.py tests/api/test_session_modes.py`.
  - `uv run ruff check`.
- > *Action: Undergo the verification/tests/validation process for this phase. Once validated,
  > commit stating: Mixed Audio and Steady Video (4 / 7) Complete: each audio input's volume and mute
  > can be changed while recording, and every open page is told.*

### Phase 5 — The record sheet: choosing both

- **Locations:**
  - `web/frontend/templates/partials/record_sheet.html`: the "Which sound" radio rows become
    **icon cards that can be chosen together** (checkbox cards, styled like the existing
    `.choice-card`).
    - For a window: "The window's sound" and "My microphone".
    - For computer sound: "Computer sound" (fixed) plus an optional "My microphone".
    - "The window's sound" keeps a small "only this window / everything playing" choice beneath
      it, which is today's `application`/`system` distinction.
  - `web/frontend/static/js/components/record-sheet.js`:
    - `_choice`/`_toOptions` produce `audio_inputs` and `levels`.
    - The "Which microphone" row shows whenever the microphone is among the inputs.
    - The refusal "Choose at least one sound" covers a window with video off and no sound.
    - The headphones hint shows when both are on.
    - `lastRecordOptions` remembers the inputs and the last levels.
  - `web/frontend/static/css/components/record-sheet.css`: a checkbox variant of `.choice-card`,
    using a check mark in the corner as well as the outline, so "several may be chosen" reads
    differently from the radio cards above it.
  - `web/frontend/static/js/components/header.js` (`describeRun`: "Window · window + mic · live").
- **Rationale:** the sheet is where every recording is decided (D-075), so choosing two inputs
  belongs there. It must be visible at the moment of confirming, the rule D-020 set after options
  applied without being shown led to lost recordings.
- **Tests:** `tests/frontend/test_rendered_accessibility.py`: the sound cards are checkboxes in a
  fieldset with a legend, each with an accessible name, and the microphone row's label is wired to
  its select.
- **Docs:** `docs/design-system.md` § Capture Modes (the sound cards, the multi-select variant, the
  headphones hint) and `docs/component-map.md`.
- **Validation:**
  - `uv run pytest tests/frontend`.
  - Browser QA on the isolated server (:8411, mock model, scratch `recording.recording_dir`) at
    600 px and 375 px:
    - each combination;
    - the microphone row appearing and disappearing;
    - the refusal;
    - a started two-input run reporting `audio_inputs` in `GET /api/session`.
- > *Action: Undergo the verification/tests/validation process for this phase. Once validated,
  > commit stating: Mixed Audio and Steady Video (5 / 7) Complete: the record sheet can choose a
  > window's sound and the microphone together.*

### Phase 6 — The sound controls while recording

- **Locations:**
  - New `web/frontend/templates/partials/sound_panel.html` and
    `web/frontend/static/js/components/sound-panel.js`. This is a popover opened from a new
    **Sound** button beside Pause in the header (`partials/header.html`, `components/header.js`),
    shown only while recording or paused.
  - One row per input:
    - icon and name;
    - a level meter fed by `mix.levels`;
    - a volume slider (`<input type="range" min="0" max="100" step="5">`) with a live "80 %"
      readout;
    - a mute toggle (`aria-pressed`).
  - Changes are sent through `stores/mix.js`, throttled to one request per 100 ms while dragging,
    with the final value always sent.
  - A muted input's meter is dimmed and its row says "Muted — not being recorded".
  - New `web/frontend/static/css/components/sound-panel.css`.
  - Wiring in `web/frontend/static/js/main.js` (or `session-start.js` if `main.js` is near its
    cap).
- **Rationale:** the header is visible in every layout and every run, unlike the monitor pane,
  which only some runs show. A popover keeps the header's width unchanged; the 1024 px header
  overflow found during the record-sheet work is the constraint here.
- **Tests:** `tests/frontend/test_rendered_accessibility.py`:
  - each slider has a label naming its input;
  - each mute toggle has `aria-pressed`;
  - the popover is a labelled dialog that returns focus to the Sound button.
- **Docs:** `docs/design-system.md` (a new § Sound controls: layout, states, the muted wording,
  keyboard: arrows move 5 %, M toggles mute on the focused row) and `docs/component-map.md`.
- **Validation:**
  - `uv run pytest tests/frontend`.
  - Browser QA on :8411:
    - start a two-input run;
    - drag a slider and confirm `GET /api/session/mix` follows;
    - mute and confirm the meter dims;
    - open a second tab and confirm it updates from `mix.changed`;
    - keyboard-only operation;
    - 1024 px and 375 px widths, with no header overflow.
- > *Action: Undergo the verification/tests/validation process for this phase. Once validated,
  > commit stating: Mixed Audio and Steady Video (6 / 7) Complete: a Sound panel in the header sets
  > each input's volume and mute live, with a meter per input.*

### Phase 7 — Real-desktop verification and close-out

- **Locations:**
  - `docs/checklist.md`:
    - close "Record a window's sound and the microphone at the same time";
    - add the discovered work: echo cancellation, volume boost, PyAV mux/stitch, crop-to-fill,
      and the switching-windows plan;
    - add the results of the user's hardware runs.
  - `README.md` (what a window recording can capture).
  - `docs/plans/README.md` (status).
  - This plan's status lines.
- **Rationale:** the two faults this plan exists for only reproduce on the real desktop, and a fix
  that has only met synthetic sources is not one to call finished.
- **Validation:** *Human intervention is needed*, on the user's desktop:
  - **Resize test:** a 60 s window recording while resizing and moving the window.
    `utils/check_video_frames.py` reports about 60 s at one frame size, and the capture log has no
    `not-negotiated`.
  - **Mix test:** a video playing in a browser window, narrated over with headphones, for 2 min.
    `audio.wav` contains both, the two tracks each contain one, and the transcript has both.
  - **Live controls:** mute the microphone for 20 s mid-run; that stretch of the microphone track
    is silent, and the transcript has no narration there. Set the window to 30 % and hear it
    quieter in the mix.
  - **Drift:** one 30 min two-input run; the narration in the mix stays in sync with the
    microphone track throughout.
  - The full `uv run pytest`, where only the 4 known environmental failures may remain.
  - `uv run ruff check`.
- > *Action: Undergo the verification/tests/validation process for this phase. Once validated,
  > commit stating: Mixed Audio and Steady Video (7 / 7) Complete: verified on the real desktop and
  > closed out, with the switching-windows plan's groundwork recorded.*

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Fixed canvas | Every recording is scaled to fit one fixed even size, so resizes never reach the encoder | `web/backend/app/services/capture/geometry.py`, `pipeline.py` |
| Canvas setting | `capture.canvas` (720p / 1080p / 1440p) and its select in the Video settings tab | `web/backend/app/config/schema.py`, settings partial and module |
| Named renegotiation failures | The capture log's `not-negotiated` / `unhandled format` become a readable error | `web/backend/app/services/capture/recorder.py`, `session/degradation.py` |
| Frame-size checker | PyAV utility reporting a recording's duration and every distinct frame size | `utils/check_video_frames.py` |
| Mixing math | Gain ramp, sum, clip, percent to gain | `web/backend/app/services/audio/mixer.py` |
| Mixer source | `MixedSource`: named inputs, drift-bounded queues, per-input levels, survives losing one input | `web/backend/app/services/audio/sources/mixed.py` |
| Two-input sessions | `audio_inputs` and `levels` options, the vocabulary mirror, plan rules, input tracks | `session/shapes.py`, `modes.py`, `plan.py`, `sources.py`, `static/js/core/modes.js`, `services/recording/` |
| Live controls API | `GET`/`PATCH /api/session/mix`, `mix.changed` and `mix.levels` events | `web/backend/app/routes/session.py`, `schemas/api.py` |
| Record sheet sound cards | Multi-select sound cards, microphone row, headphones hint, remembered levels | `partials/record_sheet.html`, `components/record-sheet.js`, `css/components/record-sheet.css` |
| Sound panel | Header popover: per-input meter, 0–100 % slider, mute | `partials/sound_panel.html`, `components/sound-panel.js`, `css/components/sound-panel.css`, `stores/mix.js` |
| Canvas tests | Canvas sizes, even dimensions, the canvas stage present whether or not the size is known | `tests/utils/test_canvas_geometry.py`, `tests/transcription/test_capture_pipeline.py` |
| Mixer tests | Sum, gain, mute, ramp, clip, drift bound, one input lost, all inputs lost (synthetic sources) | `tests/transcription/test_audio_mixer.py` |
| Session and API tests | Two-input run, legacy request, conflicts, live PATCH, mute zeroes frames, events | `tests/api/test_session_modes.py`, `tests/api/test_session_mix.py`, `tests/utils/test_run_plan.py`, `tests/utils/test_mode_vocabulary.py` |
| Interface tests | Sound cards and sound panel accessibility | `tests/frontend/test_rendered_accessibility.py` |
| Docs | D-080 (canvas), D-081 (mixer), API contract, data flow, design system, component map, structure, workflow, checklist, README | `docs/` |
