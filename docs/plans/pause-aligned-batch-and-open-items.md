# Pause-Aligned Batch and Open Items

*Status: **In progress (1 / 7)** — started 2026-09-08*

| Step | Topic | Status |
|---|---|---|
| 0 | Land the D-061 dictation work and fix the machine-dependent test failure | Complete |
| 1 | The batch pass cuts where the speaker paused, and the checkpoint records those cuts | Pending |
| 2 | A dictation's tidy runs while the next chunk transcribes | Pending |
| 3 | Every script repairs the GPU wheel the way `app.py` does | Pending |
| 4 | `segments_in_range` and `search` read one pass, never the union | Pending |
| 5 | An export can name its revision, from the API and from the page | Pending |
| 6 | Close the checklist entries, record the decisions, merge | Pending |

---

### 1. Introduction

`docs/checklist.md` carries thirty-eight unchecked boxes. Most are manual verification on the
user's own hardware, tuning that needs real use, or decisions recorded as considered. Six are code
that can be written and verified headlessly, and this plan does those six, in the order that keeps
each step independently committable.

The largest is the one D-061 named as it closed: a dictation is now cut where the speaker paused,
but the batch pass for `recorded` and `window` sessions still walks fixed thirty-second windows
with a one-second overlap reconciled by word timestamp — the exact mechanism measured losing a
word at every boundary. `plan_chunks` already exists; the pass is moved onto it and the resume
checkpoint (D-045) records pause-aligned boundaries instead of a step grid. The rest are smaller:
overlapping the dictation's tidy with its transcription, a bootstrap so a bare `uv run` of a script
no longer breaks GPU transcription, scoping two store reads to one pass, and letting an export name
its revision. Step 0 comes first because the D-061 work is uncommitted in the tree and one test in
the suite fails on this machine for a reason unrelated to it.

---

### 2. Gaps & Unanswered Questions

- **What happens to `recording.batch_overlap_s`.** A chunked pass has no overlap. *Assumption:* the
  key is removed from the schema. `ConfigStore.load` currently discards the **whole** user file
  when it holds a key the schema does not know (`extra="forbid"`), so retiring a key without a
  prune would silently reset every setting on the next start. The loader gains a short list of
  retired keys it drops with a log line before validating.
- **What `recording.batch_window_s` now means.** *Assumption:* it stays, and becomes the longest
  chunk the model is handed, cut at a pause where one exists. The name is kept because the settings
  panel, the config file and the checklist all use it; the docstring and the panel hint change.
  Default stays at thirty seconds: a pause or a cancel holds at a chunk boundary, and a longer cap
  makes that wait longer on a CPU running at about two times real time.
- **Which detector finds the pauses in a recorded session.** *Assumption:* the configured one,
  through `build_detector(config.vad)`, the same as dictation. Tests hand the runner the energy
  detector so they do not depend on the Silero model.
- **Whether the companion should also repair the wheel.** *Assumption:* yes. The repair is cheap
  when there is nothing to do and guarded on every failure, so the `--no-sync` in `radish` becomes
  belt and braces rather than the only thing keeping the GPU working.
- **How the export page exposes a revision.** *Assumption:* a second select beside the format
  select, shown only for a session that holds two passes, labelled the way the transcript pane
  labels them: Live and Final. The live page's export follows whichever pass the pane shows.
- **The GPU path itself.** Real-model verification of any of this on the GPU is still owed and is
  not attempted here; the machine's own configuration is `base` on the CPU (D-053).

---

### 3. Hierarchical Step-by-Step Instructions

#### Step 0: Land the D-061 work and fix the machine-dependent failure
- **Locations**: the uncommitted tree — `web/backend/app/services/dictation/pipeline.py`,
  `web/backend/app/services/recording/chunks.py`, `web/backend/app/services/vad/pauses.py`,
  `tests/transcription/test_pause_chunking.py`, and the modified files beside them;
  `tests/transcription/test_asr_contract.py::TestWhisperVadFilter.call_args`.
- **Rationale**: the pause-chunking work is complete and documented but not committed, and every
  later step builds on `plan_chunks`. Separately, `TestWhisperVadFilter` builds a backend with the
  shipped defaults — `small`, `int8`, `device="auto"` — which D-053 refuses on an AMD machine
  before the load, so three of its tests fail here and pass elsewhere. They are about the
  `vad_filter` argument, not the device, so they pin the CPU.
- **Verification**: `uv run --no-sync pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
- **Docs**: none beyond what the D-061 change already carries.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Pause-Aligned Batch and Open Items (0/6) Complete: A dictation is cut where the
  speaker paused (D-061), and the decoder-filter tests pin the CPU so they pass on an AMD machine.

#### Step 1: The batch pass cuts at pauses, and the checkpoint records those cuts
- **Locations**: `web/backend/app/services/recording/batch.py` (`transcribe_file`, `plan_windows`
  → `plan_recording`, `Window` → `Chunk` from `chunks.py`, `_absolute_words`);
  `web/backend/app/services/recording/runner.py` (`TranscriptionRunner.__init__`, `_run`);
  its three callers — `web/backend/app/services/session/passes.py`,
  `web/backend/app/routes/recordings.py`, `web/backend/app/routes/sessions.py`;
  `web/backend/app/config/schema.py` (`RecordingConfig`), `web/backend/app/config/store.py`
  (`ConfigStore.load`, a `RETIRED_KEYS` prune); `web/frontend/templates/partials/settings/storage.html`
  (the window hint); `web/backend/app/services/recording/__init__.py` exports.
- **Rationale**: `plan_recording` runs `find_pauses` over the whole file with the configured
  detector and `plan_chunks` with `batch_window_s` as the cap, then drops every chunk ending at or
  before `start_s` — so a resume begins on a chunk boundary exactly as it began on a window
  boundary, and the trim in `_on_window_start` keeps working unchanged. Chunks tile the file, so
  `_absolute_words` only rebases and no longer drops an overlap. A chunk that ends in a pause tells
  the segmenter so through `pause_boundary`, which is the truth the old comment said it lacked.
  The runner takes a `detector_factory` and `chunk_s`/`min_pause_ms` in place of
  `window_s`/`overlap_s`.
- **Tests**: `tests/transcription/test_batch_transcription.py` — the window tests become chunk
  tests (cover the whole file, tile without overlap, a silent file is one chunk, a resume snaps to a
  boundary); `tests/transcription/test_pass_resume.py` — `plan_windows` calls become
  `plan_recording`; `tests/transcription/test_timestamp_alignment.py` — the overlap test becomes
  "no word is emitted twice across a chunk seam"; a new test that a stale `batch_overlap_s` in a
  config file is dropped rather than the file ignored, in `tests/api/test_config_persistence.py`.
- **Verification**: `uv run --no-sync pytest`, `ruff check`, `ruff format --check`.
- **Docs**: `docs/data-flow.md` (the batch pass and the checkpoint), `docs/structure.md`
  (`batch.py` line), `docs/documentation.md` (D-062), `docs/checklist.md` (the entry under
  Discovered work, and `batch_window_s` in Part 3e).
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Pause-Aligned Batch and Open Items (1/6) Complete: The batch pass cuts a
  recording where the speaker paused and checkpoints at those cuts.

#### Step 2: A dictation's tidy runs while the next chunk transcribes
- **Locations**: `web/backend/app/services/dictation/pipeline.py` (a new
  `transcribe_and_tidy` that transcribes on the calling thread and tidies on a worker consuming a
  queue in order, keeping `tidy_one`'s give-up rule); `web/backend/app/services/dictation/service.py`
  (`_deliver`, `_transcribe`); `tests/transcription/test_dictation.py`.
- **Rationale**: the speech model and the language model are different resources. On a long
  dictation the tidy of chunk one waiting for the transcription of chunk ten is pure latency. The
  state reads `transcribing` until the last chunk is decoded and `tidying` while the tail drains,
  and the timings record both halves plus the overlap saved.
- **Tests**: with a fake model and a fake tidy that each sleep, three chunks complete in less than
  the sequential sum; the texts come back in order; a timed-out tidy still stops the chunks after
  it; the existing dictation tests are unchanged in outcome.
- **Verification**: `uv run --no-sync pytest tests/transcription/test_dictation.py` then the full
  suite, `ruff`.
- **Docs**: `docs/structure.md` (`pipeline.py` line), `docs/documentation.md` (D-063),
  `docs/checklist.md`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Pause-Aligned Batch and Open Items (2/6) Complete: A dictation tidies each chunk
  while the next one transcribes.

#### Step 3: Every script repairs the GPU wheel the way `app.py` does
- **Locations**: new `scripts/_bootstrap.py` (`prepare()`: put `web/backend` on `sys.path`, call
  `acceleration.repair_kept_wheel` guarded, print its message); every `scripts/*.py` that inserts
  the backend path replaces that with the import; `scripts/benchmark_asr.py` drops its own call;
  `web/backend/app/companion/main.py` calls the repair at the top of `main`; `scripts/radish` keeps
  `--no-sync` and its comment says why it is now belt and braces.
- **Rationale**: a bare `uv run python scripts/x.py` re-syncs against a lockfile that says PyPI,
  swaps the ROCm build of CTranslate2 out, and the server's next model load fails. `app.py` repairs
  that on its own launch and nothing else does. One module every script passes through is the
  place the checklist entry asked for.
- **Tests**: `tests/utils/test_script_bootstrap.py` — `prepare` calls the repair once and prints
  its message; every file under `scripts/*.py` imports `_bootstrap` (a textual check, so a new
  script cannot fall off the list); the companion's `main` calls the repair before it does anything
  else, with the repair patched out.
- **Verification**: `uv run --no-sync pytest tests/utils`, full suite, `ruff`.
- **Docs**: `docs/workflow.md` (the `--no-sync` paragraph), `docs/structure.md` (the new file),
  `docs/checklist.md`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Pause-Aligned Batch and Open Items (3/6) Complete: Every script and the
  companion put the ROCm wheel back on launch, so a bare `uv run` no longer breaks the GPU.

#### Step 4: `segments_in_range` and `search` read one pass
- **Locations**: `web/backend/app/services/transcript/store.py` (`segments_in_range`, `search`,
  both gaining `revision: int | None = None` meaning the latest); `web/backend/app/routes/transcript.py`
  (`/range` and `/search` accept `revision`); `tests/data/test_transcript_revisions.py`.
- **Rationale**: the context, polish and chat paths read through these, and a session reopened for
  reading after a second pass would hand the assistant both passes as one transcript — the same
  union D-022 removed from the export. Not reachable today; reachable the day a two-pass session is
  reopened, which D-041 already does for the newest session.
- **Tests**: a store holding two passes returns only the latest from a range query and a search;
  an explicit revision returns that one; a single-pass store is unchanged.
- **Verification**: `uv run --no-sync pytest tests/data tests/assistant tests/api`, full suite, `ruff`.
- **Docs**: `docs/api-contract.md` (the revisions paragraph), `docs/routes.md`, `docs/checklist.md`
  (Part 3g), `scripts/generate_contracts.py` re-run.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Pause-Aligned Batch and Open Items (4/6) Complete: Range and search reads serve
  one transcription pass, never the union.

#### Step 5: An export can name its revision
- **Locations**: `web/backend/app/routes/transcript.py` (`/export?revision=`),
  `web/backend/app/routes/sessions.py` (`/{key}/export?revision=`, `/{key}` returning
  `revisions`), `web/backend/app/services/transcript/archive.py` (`ArchivedSession.revisions`);
  `web/frontend/static/js/transport/api.js` (`exportUrl`, `sessionExportUrl` take a revision);
  `web/frontend/static/js/sessions.js` (a version select beside the format select, shown only
  when there are two passes); `web/frontend/static/js/components/settings/misc.js` (the live
  page's export follows the pane's revision); `tests/api/test_recordings_routes.py` or a new
  `tests/api/test_export_revisions.py`; `tests/frontend/test_rendered_accessibility.py` still
  passes.
- **Rationale**: both checklist entries (Part 3g and Part 3i) say the same thing — the export
  serves the latest pass and there is no way to ask for the live one — and both name the same
  remedy, a revision control on the recordings page. An unknown revision is a 404 with a code, not
  an empty file.
- **Tests**: an export of revision 0 from a two-pass store contains the live words and not the
  final ones; no parameter still means latest; an unknown revision is `404 unknown-revision`; the
  listing reports `revisions`.
- **Verification**: `uv run --no-sync pytest`, `ruff`, `scripts/generate_contracts.py`, and manual
  browser QA on the recordings page — the select appears only for a two-pass session, the link
  follows it, keyboard-only pass, 320 px viewport.
- **Docs**: `docs/routes.md`, `docs/api-contract.md`, `docs/component-map.md` if the page's
  ownership text names the controls, `docs/documentation.md` (D-064), `docs/checklist.md` (both
  entries).
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Pause-Aligned Batch and Open Items (5/6) Complete: An export names its revision,
  and the recordings page offers the choice where there is one.

#### Step 6: Close out and merge
- **Locations**: `docs/checklist.md` (last-updated line, the Discovered work entries),
  `docs/plans/README.md` (status), this plan's status table, `docs/documentation.md` (last-updated
  line and status row).
- **Rationale**: the plan is the handoff artefact; a plan whose status table says "in progress"
  after the work landed misleads the next reader.
- **Verification**: full `uv run --no-sync pytest`, `ruff check`, `ruff format --check`, then merge
  the feature branch into `main` and re-run the suite on `main`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Pause-Aligned Batch and Open Items (6/6) Complete: Checklist, plan and decision
  log closed out; branch merged into main.

---

### 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Pause-aligned batch pass | `transcribe_file` walks pause-bounded chunks, no overlap, checkpoint per chunk | `web/backend/app/services/recording/batch.py` |
| Runner on chunks | `TranscriptionRunner` takes a detector and a chunk cap | `web/backend/app/services/recording/runner.py` |
| Retired config keys pruned | A stale `batch_overlap_s` no longer discards the user's whole config | `web/backend/app/config/store.py` |
| Batch tests | Chunks tile, resume snaps to a boundary, no word emitted twice | `tests/transcription/test_batch_transcription.py`, `test_pass_resume.py`, `test_timestamp_alignment.py` |
| Overlapped dictation pipeline | Tidy of chunk *n* while chunk *n+1* transcribes | `web/backend/app/services/dictation/pipeline.py` |
| Overlap tests | Faster than sequential, in order, give-up preserved | `tests/transcription/test_dictation.py` |
| Script bootstrap | One import every script passes through | `scripts/_bootstrap.py` |
| Bootstrap tests | Repair called once; every script imports it | `tests/utils/test_script_bootstrap.py` |
| Single-pass reads | `segments_in_range` and `search` scoped to a revision | `web/backend/app/services/transcript/store.py` |
| Revision read tests | Two-pass store returns one pass | `tests/data/test_transcript_revisions.py` |
| Revision-scoped export | `revision=` on both export routes, `revisions` in listings | `web/backend/app/routes/transcript.py`, `routes/sessions.py`, `services/transcript/archive.py` |
| Version select | Beside the format select, only for two-pass sessions | `web/frontend/static/js/sessions.js`, `transport/api.js` |
| Export tests | Named revision, default, unknown | `tests/api/test_export_revisions.py` |
| Decision log entries | D-062 through D-064 | `docs/documentation.md` |
