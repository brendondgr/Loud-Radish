# API Contract

*Last updated: 2026-08-29 (what the media block means, corrected — D-032, D-034, D-035)*

> **Status: the WebSocket event contract below is frozen.** The machine-readable contract lives in
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
| `session.started` | `session_id`, `mode`, `config` snapshot, `started_at`, `source` | Capture began. `mode` is the capture mode (D-020) and is authoritative — a client reloading mid-recording adopts it rather than its own last selection |
| `session.stopped` | `session_id`, `key`, `stats`, `has_media` | Capture ended. `has_media` says whether the recording folder holds audio or video, which is what decides whether the export window opens (D-058) — asked of the folder, not inferred from the mode |
| `session.paused` | `session_id`, `at_seconds` | Capture is **held** (D-044). The recording stops growing and the clock stops with it — a pause removes time from the recording rather than adding silence to it, so nine seconds of wall clock across a three-second hold produce six seconds of audio. **Retracts nothing**: unlike a stop, a hold ends nothing, so the tentative tail and the recording figure are both still true |
| `session.resumed` | `session_id`, `at_seconds` | Capture continues, in the same session, the same file and the same store |
| `session.cancelled` | `session_id`, `kept` | The session ended and **nothing will be transcribed**. Every artefact it produced is kept — the difference from a stop is exactly one thing, whether the post-capture pass runs |
| `transcript.committed` | `id`, `text`, `start`, `end`, `wall_clock`, `confidence`, `model_id`, `speaker` | Append permanently |
| `transcript.hypothesis` | `text` (may be empty), `start` | Replace the tentative tail |
| `transcript.polished` | `id`, `start`, `end`, `text`, `source_ids` | A finished minute rewritten for reading, as one continuous paragraph. Append the block and stop drawing the segments in `source_ids` — do **not** delete them. `text` carries inline `[MM:SS]` markers, in the same format the assistant cites, so a passage can be traced back to when it was said |
| `recording.progress` | `duration_s`, `bytes` | How much audio the current recording has captured. In `recorded` mode this is the *only* sign the application is doing anything, because no transcript is being produced — a figure that climbs is what distinguishes recording from having silently stopped (D-021) |
| `transcription.progress` | `session_id`, `state`, `progress`, `transcribed_seconds`, `total_seconds`, `segments`, `error` | The post-capture pass. Progress is by **audio position** — honest, monotonic, and needing no instrumentation inside the model |
| `transcription.done` | same shape | The pass finished. Never dropped: losing it leaves a progress bar running for a pass that ended |
| `transcription.failed` | same shape, with `error` | The pass failed **and the recording is still on disk**. The message names the file, because it is now the only copy of what was said and `/api/recordings` can run the pass again against it |
| `transcription.paused` | same shape, with `next_start_s` and `resumable` | The pass is **held** at a window boundary, with a checkpoint written into the session's own database (D-045). Retracts `transcription.progress`, whose last frame says `running` — replaying that to a window opened afterwards puts a moving bar over a pass that is not moving |
| `transcription.cancelled` | same shape | The pass will not continue. The transcript so far stays committed and the audio stays on disk: cancelling declines the CPU, not the recording |
| `capture.state` | `recording`, `window_closed`, `failed`, `stalled`, `stalled_seconds`, `error`, `video_path`, `bytes`, `duration_s`, `preview`, `options` | The window capture started, stopped, stalled, or failed (D-022, D-036). Never dropped: a missed window-closed frame leaves a live preview showing for a capture that ended. `stalled` is the state that had no name — alive, running, and writing nothing — and is deliberately distinct from `failed`, because the recording is still going and there is still time to act on it |
| `audio.level` | `rms`, `peak`, `clipping` | Drives the level meter |
| `vad.state` | `speaking` (bool) | Drives the speaking indicator |
| `status` | `rtf`, `queue_depth`, `commit_latency_s`, `model_id`, `device`, `dropped_frames`, `suppressed` | Health telemetry. `suppressed` counts passes discarded as invented speech |
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
    "credentials": false,
    "window_capture": false
  },
  "acceleration": { "...": "GPU availability and what is blocking it" },
  "modes": {
    "live": { "available": true, "missing": [], "reason": "" },
    "recorded": { "available": true, "missing": [], "reason": "" },
    "window": {
      "available": false,
      "missing": ["window_capture"],
      "reason": "Window recording is not built yet — see docs/plans/"
    }
  }
}
```

`optional` reports which optional dependency groups are installed, so a missing model backend surfaces
here rather than as a confusing failure when the user presses record. `window_capture` is listed
alongside them although it is not a dependency group, because from the user's side it is the same
question: can this machine do the thing, and if not what is missing.

`modes` answers that question per capture mode (D-020). The interface shows every mode always and
disables the unavailable ones with `reason`, because a mode that vanishes when its dependency is
absent is indistinguishable from a mode that does not exist. **An empty `missing` is the common
case**: only `window` has a hard requirement. A missing capture device deliberately does *not*
disable the audio modes — the file source replaces one entirely, and gating on it made every mode
unavailable on a machine that transcribes perfectly well.

### `GET /api/sessions`

Response `200 OK`:

```json
{
  "sessions": [
    {
      "key": "20260829-174113-d60b37a9e3c4",
      "title": "Operator theory",
      "started_at": "2026-08-29T17:41:13+00:00",
      "ended_at": "2026-08-29T18:29:02+00:00",
      "segments": 412,
      "words": 9130,
      "duration_seconds": 2869.0,
      "summaries": 9,
      "glossary_terms": 22,
      "size_bytes": 483328,
      "media": {
        "video": true,
        "audio": true,
        "transcript": true,
        "audio_file": false,
        "recording_bytes": 264518912,
        "exportable": true
      },
      "problem": "",
      "readable": true
    }
  ],
  "directory": "/home/…/data/sessions",
  "running_key": ""
}
```

`media` says what the session's **recording folder** holds (D-032). The folder is named with the
same key as the database, so this is a directory listing rather than a filename search.

Three subtleties in it.

`transcript` means the database holds segments, not merely that the file exists — a recording whose
transcription pass never ran leaves an empty database, and calling that a transcript sends someone
to open it and find nothing.

`audio` means **there is sound you can play**, from the separate recording or from the video it was
muxed into. It is deliberately not "there is a WAV": a successful transcription pass deletes that
file unless retention is on, so the literal reading reported no audio precisely when a recording was
healthiest — and took `exportable` down with it (D-035). `audio_file` is the narrower fact,
reported alongside because only that file can be transcribed again.

`segments` and `words` describe **one transcription pass**, the latest, exactly as
`GET /api/sessions/{key}` and every export do. Counting the table across both passes reported
roughly double what a session holds.

A row with a non-empty `problem` could not be read. It is listed anyway: a session the user can see
and cannot open is more useful than one that has silently vanished.

### `GET /api/sessions/{key}/webapp?include_chat=`

Response `200 OK`: `application/zip`, `Content-Disposition: attachment`. The archive holds one
folder named for the key (D-034):

```text
<key>/index.html                 The application. Opens with no server.
<key>/theme.css, app.css         Its stylesheets.
<key>/util.js … app.js           Its scripts, classic — not modules.
<key>/media/<video>              The video, stored uncompressed and byte-identical.
<key>/data/transcript.json       The talk: session, media reference, segments, summaries,
                                 glossary. Plus `chat` only when `include_chat=true`.
<key>/data/settings.json         The Q&A configuration: endpoint, model, temperature, output
                                 cap, transcript budget, the system prompt, quick actions.
<key>/data/bundle.js             The same two documents as a script, for `file://`.
<key>/README.txt                 What is here and how to open it.
```

`settings.json` carries `llm.api_key` **present and empty, always**. Nothing else in the archive
carries a credential either: a ZIP is exactly the sort of thing that gets forwarded, and the
exported page keeps whatever key the user types in that browser's storage alone.

**`include_chat` defaults to false, and that default is the point (D-037).** The archive is for
sending to other people, and its Q&A panel exists so that whoever opens it connects their own model
and asks their own questions. Carrying the exporter's conversation made it open half-full of
somebody else's questions about a talk the reader had not watched. The flag is checked in both
places the conversation would otherwise appear — `transcript.json` and the `bundle.js` that mirrors
it — because a check of one alone would pass while it shipped in the other.

### `GET /api/sessions/{key}/chat?fmt=markdown|json`

The conversation on its own, as `<stamp>-chat.md` or `.json`. Markdown for reading; JSON carries the
full `ChatMessage` shape. Each answer keeps the transcript position it was based on, which is what
makes it re-checkable against the talk.

A recording nobody asked about is `200` with a sentence saying so, never `404`: "you asked nothing
during this recording" is information, and an error code is not. `422 unknown-format` for anything
but those two, naming both.

Refusals:

| Condition | Status | Code |
|---|---|---|
| The key is not a recording key | `404` | `no-recording` |
| The session file is gone | `404` | `no-such-session` |
| No video, or no transcript | `409` | `not-exportable` |

## Agreed shapes, not yet implemented

Full request and response bodies are recorded here as each phase lands. The route inventory and the
phase each belongs to are in [routes.md](routes.md). The shapes below are the ones the frontend is
built against.

### Segment

Every segment carries a `revision` (D-022): `0` is the live pass and `1` a post-capture one. Both
are kept rather than one replacing the other, so a client showing revision 1 must **replace** the
transcript rather than merge — the two cover the same audio with different ids, and interleaving
them says everything twice.

**That rule binds the reader as well as the writer, and it was where the fault was.** A post-capture
pass publishes `transcript.committed` for every revision-1 segment as it writes it, so a client
displaying revision 0 receives a second whole transcript over the same socket it is already using.
The browser now holds one revision at a time and drops anything from another, switching wholesale
when the pass finishes. On the server, `GET /api/transcript/export`, `GET /api/sessions/{key}` and
`GET /api/sessions/{key}/export` serve the **latest** revision — never the union — so the file a
reader keeps holds the talk once. The Live/Final switch is the only way to see the other pass, and
full-text search is the deliberate exception: it spans both, because a hit in either is a real hit.

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

A change lands in the `runtime` layer by default and is discarded on exit unless
`POST /api/config/save` is called — that is what lets a setting be tried during a talk and got rid
of by restarting. **Three paths are the exception** and are written straight to the user layer and
to the config file whatever `layer` was asked for:

| Path | Why |
|---|---|
| `audio.source_type` | Which kind of input to listen to is an identity, not a tuning value |
| `audio.device_id` | Same; losing it means the next recording uses the wrong input, or none |
| `audio.file_path` | Travels with the two above when the source is a file |

The set lives in `web/backend/app/config/store.py` as `PERSISTENT_PATHS`, and the split happens on
the route so that every client — the settings modal, the tray's device picker, the native settings
window — inherits it rather than each remembering separately (D-046).

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
