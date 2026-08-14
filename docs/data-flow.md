# Data Flow

*Last updated: 2026-08-14 (repository initialization)*

> **Status: planned, not built.** This is the intended movement of data through TranscriberPrototype.
> Update it in the same change that alters how data moves between browser, API, service, engine, or
> storage.

## Primary Flow — Transcribing an Audio File

```text
1. UPLOAD
   Browser ──multipart/form-data──▶ POST /api/transcriptions
   web/frontend/src/features/upload/     web/backend/app/routes/

2. ACCEPT
   Route validates via app/schemas/ ──▶ Service creates a job record
   Audio is written to data/ ; a job id is returned immediately.
   Response: 202 Accepted { job_id, status: "queued" }

3. TRANSCRIBE  (out of band — execution model undecided)
   Service ──▶ transcription engine ──▶ transcript text + segments
   Job status moves: queued → processing → completed | failed

4. POLL
   Browser ──▶ GET /api/transcriptions/{job_id}
   Returns current status; on completion, the transcript payload.

5. RENDER
   Frontend stores the result in client state and renders the viewer/editor.

6. EXPORT
   User downloads the transcript. Format handling is client-side where possible.
```

## Boundaries

| Boundary | Carries | Defined by |
|---|---|---|
| Browser ↔ API | JSON, plus multipart for upload | `docs/api-contract.md`, `web/shared/contracts/` |
| Route ↔ Service | Validated schema objects | `web/backend/app/schemas/` |
| Service ↔ Engine | Audio path in, transcript structure out | Engine adapter in `web/backend/app/services/` |
| Service ↔ Storage | Model instances | `web/backend/app/models/` |

Rules:

- Routes never touch the engine or storage directly. They call services.
- The frontend never constructs a request shape by hand; it uses types generated from the contracts.
- Raw audio never crosses back to the browser. Only transcripts and metadata do.

## State Ownership

| State | Owner | Notes |
|---|---|---|
| Uploaded audio | Backend, `data/` | Gitignored. Retention policy undecided. |
| Job records and status | Backend | Persistence layer undecided |
| Completed transcripts | Backend, served on request | |
| In-flight upload progress | Frontend only | Ephemeral |
| Unsaved transcript edits | Frontend only | Must be persisted explicitly; define the save boundary before building the editor |
| Auth/session | None | No auth model selected |

## Undecided

These gaps materially affect the flow above and are tracked in `docs/checklist.md`:

- **Out-of-band execution model** — background task, worker process, or queue.
- **Progress reporting** — polling (assumed above) versus server-sent events or websockets.
- **Persistence** — files on disk only, or a database for job records.
- **Retention** — how long uploaded audio and transcripts are kept. Until decided, treat `data/` as
  containing potentially sensitive user content: never commit it, never log its contents.
- **Large-file handling** — whether uploads need chunking or a size ceiling.
