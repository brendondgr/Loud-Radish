# TranscriberPrototype — Project Documentation

*Last updated: 2026-08-14 (repository initialization)*

## Purpose

TranscriberPrototype is a **web application for audio transcription**. Users interact with it through
a browser: a Python backend owns the API and the transcription pipeline, and a separate Node frontend
owns the interface.

This repository was initialized as a structural scaffold. The layout, documentation contract, and
agent-tooling wiring are complete; the application itself is not yet implemented.

## Status

| Area | Status |
|---|---|
| Repository structure | ✅ Scaffolded |
| Canonical documentation (`docs/`) | ✅ Created |
| Skill definitions (`docs/skills/`) | ✅ Created |
| Agent pointers (Claude Code, Codex, Cursor) | ✅ Created |
| Python environment (`uv`) | ✅ Initialized — `pyproject.toml`, `uv.lock` |
| Backend application code | ⛔ Not started |
| Frontend application code | ⛔ Not scaffolded — `web/frontend/` awaits `npm create` |
| Transcription pipeline | ⛔ Not started |
| Tests | ⛔ None yet — `tests/` tree exists |
| Deployment | ⛔ Not defined |

Open work is tracked in [checklist.md](checklist.md).

## Tech Stack

| Layer | Choice | Confidence |
|---|---|---|
| Backend language | Python 3.11+ | Confirmed |
| Python env/package manager | `uv` | Confirmed |
| Backend web framework | FastAPI | **Assumed** — see Decision Log D-004 |
| Frontend runtime | Node 22 / npm | Confirmed |
| Frontend framework | React + Vite + TypeScript | **Assumed** — see Decision Log D-005 |
| Transcription engine | Undecided | Open |
| Persistence | Undecided | Open |
| Auth | Undecided | Open |
| Deployment target | Undecided | Open |

Items marked **Assumed** were scaffolded so structure work could proceed while the user deferred
product specifics. They are cheap to change now and expensive to change later — confirm them before
substantial code lands. Each has a corresponding item in `docs/checklist.md`.

## Architecture Overview

A three-tier split, documented in detail in [architecture.md](architecture.md):

```text
Browser (web/frontend)
    │  HTTP/JSON, file upload
    ▼
API (web/backend/app/routes)
    │
    ▼
Services (web/backend/app/services) ──▶ Transcription engine
    │
    ▼
Storage (data/, database TBD)
```

The frontend and backend are separate deployables that agree on the contracts in
`web/shared/contracts/`. Neither imports the other's source directly.

## Repository Conventions

- **`docs/` is the single source of truth.** Agent folders (`.claude/`, `.agents/`, `.cursor/`)
  contain pointers only; they never hold rule content of their own.
- **All web application code lives under `web/`.**
- **Python is managed exclusively with `uv`.** No bare `pip`, `poetry`, or `conda`.
- **Files cap at 800 lines**, ideally under 500.
- Full rules: [skills/global-project-rules/SKILL.md](skills/global-project-rules/SKILL.md).

## Supported Agent Environments

Configured during initialization: **Claude Code**, **OpenAI Codex**, and **Cursor**.

| Tool | Pointer location |
|---|---|
| Claude Code | `.claude/skills/<skill>/SKILL.md` |
| OpenAI Codex | `.agents/skills/<skill>/SKILL.md` |
| Cursor | `.cursor/rules/<rule>.mdc` |

Antigravity (`.agent/`) and Gemini CLI (`.gemini/`) were not requested and are not configured.

## Decision Log

| ID | Decision | Rationale |
|---|---|---|
| D-001 | `docs/` is the source of truth; agent folders hold pointers only | Prevents the same rule drifting across three tool-specific copies. Adding a tool becomes a pointer file, not a rule fork. |
| D-002 | Mode G layout — API plus separate frontend under `web/` | Matches the confirmed Python-backend / Node-frontend split. Keeps the two toolchains and their lockfiles cleanly separated. |
| D-003 | `uv` for Python, `npm` for the frontend | `uv` is the mandated Python manager for this ecosystem. npm keeps frontend tooling conventional and lockfile-stable. |
| D-004 | FastAPI assumed for the backend | Async-native, which suits long-running transcription jobs and streaming responses; generates the OpenAPI spec that `web/shared/contracts/` needs. **Assumption — confirm before building.** |
| D-005 | React + Vite + TypeScript assumed for the frontend | The most conventional Node frontend stack; `structures/web-interfaces.md` Mode G assumes a `src/`-based SPA. **Assumption — confirm before building.** |
| D-006 | Only `repository-structure` and `planner` skills adopted | The user selected these two. `website-architecture`, `ui-frontend`, `accessibility-mobile`, and `ada-compliance` were referenced by the initializer but **did not exist in this repository** and were explicitly not selected for synthesis. |
| D-007 | Accessibility requirements folded into `docs/design-system.md` rather than dedicated skills | Since the a11y skills were not adopted but this is a web project, WCAG 2.1 AA and mobile-touch baselines are still recorded — as documentation rather than as skills. Tracked in `docs/checklist.md`. |
| D-008 | No `CHANGELOG.md` | Git history plus this Decision Log and `docs/checklist.md` cover it. Revisit if versioned artifacts are ever published. |
| D-009 | Initializer files deleted after migration | `initialize.md`, `read-yaml.py`, `repo-structure/`, and `plan/` were removed at the user's request once their content was migrated into `docs/skills/`. Nothing was lost — see "Initialization Artifacts" below. |

## Initialization Artifacts

Initialization inputs were removed after their content was migrated. Where each one went:

| Removed | Migrated to |
|---|---|
| `initialize.md` | Its Definition of Done became the setup checklist in `docs/checklist.md`; its rules became `docs/skills/global-project-rules/SKILL.md`. |
| `read-yaml.py` | Not needed after skill discovery; skills are now enumerated in `docs/skills/`. |
| `repo-structure/SKILL.md` | `docs/skills/repository-structure/SKILL.md` (tailored to this project). |
| `repo-structure/SETUP.md` | `docs/skills/repository-structure/SETUP.md` (with answers recorded). |
| `repo-structure/structures/` | `docs/skills/repository-structure/structures/` (verbatim). |
| `plan/SKILL.md` | `docs/skills/planner/SKILL.md` (tailored to this project). |
| `plan/SETUP.md` | `docs/skills/planner/SETUP.md` (with answers recorded). |
| `plan/planner.md` | `docs/skills/planner/planner.md` (verbatim). |

Nothing was retained outside `docs/`.

## Where to Go Next

- Layout and file placement → [structure.md](structure.md)
- Commands and daily workflow → [workflow.md](workflow.md)
- Open work → [checklist.md](checklist.md)
- System design → [architecture.md](architecture.md)
