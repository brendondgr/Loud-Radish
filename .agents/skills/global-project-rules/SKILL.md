---
name: global-project-rules
description: Read this before any work in this repository — before editing code, creating files, running commands, or answering questions about the project. Defines the required reading list, the uv environment rules, documentation maintenance duties, testing expectations, and cleanup rules.
---

# Global Project Rules

This is a pointer. The canonical rules live in `docs/`.

Read these files before acting:

1. `docs/skills/global-project-rules/SKILL.md` — the repository-wide contract
2. `docs/documentation.md` — purpose, stack, architecture, decision log, status
3. `docs/structure.md` — canonical tree and why each path exists
4. `docs/workflow.md` — install, run, test, lint, build, env, handoff commands
5. `docs/checklist.md` — active work and open follow-ups

Non-negotiables, in brief:

- `docs/` is the single source of truth. This file must never carry rule content of its own.
- Python is managed **only** with `uv`. The frontend has no package manager and no build step.
- All web application code lives under `web/`.
- Documentation updates ship in the same change as the code they describe.
- Files cap at 800 lines, ideally under 500.
