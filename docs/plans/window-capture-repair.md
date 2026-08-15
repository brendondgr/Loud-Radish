# Window Capture Repair

**Status:** Phase 1 complete (the resolution fault), Phases 2–8 not started.
**Branch:** `claude/gpu-and-capture-fixes`

---

## 1. Introduction

Window recording is built, tested, and does not work. A user picks a window, gets several
consecutive portal dialogs, watches a monitor pane that says "not recording a video" for the whole
run, and ends up with a file that does not show the window they chose. Every one of those symptoms
has survived a suite of 1291 passing tests, which is the most important fact in this plan: the tests
assert the shape of what the code builds and never once look at what the machine produced. One
recording was written **480 pixels wide by 16 pixels tall** — a sixteen-pixel strip of a talk — and
nothing in the repository noticed, because the launch line was well-formed and GStreamer exited
zero.

This plan repairs the feature and changes how it is verified, so the class of fault that produced a
480×16 recording cannot be reported as green again. It proceeds in the order the faults block each
other: prove what is actually happening on this machine, stop the repeated dialogs, get a frame on
screen, then correct where the audio comes from and when the recording stops. It closes with the
timestamp fault, which is separate from capture but shares the same end goal — a recording, a
transcript that lines up with it, and an assistant whose citations point at the moment a thing was
actually said.

**The end state being built toward**, stated once so each phase can be judged against it: one
session produces a video of the chosen window, an audio track that contains that window's sound and
not the user's microphone, a transcript whose timestamps are session-relative and correct at every
revision, and an assistant that answers from that transcript with citations a human can click and
verify.

---

## 2. Gaps & Unanswered Questions

- **Why the recording showed the wrong thing.** Established: the preview branch's fixed `width=480`
  propagated upstream through the `tee` and set the recording's width, and an open height range let
  the pair resolve to a strip. Fixed in Phase 1 and verified against real GStreamer. *Assumption for
  the rest of this plan: this was the whole of the "wrong window" symptom.* Phase 2 tests it rather
  than trusting it — if a corrected capture still shows the wrong content, the fault is the node the
  compositor handed over, which is a different problem with a different fix.

- **Why the portal dialog appears about five times.** Partly established by bus tracing: seven
  `CreateSession` → `SelectSources` → `Start` cycles from seven separate connections, each ending in
  response code **1 (cancelled)**, the next beginning ~90 ms later. A 90 ms gap is a program
  retrying, not a person clicking. The retry is **not** in `PortalSession`, not in the header's
  toggle handler, and not in the manager's start path. *Not yet located.* Phase 3 finds it before
  changing anything, because a guard added to the wrong layer hides the loop instead of removing it.

- **Which audio belongs to "the window".** The ScreenCast portal carries **video only** — D-022
  records this and it has not changed. There is no supported route from "the window the compositor
  let me pick" to "that application's audio stream": the portal returns no PID, no application id,
  and no audio node. What PipeWire *does* offer is the default sink's **monitor**, which is
  everything the machine plays and contains no microphone at all. *Assumption: the machine's output
  monitor is what "the window's audio" means in practice*, since in the case that matters — a talk
  playing in a window — the window is the thing making sound. Isolating one application's stream
  while others play is possible via its sink-input node but requires matching the window to the app
  by name, which is a heuristic that will silently record the wrong application when two match.
  **Human intervention is needed to answer this question:** is the machine's whole output acceptable,
  or is per-application isolation required despite being heuristic?

- **Whether the microphone should be available at all in window mode.** *Assumption: it is offered
  as an explicit per-run choice and defaults to off*, because the reported fault is the microphone
  being recorded when it was not wanted, and a default that surprises is the fault being fixed.

- **Why assistant timestamps do not correlate.** Not yet diagnosed. Three candidates, distinguishable
  by inspection and ranked by likelihood: segment `start` values stored relative to an inference
  pass rather than to the session; the post-capture pass (revision 1) writing a different origin
  from the live pass (revision 0); or the model interpolating timestamps rather than copying the
  `[MM:SS]` markers it is given. Phase 7 measures before it changes anything — a prompt fix applied
  to a storage fault would make wrong numbers more confident.

- **Whether existing recordings are recoverable.** They are 480×16 and the pixels are gone. *No
  migration is offered; the transcripts beside them remain valid.*

---

## 3. Hierarchical Step-by-Step Instructions

### Phase 1 — The recording's resolution is stated, not negotiated ✅ COMPLETE

- **Locations**: `web/backend/app/services/capture/pipeline.py` (`build`, new `_record_caps`);
  `web/backend/app/services/session/manager.py` (`_start_window_capture`);
  `tests/transcription/test_capture_pipeline.py`.
- **Rationale**: The tee shared one `videoscale` between the recording and the preview, so the
  preview's fixed width decided the recording's width, and an open height range let the result
  degenerate. Each branch now scales for itself after the tee, and concrete even dimensions are
  computed from the size the portal reports, capped at the ceiling and never scaled up. The manager
  logs the node id, the reported source size, and the launch line, so a future "it recorded the
  wrong thing" separates a bad node from a bad pipeline immediately.
- **Validation**: `uv run pytest tests/transcription/test_capture_pipeline.py`; a real
  `gst-launch-1.0` run confirming 1920×1080 in gives 1280×720 recorded and 480×270 previewed.
- **Committed**: `Window capture: the preview branch was deciding the recording's resolution`.

### Phase 2 — A capture is verified by opening the file it produced

- **Locations**: new `tests/transcription/test_capture_output.py`; new helper in
  `scripts/verify_capture.py`.
- **Rationale**: This is the phase that stops the recurrence the user asked about. Every existing
  capture test asserts the *command*; none opens the *file*. A test that renders a known pattern
  through the real pipeline — substituting `videotestsrc` for `pipewiresrc`, which is the only part
  needing a compositor — and then reads back the written file's width, height, frame count and mean
  pixel value would have failed on 480×16 on the day it was introduced, and would fail on a black
  or empty recording too. The script is the manual counterpart: it runs one real portal capture end
  to end and prints what was actually written, for the cases a test cannot reach.
- **Validation**: `uv run pytest tests/transcription/test_capture_output.py`; deliberately reinstate
  the shared-scaler pipeline and confirm the new test fails; restore and confirm it passes.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Window Capture Repair (2 / 8) Complete: a capture test opens the file it produced,
  so a well-formed pipeline that writes a sixteen-pixel strip fails rather than passes.`

### Phase 3 — One dialog per recording

- **Locations**: `web/backend/app/services/session/manager.py` (`_start_window_capture`, `start`,
  `toggle`); `web/backend/app/routes/session.py`; `web/frontend/static/js/components/preflight.js`
  and `header.js`; `utils/transcriber_ctl.py`; `web/backend/app/companion/shortcuts.py`.
- **Rationale**: Seven portal negotiations for one recording, each cancelled, each retried within
  90 ms. Locating the loop comes first: instrument `PortalSession.open` with a call counter and a
  stack log, reproduce once, and read which caller repeats. The prime suspects are a start path
  reachable from more than one entry point at once — the toggle endpoint, the global shortcut, and
  the browser control all reach the same manager — and an `arming` → `start` transition that opens a
  portal per attempt. Whatever the cause, the fix ends with the manager refusing to open a second
  portal while one negotiation is in flight, because a dialog the user is looking at is a resource
  and re-entrancy on it is always a fault.
- **Validation**: `uv run pytest tests/transcription/test_window_session.py`; a new test asserting
  concurrent start attempts negotiate exactly once; a real capture traced with
  `dbus-monitor --session` showing exactly one `CreateSession`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Window Capture Repair (3 / 8) Complete: one recording negotiates exactly one
  portal session, and a second attempt while a dialog is open is refused rather than queued.`

### Phase 4 — The monitor pane shows the capture

- **Locations**: `web/frontend/static/js/components/recording-monitor.js` (`_renderPreview`,
  `_refresh`, `_syncTimer`); `web/frontend/static/js/stores/capture.js`;
  `web/backend/app/routes/capture.py` (`preview`); `web/backend/app/services/capture/recorder.py`
  (`state.preview_path`).
- **Rationale**: The preview JPEGs *are* being written — `data/recordings/` has one per session at
  one frame a second — so the capture side works and the pane is not being told. The image is gated
  on `capture.recording && capture.preview`, so either the `capture.state` event never sets
  `preview`, or the route cannot find `recorder.state.preview_path`, or the pane's visibility check
  suppresses the timer. Diagnosis is three assertions against a live session, in that order. The
  fix ends with the pane distinguishing its three real states — recording with frames, recording
  without frames yet, and not recording — because a black rectangle and a dead capture look
  identical, which is the whole reason the pane exists.
- **Validation**: `npm run build && npm run typecheck` in `web/frontend`;
  `uv run pytest tests/api/`; manual browser QA — start a window capture and confirm the pane shows
  a frame that updates within two seconds and stops when the recording stops.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Window Capture Repair (4 / 8) Complete: the monitor pane shows the live capture
  and distinguishes "no frame yet" from "not recording".`

### Phase 5 — Window audio, not the user's microphone

- **Locations**: `web/backend/app/config/schema.py` (`AudioConfig`, `SourceType`, `CaptureConfig`);
  `web/backend/app/services/audio/sources/device.py`; new `web/backend/app/services/audio/monitor.py`
  for resolving the default sink's monitor; `web/backend/app/services/session/manager.py`
  (`CaptureOptions`, `_open_source`); `web/frontend/static/js/components/preflight.js`;
  `web/frontend/static/js/components/settings/`.
- **Rationale**: The portal hands over video only, so window audio is a separate capture and always
  was — D-022 says so and the interface does not. Window mode gains an explicit audio choice with
  three values — **system output** (the default sink's monitor, which contains no microphone),
  **microphone**, and **both** — defaulting to system output, because the reported fault is a
  microphone recorded when it was not wanted. Resolving the monitor must be done at start time
  rather than configured once: the default sink changes when a dock or a Bluetooth headset appears,
  and a recording that silently captures a disconnected device is the same class of fault as this
  plan's others. The pre-flight sheet states in words which audio the run will capture, since a
  user who assumes wrongly discovers it after the talk.
- **Validation**: `uv run pytest tests/transcription/ tests/api/`; a new test asserting window mode
  defaults to the monitor source and never opens a microphone; manual QA — record a window playing
  audio while speaking aloud, and confirm the transcript contains the window's speech and not the
  spoken words.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Window Capture Repair (5 / 8) Complete: window mode records the machine's output
  by default, the microphone is an explicit opt-in, and the pre-flight sheet says which.`

### Phase 6 — Stop stops

- **Locations**: `web/backend/app/services/session/manager.py` (`stop`, `_teardown`,
  `_stop_window_capture`, `_mux_if_wanted`); `web/backend/app/services/capture/recorder.py`
  (`stop`); `web/frontend/static/js/components/header.js`.
- **Rationale**: Stopping currently waits on the video recorder's settle and the mux before the
  audio is released, so a user who presses stop watches a control that has not yet stopped anything.
  Audio capture is the part that must end at the instant it is asked to — it is the only irreversible
  thing still happening — and the video's finalisation, the remux and the post-capture transcription
  are all work that can proceed after the microphone or monitor has been released. The ordering
  becomes: release audio, publish `stopping`, then finalise video and start the pass.
- **Validation**: `uv run pytest tests/transcription/test_window_session.py tests/api/test_session_toggle.py`;
  a new test asserting the audio source is closed before the recorder is awaited; manual QA — stop a
  window recording and confirm capture ends immediately while the video finalises behind it.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Window Capture Repair (6 / 8) Complete: stopping releases the audio immediately
  and finalises the video behind it.`

### Phase 7 — Timestamps that point at the moment a thing was said

- **Locations**: `web/backend/app/services/recording/batch.py` (window offsets);
  `web/backend/app/services/transcript/` (segment `start` at write time);
  `web/backend/app/services/context/assembler.py` (`_render_segments`, `_render_summaries`,
  `timestamp`); `web/backend/app/services/llm/prompts.py`; new
  `tests/transcription/test_timestamp_alignment.py`.
- **Rationale**: Diagnosis precedes repair, because the three candidate causes have opposite fixes.
  Measure first: transcribe a fixture whose speech occurs at known times, then assert each stored
  segment's `start` lands within a tolerance of the real one — at **both** revisions, since the live
  pass and the post-capture pass compute their origins differently and only one of them can be
  wrong without the other showing it. If storage is correct, the fault is the assistant inventing
  numbers rather than copying the markers it is given, and the repair is in the prompt and in
  validating citations against the store before they are shown. A citation that cannot be resolved
  to a real segment should be dropped rather than displayed, because a wrong timestamp shown
  confidently is worse than no timestamp.
- **Validation**: `uv run pytest tests/transcription/test_timestamp_alignment.py tests/assistant/`;
  manual QA — ask the assistant about something said at a known moment and confirm the citation
  seeks the recording to it.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Window Capture Repair (7 / 8) Complete: stored timestamps are session-relative at
  every revision and a citation that cannot be resolved is dropped rather than shown.`

### Phase 8 — Documentation, decision record, and merge

- **Locations**: `docs/documentation.md` (decision **D-026**); `docs/deployment.md`;
  `docs/checklist.md`; `docs/component-map.md`; `docs/data-flow.md`; this plan's status line;
  `docs/plans/README.md`.
- **Rationale**: D-022 states that audio is the machine's and not the window's; Phase 5 makes that a
  user-facing choice and the decision record must say so rather than leaving the interface and the
  documentation disagreeing. D-026 also records the verification change from Phase 2 — that capture
  is checked by reading the file, not the command — because that is the durable answer to "make it
  so this does not keep recurring", and a future contributor who does not know why will delete the
  slow test.
- **Validation**: full `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and a
  final manual run of a complete window session end to end.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Window Capture Repair (8 / 8) Complete: D-026 records the window-audio choice and
  the output-verified capture tests, and the branch is merged into main.`

---

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Per-branch scaling | Recording and preview scale independently; recorded size stated from the portal's reported source size | `web/backend/app/services/capture/pipeline.py` |
| Output-verified capture tests | Renders a known pattern through the real pipeline and asserts the written file's dimensions, frame count and mean pixel value | `tests/transcription/test_capture_output.py` |
| Capture verification script | Runs one real portal capture and prints what was actually written | `scripts/verify_capture.py` |
| Portal re-entrancy guard | One negotiation per recording; a second attempt while a dialog is open is refused | `web/backend/app/services/session/manager.py` |
| Single-negotiation test | Concurrent start attempts negotiate exactly once | `tests/transcription/test_window_session.py` |
| Live preview wiring | Monitor pane shows the capture and distinguishes "no frame yet" from "not recording" | `web/frontend/static/js/components/recording-monitor.js`, `web/backend/app/routes/capture.py` |
| Monitor source resolver | Resolves the default sink's monitor at start time, so a changed default device is not silently recorded | `web/backend/app/services/audio/monitor.py` |
| Window audio choice | System output / microphone / both, defaulting to system output, stated in the pre-flight sheet | `web/backend/app/config/schema.py`, `web/frontend/static/js/components/preflight.js` |
| Audio-source test | Window mode defaults to the monitor and never opens a microphone | `tests/transcription/test_window_session.py` |
| Immediate stop ordering | Audio released before the video is finalised or the pass starts | `web/backend/app/services/session/manager.py` |
| Timestamp alignment test | A fixture with speech at known times; stored `start` values checked at both revisions | `tests/transcription/test_timestamp_alignment.py` |
| Citation resolution | A citation that cannot be resolved to a real segment is dropped rather than shown | `web/backend/app/services/context/assembler.py` |
| Decision record D-026 | Window audio as an explicit choice; capture verified by output rather than by command | `docs/documentation.md` |
