# TranscriberPrototype

A web application for audio transcription. A Python backend owns the API and the transcription
pipeline; a separate Node frontend owns the browser interface.

> **Current state:** structural scaffold. The repository layout, documentation contract, and
> agent-tooling wiring are in place. The application itself is not yet implemented — see
> [docs/checklist.md](docs/checklist.md).

## Documentation

`docs/` is the single source of truth for this repository. Start there.

| Read this | For |
|---|---|
| [docs/documentation.md](docs/documentation.md) | Purpose, stack, decision log, current status |
| [docs/structure.md](docs/structure.md) | Repository layout and why each directory exists |
| [docs/workflow.md](docs/workflow.md) | Install, run, test, lint, build, and env commands |
| [docs/checklist.md](docs/checklist.md) | What is done and what is still open |
| [docs/architecture.md](docs/architecture.md) | System design and component boundaries |
| [docs/routes.md](docs/routes.md) · [docs/api-contract.md](docs/api-contract.md) | HTTP surface and payload shapes |
| [docs/data-flow.md](docs/data-flow.md) | How data moves through the system |
| [docs/component-map.md](docs/component-map.md) · [docs/design-system.md](docs/design-system.md) | Frontend ownership and UI/accessibility baseline |
| [docs/deployment.md](docs/deployment.md) | Build and deploy (target not yet chosen) |

## Quick Start

Requires Python 3.11+ with [`uv`](https://docs.astral.sh/uv/), and Node 22.

```bash
uv sync
```

```bash
cp .env.example .env
```

Run the checks:

```bash
uv run pytest && uv run ruff check .
```

There is nothing to serve yet — the backend application and the frontend have not been built. Full
command reference: [docs/workflow.md](docs/workflow.md).

## Working in This Repository

Python is managed **only** with `uv`; the frontend is managed with `npm` from `web/frontend/`. All
web application code lives under `web/`, and all documentation under `docs/`.

AI agents must read [docs/skills/global-project-rules/SKILL.md](docs/skills/global-project-rules/SKILL.md)
before making changes. Pointer files for **Claude Code** (`.claude/skills/`), **OpenAI Codex**
(`.agents/skills/`), and **Cursor** (`.cursor/rules/`) all route back to it.
