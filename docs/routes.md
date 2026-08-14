# Route Map

*Last updated: 2026-08-14 (Phase 8 — transport layer)*

> **Status column is authoritative.** Only `GET /api/health` is implemented. Everything else is the
> agreed surface, landing in the phase named beside it. Update this file in the same change that adds,
> removes, or changes any route or page.

The upload-and-poll job routes recorded at initialization are **gone** — see Decision D-010 in
`docs/documentation.md`. There is no `/api/transcriptions` surface.

## Pages

Server-rendered from `web/frontend/templates/` by `web/backend/app/routes/pages.py`.

| Path | Purpose | Template | Status |
|---|---|---|---|
| `/` | The live application — transcript pane, chat pane, header, status bar | `pages/app.html` | Phase 11 |
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
| `POST` | `/api/session/start` | Begin capture and transcription | **Implemented** |
| `POST` | `/api/session/stop` | End the session and return final statistics | **Implemented** |
| `GET` | `/api/session` | Current session state and metadata | **Implemented** |
| `GET` | `/api/session/list` | Past sessions | Phase 8 |

### Audio

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/api/audio/devices` | Input and loopback devices in one merged list, each tagged with its type | **Implemented** |
| `POST` | `/api/audio/device` | Select the capture device | **Implemented** |
| `POST` | `/api/audio/preprocess` | Set gain normalisation and high-pass toggles | Phase 8 |

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
| `GET` | `/api/llm/config` | Current mode and both provider configurations. Never returns a credential. | Phase 9 |
| `PUT` | `/api/llm/config` | Update mode or provider configuration | Phase 9 |
| `POST` | `/api/llm/credential` | Store a credential in the OS credential store | Phase 9 |
| `DELETE` | `/api/llm/credential` | Remove a stored credential | Phase 9 |
| `POST` | `/api/llm/test` | Connection test — returns a specific result, never generic failure text | Phase 9 |
| `GET` | `/api/llm/models` | Models the configured endpoint reports | Phase 9 |

### Chat

| Method | Path | Purpose | Status |
|---|---|---|---|
| `POST` | `/api/chat/send` | Ask a question. The answer streams over the WebSocket. | Phase 10 |
| `POST` | `/api/chat/cancel` | Cancel an in-flight request | Phase 10 |
| `GET` | `/api/chat/history` | Conversation history | Phase 10 |
| `DELETE` | `/api/chat/history` | Clear the conversation | Phase 10 |
| `GET` | `/api/chat/quick-actions` | The configured quick-action prompts | Phase 10 |

### Transcript

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/api/transcript/since/{segment_id}` | Everything after a segment id — the reconnection path | **Implemented** |
| `GET` | `/api/transcript/range` | Everything in a time range | **Implemented** |
| `GET` | `/api/transcript/search` | Full-text search over the session | **Implemented** |
| `GET` | `/api/transcript/export` | Export as text, Markdown, SRT, VTT, or JSON | **Implemented** |
| `GET` | `/api/transcript/glossary` | The session glossary | **Implemented** (empty until Phase 10) |
| `GET` | `/api/transcript/summaries` | The rolling outline | **Implemented** (empty until Phase 10) |

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
