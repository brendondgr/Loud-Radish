# API Contract

*Last updated: 2026-08-14 (repository initialization)*

> **Status: proposed, nothing implemented.** The shapes below are a starting point recorded at
> initialization, not a frozen contract. Update this file, `web/shared/contracts/`, and both sides of
> the boundary together in the same change.

## Rules

- Base path for all API endpoints: `/api`.
- The authoritative machine-readable contract is the OpenAPI spec in `web/shared/contracts/`,
  generated from the backend. This document is the human-readable companion.
- Frontend types are **generated** from that spec, never hand-copied.
- Backend validation lives in `web/backend/app/schemas/` and must match this document.
- Breaking a shape requires updating: this file, `web/shared/contracts/`,
  `web/backend/app/schemas/`, the frontend API client, and `docs/routes.md`.

## Conventions

- Content type `application/json`, except upload which is `multipart/form-data`.
- Timestamps are ISO 8601 UTC strings.
- Identifiers are opaque strings; clients must not parse them.
- Errors use a single consistent envelope (below). Never return a bare string.

## Proposed Shapes

### `POST /api/transcriptions`

Request: `multipart/form-data` with a single `file` part containing the audio.

Response `202 Accepted`:

```json
{
  "job_id": "string",
  "status": "queued",
  "filename": "string",
  "created_at": "2026-08-14T00:00:00Z"
}
```

### `GET /api/transcriptions/{job_id}`

Response `200 OK`:

```json
{
  "job_id": "string",
  "status": "queued | processing | completed | failed",
  "filename": "string",
  "created_at": "2026-08-14T00:00:00Z",
  "completed_at": "2026-08-14T00:00:00Z | null",
  "error": "string | null",
  "transcript": {
    "text": "string",
    "segments": [
      { "start": 0.0, "end": 0.0, "text": "string" }
    ]
  }
}
```

`transcript` is `null` unless `status` is `completed`. `error` is non-null only when `status` is
`failed`.

### `GET /api/transcriptions`

Response `200 OK`: an array of job summaries — the object above without `transcript`.

### `DELETE /api/transcriptions/{job_id}`

Response `204 No Content`. Deletes the job record, the stored audio, and the transcript.

### `GET /api/health`

Response `200 OK`: `{ "status": "ok" }`.

## Error Envelope

```json
{
  "error": {
    "code": "string",
    "message": "human-readable, safe to display"
  }
}
```

| Status | When |
|---|---|
| `400` | Malformed request |
| `404` | Unknown `job_id` |
| `413` | Upload exceeds the size limit |
| `415` | Unsupported audio format |
| `422` | Validation failure |
| `500` | Unexpected server error — `message` must not leak internals |

## Open Questions

Tracked in `docs/checklist.md`:

- Supported audio formats and the maximum upload size.
- Whether progress is polled (assumed here) or pushed via SSE/websocket; pushing would add a
  streaming endpoint not listed above.
- Pagination for `GET /api/transcriptions`.
- Authentication — no scheme selected, so no auth headers are specified.
- Whether transcripts expose speaker labels, word-level timing, or confidence scores.
