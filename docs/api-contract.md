# API Contract

*Last updated: 2026-08-14 (Phase 1 — foundations)*

> **Status: the WebSocket event contract below is frozen; the HTTP shapes land phase by phase.**
> Only `GET /api/health` is implemented. The machine-readable contract lives in
> `web/shared/contracts/` and is generated from the backend; this document is its human-readable
> companion. Update both together.

The upload-and-poll job contract recorded at initialization is **superseded** — see Decision D-010.

## Rules

- Base path for HTTP endpoints: `/api`. The event stream is `/ws`.
- Content type `application/json` throughout. There is no file upload.
- Session-relative times are **seconds as floats**. Wall-clock times are ISO 8601 UTC strings.
- Segment ids are monotonic integers within a session. Clients order by id, not by arrival.
- Errors use the envelope below. Never a bare string.
- **No response ever contains a credential.** The frontend learns only whether one is present.

## The WebSocket contract

This is the most important part of the API and the part most likely to be got wrong.

### The one rule that matters

| Event | Client action |
|---|---|
| `transcript.committed` | **Append.** Never modify an existing entry. |
| `transcript.hypothesis` | **Replace the tail wholly.** It is not a list entry. |

Treating the hypothesis as the last array element duplicates text on screen. It is a single mutable
element that always sits at the end and is replaced entirely on each event.

### Server-to-client events

Every frame is `{"event": "<name>", "data": { … }}`.

| Event | Payload | Meaning |
|---|---|---|
| `session.started` | `session_id`, `config` snapshot, `started_at` | Capture began |
| `session.stopped` | `session_id`, `stats` | Capture ended |
| `transcript.committed` | `id`, `text`, `start`, `end`, `wall_clock`, `confidence`, `model_id`, `speaker` | Append permanently |
| `transcript.hypothesis` | `text` (may be empty), `start` | Replace the tentative tail |
| `transcript.polished` | `id`, `start`, `end`, `text`, `source_ids` | A finished minute rewritten for reading. Append the block and stop drawing the segments in `source_ids` — do **not** delete them |
| `audio.level` | `rms`, `peak`, `clipping` | Drives the level meter |
| `vad.state` | `speaking` (bool) | Drives the speaking indicator |
| `status` | `rtf`, `queue_depth`, `commit_latency_s`, `model_id`, `device`, `dropped_frames` | Health telemetry |
| `summary.added` | `start`, `end`, `text` | A new rolling summary |
| `glossary.added` | `term`, `definition`, `first_seen` | A new term identified |
| `chat.delta` | `request_id`, `text` | A streaming answer fragment |
| `chat.done` | `request_id`, `usage`, `context_timestamp`, `cites` | The answer is complete |
| `error` | `code`, `message`, `severity`, `remedy`, `remedy_label`, `opens_settings` | Something went wrong |

`remedy` is a map of dotted configuration paths that would fix the failure, offered as a one-click
action. Some failures have no single answer — an unplugged microphone needs a choice only the user
can make — and those carry `opens_settings` instead, naming the settings tab where the choice lives.
A failure with `remedy_label` but neither of the two renders as text with nothing to click, so one
of them must be set whenever a label is.

`severity` is `info`, `warning`, or `critical`, and the frontend presents each differently: status-bar
text, a dismissible inline banner, or a banner with an explicit recovery action. Never a modal — a
modal during a live talk blocks the transcript.

### Client-to-server frames

| Frame | Payload | Meaning |
|---|---|---|
| `hello` | `since` (segment id or `null`), `client_id` | Opens the stream and requests replay |
| `ping` | — | Keepalive |

### Reconnection

The socket is disposable by design.

1. The client detects the disconnect and shows it in the status bar. **It does not clear the transcript.**
2. It reconnects with exponential backoff.
3. On reconnect it sends `hello` with the last segment id it received.
4. The server replays every committed segment after that id, then every polished block, then
   resumes live events.
5. The client ignores any segment id or block id it already holds, which makes the replay idempotent.

Polished blocks are replayed **in full**, not from a cursor. There is one a minute so the whole set
is small even for a long talk, and a block is produced once and never re-sent — a client that missed
one would show that minute as raw text for the rest of the session.

Frame order within a replay is not part of the contract and cannot be: a client is registered the
moment it connects, so live events may already be queued when its `hello` arrives. Read the stream
by event type, never by position.

## Implemented HTTP shapes

### `GET /api/health`

Response `200 OK`:

```json
{
  "status": "ok",
  "credentials_backend": "os-credential-store | environment",
  "optional": {
    "asr_whisper": false,
    "audio_device": false,
    "vad_silero": false,
    "credentials": false
  }
}
```

`optional` reports which optional dependency groups are installed, so a missing model backend surfaces
here rather than as a confusing failure when the user presses record.

## Agreed shapes, not yet implemented

Full request and response bodies are recorded here as each phase lands. The route inventory and the
phase each belongs to are in [routes.md](routes.md). The shapes below are the ones the frontend is
built against.

### Segment

The unit the transcript store holds, the frontend renders as a paragraph, and the context pipeline
chunks on.

```json
{
  "id": 41,
  "text": "string",
  "start": 732.4,
  "end": 738.1,
  "wall_clock": "2026-08-14T14:12:03Z",
  "confidence": 0.91,
  "model_id": "faster-whisper:small",
  "speaker": null,
  "words": [{ "text": "string", "start": 732.4, "end": 732.7, "confidence": 0.94 }]
}
```

`confidence` is `null` when the backend cannot provide one — it is never faked. `speaker` is reserved
for diarisation and is always `null` in v1. `words` is optional and may be omitted for compactness.

### Configuration

`GET /api/config` returns the full resolved configuration, matching the Pydantic models in
`web/backend/app/config/schema.py`. `PATCH /api/config` takes dotted paths:

```json
{ "changes": { "vad.sensitivity": 0.4, "asr.model": "medium" } }
```

and responds with the **hot-swap class** of the change — `live`, `restart-stage`, or
`restart-session` — plus a plain-language consequence, so the frontend can warn before applying:

```json
{ "applied": true, "hot_swap": "restart-stage",
  "consequence": "Briefly interrupts transcription while that stage restarts." }
```

### Connection test

`POST /api/llm/test` never returns generic failure text. The `result` field is one of
`connected`, `no_server`, `auth_rejected`, `server_error`, and `message` states the specific remedy:

```json
{ "result": "no_server",
  "message": "No server responded at http://localhost:11434/v1 — check that Ollama is running.",
  "models": [] }
```

## Error envelope

```json
{ "error": { "code": "string", "message": "human-readable, safe to display", "severity": "warning" } }
```

| Status | When |
|---|---|
| `400` | Malformed request |
| `404` | Unknown session, segment, or model |
| `409` | Operation conflicts with the current session state — e.g. starting a session already running |
| `422` | Validation failure, including an out-of-range configuration value |
| `500` | Unexpected server error. `message` must not leak internals. |

Error messages state what happened and what to do. Not "connection failed" but "No server responded at
localhost:11434 — check that Ollama is running."
