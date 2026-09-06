# Capture Resilience and the Export Window

*Written 2026-09-06. Status: **Complete (9 / 9 steps)**. Recorded as D-036 and D-037.*

Four things reported together against the application after a Zoom seminar: a recording that stopped
capturing partway through, an export that carries the user's own conversation into a file meant for
other people, no control over the size of what it produces, and no window in which to see or decide
any of it.

---

## 1. Introduction

A 68-minute Zoom seminar was recorded on 2026-09-04. The audio and the transcript are complete. The
video is not, and the sound that survived on disk was subsequently destroyed by the step that
combines the two. This plan repairs that, and then builds the export surface the same recording
showed to be missing.

The failure is not a hypothesis. It was measured directly from
`data/recordings/20260904-155600-08ab28c2d732/`:

| Measurement | Value |
|---|---|
| Session wall clock (`session` table) | 15:56:00 → 17:04:20 UTC = **68 m 20 s** |
| Audio captured (`audio.json`) | **3925.7 s** (65 m 26 s), fully transcribed, 692 segments |
| Video frames, steady 15.0 fps | t = 0.000 s → **t = 882.542 s** (14 m 43 s), 13 239 frames |
| Then | **a gap of 882.6 s with no frames at all** |
| Two final frames (the EOS flush) | t = 1765.155 s, t = 1765.161 s |
| Muxed container duration | **1765.227 s** — *both* the video **and the audio** stream end here |
| Recorded resolution | **2560 × 1532**, though the session's own config snapshot says `max_height: 720` |

Read in order, that is a chain of three distinct faults:

1. **The PipeWire stream stopped delivering buffers at 14 m 43 s** and nothing noticed.
   `WindowRecorder._watch` ([recorder.py:197](../../web/backend/app/services/capture/recorder.py))
   polls `process.poll()` every 0.5 s and asks one question: has the process exited? A `gst-launch`
   that is alive and receiving nothing answers "no" forever. There is no frame-progress check, no
   file-growth check, and no GStreamer bus watch, so a stalled capture is indistinguishable from a
   healthy one for as long as it lasts.

2. **When the recorder finally did end, at 29 m 25 s, nothing restarted it.**
   `SessionManager._start_window_capture`
   ([manager.py:819](../../web/backend/app/services/session/manager.py)) is called exactly once per
   session. `_on_recorder_stopped` emits a warning banner and returns. The portal's restore token is
   already persisted for exactly this kind of reuse, and is never used for it. The remaining 39
   minutes of the seminar were never offered to an encoder.

3. **The mux then deleted the audio that had survived.** `combine()`
   ([mux.py:196](../../web/backend/app/services/capture/mux.py)) passes `-shortest`, which truncates
   the output to the shorter input — the broken video. It then probes the result, finds both a video
   and an audio track (both truncated, but both present), and deletes the source video; the source
   `audio.wav` is separately deleted by `TranscriptionRunner` once the pass succeeds. **The last 36
   minutes of sound now exist only as text.** `-shortest` was added for a good reason — a closed
   window used to leave a long tail of audio over a frozen frame — but it answers that cosmetic
   problem by discarding evidence, which is the wrong trade in every case where the video is the
   thing that broke.

A fourth measurement explains the file size, and is the bridge to the rest of the plan. The session
asked for `max_height: 720` and recorded at 2560 × 1532. `_record_scaler`
([pipeline.py:304](../../web/backend/app/services/capture/pipeline.py)) omits the scaler entirely
when `geometry.resolve()` raises, and it raises whenever the portal reports no source dimensions —
which its own comment records as "the normal case on this desktop." So the ceiling is advisory in
practice, the encoder does roughly four times the intended pixel work while competing with the speech
model for the same cores, and the file that reaches the user is the size of a full-resolution
recording. That is both a plausible contributor to fault 1 and the direct cause of the reported
"30 minutes came out at about 100 MB."

The approach has four parts, ordered so that nothing that loses data survives the first three steps:

- **Stop losing things** (Steps 1–3). Make the mux non-destructive, teach the recorder to notice a
  stall, and make the capture resume where it left off.
- **Separate the conversation from the record** (Step 4). The user's questions to the assistant leave
  the shared export and become their own artifact.
- **Make size a decision** (Steps 5–6). Measure a finished recording, model what a re-encode of it
  would cost, and run that re-encode as a staged job with real progress.
- **Put it in one window** (Steps 7–9). A dedicated post-recording view that previews the recording,
  offers the options, shows the projected sizes, and watches the pipeline run.

Steps 1–3 are repairs and land first. Steps 4–9 are new capability and build on the export job the
middle steps introduce.

---

## 2. Gaps & Unanswered Questions

**Resolved by assumption — proceeding.**

- **Why the PipeWire node stalled rather than ending.** The measurement establishes that it stopped
  producing buffers 14 m 43 s in and was not torn down for another 14 m 43 s. Whether Zoom
  re-negotiated its surface after a network drop, whether the compositor suspended the node, or
  whether `pipewiresrc keepalive-time=1000` failed to fire is not determinable from the artifacts
  that survive. *Assumption: it does not need to be.* Every remedy in Steps 1–3 is triggered by the
  observable symptom — no frames arriving — and none depends on the cause. Recording the cause when
  it next happens is what the bus watch in Step 2 is for.

- **Whether the recorder exited 0 or non-zero.** `logs/capture.log` still holds an unrelated
  `amdgpu` message from 2026-08-16, and `_write_log` only writes when stderr was non-empty, so this
  run produced none. A clean EOS from a vanished source exits 0, which `_watch` classifies as
  `window_closed` — an ordinary event with a dismissible warning. *Assumption: it exited 0.* Step 2
  makes the distinction moot by treating "ended while the session is still running" as a resumable
  condition regardless of code, and Step 3's segment log records the exit code either way.

- **Container and codec for the export.** Capture writes VP8 in WebM because that is what this
  machine's GStreamer can do. `ffmpeg` here has `libx264`, `libx265`, `libvpx-vp9`, `libsvtav1`,
  `aac` and `libopus`. *Assumption: export defaults to H.264 + AAC in MP4*, because the stated
  purpose is sending files to other people and MP4 plays everywhere WebM does and several places it
  does not. WebM/VP9 and "Original — no re-encode" stay as options; nothing about capture changes.

- **Whether re-encoding at export is acceptable.** It costs CPU time proportional to the recording.
  *Assumption: yes* — it is what "control resolution, frame rate, and other encoding parameters" asks
  for, and it is the only place those can be applied reliably, since Step 1's measurement shows the
  capture-time ceiling is not always honoured. Step 6 shows the estimated time before the user
  commits, and "Original" skips the encode entirely.

- **Whether existing Markdown and JSON exports should keep carrying chat.** They do today
  ([export.py](../../web/backend/app/services/transcript/export.py) `to_markdown`, `to_json`).
  *Assumption: they keep the capability but lose it by default* — an `include_chat` flag defaulting
  to `False`. Removing it outright would delete a working feature to fix a default.

**Human intervention is needed to answer this question.**

- **Whether a resumed capture should re-prompt if the restore token no longer matches.** KDE restores
  a window session by fuzzy-matching the saved title, so a Zoom window whose title changed will
  legitimately fail to restore and the compositor will show its picker — in the middle of a seminar,
  over the thing being recorded. Step 3 implements the conservative reading: **resume silently when
  the token restores, and give up with a banner when it does not**, never raising a dialog the user
  did not ask for mid-recording. If the preference is the opposite — interrupt and ask, rather than
  lose the rest of the video — that is a one-flag change in `_resume_window_capture`, and it needs a
  human answer.

---

## 3. Hierarchical Step-by-Step Instructions

### Step 1: The mux stops destroying the audio it was given ✅

- **Locations**: `web/backend/app/services/capture/mux.py` — `combine()`, `MuxResult`,
  `has_both_streams()`, new `_duration()` helper. `web/backend/app/services/recording/runner.py` —
  the retention branch that deletes the source WAV. `tests/transcription/test_capture_mux.py`.
- **Rationale**: This is the only fault of the three that destroys something, and it destroys it
  *after* the recording is over, which means it is the one fix that changes what a future failure
  costs. Drop `-shortest`; the output runs for as long as the longer input, and a video that ended
  early holds its last frame while the sound continues — which is a truthful picture of what
  happened, and is what the frozen tail `-shortest` was avoiding actually looked like. Then make
  deletion conditional on more than "both stream types are present": compare the muxed duration
  against both sources with `ffprobe`, and keep every source whenever the output is materially
  shorter than either. Carry the shortfall on `MuxResult` so the caller can say it out loud, and
  make `TranscriptionRunner` refuse to delete `audio.wav` when the mux reported one — an incomplete
  video is exactly the case where the original sound must survive.
- **Action**: Undergo the verification/tests/validation process for this phase: `uv run pytest
  tests/transcription/test_capture_mux.py`, then `uv run pytest`, `uv run ruff check .`, `uv run
  ruff format --check .`. Once validated, commit stating: Capture Resilience and Export (1 / 9)
  Complete: The mux keeps the whole of the audio it was given and refuses to delete a source the
  output does not fully contain.
- **Docs**: `docs/documentation.md` (Decision Log — new entry), `docs/checklist.md`.

### Step 2: The recorder notices a capture that has stopped producing ✅

- **Locations**: `web/backend/app/services/capture/recorder.py` — `RecorderState` (new
  `stalled`, `last_progress_at`, `frozen_seconds` fields), `_watch()`, new `_sample_progress()`.
  `web/backend/app/services/capture/pipeline.py` — drop `-q` so GStreamer's bus messages reach
  stderr, and add a `progressreport` element on the recording branch so progress is parseable.
  `web/backend/app/services/session/degradation.py` — a new `capture_stalled` failure.
  `web/backend/app/services/session/manager.py` — surface it on `capture.state`.
  `tests/transcription/test_capture_stall.py` (new).
- **Rationale**: `_watch` currently asks one question — has the process exited — and a stalled
  pipeline answers "no" indefinitely, which is precisely how 14 minutes of a seminar went unrecorded
  without a single log line. Sampling `filesink`'s output size on the existing 0.5 s tick answers a
  second question — is it still writing — for the cost of one `stat` per tick. A file that has not
  grown for `STALL_TIMEOUT_S` (12 s: long enough that a static slide under a constant-quality
  encoder does not trip it, short enough that no one loses a talk) is stalled, and that is a
  reportable state whether or not the process is alive. Reading the bus is the other half: `-q`
  suppresses the messages that would have named the cause, and a diagnosis is worth more than a
  quiet log. This step only *detects and reports*; Step 3 acts on it, so the detector can be
  validated on its own before anything restarts on its verdict.
- **Action**: Undergo the verification/tests/validation process for this phase: `uv run pytest
  tests/transcription/test_capture_stall.py tests/transcription/test_capture_pipeline.py`, then the
  full `uv run pytest`, `ruff check`, `ruff format --check`. Once validated, commit stating: Capture
  Resilience and Export (2 / 9) Complete: A capture that stops producing frames is detected and
  reported instead of running silently to the end of the session.
- **Docs**: `docs/documentation.md`, `docs/api-contract.md` (the `capture.state` shape).

### Step 3: A capture that ends mid-session resumes where it left off ✅

- **Locations**: `web/backend/app/services/session/manager.py` — `_on_recorder_stopped`, new
  `_resume_window_capture()` and `_capture_segments` list; `_start_window_capture` refactored so the
  portal-open-and-build half is callable again. `web/backend/app/services/recording/layout.py` — a
  `video_segment(n, ext)` name (`video.002.webm`) beside the existing `video()`.
  `web/backend/app/services/capture/portal.py` — reopen with the stored restore token, and report
  whether it restored or would have prompted. `web/backend/app/services/capture/stitch.py` (new) —
  joins the segments onto one timeline. `tests/transcription/test_capture_resume.py` (new).
- **Rationale**: This is the user's own request — "detect it and resume capture where it left off
  rather than silently dying" — and the machinery it needs already exists in pieces. The portal's
  restore token is persisted on every start specifically so a second session skips the picker; using
  it to reopen *within* a session is the same call. Each resume writes its own segment file rather
  than appending, because appending to a finalised WebM is not a thing that can be done safely and a
  segment that plays is worth more than a single file that might not. Every segment records the
  session-relative second at which it started, from the same monotonic clock the alignment
  measurement already uses, so the gaps are known rather than inferred. `stitch.py` then builds one
  video from them: generate a filler clip per gap at the segment's own codec, resolution and frame
  rate — a held last frame, which is cheap because it is nearly all skipped macroblocks — and join
  everything with ffmpeg's concat demuxer as a **stream copy**, so an hour of recording is not
  re-encoded to repair fifteen minutes of it. Resume is bounded: `MAX_RESUMES` (5) and a refusal to
  retry within `RESUME_BACKOFF_S`, so a portal that will never come back produces one banner rather
  than a restart loop. Per §2, a token that fails to restore ends the video with a banner and never
  raises a picker mid-recording.
- **Action**: Undergo the verification/tests/validation process for this phase: `uv run pytest
  tests/transcription/test_capture_resume.py tests/transcription/test_capture_mux.py
  tests/transcription/test_window_session.py`, then the full suite, `ruff check`, `ruff format
  --check`. Manual: a window recording where the captured window is closed and reopened mid-run
  produces one continuous video with a held frame across the gap, and audio that stays in sync after
  it. Once validated, commit stating: Capture Resilience and Export (3 / 9) Complete: A window
  capture that dies mid-session reopens the portal and resumes, and the segments are stitched onto
  one timeline with the gaps held rather than dropped.
- **Docs**: `docs/documentation.md`, `docs/structure.md` (the new `stitch.py`), `docs/data-flow.md`,
  `docs/checklist.md`.

### Step 4: The user's conversation leaves the shared export ✅

- **Locations**: `web/backend/app/services/export/payload.py` — drop `chat` from
  `transcript_payload()`, add `chat_payload()`. `web/backend/app/services/export/webapp.py` —
  `build_webapp(..., include_chat: bool = False)`, `_bundle()` follows.
  `web/backend/app/services/transcript/export.py` — `include_chat: bool = False` on `to_markdown`
  and `to_json`, and two new chat-only renderings. `web/backend/app/routes/sessions.py` — an
  `include_chat` query parameter, and a new `GET /api/sessions/{key}/chat?fmt=markdown|json`.
  `web/backend/app/services/export/template/assistant.js` — read `data.chat` only when present.
  `web/frontend/static/js/transport/api.js` — `sessionChatUrl(key, fmt)`.
  `tests/data/test_export_formats.py`, `tests/data/test_webapp_export.py`.
- **Rationale**: The recording measured for this plan carries six chat messages, and every one of
  them rides into `transcript.json`, into `bundle.js` beside it, into the Markdown export and into
  the JSON export. The exported page then seeds its assistant with them, so a recipient opening the
  ZIP starts mid-conversation with someone else's questions — which is the opposite of "connect your
  own model and ask your own questions." Making the conversation a separate artifact rather than
  deleting it keeps both readings of what the user asked for: the shared export is clean, and their
  own questions are still exportable, as `chat.md` or `chat.json`, on their own. The default is what
  changes; the capability is preserved behind an explicit flag, because a default is a decision about
  the common case and removing a feature is a decision about all of them.
- **Action**: Undergo the verification/tests/validation process for this phase: `uv run pytest
  tests/data/`, then the full suite, `ruff check`, `ruff format --check`. Manual: export the
  2026-09-04 session's web app and confirm the assistant panel opens empty and no message text
  appears anywhere in the ZIP. Once validated, commit stating: Capture Resilience and Export (4 / 9)
  Complete: A shared export carries the transcript and not the conversation, and the conversation
  exports on its own.
- **Docs**: `docs/api-contract.md`, `docs/routes.md`, `docs/documentation.md`, `docs/data-flow.md`.

### Step 5: A recording can be measured, and a re-encode of it can be predicted ✅

- **Locations**: `web/backend/app/services/export/profile.py` (new) — `SourceProfile` from
  `ffprobe` (width, height, frame rate, duration, video and audio bitrate, size on disk) and
  `EncodePlan` (container, video codec, CRF, resolution ceiling, frame-rate ceiling, audio bitrate).
  `web/backend/app/services/export/estimate.py` (new) — bytes and seconds predicted for a plan.
  `web/backend/app/services/export/presets.py` (new) — the named plans. `tests/data/test_export_estimate.py` (new).
- **Rationale**: The user asks to see the size *before* committing, which means the estimate has to
  exist independently of the encode. Base it on bits per pixel per frame rather than on a flat
  bitrate, because the same setting must be able to say that a 640 × 360 window is small and a
  2560 × 1532 one is not — the project has already measured exactly this asymmetry for the capture
  encoder (`schema.py` records 4909 kbps at 1080 × 1064 against 1038 kbps at 640 × 360 for one
  setting), and that measured table is the calibration point. Presets: **Original** (no re-encode,
  the current behaviour, exact size known), **High** (1080p cap, source frame rate, CRF 20),
  **Balanced** (720p cap, 15 fps cap, CRF 26 — the default), **Small** (540p cap, 10 fps cap,
  CRF 32), and **Audio only** (no video track at all, for when the picture does not matter). Keep
  the estimate honest: return a range rather than a single number, and label it as an estimate,
  because a constant-quality encode's size depends on content and a figure presented as exact will
  be wrong. Encode time is predicted the same way, from a measured throughput constant per codec,
  and refined by the calibration script in the deliverables.
- **Action**: Undergo the verification/tests/validation process for this phase: `uv run pytest
  tests/data/test_export_estimate.py`, then the full suite, `ruff check`, `ruff format --check`.
  Manual: run `uv run scripts/calibrate_export_estimate.py` against
  `data/recordings/20260904-155600-08ab28c2d732/` and confirm each preset's real output falls inside
  its predicted range. Once validated, commit stating: Capture Resilience and Export (5 / 9)
  Complete: A finished recording is measured and every export preset's size and duration are
  predicted from it before anything is encoded.
- **Docs**: `docs/structure.md`, `docs/documentation.md`, `docs/api-contract.md`.

### Step 6: The export runs as a staged job that reports its progress ✅

- **Locations**: `web/backend/app/services/export/job.py` (new) — `ExportJob` and `ExportStage`,
  modelled on `services/recording/job.py`, with per-stage progress and its own single-slot
  `ExportRegistry`. `web/backend/app/services/export/runner.py` (new) — a background thread driving
  the stages. `web/backend/app/services/export/encode.py` (new) — the `ffmpeg` invocation, parsing
  `-progress pipe:1` for real per-frame progress. `web/backend/app/services/export/webapp.py` —
  accept a prepared media file and report ZIP progress. `web/backend/app/transport/events.py` —
  `EXPORT_PROGRESS` (coalescing), `EXPORT_DONE`, `EXPORT_FAILED` (both critical), and an `INVALIDATES`
  entry so `EXPORT_DONE`/`EXPORT_FAILED` retract `EXPORT_PROGRESS`.
  `web/backend/app/routes/sessions.py` — `GET /api/sessions/{key}/export/options`,
  `POST /api/sessions/{key}/export/start`, `GET /api/sessions/{key}/export/result`.
  `web/frontend/static/js/transport/events.js` and `stores/export.js` (new).
  `tests/data/test_export_job.py` (new), `tests/api/test_transport.py`.
- **Rationale**: A re-encode of an hour of video is minutes of work, so it cannot be a synchronous
  download; and the user asked to "watch the pipeline run through its stages," which means stages
  have to be a first-class thing rather than a spinner. Stages: **measure** → **encode video** →
  **build transcript** → **package**, each with its own fraction and its own estimated remaining
  time, and a job-level fraction weighted by predicted stage cost so the overall bar does not stall
  at 90 %. `TranscriptionJob` is the model to follow, including `as_event()` — the frontend already
  knows how to consume that shape. The `INVALIDATES` entry is not optional: without it, a client
  connecting after an export finished is replayed a stale "in progress" frame for the life of the
  process, which is a bug this project has already fixed twice for transcription (D-033). The result
  is written into the recording's own folder and served by a separate GET, so a completed export
  survives a page reload.
- **Action**: Undergo the verification/tests/validation process for this phase: `uv run pytest
  tests/data/test_export_job.py tests/api/test_transport.py`, then the full suite, `ruff check`,
  `ruff format --check`. Once validated, commit stating: Capture Resilience and Export (6 / 9)
  Complete: Exporting is a staged background job with per-stage progress and estimated time, pushed
  over the WebSocket and retracted when it ends.
- **Docs**: `docs/api-contract.md`, `docs/routes.md`, `docs/data-flow.md`, `docs/structure.md`,
  `docs/documentation.md`.

### Step 7: The export window — preview, options, and projected sizes ✅

- **Locations**: `web/frontend/templates/partials/export.html` (new), included in
  `templates/pages/app.html` outside `.app` beside `preflight.html`.
  `web/frontend/static/js/components/export-dialog.js` (new).
  `web/frontend/static/css/components/export.css` (new), linked in `templates/base.html`.
  `web/frontend/static/js/core/export-presets.js` (new) — mirrored from `services/export/presets.py`.
  `tests/utils/test_export_preset_vocabulary.py` (new).
- **Rationale**: One window, built the way this project builds windows: the `.modal` /
  `.modal__scrim` / `.modal__dialog` shell from `css/components/modal.css`, the promise-returning
  `show()` and `FocusTrap` pattern from `components/preflight.js`, the `.field` vocabulary from the
  settings tabs, and tokens for every colour and length. It holds a `<video>` preview of the
  recording, the preset list with a live size figure against each, the four controls the user named
  (resolution ceiling, frame-rate ceiling, quality, and whether to include their conversation), and a
  projected size and duration that update as those move. Presets are mirrored into a JS module and
  held identical to the Python by a parsing test, exactly as `core/modes.js` is — the two sides
  disagreeing about what "Balanced" means is a fault that a test can make impossible.
- **Action**: Undergo the verification/tests/validation process for this phase: `uv run pytest
  tests/utils/test_export_preset_vocabulary.py`, then the full suite, `ruff check`, `ruff format
  --check`. Manual browser QA per `docs/design-system.md`: a 320 px viewport, a keyboard-only pass
  through every control, Escape and scrim-click both closing, and the estimate changing when a
  control moves. Once validated, commit stating: Capture Resilience and Export (7 / 9) Complete: A
  post-recording export window previews the recording, offers the encoding options, and projects the
  file size for each before anything is exported.
- **Docs**: `docs/component-map.md`, `docs/design-system.md`, `docs/routes.md`, `docs/structure.md`.

### Step 8: The window watches the pipeline run ✅

- **Locations**: `web/frontend/static/js/components/export-dialog.js` — the progress view.
  `web/frontend/static/js/stores/export.js` — consume `export.progress` / `.done` / `.failed`.
  `web/frontend/static/js/main.js` — `wireExport()` beside the existing `wireRecording()`, and open
  the dialog from `onStop`. `web/frontend/static/js/sessions.js` — open the same dialog from the
  Recordings page. `web/frontend/static/js/components/scroll-controller.js` — reused for the stage log.
- **Rationale**: The second half of the same window, and the reason the job in Step 6 carries stages
  rather than one number: a stage list where each row shows its own bar, its own elapsed time and its
  own estimate is the difference between watching a pipeline and watching a black box. Transcription
  belongs in that list even though it runs before the export — a window recording's second pass and
  its export are one wait from where the user sits, and splitting them across two unrelated
  indicators is what makes the current flow feel like nothing is happening. Reaching the dialog from
  both `onStop` and the Recordings page matters because an export is retried more often than it is
  run: the same window, one entry point per occasion.
- **Action**: Undergo the verification/tests/validation process for this phase: full `uv run
  pytest`, `ruff check`, `ruff format --check`. Manual browser QA: stop a window recording and watch
  every stage advance to completion; reload mid-export and confirm the window re-attaches to the
  running job rather than showing an idle state; cancel mid-encode and confirm nothing is left
  half-written. Once validated, commit stating: Capture Resilience and Export (8 / 9) Complete: The
  export window shows every stage of the pipeline with its own progress and estimated time, and
  re-attaches to a running job after a reload.
- **Docs**: `docs/component-map.md`, `docs/data-flow.md`, `docs/motion-spec.md` if any new indicator
  motion is introduced.

### Step 9: Documentation, the decision log, and the merge ✅

- **Locations**: `docs/documentation.md` (status table and Decision Log entries D-036 and D-037),
  `docs/structure.md`, `docs/api-contract.md`, `docs/routes.md`, `docs/data-flow.md`,
  `docs/component-map.md`, `docs/workflow.md` (the new manual verification passes),
  `docs/checklist.md` (this plan's items closed, new verification debt opened),
  `docs/plans/README.md` (index row), `.env.example` if any variable was added.
- **Rationale**: The repository's contract is that documentation ships with the code, and eight
  steps of it have accumulated. Two decisions in particular need recording with their reasoning
  rather than their outcome: **why a stalled capture is detected by file growth rather than by asking
  GStreamer** (because a stalled pipeline answers every question except that one), and **why export
  quality is a re-encode at export time rather than a capture setting** (because the capture ceiling
  is advisory whenever the portal reports no geometry, which this machine does normally — a setting
  that is silently ignored is worse than no setting). The verification that could not be run headless
  goes into `docs/checklist.md` Part 5 as debt, named, rather than implied as passing.
- **Action**: Undergo the verification/tests/validation process for this phase: the full `uv run
  pytest`, `ruff check`, `ruff format --check`, and a read-through of each changed doc against the
  code it describes. Once validated, commit stating: Capture Resilience and Export (9 / 9) Complete:
  Documentation, the decision log, and the checklist record the capture repairs and the export
  window. Then merge `capture-resilience-and-export` into `main`, resolving any conflict in favour
  of the branch's newer behaviour, and do not push.
- **Docs**: all of the above.

---

## 4. Deliverables Table

| Deliverable | Description | Location |
|---|---|---|
| Non-destructive mux | `-shortest` removed; sources kept whenever the output is materially shorter than either | `web/backend/app/services/capture/mux.py` |
| Retention guard | `audio.wav` survives when the mux reported a shortfall | `web/backend/app/services/recording/runner.py` |
| Stall detection | Output-file growth sampled on the supervisor tick; a new `capture_stalled` failure | `web/backend/app/services/capture/recorder.py`, `services/session/degradation.py` |
| GStreamer bus diagnostics | `-q` dropped, `progressreport` added, stderr retained on every exit | `web/backend/app/services/capture/pipeline.py`, `capture/recorder.py` |
| Capture resume | Portal reopened on the stored token, a new segment per resume, bounded retries | `web/backend/app/services/session/manager.py`, `capture/portal.py`, `recording/layout.py` |
| Segment stitching | Gap fillers generated per segment's own caps; concat demuxer stream-copy join | `web/backend/app/services/capture/stitch.py` |
| Chat separated from the export | `chat` out of `transcript_payload`/`bundle.js`; `include_chat` defaulting to `False`; a chat-only export | `web/backend/app/services/export/payload.py`, `export/webapp.py`, `services/transcript/export.py`, `routes/sessions.py` |
| Source measurement and encode plans | `ffprobe`-derived `SourceProfile`, `EncodePlan`, five named presets | `web/backend/app/services/export/profile.py`, `export/presets.py` |
| Size and time estimator | Bits-per-pixel model returning a labelled range, calibrated against measured data | `web/backend/app/services/export/estimate.py` |
| Staged export job | `ExportJob`, `ExportStage`, single-slot registry, background runner, `ffmpeg -progress` parsing | `web/backend/app/services/export/job.py`, `export/runner.py`, `export/encode.py` |
| Export events | `export.progress` / `.done` / `.failed` with the `INVALIDATES` retraction entry | `web/backend/app/transport/events.py` |
| Export routes | `GET .../export/options`, `POST .../export/start`, `GET .../export/result`, `GET .../chat` | `web/backend/app/routes/sessions.py` |
| Export window | Preview, presets, controls, projected sizes, per-stage progress; `.modal` shell, `FocusTrap`, tokens only | `web/frontend/templates/partials/export.html`, `static/js/components/export-dialog.js`, `static/css/components/export.css` |
| Export store and wiring | Client state for the job; opened from `onStop` and from the Recordings page | `web/frontend/static/js/stores/export.js`, `main.js`, `sessions.js` |
| Preset mirror | `export-presets.js` held identical to `presets.py` by a parsing test | `web/frontend/static/js/core/export-presets.js` |
| **Mux truncation tests** | The output keeps the longer input; a short output keeps every source | `tests/transcription/test_capture_mux.py` |
| **Stall detection tests** | A file that stops growing is reported stalled; a static slide under a constant-quality encoder is not | `tests/transcription/test_capture_stall.py` |
| **Resume tests** | A recorder death mid-session opens a second segment; retries are bounded; stitched output holds the gap and stays in sync | `tests/transcription/test_capture_resume.py` |
| **Export payload tests** | No chat in the default web app or the default Markdown/JSON; the chat export carries all of it | `tests/data/test_webapp_export.py`, `tests/data/test_export_formats.py` |
| **Estimator tests** | Estimates scale with pixels and frame rate; a real recording's output falls inside its predicted range | `tests/data/test_export_estimate.py` |
| **Export job tests** | Stage sequencing, weighted progress, failure and cancellation paths, event retraction | `tests/data/test_export_job.py`, `tests/api/test_transport.py` |
| **Preset vocabulary test** | `core/export-presets.js` and `services/export/presets.py` agree exactly | `tests/utils/test_export_preset_vocabulary.py` |
| Estimator calibration script | Encodes a real recording at every preset and reports predicted against actual | `scripts/calibrate_export_estimate.py` |
| Recording forensics script | Reports frame-rate continuity, gaps, and stream end times for a recording folder — the analysis that diagnosed this fault, made repeatable | `scripts/inspect_recording.py` |


---

## 5. What Changed From the Plan

Recorded because a plan that is quietly departed from is a plan nobody trusts next time.

- **`progressreport` was not added to the GStreamer pipeline** (Step 2). Sampling the output file's
  size answers the same question — is it still writing — for one `stat` per tick and no change to a
  launch line with a bad history. `progressreport` writes to stdout, which the recorder sends to
  `DEVNULL`, so using it would have meant a second reader thread for an answer already in hand.

- **stderr goes to a file rather than a pipe** (Step 2), which the plan did not call for. Dropping
  `-q` makes the pipeline talkative, and a pipe nobody drains until the process exits blocks its
  writer at 64 KB — a way of *causing* the stall being detected.

- **The sixth export preset was removed after it was measured** (Step 5). VP9 in WebM at the same
  nominal quality produced a sixth more bytes in ten times the wall clock. The plan listed it; the
  calibration script the plan also called for is what disqualified it.

- **`capture.max_height` was left as it is** (Step 5's rationale, not its steps). The plan's §2 said
  the ceiling is advisory; the remedy taken is to make resolution an export-time decision rather
  than to change `_record_scaler`, whose caps history includes a 480 × 16 recording and an integer
  overflow. Whether capture should also enforce it is now an open item in
  [../checklist.md](../checklist.md), needing a real portal stream to test against.

- **Three faults were found by running the window in a browser** (Step 8) rather than by the tests:
  a stray click reaching an unopened controller, a refusal left standing beside a success, and a
  finished job whose elapsed time kept climbing. All three are fixed and the last has a test.
