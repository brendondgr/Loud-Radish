# Repository Structure

*Last updated: 2026-08-14 (repository initialization)*

Canonical map of TranscriberPrototype. This file documents **purpose**, not source code. Update it in
the same change that adds, moves, renames, or removes a directory or significant file — see the
maintenance table in [skills/global-project-rules/SKILL.md](skills/global-project-rules/SKILL.md).

The layout follows **Mode G — API plus separate frontend** from
[skills/repository-structure/structures/web-interfaces.md](skills/repository-structure/structures/web-interfaces.md).

## Tree

```text
TranscriberPrototype/
├── docs/                          # Source of truth for all repository documentation
│   ├── plans/                     # Implementation and handoff plans
│   │   └── README.md              # Plan conventions and index
│   ├── skills/                    # Canonical skill definitions used by every agent tool
│   │   ├── global-project-rules/
│   │   │   └── SKILL.md
│   │   ├── planner/
│   │   │   ├── SKILL.md
│   │   │   ├── SETUP.md
│   │   │   └── planner.md
│   │   └── repository-structure/
│   │       ├── SKILL.md
│   │       ├── SETUP.md
│   │       └── structures/
│   │           ├── web-interfaces.md
│   │           ├── lab-reports.md
│   │           └── langgraph.md
│   ├── documentation.md           # Purpose, stack, architecture summary, decision log
│   ├── structure.md               # This file
│   ├── workflow.md                # Install, run, test, lint, build, env, handoff
│   ├── checklist.md               # Setup Definition of Done + open project work
│   ├── architecture.md            # System design and component boundaries
│   ├── routes.md                  # HTTP route and page map
│   ├── component-map.md           # Frontend component ownership
│   ├── data-flow.md               # How data moves through the system
│   ├── api-contract.md            # Request/response contracts
│   ├── deployment.md              # Build, environment, and deploy targets
│   └── design-system.md           # Design tokens, UI conventions, a11y baseline
│
├── web/                           # ALL web application code
│   ├── backend/                   # Python API + transcription pipeline (uv)
│   │   └── app/
│   │       ├── routes/            # HTTP endpoint definitions
│   │       ├── services/          # Business logic, transcription orchestration
│   │       ├── models/            # Persistence models
│   │       └── schemas/           # Request/response validation schemas
│   ├── frontend/                  # Node/TypeScript browser client (npm)
│   │   └── README.md              # Scaffolding instructions; delete once the frontend exists
│   └── shared/
│       └── contracts/             # API schemas and types both sides depend on
│           └── README.md
│
├── libs/                          # Internal packages with more than one consumer
├── utils/                         # Standalone helpers not specific to the web app
├── tests/                         # Python tests, grouped by area
│   ├── api/
│   ├── transcription/
│   ├── data/
│   └── utils/
├── scripts/                       # Developer and operational scripts
├── data/                          # Input audio and generated transcripts (gitignored)
├── logs/                          # Runtime logs (gitignored)
│
├── .claude/skills/                # Claude Code pointers → docs/skills/
├── .agents/skills/                # OpenAI Codex pointers → docs/skills/
├── .cursor/rules/                 # Cursor rule pointers → docs/skills/
│
├── pyproject.toml                 # Python project + tool configuration
├── uv.lock                        # Committed, authoritative Python lockfile
├── .env.example                   # Every environment variable, with safe placeholders
├── .gitignore
└── README.md                      # Entry point; points at docs/
```

## Top-Level Directory Purposes

| Path | Why it exists |
| :--- | :--- |
| `docs/` | The single source of truth for every durable instruction, decision, and map in this repository. Nothing outside `docs/` may contradict it. |
| `docs/plans/` | Written implementation plans. Each is a handoff artifact another agent can resume from cold. |
| `docs/skills/` | Canonical skill definitions. The three agent-tool folders point here rather than carrying their own copies. |
| `web/` | All web application code, assets, and runtime files. Enforced by `repository-structure`; keeps application code from leaking into the repository root. |
| `web/backend/` | The Python side: HTTP API and the transcription pipeline. Owns its own dependency surface via the root `pyproject.toml`. |
| `web/backend/app/routes/` | HTTP endpoint definitions only. Thin — they delegate to services. |
| `web/backend/app/services/` | Business logic and transcription orchestration. Where real work happens. |
| `web/backend/app/models/` | Persistence models. Separated so storage concerns do not leak into routes. |
| `web/backend/app/schemas/` | Request and response validation schemas, the runtime enforcement of `docs/api-contract.md`. |
| `web/frontend/` | The Node/TypeScript browser client. Separate toolchain and separate lockfile from the backend. |
| `web/shared/contracts/` | Schemas and types both sides depend on — the OpenAPI spec and generated types. Prevents the frontend and backend from drifting apart. |
| `libs/` | Internal packages once a piece of code genuinely has more than one consumer. Until then, code belongs in `web/backend/app/services/`. |
| `utils/` | Small standalone helpers that support the codebase but are not part of the web application itself. |
| `tests/` | Python tests, grouped by area so the tree stays readable as coverage grows. Frontend tests live under `web/frontend/`. |
| `scripts/` | Developer and operational scripts — data seeding, batch runs, maintenance tasks. Keeps one-off tooling out of the application packages. |
| `data/` | Input audio and generated transcripts. Gitignored: these are large, local, and often sensitive. |
| `logs/` | Runtime log output. Gitignored. |
| `.claude/`, `.agents/`, `.cursor/` | Tool-specific pointer files. Contain only frontmatter plus a reading list aimed at `docs/`. Never rule content. |

## Test Layout

```text
tests/
├── api/            # Route and endpoint behavior
├── transcription/  # Transcription pipeline and model adapters
├── data/           # Parsing, serialization, storage
└── utils/          # Helper and utility coverage
```

Use `tests/<area>/test_<behavior>.py`. Keep shared fixtures close to the area they support.

## Scaffold Status

These directories exist but hold no real content yet. Each is retained because it is documented above
and will be filled as the application is built. Remove the placeholder in the change that adds real
content.

| Held by | Directories |
|---|---|
| `__init__.py` (required for Python packages) | `web/backend/app/` and its `routes/`, `services/`, `models/`, `schemas/`; `libs/`; `utils/`; `tests/` and all four sub-directories |
| `.gitkeep` | `scripts/`, `data/`, `logs/` |
| `README.md` explaining what belongs there | `web/frontend/`, `web/shared/contracts/`, `docs/plans/` |

`web/frontend/` is intentionally unscaffolded — it awaits a framework decision and an `npm create`
run. Its `README.md` carries the instructions. See `docs/checklist.md`.

## Rules

- **Maximum file length 800 lines; target under 500.** Split rather than grow.
- Every Python sub-package needs an `__init__.py`.
- Documentation lives only in `docs/`. Web application code lives only in `web/`.
- New environment variables go into `.env.example` in the same change that introduces them.
