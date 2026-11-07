# Recordings on the Main Screen: Ask About Any Past Recording

*Written 2026-11-07, revised the same day with the user's answers. Status: **In progress (5 / 6)**.
Runs **alongside** [unified-record-sheet.md](unified-record-sheet.md), which is being implemented at
the same time in the main checkout. This plan is built in a separate worktree
(`../TranscriberPrototype-recordings`, branch `feature/recordings-on-main-screen`), so the two never
share a working tree. That plan holds **D-075**. This one uses **D-076 to D-079**. If the other
plan has taken any of those by the time this merges, renumber here.*

## 1. Introduction

When a recording ends, its transcript and its chat stay on the main screen. They stay because
`SessionManager` keeps the finished session's `TranscriptStore` open as `_last_store` (D-031). At
start-up the newest finished session is reopened the same way (D-041). Everything on the main
screen reads through `SessionManager.store`:

- the assistant's `store_provider`;
- the transcript readers in `routes/transcript.py`;
- `state()`.

Once another recording starts, the old one can only be reached from the Recordings page
(`/sessions`). That page can export a recording, but you cannot ask questions about it there.

This plan adds **bringing a past recording back to the main screen**, so you can ask the chat pane
on the right about it. Opening a recording puts its store into the retained slot, and every
existing reader then works unchanged. The main screen shows a banner, *"Viewing <title> ·
<date>"*, with **Close**. The recording's earlier conversation is restored. New questions continue
it and are saved to the same file.

**Only a recording with a transcript can be opened.** A recording that has audio or video but no
transcript can be **transcribed, but only once the user approves it**. Nothing is processed
automatically. For a video, its sound is extracted to a WAV first. Then the same batch pass that a
`recorded` session uses transcribes it. **Recording folders that have media but no transcript
database at all**, which are not listed today, are listed and can be transcribed the same way. A
recording with nothing usable cannot be opened, and the button says why. Examples are a video with
no sound track, or an empty folder.

The Recordings page also gets **one recording window** in place of the row's separate **Export**
link, **Conversation** link and **Export…** button, because two buttons with the same label were
reported as confusing. The window holds a summary of what the recording contains, *Bring to main
screen* or *Transcribe…*, every export, and *Delete*. The web-app export's preview, presets and
progress move into that same window as one section, not a second dialog.

## 2. Gaps & Unanswered Questions

### Answered by the user (2026-11-07)

- **No transcript**: it cannot be opened as it is. Offer to transcribe it. A video has its audio
  extracted first.
- **Approval**: **nothing is processed without the user approving it.** The consent dialog says
  exactly what will run: extraction if needed, then transcription. Nothing else runs, so there is
  no automatic summarising, polishing or glossary building. The glossary and summaries keep being
  built on demand when a question is asked, as for any recording.
- **Entry points**: all three. A row button, the recording window, and a picker on the main
  screen.
- **Main screen while viewing**: a banner and **Close**. No media player.
- **Chat history**: restore the old conversation and continue it.
- **Exports**: one self-contained window.
- **After a restart**: a viewed recording does **not** come back. The main screen behaves as it
  does today, which means D-041's newest-recording reopen still applies with no banner.
- **Media folders with no transcript database**: listed and processable **in this plan**.

### Coordination with the unified-record-sheet plan (URS)

Both plans are in progress at once, so this one avoids the files URS rewrites and keeps its edits
to shared files small:

- **`services/session/manager.py` (796 / 800 lines), which URS Step 2 changes heavily.** This plan
  adds **one line** to it: the `viewing` key in `state()`. All other new behaviour goes in the
  `TranscriptionPassMixin` (`services/session/passes.py`), which already owns retention. That
  includes adopting a store, the `_viewing` description, and clearing it in `_release_retained`,
  which `start()` already calls.
- **The mode vocabulary (`modes.py`, `core/modes.js`)**: never touched. Viewing a recording is not
  a run state.
- **The header, the record sheet and the pre-flight sheet**: never touched. The banner sits above
  the transcript pane. It says *"Starting a new recording closes this view"*, so the record sheet
  does not need to say it.
- **`static/js/main.js`, which URS Step 4 splits.** This plan only adds a construction of the
  banner and picker, plus a re-hydrate call, next to the existing `hydrate()`.
- **`docs/*.md`**: entries are appended, not restructured. Before the final merge, rebase onto
  `main` and resolve the decision-log and checklist tables by keeping both sides' rows.

### Assumptions (simple gaps)

- **Opening is refused while a session is recording or a transcription is running** (409), with
  the reason shown on the disabled buttons. This keeps one writer per store and one user of the
  speech model.
- **Delete removes everything.** Today `DELETE /api/sessions/{key}` removes only the `.db` and
  leaves the recording folder. Once media-only folders are listed, every deleted recording would
  come back as a media-only row. *Assumption:* delete removes the database **and** the recording
  folder. The confirmation names exactly what will go (transcript, chat, video, audio). A
  media-only row's delete removes its folder. If the deleted recording is the one held in the
  retained slot, that store is released first.
- **A media-only folder's transcription** creates `<session_dir>/<key>.db`, using the folder's key,
  so the two stay joined (D-032). Its `started_at` comes from the key, and its mode is `recorded`.
- **Extracted audio** is written to the folder's `audio.wav`, as 16 kHz mono PCM, which is what
  the batch pass reads. It is deleted after a successful pass when audio retention is off, because
  the video still holds it.
- **A transcript-less recording that has a resumable checkpoint**: the dialog offers *resume*,
  using the existing `/transcribe/resume`, not a fresh pass.
- **Opening when the pass finishes** is a checkbox in the consent dialog, on by default. It is the
  user's choice, not processing.
- **The recording window also opens when a recording stops** (D-037). It is the same component,
  showing the same sections. Its *Bring to main screen* section reads "On the main screen now".

### Complex gaps

None outstanding.

## 3. Hierarchical Step-by-Step Instructions

Read before starting: `docs/skills/global-project-rules/SKILL.md`, `docs/architecture.md`,
`docs/data-flow.md`, `docs/component-map.md`, `docs/api-contract.md`, `docs/routes.md`,
`docs/design-system.md`, and the D-031, D-037 and D-041 rows in `docs/documentation.md`.

### Step 1: The plan and the design specification

- **Locations**: this plan; `docs/plans/README.md` (index row); `docs/design-system.md` (new
  § Recordings and the Main Screen); `docs/documentation.md` (decision rows **D-076**, a chosen
  recording is moved into the retained slot, and **D-077**, one recording window replaces the
  row's export controls).
- **What the spec holds**:
  - **Eligibility**, from `SessionMedia` (`services/transcript/archive.py`) and `RecordingLayout`
    (`services/recording/layout.py`). Each row of the table is *open*, *transcribe on approval*,
    *resume on approval*, or *refused, with reason text*.
  - **The recording window's sections**, in order: summary, Ask, Transcript, Conversation, Web app,
    Delete.
  - **The row**, which shows: media chips, **Bring to main screen** or **Transcribe…**, **Open…**,
    and **Delete**.
  - **The banner, the picker, and the consent dialog's copy.**
- **Verification**: a documentation review. Every eligibility row must have a result and copy.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: *Recordings on the Main Screen (1 / 6) Complete: Planned and specified bringing
  past recordings back to the main screen, approval-gated transcription, and one recording window.*

### Step 2: One recording window (frontend)

- **Locations**:
  - `web/frontend/static/js/components/export-dialog.js` is renamed to `recording-window.js`
    (class `RecordingWindow`). It gains a third face, *overview*, next to *choosing* and
    *watching*. The web-app export is reached from the overview's *Web app* section, inside the
    same modal.
  - `web/frontend/static/js/components/recording-overview.js` (**new**) builds the overview
    sections from a session row. The transcript format and version logic moves here from
    `sessions.js`.
  - `templates/partials/export.html` is renamed to `recording_window.html`, with the overview
    container added. Its includes are updated in `templates/pages/sessions.html` and
    `templates/pages/app.html`.
  - `web/frontend/static/js/sessions.js`, `SessionsPage.actions()`: the row is reduced to
    **Open…** and **Delete**.
  - `static/js/main.js`: the import path of the window that opens on stop.
  - CSS under `static/css/components/` for the overview.
- **Rationale**: this removes the duplicate Export labels before new actions crowd the row. It has
  no backend dependency.
- **Tests**: `tests/frontend/test_recording_window.py`. Both pages render the one window, no two
  footer actions share a name, nothing loads `export-dialog.js`, and the row carries no exports.
  The web-app export's internal `data-export-*` hooks are kept: they still describe that face.
- **Done (2026-11-07)**: shared descriptions moved to `static/js/core/recording-facts.js`; the
  overview styles live in `export.css` so `base.html`, which URS edits, is untouched. Focus is kept
  inside the window when Back hides itself, and returns to the row's Open… on close.
- **Verification**: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`. Then
  browser QA on `/sessions`: open the window for a transcript-only recording and for a complete
  one, download each export, check that Escape returns focus, and check a 375 px width.
- **Docs updated**: `component-map.md`, `structure.md`, `routes.md` if needed.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: *Recordings on the Main Screen (2 / 6) Complete: Every export, the preview and
  delete now live in one recording window instead of several Export buttons.*

### Step 3: Open, close and delete (backend)

- **Locations**:
  - `services/session/passes.py`: `view_recording(path, key, title, started_at)`,
    `close_viewing()`, and the `viewing` property. `_release_retained` clears `_viewing`.
    `release_if_holding(path)` is added for delete.
  - `services/session/manager.py`: one line, `"viewing": self.viewing` in `state()`.
  - `schemas/api.py`: `SessionResponse.viewing`.
  - `routes/sessions.py`:
    - `POST /api/sessions/{key}/open` returns 409 for `session-running`, `transcription-running`
      and `needs-transcription`, and 404 for an unknown key. It emits `session.viewing`.
    - `POST /api/sessions/close`.
    - `DELETE /api/sessions/{key}` releases the store if it is held, then removes the database and
      the recording folder.
    - *As built:* open, close and delete live in a new `routes/session_actions.py` (delete moved out
      of `sessions.py`), which keeps `sessions.py` at 613 lines and gives Step 4's routes a home.
    - The `session.viewing` event is registered in `transport/events.py` (critical),
      `transport/events.js` and `scripts/generate_contracts.py`; contracts regenerated.
- **Tests**:
  - `tests/api/test_open_recording.py` (**new**): open, then transcript and chat read from it; the
    three refusals; close; delete while held; and start releasing it.
- **Verification**: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
  `manager.py` stays under 800 lines.
- **Docs updated**: `api-contract.md`, `data-flow.md`, `architecture.md`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: *Recordings on the Main Screen (3 / 6) Complete: A past recording with a
  transcript can be opened into the main screen and closed again, and delete removes all of it.*

### Step 4: Media-only folders and approval-gated transcription (backend)

- **Locations**:
  - `services/transcript/archive.py`:
    - `list_sessions` also yields a row for each recording folder with no database, marked
      `orphan` and with no transcript fields. Its title and start time come from the key.
    - `SessionMedia` gains `openable`, `transcribable` and `reason`. A video with no WAV and no
      muxed file is probed for an audio stream.
  - `services/recording/extract.py` (**new**): `extract_audio(video, wav)` uses the ffmpeg/ffprobe
    helpers in `services/capture/mux.py`. It never raises, and it refuses a video with no audio
    stream.
  - *As built:* the routes live in `routes/session_actions.py` beside open/close/delete, and the
    planning and starting in `services/session/past_pass.py`. **ffmpeg is not installed on the
    development machine**, so extraction and probing use PyAV (already present through
    `faster-whisper`, now a declared dependency) rather than the `capture/mux.py` helpers. The
    model is loaded by the approval if it is not loaded. Extraction tests are in
    `tests/transcription/test_extract_audio.py`, there being no `tests/recording/` area.
  - `routes/sessions.py`, or a new `routes/session_transcribe.py`:
    - `GET /api/sessions/{key}/transcribe/plan` returns the source (`wav`, `video` or `resume`),
      its duration, whether a model is loaded, and any refusal.
    - `POST /api/sessions/{key}/transcribe` takes `{open_when_done}`. It extracts audio if needed,
      opens or creates the database for that key, and starts a `TranscriptionRunner` with the same
      guards as `/transcribe/resume`. On `on_released`, it opens the recording when asked to.
- **Tests**:
  - `tests/recording/test_extract_audio.py` (**new**): a generated tone video produces a 16 kHz
    WAV, and a video with no sound is refused. Skipped when ffmpeg is absent.
  - `tests/api/test_transcribe_recording.py` (**new**, mock ASR): the orphan listing, the plan
    endpoint, a WAV-only pass that produces segments, an orphan that gets a database, auto-open,
    and the 409 guards.
- **Verification**: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
- **Docs updated**: `api-contract.md`, `data-flow.md`, `structure.md`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: *Recordings on the Main Screen (4 / 6) Complete: Media-only recordings are listed,
  and any recording without a transcript can be transcribed once the user approves.*

### Step 5: Main screen, row button and consent dialog (frontend)

- **Locations**:
  - New components in `static/js/components/`:
    - `viewing-banner.js`, with the partial `templates/partials/viewing_banner.html`;
    - `recording-picker.js`;
    - `transcribe-consent.js`, with the partial `templates/partials/transcribe_consent.html`.
  - `transport/api.js`.
  - `stores/session.js`, so that `viewing` is hydrated.
  - `main.js`, which constructs these and re-runs `hydrate()` after an open or a close, and on a
    `session.viewing` event.
  - `sessions.js` and `recording-overview.js`, which wire up the row and window buttons.
- **Tests**: `tests/frontend/test_viewing_markup.py` checks the banner and consent markup: a named
  Close, a dialog heading, and the checkbox label.
- **Done (2026-11-07)**: the main-screen wiring is its own module, `static/js/viewing.js`, so
  `main.js` (which URS Step 4 splits) gains only a `reloadForView` and one call. "Open recording…"
  is a template button in the transcript toolbar and the empty state, wired by the picker without
  touching `transcript-pane.js`. The pane's reset now also resets its segment count, which had
  stayed at the old figure after Close. Default and media-only titles are now in local time, as
  every other date on the page is. Styles in `static/css/components/viewing.css`.
- **Verification**: `uv run pytest`, `uv run ruff check .`. Then browser QA with the mock ASR:
  1. Open a recording from the row, the window and the picker. Check the old chat is shown, then
     ask a question.
  2. Close it.
  3. Transcribe an audio-only recording and an orphan through the consent dialog, and check it
     opens itself.
  4. Reload while viewing.
- **Docs updated**: `component-map.md`, `routes.md`, `data-flow.md`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: *Recordings on the Main Screen (5 / 6) Complete: Past recordings can be brought
  back to the main screen from the row, the window or the picker, and asked about.*

### Step 6: Close-out and merge

- **Locations**: `docs/checklist.md`, this plan's status, `docs/plans/README.md`, and a sweep for
  `ExportDialog`, `export-dialog.js` and `data-export` leftovers.
- **Verification**: the full `uv run pytest`, `uv run ruff check .` and
  `uv run ruff format --check .`. Then rebase onto `main`, re-run everything, merge into `main` with
  `--no-ff`, and run the suite again on `main`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: *Recordings on the Main Screen (6 / 6) Complete: Documented, verified and merged
  bringing past recordings back to the main screen.*

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Spec and decisions | Eligibility, window, banner, picker and consent; D-076 and D-077 | `docs/design-system.md`, `docs/documentation.md` |
| Recording window | One modal with three faces: overview, choosing, watching | `static/js/components/recording-window.js`, `recording-overview.js`, `templates/partials/recording_window.html` |
| Viewing in the retained slot | Adopt, close, release-on-delete, and `viewing` on state | `services/session/passes.py` |
| Open / close / delete routes | `POST /{key}/open`, `POST /close`, and delete removing the folder too | `routes/sessions.py` |
| Orphan listing and eligibility | Media-only folders listed; `openable`, `transcribable` and `reason` | `services/transcript/archive.py` |
| Audio extraction | A video's sound to `audio.wav`, probed first | `services/recording/extract.py` |
| Approval-gated transcription | Plan and start routes, with `open_when_done` | `routes/session_transcribe.py` |
| Banner, picker, consent | The main-screen pieces | `static/js/components/viewing-banner.js`, `recording-picker.js`, `transcribe-consent.js` |
| Open-recording tests | Open, refusals, close, delete, and start | `tests/api/test_open_recording.py` |
| Transcription tests | Orphans, plan, pass, auto-open, guards (mock ASR) | `tests/api/test_transcribe_recording.py` |
| Extraction tests | A small generated video, and the no-sound refusal | `tests/recording/test_extract_audio.py` |
| Frontend markup tests | Window, banner and consent markup | `tests/frontend/` |
