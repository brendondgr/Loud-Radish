# Recording Folders, Transcription Completion, and the HTML Web-App Export

*Status: in progress — phases are marked complete only once their validation has run.*
*Owner: this plan is the handoff artifact. Resume from the phase table at the bottom.*

## 1. Introduction

Five reported problems, one underlying theme: a recording is a **set of artefacts** — audio, video,
preview frame, measurement sidecar, transcript — and the application has never treated it as one.
The files were dumped flat into `data/recordings/`, the transcript lived somewhere else entirely,
and nothing in the interface could say what a past session actually contained. This plan makes the
recording the unit: one directory per recording, named by the moment it started, holding everything
that recording produced.

Two of the five are bugs rather than design gaps. The post-capture transcription pass leaves the
interface wedged on "Transcribing… 100%", and a reload leaves the session clock ticking forever
under a button that says "Start recording" — both traced to the same root cause, a *coalescing*
progress event replayed from the hub's `latest` cache to every client that connects, for the rest
of the process's life. The past-sessions list reports zero words because the test suite writes its
sessions into the developer's real `data/sessions/`, burying the genuine ones under hundreds of
empty files.

The fifth is new work: exporting a finished recording as a self-contained HTML web application —
video, synchronised transcript, and a question-and-answer panel that talks to a local language model
directly from the browser — delivered as a ZIP.

## 2. Gaps & Unanswered Questions

- **Where does the transcript database live?** *Assumption*: it stays in `data/sessions/`. The
  recording directory is named with the same `<stamp>-<session-id>` key as the database's stem, so
  the two are joined by name rather than by moving a live SQLite file mid-session. This is the
  cheapest correct link and it makes both the media indicators and the export a directory lookup.
- **What about the recordings already on disk, flat?** *Assumption*: they are migrated on start-up,
  once, by grouping on the `<stamp>-<id>` prefix every existing name already carries. Nothing is
  deleted; a file that does not match the prefix pattern is left where it is.
- **How does the exported page reach a language model?** *Assumption*: an OpenAI-compatible
  `/v1/chat/completions` endpoint called from the browser, with the endpoint, model, temperature and
  token budget editable inside the Q&A panel. A static export cannot ship a model, and the source
  application's local mode is already OpenAI-compatible, so the exported page uses the same shape.
  The user must allow cross-origin requests on their own server (Ollama's `OLLAMA_ORIGINS`); the
  panel says so when a request fails.
- **"Questions can be asked against both the transcript and the video."** *Assumption*: the
  transcript is the evidence, and the video is addressed *through* it — the answer cites timestamps
  and clicking one seeks the player. Sending video frames to a local text model is not something a
  static page can do, and the citation link is what makes a question about the video answerable.
- **Does the export need the audio file separately?** *Assumption*: no. The requirement lists video,
  transcript, and settings; the video carries the audio once muxed, and the export prefers the
  muxed file. A recording whose video has no audio track still exports, and the manifest says so.

## 3. Hierarchical Step-by-Step Instructions

### Step 1: One directory per recording

- **Locations**: `web/backend/app/services/recording/layout.py` (new — `RecordingLayout`,
  `layout_for`, `iter_recordings`, `migrate_flat_recordings`); `web/backend/app/paths.py`;
  `web/backend/app/services/session/manager.py` (`_open_sink`, `_start_window_capture`);
  `web/backend/app/routes/recordings.py` (`_resolve`, `list_recordings`, `transcribe_recording`,
  `delete_recording`); `web/backend/app/main.py` (start-up migration).
- **Rationale**: every later step reads from this shape. The listing needs to know what a recording
  contains, the archive needs to answer "is there a video for this session", and the export needs to
  find the video from a session key — all three are one directory listing once the layout exists,
  and three different filename-guessing exercises if it does not.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recording Folders and Web Export (1/6) Complete: every recording now
  writes into its own timestamped directory, with existing flat files migrated on start-up.

### Step 2: The transcription pass returns the interface to a usable state

- **Locations**: `web/backend/app/transport/events.py` (`INVALIDATES`);
  `web/backend/app/transport/hub.py` (`EventHub.emit`, `EventHub.forget`);
  `web/backend/app/services/session/manager.py` (`start`, clearing the finished job);
  `web/frontend/static/js/stores/session.js` (`hydrate`);
  `web/frontend/static/js/stores/mode.js` (`adoptSession`); `web/frontend/static/js/main.js`
  (`SESSION_STATE`).
- **Rationale**: the hub replays the most recent instance of each coalescing event to every new
  client so a reconnecting page paints a correct screen. `transcription.progress` is coalescing, so
  a *finished* pass keeps announcing itself as running to every client that connects afterwards —
  which is precisely "stuck at Transcribing… 100%", and, in `live` mode where the interface refuses
  a `processing` state, precisely "the button says Start recording and the clock keeps climbing".
  A terminal event must therefore retract the progress event it terminates. The session store's
  frozen clock is the same fault seen from the other side: it never adopted `ended_at`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recording Folders and Web Export (2/6) Complete: a finished
  transcription pass now retracts its progress event, and a reload after one lands on an idle
  interface with a frozen clock.

### Step 3: The suite stops writing into the developer's session folder

- **Locations**: `tests/conftest.py` (new autouse `isolated_data_dirs` fixture, using
  `TRANSCRIBER_CONFIG_PATH`); `docs/workflow.md`.
- **Rationale**: 949 session files, 690 of them empty, all written by test runs — that is why the
  past-sessions page opens on a wall of "0 words, 0 segments". A test that constructs `ConfigStore()`
  with no path gets `./data/transcriber-config.json` and therefore the real `./data/sessions`. The
  environment override already exists; pointing it at a per-test temporary directory closes the
  whole class.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recording Folders and Web Export (3/6) Complete: tests no longer write
  sessions or recordings into the developer's data directory.

### Step 4: The recordings list says what each session contains

- **Locations**: `web/backend/app/services/transcript/archive.py` (`ArchivedSession.media`,
  `describe`, `list_sessions`); `web/backend/app/routes/sessions.py`;
  `web/frontend/static/js/sessions.js` (`row`, `media`);
  `web/frontend/static/css/components/sessions.css`; `web/frontend/templates/pages/sessions.html`;
  `web/frontend/templates/partials/header.html`; `docs/api-contract.md`.
- **Rationale**: the media indicators and the header button are the same question asked twice — "what
  is in here, and how do I get to it". Both depend on Step 1's directory layout: without it, deciding
  whether a session has a video means guessing at filenames.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recording Folders and Web Export (4/6) Complete: the header carries a
  labelled Recordings button and each past session shows whether it holds video, audio, or a
  transcript.

### Step 5: The self-contained web-app export

- **Locations**: `web/backend/app/services/export/` (new package — `webapp.py` building the ZIP,
  `payload.py` shaping `transcript.json` and `settings.json`, `template.py` holding the page);
  `web/backend/app/routes/sessions.py` (`GET /api/sessions/{key}/webapp`);
  `web/frontend/static/js/sessions.js`; `docs/api-contract.md`, `docs/routes.md`.
- **Rationale**: the ZIP is assembled server-side because only the server can read the video off
  disk. The exported page is written as one HTML file plus its assets so it opens from a file
  manager with no server; the CSS is a copy of the application's own tokens so it looks like the
  application it came from.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recording Folders and Web Export (5/6) Complete: a session holding
  video, audio and a transcript exports as a self-contained HTML web application in a ZIP.

### Step 6: Documentation, tests, and the merge

- **Locations**: `docs/structure.md`, `docs/documentation.md`, `docs/api-contract.md`,
  `docs/routes.md`, `docs/data-flow.md`, `docs/component-map.md`, `docs/workflow.md`,
  `docs/checklist.md`; `tests/data/`, `tests/api/`, `tests/transcription/`.
- **Rationale**: the repository contract requires documentation to ship in the same change as the
  code, and the plan is not complete until the checklist reflects it.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recording Folders and Web Export (6/6) Complete: documentation and
  tests updated, and the branch merged into main.

## 4. Deliverables

| Deliverable | Description | Location |
| --- | --- | --- |
| Recording layout | One directory per recording; resolution, iteration, and migration of flat files | `web/backend/app/services/recording/layout.py` |
| Layout tests | Naming, containment, media detection, migration of an existing flat directory | `tests/transcription/test_recording_layout.py` |
| Event retraction | A terminal event drops the coalescing event it ends, so it is never replayed | `web/backend/app/transport/events.py`, `hub.py` |
| Retraction tests | A finished pass is not replayed to a client that connects afterwards | `tests/api/test_transport.py` |
| Frontend state repair | Clock freezes on `ended_at`; `processing` is left when the pass is not running | `web/frontend/static/js/stores/session.js`, `stores/mode.js`, `main.js` |
| Test isolation | Autouse fixture pointing every default `ConfigStore` at a temporary data directory | `tests/conftest.py` |
| Media indicators | What each past session holds, in the API and on the page | `web/backend/app/services/transcript/archive.py`, `web/frontend/static/js/sessions.js` |
| Archive tests | Media flags for every combination of present and absent artefacts | `tests/data/test_session_archive.py` |
| Web-app export | ZIP containing the video, transcript, settings, and a self-contained page | `web/backend/app/services/export/` |
| Export tests | Archive contents, refusal when media is missing, and the manifest's shape | `tests/data/test_webapp_export.py` |

## 5. Phase Status

| Phase | Status |
| --- | --- |
| 1 — One directory per recording | ⬜ Not started |
| 2 — Transcription completion state | ⬜ Not started |
| 3 — Test data isolation | ⬜ Not started |
| 4 — Recordings button and media indicators | ⬜ Not started |
| 5 — HTML web-app export | ⬜ Not started |
| 6 — Documentation and merge | ⬜ Not started |
