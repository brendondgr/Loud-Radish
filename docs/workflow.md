# Workflow

*Last updated: 2026-08-14 (repository initialization)*

Every command needed to work in TranscriberPrototype. If a command here is wrong, fix this file in the
same change — do not work around it silently.

## Environment Managers

| Side | Manager | Non-negotiable |
|---|---|---|
| Python backend | **`uv`** | Never use bare `pip`, `poetry`, `pipenv`, or `conda`. `uv` owns `.venv/`. |
| Frontend | **`npm`** | Run from `web/frontend/`, not the repository root. |

Both lockfiles (`uv.lock`, `web/frontend/package-lock.json`) are committed and authoritative. Never
hand-edit either.

## Prerequisites

| Tool | Version used at initialization |
|---|---|
| Python | 3.11+ |
| `uv` | 0.11+ |
| Node | 22.x |
| npm | bundled with Node |

## Install

Python dependencies (from the repository root):

```bash
uv sync
```

Frontend dependencies (once `web/frontend/` is scaffolded):

```bash
npm --prefix web/frontend install
```

## Adding Dependencies

```bash
uv add <package>
```

```bash
uv add --dev <package>
```

```bash
npm --prefix web/frontend install <package>
```

## Run

Backend API — **not yet implemented.** Once `web/backend/app/main.py` exists, the FastAPI dev server
runs as:

```bash
uv run uvicorn app.main:app --reload --app-dir web/backend --port 8000
```

Frontend dev server — **not yet scaffolded.** Once `web/frontend/` exists:

```bash
npm --prefix web/frontend run dev
```

Any Python command runs inside the project environment via `uv run`:

```bash
uv run python -c "import sys; print(sys.version)"
```

## Test

Python tests:

```bash
uv run pytest
```

A single area:

```bash
uv run pytest tests/api
```

Frontend tests — configured when the frontend is scaffolded. Record the command here at that time.

Tests live in `tests/<area>/test_<behavior>.py`. Add them alongside features, not afterwards.

## Lint and Format

```bash
uv run ruff check .
```

```bash
uv run ruff format .
```

Check formatting without writing changes:

```bash
uv run ruff format --check .
```

Frontend lint and type check — configured when the frontend is scaffolded (expected
`npm --prefix web/frontend run lint` and `npm --prefix web/frontend run typecheck`).

## Build

The backend has no build step; it runs from source.

Frontend production build, once scaffolded:

```bash
npm --prefix web/frontend run build
```

## Environment Variables

- `.env.example` lists every variable the project uses, with safe placeholder values.
- Copy it to `.env` locally. `.env` is gitignored and must never be committed.
- **Every new variable goes into `.env.example` in the same change that introduces it.**

```bash
cp .env.example .env
```

## Verification Before Declaring Work Done

Run what applies to the change:

1. `uv run pytest` — Python behavior.
2. `uv run ruff check .` — lint.
3. `uv run ruff format --check .` — formatting.
4. `npm --prefix web/frontend run build` — frontend compiles (once scaffolded).
5. Manual browser QA for any UI-visible change, including a mobile-width viewport and a
   keyboard-only pass — see `docs/design-system.md`.

**If a command was not run, say so and explain why.** Never imply a check passed when it was skipped.
Report failures with the actual output.

## Documentation Maintenance

Documentation updates ship with the code, not after it. The full trigger table is in
[skills/global-project-rules/SKILL.md](skills/global-project-rules/SKILL.md) §3. In short:

| Change | Update |
|---|---|
| Directory or significant file added/moved/removed | `docs/structure.md` |
| Stack, architecture, or notable decision | `docs/documentation.md` |
| Any command on this page | `docs/workflow.md` |
| Task finished or new work found | `docs/checklist.md` |
| Route or page | `docs/routes.md` |
| API shape | `docs/api-contract.md` + `web/shared/contracts/` |
| Component ownership | `docs/component-map.md` |
| Data movement | `docs/data-flow.md` |
| Design tokens or UI conventions | `docs/design-system.md` |
| Build or deploy | `docs/deployment.md` |

## Planning and Handoff

- Multi-step or multi-session work gets a plan in `docs/plans/<short-kebab-topic>.md` **before**
  implementation, following `docs/skills/planner/planner.md`.
- A plan must let another agent resume cold, without re-asking setup questions.
- Update phase status as work lands. Mark finished plans complete; do not delete them.
- Handoff = the plan plus current `docs/checklist.md` state. Nothing important should live only in
  chat history.

## Git Workflow

- Work on a feature branch. Do not commit directly to `main`.
- Commit per validated phase. Push only when the user explicitly asks.
- Phase commit message format:
  `[Plan Name] ([Step] / [Total]) Complete: <sentence describing what was done>`
- Keep documentation updates in the same commit as the code they describe.

## Supported Agent Environments

Configured: **Claude Code**, **OpenAI Codex**, **Cursor**.

| Tool | Pointer location | Format |
|---|---|---|
| Claude Code | `.claude/skills/<skill>/SKILL.md` | `SKILL.md` + `name`/`description` frontmatter |
| OpenAI Codex | `.agents/skills/<skill>/SKILL.md` | `SKILL.md` + `name`/`description` frontmatter |
| Cursor | `.cursor/rules/<rule>.mdc` | `.mdc` + `description`/`globs`/`alwaysApply` frontmatter |

Adding a canonical skill under `docs/skills/` requires adding the matching pointer to **all three**
tools in the same change. Antigravity and Gemini CLI are not configured.
