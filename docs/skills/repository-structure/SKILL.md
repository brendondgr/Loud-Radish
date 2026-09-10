---
name: repository-structure
description: Use this skill when setting up, restructuring, documenting, or enforcing repository layout for Loud Radish — creating directories, deciding where a new file belongs, splitting oversized modules, organizing tests, or updating docs/structure.md.
---

# Repository Structure Standard

This is the canonical layout contract for Loud Radish. Read
`docs/skills/global-project-rules/SKILL.md` first, then this file, then `docs/structure.md` for the
current actual tree.

## Selected Layout

Loud Radish uses **Mode G — API plus separate frontend** from
[structures/web-interfaces.md](structures/web-interfaces.md): a Python backend owns the API and
transcription pipeline, and a separate Node frontend owns rendering.

```text
root/
├── docs/       # Source of truth for all repository documentation
├── web/        # All web application code
│   ├── backend/    # Python API + transcription pipeline (uv)
│   ├── frontend/   # Jinja templates + static ES modules (no build step)
│   └── shared/     # Contracts both sides depend on
├── libs/       # Internal shared packages
├── utils/      # Utility functions and helper classes
├── tests/      # Python tests, grouped by area
├── scripts/    # Developer and operational scripts
├── data/       # Input audio, generated transcripts, local artifacts (gitignored)
└── logs/       # Runtime logs (gitignored)
```

**All web application code, assets, and runtime files live under `web/`.** All repository
documentation lives under `docs/`. Neither rule may be bypassed without recording the decision in
`docs/documentation.md`.

## Where Things Belong

| Kind of file | Location |
|---|---|
| HTTP route handlers | `web/backend/app/routes/` |
| Business logic, transcription orchestration | `web/backend/app/services/` |
| Persistence models | `web/backend/app/models/` |
| Request/response schemas | `web/backend/app/schemas/` |
| Backend configuration | `web/backend/app/config.py` |
| Pages and route-level views | `web/frontend/src/pages/` |
| Reusable visual primitives | `web/frontend/src/components/ui/` |
| Nav, sidebars, footers, app chrome | `web/frontend/src/components/layout/` |
| Domain-specific UI (uploader, transcript editor) | `web/frontend/src/features/` |
| React hooks | `web/frontend/src/hooks/` |
| Shared frontend helpers, API client | `web/frontend/src/lib/` |
| Design tokens, global styles | `web/frontend/src/styles/` |
| OpenAPI spec, shared types both sides use | `web/shared/contracts/` |
| Cross-project internal packages | `libs/` |
| Small standalone helpers | `utils/` |
| Python tests | `tests/<area>/test_<behavior>.py` |

## 1. Documentation (`docs/`)

All documentation — architecture, setup guides, structural maps, skills, plans — lives here.

- **Mandatory file:** `docs/structure.md`, kept current with the actual tree.
- It documents purpose, not source code. Explain why each directory exists.
- Web-project documents required by this repository: `docs/architecture.md`, `docs/routes.md`,
  `docs/component-map.md`, `docs/data-flow.md`, `docs/api-contract.md`, `docs/deployment.md`,
  `docs/design-system.md`.

## 2. Utilities (`utils/`)

Utilities that support the main codebase but are not part of the web application itself.

- **Small utilities:** a single file directly in `utils/` (for example `utils/logger.py`).
- **Large utilities:** their own sub-folder when the logic grows into multiple modules.
- **Initialization:** every Python sub-folder needs an `__init__.py`.

Code specific to the API or the transcription pipeline belongs under `web/backend/app/`, not `utils/`.

## 3. Libraries (`libs/`)

Internal packages that are reusable across the repository — or extractable into their own distribution
later — are modularized here. Prefer `web/backend/app/services/` until a piece of code genuinely has
more than one consumer.

## 4. Tests (`tests/`)

Python tests live in a top-level `tests/` directory, grouped into purpose-based sub-directories as
coverage grows.

- Keep tests small and focused; add them as features land.
- Use descriptive names so the tree stays readable.
- Prefer `tests/<area>/test_<behavior>.py` over one oversized flat folder.
- Keep shared fixtures close to the area they support.

Planned sub-directories for this project:

```text
tests/
├── api/            # Route and endpoint behavior
│   └── test_routes.py
├── transcription/  # Transcription pipeline and model adapters
├── data/           # Parsing, serialization, storage
└── utils/          # Helper and utility coverage
```

Frontend tests live beside the frontend under `web/frontend/`, following that toolchain's convention.

## Global Code Guidelines

### File Length Limits

- **Maximum length:** 800 lines.
- **Ideal length:** under 500 lines.
- **Rule:** favor modularity. A file past 800 lines must be split into secondary modules.

### Package Management

- Python: `uv` only — `uv init`, `uv add`, `uv run`. No other Python package manager.
- Frontend: none. No `package.json`, no lockfile, no bundler — see
  `docs/skills/global-project-rules/SKILL.md` §2.

## Other Reference Structures

These are kept for reference in case the project grows a second workflow. They are **not** the active
layout.

- [Web Interfaces](structures/web-interfaces.md) — the active reference; Mode G is selected.
- [Lab Reports](structures/lab-reports.md)
- [LangGraph Structure](structures/langgraph.md)

## Directory Summary Table Format

`docs/structure.md` entries should read like this:

| File / Folder | Purpose |
| :--- | :--- |
| `web/backend/app/routes/` | HTTP endpoint definitions. |
| `web/shared/contracts/` | API schemas shared by frontend and backend. |
| `utils/logger.py` | Lightweight logging helper. |
