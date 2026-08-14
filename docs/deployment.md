# Deployment

*Last updated: 2026-08-14 (repository initialization)*

> **Status: no deployment target selected.** Nothing is deployed and no hosting decision has been
> made. This file records what is already true and what must be decided. Fill it in properly once a
> target is chosen — tracked in `docs/checklist.md`.

## Current State

Local development only. Commands are in [workflow.md](workflow.md).

| Artifact | Build | Notes |
|---|---|---|
| Backend | None — runs from source | Python 3.11+, dependencies via `uv sync` |
| Frontend | `npm --prefix web/frontend run build` | Not yet scaffolded |

## Environments

| Environment | Status |
|---|---|
| Local | The only one that exists |
| Staging | Not defined |
| Production | Not defined |

## Configuration

- All configuration comes from environment variables. No secrets in source, ever.
- `.env.example` is the complete list of variables with safe placeholders.
- `.env` is gitignored and must never be committed.
- Adding a variable means adding it to `.env.example` in the same change.

## Runtime Requirements (known so far)

- **Python 3.11+** and **Node 22** to build and run.
- **Writable `data/` directory** — uploaded audio and generated transcripts land there.
- **Writable `logs/` directory**.
- Transcription is CPU- or GPU-intensive depending on the engine chosen. Sizing cannot be estimated
  until that decision is made.

## Decisions Required Before Deploying

| Decision | Blocks |
|---|---|
| Hosting target — VM, container platform, PaaS, serverless | Everything below |
| Whether the frontend is served by the backend or hosted separately (CDN/static host) | CORS configuration, build pipeline |
| Transcription engine — local model vs. hosted API | Hardware sizing, cost model, secret management |
| Persistence — local files vs. managed database | Backup strategy, migrations |
| Async execution — in-process background tasks vs. separate worker | Process topology, scaling |
| Storage for uploaded audio — local disk vs. object storage | Durability, multi-instance viability |
| Retention policy for audio and transcripts | Privacy and compliance posture |
| TLS termination and reverse proxy | Networking |

Until the async execution model and audio storage decisions are made, **the backend cannot safely run
as more than one instance** — in-process jobs and local-disk audio are not shared across replicas.
Record that constraint here when it is resolved.

## Data Sensitivity

Uploaded audio may contain personal or confidential speech. Treat `data/` accordingly:

- Never commit it. Never include its contents in logs or error messages.
- Any hosted transcription engine means user audio leaves the deployment boundary — that requires an
  explicit, recorded decision, not a default.

## Checklist Before a First Deploy

- [ ] Hosting target chosen and recorded here
- [ ] All environment variables present in `.env.example` and set in the target
- [ ] Health check endpoint (`GET /api/health`) implemented and wired to the platform
- [ ] `data/` and `logs/` writable and backed by durable storage
- [ ] `uv run pytest`, `uv run ruff check .`, and the frontend build all pass
- [ ] Retention and privacy posture for uploaded audio decided and documented
- [ ] Rollback procedure documented here
