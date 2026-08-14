# Architecture

*Last updated: 2026-08-14 (repository initialization)*

> **Status: planned, not built.** This document records the intended shape agreed at initialization.
> Framework choices marked *(assumed)* were defaults chosen so structure work could proceed while
> product specifics were deferred. Confirm them before substantial code lands — see
> `docs/checklist.md`.

## Application Mode

**Mode G — API plus separate frontend.** A Python backend owns the HTTP API and the transcription
pipeline; a Node frontend owns rendering. They are separate deployables that agree only on the
contracts in `web/shared/contracts/`.

Chosen because the runtime split (Python + `uv` for transcription work, Node for the UI) was
confirmed by the user, and because transcription is long-running work that benefits from a backend
that can own jobs independently of any browser session.

## Components

```text
┌──────────────────────────────┐
│  Browser                     │
│  web/frontend/               │  React + Vite + TS (assumed)
│  upload · progress · editor  │
└───────────────┬──────────────┘
                │ HTTP/JSON + multipart upload
                ▼
┌──────────────────────────────┐
│  API layer                   │
│  web/backend/app/routes/     │  FastAPI (assumed)
│  thin — validates, delegates │
└───────────────┬──────────────┘
                │
                ▼
┌──────────────────────────────┐
│  Service layer               │
│  web/backend/app/services/   │
│  job orchestration, business │
│  logic, engine adapters      │
└───────┬──────────────┬───────┘
        │              │
        ▼              ▼
┌───────────────┐  ┌──────────────────┐
│ Transcription │  │ Storage          │
│ engine (TBD)  │  │ data/ + DB (TBD) │
└───────────────┘  └──────────────────┘
```

## Layer Responsibilities

| Layer | Location | Owns | Must not |
|---|---|---|---|
| Frontend | `web/frontend/src/` | Rendering, client state, user interaction | Contain business rules the backend also needs |
| Contracts | `web/shared/contracts/` | OpenAPI spec, shared types | Contain logic |
| Routes | `web/backend/app/routes/` | HTTP shape, validation, status codes | Contain business logic |
| Schemas | `web/backend/app/schemas/` | Request/response validation | Reach into storage |
| Services | `web/backend/app/services/` | Orchestration, business logic, engine adapters | Know about HTTP |
| Models | `web/backend/app/models/` | Persistence shape | Contain business logic |

The one rule that matters: **routes stay thin, services hold the work, and neither the frontend nor
the backend imports the other's source.**

## Frontend / Backend Boundary

- The only coupling is HTTP plus the contracts in `web/shared/contracts/`.
- Types consumed by the frontend are generated from the backend's OpenAPI spec, not hand-copied.
- A contract change updates `docs/api-contract.md`, `web/shared/contracts/`, and both sides together.

## Long-Running Work

Transcription is slow relative to an HTTP request. The intended model:

1. Client uploads audio; the API accepts it and returns a job identifier immediately.
2. The service layer runs transcription out of band.
3. The client polls job status, or subscribes to updates.
4. The completed transcript is fetched by job identifier.

Whether out-of-band execution uses background tasks, a worker process, or a queue is **undecided** and
depends on the deployment target. Tracked in `docs/checklist.md`.

## User Roles and Auth Boundaries

**Undecided.** No authentication model has been selected. Until one is, assume every endpoint is
unauthenticated and do not store user-identifying data. Record the decision in `docs/documentation.md`
and this file when it is made.

## Open Architectural Questions

| Question | Impact if deferred |
|---|---|
| Backend framework — FastAPI assumed | Cheap to change now; expensive once routes exist |
| Frontend framework — React/Vite/TS assumed | Cheap to change now; expensive once components exist |
| Transcription engine (local model vs. hosted API) | Drives dependencies, hardware needs, cost, and latency |
| Persistence (files only vs. database) | Drives `models/`, migrations, and deployment |
| Async execution model (background task vs. worker vs. queue) | Drives deployment topology |
| Authentication and multi-user support | Drives routes, models, and data isolation |
| Deployment target | Drives `docs/deployment.md` in full |

All are tracked as open items in `docs/checklist.md`.
