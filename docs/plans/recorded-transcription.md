# Plan 3 — Toggleable Recorded Transcription

*Created: 2026-08-15 · Status: **complete** (6 / 6 steps)*

Part three of the five-plan expansion. Depends on [Plan 2](multi-mode-ui-implementation.md).

## 1. Introduction

`recorded` mode inverts the live pipeline's trade. Live transcription commits text two to four
seconds behind the speaker because it must decide what is settled while more audio is still arriving;
that latency is the price of seeing words appear. When nobody is reading along, the price buys
nothing. Toggling recording on captures audio to a file and does no inference at all; toggling it off
transcribes the whole file in one pass, with every word of context available to the model, and
commits the result as a finished transcript.

Concretely this means a capture sink that writes the canonical 16 kHz mono stream to disk without
touching the streaming engine, a batch transcription job that reads a finished file and produces
segments, and a run state — `processing` — that the interface already knows how to display because
Plan 1 defined it. The batch pass deliberately does **not** reuse the LocalAgreement commit policy:
agreement exists to decide what is safe to show before the audio has finished arriving, and when the
audio has finished arriving the correct answer is to transcribe it and keep the result.

---

## 2. Gaps & Unanswered Questions

- **Does the recording keep its audio afterwards?** *Assumption*: it is kept until transcription
  succeeds, then deleted unless `storage.retain_audio` is on — the existing setting, which defaults
  to off. Deleting before transcription would make a failed pass unrecoverable; keeping it
  afterwards by default would quietly accumulate hours of a private room's audio on disk under a
  setting the user has turned off. If the pass fails, the file is kept regardless and the failure
  message names its path, because the audio is then the only copy of what was said.

- **Where do recordings live?** *Assumption*: `data/recordings/<session_id>.wav`, a new gitignored
  directory beside `data/sessions/`. Not the existing `data/audio/` library, which holds recordings
  the *file source* replays — a user's uploaded fixtures and the machine's own captures being mixed
  in one list is a way to delete the wrong thing.

- **Is the transcription pass resumable or cancellable?** *Assumption*: neither, in this plan. It
  runs to completion or fails whole. A 40-minute recording at a real-time factor around 1.5 takes
  roughly 27 minutes to transcribe; that is long enough that resumption is worth wanting and not
  worth guessing at before anyone has run one. Recorded in `docs/checklist.md` rather than built.

- **What if the server restarts mid-pass?** *Assumption*: the recording survives, the job does not.
  On the next start, any `data/recordings/*.wav` without a completed transcript is listed as
  unfinished with a button to transcribe it. This falls out of writing the file first and is much
  cheaper than persisting job state.

- **Does VAD run during a recorded capture?** *Assumption*: yes, but only as a meter — the frontend's
  level and speaking indicators stay meaningful, and the recording's speech ratio is worth reporting.
  It gates nothing. Skipping silent stretches at *capture* time would produce a file whose
  timestamps no longer match the clock, and the timestamps are what the transcript is indexed by.

- **How is progress reported for a pass with no natural unit?** *Assumption*: by audio position —
  seconds transcribed over seconds recorded. It is honest, monotonic, and derivable without
  instrumenting the model.

- **Does the assistant work during a recorded capture?** *Assumption*: it is available but has
  nothing to read, and says so. There is no transcript until the pass finishes, and pretending
  otherwise by answering from an empty context produces confident nonsense.

- **What does the header show when a pass is re-run from settings?** *Answered during step 6, by
  running it.* Nothing — it stays idle. A re-run is a background job rather than a session, and the
  run state belongs to the *selected* capture mode: if the selector is on `live`, D-020's map says
  `live` cannot reach `processing`, and forcing it there would put the header into a state its own
  mode declares impossible. The feedback is elsewhere and is sufficient: the settings panel confirms
  the pass started by name, and the transcript pane fills as segments commit. Recorded as a decision
  rather than left as an inconsistency someone later 'fixes' by breaking the map.

---

## 3. Hierarchical Step-by-Step Instructions

### Step 1: The capture sink

- **Locations**: New package `web/backend/app/services/recording/` with `__init__.py` and
  `sink.py` — a `WavSink` opened with a session id, fed canonical float32 frames, converting to
  PCM16 and writing incrementally; exposes bytes written, duration, and a `close()` that finalises
  the RIFF header. New `web/backend/app/paths.py` entry for `data/recordings/`. Configuration:
  `RecordingConfig` in `web/backend/app/config/schema.py` (`max_minutes`, `sample_format`), defaults
  in `web/backend/app/config/defaults.py`, hot-swap class in `web/backend/app/config/hotswap.py`.
  Tests: `tests/transcription/test_recording_sink.py`.
- **Rationale**: the sink is the one piece that must not fail — everything downstream can be retried
  from the file, and nothing can be retried without it. Writing incrementally with a finalised header
  at close means a crash leaves a file that is short rather than a file that is corrupt, and
  `max_minutes` is the guard against a forgotten toggle filling the disk.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/transcription/test_recording_sink.py`, including a test that reads the written file back with
  the standard library `wave` module and confirms sample rate, channel count, and duration. Once
  validated, commit stating: `Recorded Transcription (1 / 6) Complete: Captured audio is written to a
  finalised WAV file with a duration cap and a crash-safe header.`

### Step 2: The batch transcription job

- **Locations**: `web/backend/app/services/recording/batch.py` — reads a finished WAV in windows,
  drives `services/asr/lifecycle.py` directly rather than `services/streaming/engine.py`, feeds the
  resulting word tokens through `services/streaming/segmenter.py`, and yields progress by audio
  position. `web/backend/app/services/recording/job.py` — job identity, state, progress, failure, and
  the single-job-at-a-time rule. Tests: `tests/transcription/test_batch_transcription.py` against the
  mock ASR backend and a generated fixture.
- **Rationale**: reusing the segmenter keeps segment shape identical to the live path, so the store,
  the search index, export, citations, and the polish pass all work on recorded transcripts without
  knowing they were produced differently. Bypassing the agreement policy is the point of the mode.
  Existing hallucination filtering in `AsrLifecycle` applies unchanged, which matters more here — a
  long recording contains more silence than a live talk does.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/transcription`, asserting contiguous segment ids, timestamps that span the fixture's real
  duration, and a progress sequence that is monotonic and ends at 1.0. Once validated, commit
  stating: `Recorded Transcription (2 / 6) Complete: A finished recording is transcribed in one pass
  into the same segment shape the live path produces.`

### Step 3: Wire the mode into the session manager

- **Locations**: `web/backend/app/services/session/manager.py` — a `recorded` branch that starts
  capture and the sink, leaves the streaming engine unstarted, keeps VAD running as a meter, and on
  `stop()` closes the sink, enters `processing`, and runs the batch job on a worker thread before
  reporting the session finished. `web/backend/app/services/session/workers.py` if the job needs its
  own drain. `web/backend/app/routes/session.py` drops the `mode-unavailable` rejection for
  `recorded`. Tests: `tests/transcription/test_recorded_session.py`.
- **Rationale**: the manager already owns "what runs during a session"; a mode is exactly that
  question, so the branch belongs there and nowhere else. Not starting the engine is what makes the
  mode cheap — no inference happens while recording, which is the whole reason a user would choose
  it on a laptop.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/transcription/test_recorded_session.py`, driving a full start → record → stop → transcribe
  cycle over the synthetic source and asserting the transcript store holds the result. Once
  validated, commit stating: `Recorded Transcription (3 / 6) Complete: Recorded mode captures without
  inference and transcribes the whole file when the toggle is switched off.`

### Step 4: The transport events and the HTTP surface

- **Locations**: `web/backend/app/transport/events.py` — `RECORDING_PROGRESS` (coalescing),
  `TRANSCRIPTION_PROGRESS` (coalescing), `TRANSCRIPTION_DONE` and `TRANSCRIPTION_FAILED` (critical);
  `web/backend/app/transport/ws.py` reconnection replay for the current job;
  new `web/backend/app/routes/recordings.py` — `GET /api/recordings` (files with transcript status),
  `POST /api/recordings/{session_id}/transcribe` (run or re-run a pass), `DELETE
  /api/recordings/{session_id}`; `web/backend/app/schemas/api.py` for their shapes;
  `web/backend/app/main.py` registers the router. Tests: `tests/api/test_recordings_routes.py`.
- **Rationale**: a pass long enough to leave the room is a pass that must survive a page reload, so
  its progress belongs on the socket's replay path rather than in a variable in the browser. The
  re-run endpoint is what makes step 1's "the file is kept when the pass fails" actionable rather
  than merely reassuring.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/api`, plus `uv run python scripts/generate_contracts.py` with the regenerated contracts
  committed. Once validated, commit stating: `Recorded Transcription (4 / 6) Complete: Recording and
  transcription progress stream over the socket and survive a reload, with endpoints to list, re-run,
  and delete recordings.`

### Step 5: The interface

- **Locations**: `web/frontend/static/js/stores/mode.js` (adopt `processing` from the new events);
  new `web/frontend/static/js/stores/recording.js` (bytes, duration, job progress);
  `web/frontend/static/js/transport/events.js` (the four new event names);
  `web/frontend/static/js/components/header.js` (the `processing` state, its progress, its disabled
  rule); `web/frontend/templates/partials/transcript/empty_state.html` and
  `web/frontend/static/js/components/transcript-pane.js` (the recorded-mode copy and the progress
  card); `web/frontend/static/css/components/record-control.css`;
  `web/frontend/static/js/main.js` wiring. Optionally a list of unfinished recordings in the
  settings modal's storage tab, `web/frontend/templates/partials/settings/storage.html`.
- **Rationale**: the run state and its progress were specified in Plan 1 and the control was built in
  Plan 2, so this step is mostly connecting real numbers to states that already render. The
  unfinished-recordings list is where step 4's re-run endpoint becomes reachable without a terminal.
- **Action**: Undergo the verification/tests/validation process for this phase — manual browser QA: a
  real toggle-on/toggle-off cycle with a microphone, the progress bar advancing, the transcript
  appearing whole, a mid-pass page reload that resumes showing progress, plus a 320 px and a
  keyboard-only pass. Once validated, commit stating: `Recorded Transcription (5 / 6) Complete: The
  interface records on a toggle, shows the pass's progress, and renders the finished transcript.`

### Step 6: Retention, cleanup, and the documentation

- **Locations**: `web/backend/app/services/recording/sink.py` and `job.py` (delete the WAV on success
  unless `storage.retain_audio`, always keep it on failure);
  `web/frontend/templates/partials/settings/storage.html` (the plain-language implication at the
  point of use); `docs/documentation.md` (Decision **D-021**), `docs/architecture.md`,
  `docs/data-flow.md`, `docs/api-contract.md`, `docs/routes.md`, `docs/structure.md`,
  `docs/component-map.md`, `docs/workflow.md`, `docs/checklist.md`; `.env.example` if any variable is
  added.
- **Rationale**: the retention rule is a privacy decision, not a housekeeping one, and the existing
  documentation states that audio retention is off by default — shipping a mode that writes hours of
  audio to disk without reconciling that statement would leave two contradictory sources of truth,
  which the repository rules forbid outright.
- **Action**: Undergo the verification/tests/validation process for this phase — the full suite,
  `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and an inspection
  confirming the WAV is gone after a successful pass with retention off and present after a forced
  failure. Once validated, commit stating: `Recorded Transcription (6 / 6) Complete: Captured audio is
  deleted once transcribed unless retention is on, kept when a pass fails, and D-021 records why.`

---

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Capture sink | Incremental PCM16 WAV writer with a duration cap and crash-safe finalisation | `web/backend/app/services/recording/sink.py` |
| Batch job | Whole-file transcription bypassing the agreement policy, progress by audio position | `web/backend/app/services/recording/batch.py` |
| Job state | Identity, progress, failure, the one-at-a-time rule | `web/backend/app/services/recording/job.py` |
| Recorded mode wiring | Capture without inference; transcribe on stop | `web/backend/app/services/session/manager.py` |
| Recording routes | List, re-run, delete | `web/backend/app/routes/recordings.py` |
| New events | `recording.progress`, `transcription.progress`, `transcription.done`, `transcription.failed` | `web/backend/app/transport/events.py` |
| Recording store | Bytes, duration, and job progress on the client | `web/frontend/static/js/stores/recording.js` |
| Processing UI | Progress in the record control and a card in the transcript pane | `web/frontend/static/js/components/header.js`, `.../transcript-pane.js` |
| Sink tests | Round-trips a written file through `wave`; asserts rate, channels, duration, cap | `tests/transcription/test_recording_sink.py` |
| Batch tests | Segment ids, timestamps, monotonic progress, hallucination filtering on silence | `tests/transcription/test_batch_transcription.py` |
| Session tests | Full record → stop → transcribe cycle over the synthetic source | `tests/transcription/test_recorded_session.py` |
| Route tests | List, re-run, delete, and the socket replay of an in-flight job | `tests/api/test_recordings_routes.py` |
| Decision D-021 | Why the batch pass skips agreement, and the retention rule | `docs/documentation.md` |
