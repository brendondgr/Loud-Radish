# Route Map

*Last updated: 2026-08-14 (repository initialization)*

> **Status: proposed, none implemented.** No routes or pages exist yet. The tables below are the
> intended surface, recorded so implementation has a starting point. Update this file in the same
> change that adds, removes, or changes any route or page.

## Frontend Pages

Rendered by `web/frontend/`. Paths are browser paths.

| Path | Purpose | Owning location | Status |
|---|---|---|---|
| `/` | Upload an audio file and start a transcription job | `web/frontend/src/pages/` | Proposed |
| `/jobs/:jobId` | Job progress, then the transcript viewer/editor | `web/frontend/src/pages/` | Proposed |
| `/jobs` | List of past transcription jobs | `web/frontend/src/pages/` | Proposed |

## API Endpoints

Served by `web/backend/app/routes/`. All paths are prefixed `/api`.

| Method | Path | Purpose | Status |
|---|---|---|---|
| `POST` | `/api/transcriptions` | Accept an audio upload, create a job, return its id | Proposed |
| `GET` | `/api/transcriptions/{job_id}` | Job status and, when complete, the transcript | Proposed |
| `GET` | `/api/transcriptions` | List jobs | Proposed |
| `DELETE` | `/api/transcriptions/{job_id}` | Delete a job and its artifacts | Proposed |
| `GET` | `/api/health` | Liveness check | Proposed |

Request and response shapes belong in [api-contract.md](api-contract.md), not here. This file is the
index; that file is the contract.

## Conventions

- API routes live under `/api`; everything else is frontend territory.
- Route modules stay thin — validate input, call a service, shape the response. No business logic.
- One route module per resource, in `web/backend/app/routes/`.
- Adding a route requires updating this file, `docs/api-contract.md`, and
  `web/shared/contracts/` in the same change.

## Auth Boundaries

**None defined.** No authentication model has been chosen, so every route above is currently assumed
public. When auth is introduced, add a "Requires auth" column to both tables and record the model in
`docs/architecture.md`. Tracked in `docs/checklist.md`.
