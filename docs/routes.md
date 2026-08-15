# Route Map

*Last updated: 2026-08-15 (capture modes and recordings — D-020, D-021)*

> **Status column is authoritative.** Every API group is implemented and tested, as is the
> WebSocket. Only the `/sessions` page remains. Update this file in the same change that adds,
> removes, or changes a route.

The upload-and-poll job routes recorded at initialization are **gone** — see Decision D-010 in
`docs/documentation.md`. There is no `/api/transcriptions` surface.

## Pages

Server-rendered from `web/frontend/templates/` by `web/backend/app/routes/pages.py`.

| Path | Purpose | Template | Status |
|---|---|---|---|
| `/` | The live application — transcript pane, header, status bar | `pages/app.html` | **Implemented** |
| `/sessions` | Past sessions, with export | `pages/sessions.html` | Phase 14 |

## WebSocket

| Path | Purpose | Status |
|---|---|---|
| `/ws` | The live event stream. Accepts a `since` segment id on connect and replays everything after it. | **Implemented** |

Event envelopes are specified in [api-contract.md](api-contract.md) and machine-readably in
`web/shared/contracts/ws-events.json`.

## API endpoints

Served by `web/backend/app/routes/`. All paths are prefixed `/api`.

### Health

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/api/health` | Liveness, plus which optional dependency groups are installed | **Implemented** |

### Session

| Method | Path | Purpose | Status |
|---|---|---|---|
| `POST` | `/api/session/start` | Begin capture in a capture mode | **Implemented** — see below |
| `POST` | `/api/session/stop` | End the session and return final statistics | **Implemented** |
| `POST` | `/api/session/toggle` | Start if idle, stop if running — one call, because a keystroke cannot know which (D-024) | **Implemented** |
| `GET` | `/api/session` | Current session state and metadata | **Implemented** |
| `GET` | `/api/session/list` | Past sessions | Phase 14 (with the sessions page) |

**Capture mode on start (D-020).** `POST /api/session/start` takes `mode` — `live`, `recorded`, or
`window` — defaulting to `live` so a client written before capture modes keeps working. `window`
also takes `options`, three per-run booleans: `live_transcription`, `post_transcription`, `video`.

Three refusals, in the order they are checked:

| Condition | Status | Code |
|---|---|---|
| A mode outside the vocabulary | `422` | Pydantic validation |
| All three options false | `422` | `records-nothing` |
| A mode this build does not implement | `501` | `mode-unavailable` |

`501` rather than `409` is deliberate: the request is valid and the session state is fine, the build
simply lacks the feature — a different problem with a different remedy. Each of Plans 3 and 4
removes its own entry from that set. `GET /api/health` reports per-mode availability so the
interface can disable a mode with its reason rather than hiding it.

### Recordings

Audio this machine captured, as opposed to the library the file source replays from. A recording
appears here only because a transcription pass failed, was interrupted, or because audio retention
is on — a successful pass deletes its own audio (D-021).

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/api/recordings` | Every recording still on disk, newest first | **Implemented** |
| `POST` | `/api/recordings/{name}/transcribe` | Run or re-run a pass, into a **new** session | **Implemented** |
| `DELETE` | `/api/recordings/{name}` | Remove one | **Implemented** |

`{name}` is a file name and is resolved strictly inside the recordings directory: a loopback-bound
server is still reachable from any page in any other tab, so a traversal here would be a real file
read. A re-run writes into a new session rather than the one that produced the recording, because
the original may hold a partial transcript from the pass that failed.

### Window capture

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/api/capture/state` | What the monitor pane draws | **Implemented** |
| `GET` | `/api/capture/preview.jpg` | The most recent preview frame; `404` when there is none | **Implemented** |

The preview is **pulled as an image on a timer, never pushed over the WebSocket** (D-022). The
socket's backpressure policy makes transcript events undroppable, and video frames sharing that
channel are the one thing capable of delaying a committed segment. A `404` is ordinary — no capture,
no JPEG encoder, or the first frame not yet written — and the monitor shows a card rather than a
black rectangle.

### Audio

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/api/audio/devices` | Input and loopback devices in one merged list, each tagged with its type | **Implemented** |
| `POST` | `/api/audio/device` | Select the capture device | **Implemented** |
| `GET` | `/api/audio/files` | Recordings the file source can replay, with duration and sample rate. A configured file outside the library is included. | **Implemented** |
| `POST` | `/api/audio/files` | Upload a WAV into the library and select it. Multipart. | **Implemented** |
| `DELETE` | `/api/audio/files?path=` | Remove a recording. Refuses any path outside the library. | **Implemented** |
| `POST` | `/api/audio/preprocess` | Set gain normalisation and high-pass toggles | Use `PATCH /api/config` — these are ordinary live settings |

### Speech recognition

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/api/asr/models` | Available models with their declared capabilities | **Implemented** |
| `POST` | `/api/asr/load` | Load a model — asynchronous, with progress over the WebSocket | **Implemented** |
| `POST` | `/api/asr/unload` | Free the model and its device memory | **Implemented** |
| `POST` | `/api/asr/prompt` | Set the session biasing prompt | **Implemented** |

### Language model

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/api/llm/config` | Current mode and both provider configurations, plus suggested endpoints and providers. Never returns a credential. | **Implemented** |
| `GET` | `/api/llm/status` | Whether the assistant is set up, and whether it is local. Makes no network call. | **Implemented** |
| `PUT` | `/api/llm/credential` | Store a credential in the OS credential store | **Implemented** |
| `DELETE` | `/api/llm/credential/{provider}` | Remove a stored credential | **Implemented** |
| `POST` | `/api/llm/test` | Connection test — one of four specific results, never generic failure text. Accepts an unsaved partial config so the form tests what is on screen. | **Implemented** |
| `GET` | `/api/llm/models` | Models the configured endpoint reports. Degrades to an empty list with an explanation rather than an HTTP error. | **Implemented** |

There is no `PUT /api/llm/config`. Configuration is written through the one configuration surface,
`PATCH /api/config`, with dotted paths such as `llm.local.model` — a second writer for the same
state is a second place for it to drift.

### Chat

| Method | Path | Purpose | Status |
|---|---|---|---|
| `POST` | `/api/chat/send` | Ask a question. Returns a `request_id` immediately; the answer streams over the WebSocket. | **Implemented** |
| `POST` | `/api/chat/cancel` | Cancel an in-flight request. Cancelling nothing is not an error. | **Implemented** |
| `GET` | `/api/chat/history` | Conversation history | **Implemented** |
| `DELETE` | `/api/chat/history` | Clear the conversation. The transcript is untouched. | **Implemented** |
| `GET` | `/api/chat/quick-actions` | The configured quick-action prompts | **Implemented** |
| `POST` | `/api/chat/read` | Record how far the user has read, for "what did I miss" | **Implemented** |

### Transcript

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/api/transcript/since/{segment_id}` | Everything after a segment id — the reconnection path | **Implemented** |
| `GET` | `/api/transcript/range` | Everything in a time range | **Implemented** |
| `GET` | `/api/transcript/search` | Full-text search over the session | **Implemented** |
| `GET` | `/api/transcript/export` | Export as text, Markdown, SRT, VTT, or JSON | **Implemented** |
| `GET` | `/api/transcript/glossary` | The session glossary | **Implemented** |
| `GET` | `/api/transcript/summaries` | The rolling outline | **Implemented** |
| `GET` | `/api/transcript/revisions` | Which transcription passes this session holds (D-022) | **Implemented** |
| `GET` | `/api/transcript/at/{revision}` | Every segment from one pass — what the Live/Final switch fetches | **Implemented** |
| `GET` | `/api/transcript/polished` | Finished minutes rewritten for reading, with inline `[MM:SS]` markers. Empty with no language model, which is the ordinary state and not an error | **Implemented** |

### Configuration

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/api/config` | The full resolved configuration | **Implemented** |
| `PATCH` | `/api/config` | Apply dotted-path changes; returns the hot-swap class of the change | **Implemented** |
| `POST` | `/api/config/preset` | Apply a named preset | **Implemented** |
| `POST` | `/api/config/save` | Persist runtime changes to the user config file | **Implemented** |

## Conventions

- API routes live under `/api`. `/ws` is the event stream. Everything else is a page.
- Route modules stay thin — validate, call a service, shape the response. No business logic.
- One route module per area, in `web/backend/app/routes/`.
- Adding a route requires updating this file, `docs/api-contract.md`, and `web/shared/contracts/` in
  the same change.

## Auth boundaries

**None.** Single-user, loopback-bound, no authentication model — see `docs/architecture.md`. Every
route is reachable by anything that can reach the port, which is why the port is bound to `127.0.0.1`.
