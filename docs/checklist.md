# Project Checklist

*Last updated: 2026-08-14 (repository initialization)*

The active work list for TranscriberPrototype. Update it whenever a task is finished or new work is
discovered — see the maintenance table in
[skills/global-project-rules/SKILL.md](skills/global-project-rules/SKILL.md).

---

## Part 1 — Setup Definition of Done

Verified item by item on 2026-08-14 by inspecting the repository after cleanup.

### Intake Complete

- [x] Project goal, runtime, deliverables, supported tools, and validation workflow are known
- [x] Ambiguous answers resolved, or defaults chosen and recorded as explicit assumptions
- [x] Website architecture questions answered — Mode G selected; product-level specifics were
      explicitly deferred by the user and are tracked in Part 2 below

### Canonical Docs Complete

- [x] `docs/` exists
- [x] `docs/documentation.md` — purpose, stack, architecture summary, decision log, status
- [x] `docs/structure.md` — matches the actual tree
- [x] `docs/workflow.md` — install, run, test, lint, build, env, docs, handoff
- [x] `docs/checklist.md` — this file
- [x] `docs/plans/` exists, with a README and index
- [x] `docs/skills/` exists
- [x] `docs/skills/global-project-rules/SKILL.md` names the required reading for every agent
- [x] Every selected skill has a canonical folder: `repository-structure/`, `planner/`
- [x] Supporting files preserved: `SETUP.md` (both skills, with answers recorded),
      `planner.md`, `structures/` (all three, byte-identical to the originals)
- [x] Web-project docs created: `architecture.md`, `routes.md`, `component-map.md`,
      `data-flow.md`, `api-contract.md`, `deployment.md`, `design-system.md`

### Agent Pointers Complete

- [x] Claude Code — `.claude/skills/{global-project-rules,repository-structure,planner}/SKILL.md`
- [x] OpenAI Codex — `.agents/skills/{global-project-rules,repository-structure,planner}/SKILL.md`
- [x] Cursor — `.cursor/rules/{global-project-rules,repository-structure,planner}.mdc`
- [x] Every pointer has valid frontmatter for its tool
- [x] Every pointer references `docs/skills/global-project-rules/SKILL.md`
- [x] Every pointer references its canonical skill under `docs/skills/`
- [x] No agent folder holds the only copy of any instruction

### Project Structure Complete

- [x] Top-level directories exist: `web/`, `docs/`, `libs/`, `utils/`, `tests/`, `scripts/`,
      `data/`, `logs/`
- [x] All web application code is under `web/` (Mode G: `backend/`, `frontend/`, `shared/`)
- [x] `pyproject.toml` and `uv.lock` exist; `uv sync` succeeds
- [x] `.env.example` exists with every currently-known variable
- [x] `.gitignore` excludes `.env`, `.venv/`, `node_modules/`, `data/`, `logs/`
- [x] `README.md` points readers to `docs/`
- [x] Frontend runtime files documented as intentionally deferred — see Part 2

### Cleanup Complete

- [x] `repo-structure/` deleted after migration to `docs/skills/repository-structure/`
- [x] `plan/` deleted after migration to `docs/skills/planner/`
- [x] `read-yaml.py` deleted
- [x] `initialize.md` deleted (user confirmed at intake)
- [x] Nothing intentionally retained outside `docs/`; migration map is in
      `docs/documentation.md` § Initialization Artifacts
- [x] No duplicate competing sources of truth remain

### Verification Complete

- [x] Final repository tree listed and inspected after cleanup
- [x] Generated canonical docs opened and checked
- [x] Representative pointer files opened and checked
- [x] Every pointer target verified to exist on disk
- [x] `uv sync` — succeeded
- [x] `uv run ruff check .` — passes
- [x] `uv run ruff format --check .` — passes
- [x] `uv run pytest` — runs, collects 0 tests (no tests exist yet; tracked in Part 2)
- [ ] Frontend build — **not run.** `web/frontend/` is not scaffolded, so there is nothing to build
- [x] Remaining gaps listed in Part 2 below

**Setup status: complete.** The one unchecked box is a deliberate deferral, not a failure.

---

## Part 2 — Open Project Work

Nothing below blocks setup. All of it blocks having a working application.

### Decisions to Confirm (cheap now, expensive later)

- [ ] **Backend framework** — FastAPI assumed (Decision D-004). Confirm before writing routes
- [ ] **Frontend framework** — React + Vite + TypeScript assumed (Decision D-005). Confirm before
      scaffolding `web/frontend/`
- [ ] **Transcription engine** — local model vs. hosted API. Drives dependencies, hardware, cost,
      latency, and whether user audio leaves the deployment boundary
- [ ] **Persistence** — files on disk only, or a database for job records
- [ ] **Async execution model** — in-process background tasks, worker process, or queue. Until this
      is settled the backend cannot safely run more than one instance
- [ ] **Progress reporting** — polling (assumed throughout the docs) vs. SSE or websockets
- [ ] **Authentication and multi-user support** — currently every route is assumed public
- [ ] **Deployment target** — blocks all of `docs/deployment.md`
- [ ] **Retention policy** for uploaded audio and transcripts

Update `docs/documentation.md`, `docs/architecture.md`, and the affected docs when each is decided.

### Backend

- [ ] Add the web framework dependency via `uv add`
- [ ] Create `web/backend/app/main.py` and verify the run command in `docs/workflow.md`
- [ ] Implement `GET /api/health`
- [ ] Implement the transcription endpoints proposed in `docs/routes.md`
- [ ] Define request/response schemas in `web/backend/app/schemas/`
- [ ] Implement the transcription service and engine adapter in `web/backend/app/services/`
- [ ] Generate the OpenAPI spec into `web/shared/contracts/`

### Frontend

- [ ] Scaffold `web/frontend/` and delete its placeholder README
- [ ] Record the real install, dev, build, lint, typecheck, and test commands in `docs/workflow.md`
- [ ] Generate typed API client from `web/shared/contracts/`
- [ ] Define design tokens in `web/frontend/src/styles/` and fill in the token table in
      `docs/design-system.md`
- [ ] Build the upload, job status, and transcript views
- [ ] Choose automated accessibility tooling (axe or equivalent) and wire it into the frontend checks

### Testing

- [ ] Write the first Python tests — `tests/` is scaffolded but empty
- [ ] Add API tests once endpoints exist
- [ ] Add transcription pipeline tests
- [ ] Configure frontend testing

### Documentation Debt

- [ ] Replace the proposed shapes in `docs/api-contract.md` with the implemented ones
- [ ] Fill in `docs/deployment.md` once a target is chosen
- [ ] Fill in the token table in `docs/design-system.md`
- [ ] Keep `docs/routes.md` and `docs/component-map.md` current as real routes and components land

### Known Scope Gaps

- [ ] **Accessibility and UI skills were not adopted.** The initializer referenced
      `website-architecture`, `ui-frontend`, `accessibility-mobile`, and `ada-compliance`, but those
      directories did not exist in this repository and were not selected for synthesis
      (Decision D-006). Their substance is captured as documentation in `docs/design-system.md` and
      `docs/architecture.md` rather than as invokable skills. Promote them to
      `docs/skills/<name>/` — with pointers added to all three agent tools — if enforcement at the
      skill level is wanted
- [ ] **Supported audio formats and maximum upload size** are unspecified in `docs/api-contract.md`
- [ ] **Large-file upload handling** (chunking, size ceiling) is undecided
