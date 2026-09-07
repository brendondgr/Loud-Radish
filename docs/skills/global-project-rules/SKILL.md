---
name: global-project-rules
description: Read this first, before any work in the Loud Radish repository. Defines the required reading list, the uv/npm environment rules, documentation maintenance duties, testing expectations, and cleanup rules that every AI agent and human contributor must follow.
---

# Global Project Rules

These rules apply to **every** agent and contributor working in this repository, regardless of tool
(Claude Code, OpenAI Codex, Cursor, or a human editor). They are the repository-wide contract.

`docs/` is the single source of truth. Agent folders (`.claude/`, `.agents/`, `.cursor/`) contain
pointers only — never duplicate rule content into them.

## 1. Required Reading Before Any Change

Read these files before editing anything:

1. `docs/skills/global-project-rules/SKILL.md` — this file.
2. `docs/documentation.md` — project purpose, stack, architecture, decisions, status.
3. `docs/structure.md` — canonical repository tree and the reason each path exists.
4. `docs/workflow.md` — install, run, test, lint, format, build, env, and handoff commands.
5. `docs/checklist.md` — active work and open follow-ups.

Additionally, read the skill that matches the task:

- Repository layout, new directories, structure docs → `docs/skills/repository-structure/SKILL.md`
- Implementation plans, roadmaps, staged work → `docs/skills/planner/SKILL.md`

For work touching the web application, also read `docs/architecture.md`, `docs/routes.md`,
`docs/data-flow.md`, `docs/component-map.md`, `docs/api-contract.md`, `docs/design-system.md`,
and — for anything that draws the application's state — `docs/motion-spec.md`.

## 2. Environment Manager Rules

This repository is a Python backend plus a Node frontend. The environment managers are **not** optional.

### Python — `uv` only

- Use `uv` for every Python operation. Never use bare `pip`, `poetry`, `pipenv`, or `conda`.
- Add dependencies with `uv add <package>`; dev-only ones with `uv add --dev <package>`.
- Run anything Python with `uv run <command>` so it executes inside the project environment.
- `uv.lock` is committed and authoritative. Never hand-edit it.
- Never create or activate a virtualenv manually; `uv` owns `.venv/`.

### Frontend — `npm`

- The frontend lives in `web/frontend/` and is managed with `npm`.
- `package-lock.json` is committed. Never hand-edit it.
- Run frontend commands from `web/frontend/`, not from the repository root.

Exact commands live in `docs/workflow.md`. If a command there is wrong, fix `docs/workflow.md` in the
same change — do not work around it silently.

## 3. Documentation Maintenance

Documentation updates are part of the change, not a follow-up task. A change is incomplete if the
relevant doc below was not updated.

| When you... | Update |
|---|---|
| Add, remove, move, or rename a directory or a significant file | `docs/structure.md` |
| Change the stack, architecture, or make a notable technical decision | `docs/documentation.md` |
| Change an install, run, test, lint, format, or build command | `docs/workflow.md` |
| Finish a tracked task, or discover new work | `docs/checklist.md` |
| Add or change an HTTP route or page | `docs/routes.md` |
| Add or change an API request/response shape | `docs/api-contract.md` |
| Add or change a frontend component's ownership | `docs/component-map.md` |
| Change how data moves between browser, API, worker, or storage | `docs/data-flow.md` |
| Change design tokens, colors, typography, or spacing | `docs/design-system.md` |
| Change how the app is built, containerized, or deployed | `docs/deployment.md` |

Rules:

- Keep `docs/structure.md` accurate. It describes purpose, not source code.
- Record *why* a decision was made in `docs/documentation.md`, not only *what* was decided.
- Never invent status. If something is unbuilt, say so and add it to `docs/checklist.md`.

## 4. Planning and Handoff

- Multi-step or multi-session work gets a written plan in `docs/plans/` before implementation.
- Plans follow the format in `docs/skills/planner/planner.md`.
- Name plans `docs/plans/<short-kebab-topic>.md`.
- A plan is the handoff artifact: another agent must be able to resume from it without re-asking
  setup questions.
- Update the plan's step status as phases complete. Do not delete finished plans; mark them complete.

## 5. Change Log Policy

This project does not maintain a separate `CHANGELOG.md`. Git history plus `docs/documentation.md`
("Decision Log") and `docs/checklist.md` serve that purpose. If a released, versioned artifact is ever
published from this repository, introduce `CHANGELOG.md` and record that decision in
`docs/documentation.md`.

## 6. Testing and Verification

- Python tests live in the top-level `tests/` directory, grouped by area:
  `tests/<area>/test_<behavior>.py`. Do not create a single flat test dump.
- Add tests alongside features as they are built. Small and focused beats exhaustive and late.
- Before declaring work done, run the applicable commands from `docs/workflow.md`
  (tests, lint, type check, frontend build).
- If a verification command was not run, say so explicitly and explain why. Never imply a check passed
  when it was skipped.
- Report failures with the actual output. Do not summarize a failure as a success.

## 7. Code Guidelines

- **Maximum file length: 800 lines. Target under 500.** Split oversized files into modules rather than
  letting them grow.
- Prefer modular, single-responsibility files over catch-all `helpers.py` / `misc.ts` dumping grounds.
- Python sub-packages under `utils/` and `libs/` must include `__init__.py`.
- Secrets never enter source control. Add every new environment variable to `.env.example` with a safe
  placeholder value in the same change that introduces it.

## 8. Cleanup Expectations

- Delete scaffolding and placeholder files once real code replaces them.
- Do not leave empty generated directories that serve no documented purpose. If a directory must exist
  before it has content, keep a `.gitkeep` and explain the directory in `docs/structure.md`.
- Do not leave two competing sources of truth for the same rule. Canonical content lives in `docs/`;
  everything else points at it.
- Remove dead code and unused dependencies as part of the change that orphans them.

## 9. Git Workflow

- Work on a feature branch; do not commit directly to `main`.
- Commit and push only when the user asks.
- Write commit messages describing behavior change, not file lists.
- Keep documentation updates in the same commit as the code they describe.

## 10. Supported Agent Environments

This repository maintains pointer files for:

| Tool | Pointer location | Format |
|---|---|---|
| Claude Code | `.claude/skills/<skill>/SKILL.md` | `SKILL.md` with `name` + `description` frontmatter |
| OpenAI Codex | `.agents/skills/<skill>/SKILL.md` | `SKILL.md` with `name` + `description` frontmatter |
| Cursor | `.cursor/rules/<rule>.mdc` | `.mdc` with `description`, `globs`, `alwaysApply` frontmatter |

Adding a new canonical skill under `docs/skills/` requires adding the matching pointer to **all three**
tools in the same change. Antigravity (`.agent/`) and Gemini CLI (`.gemini/`) are not configured; add
them the same way if they are ever needed.

## 11. Setup Completion Rule

Repository setup is **not** complete until the Definition of Done checklist in `docs/checklist.md` has
been verified item by item, by inspection rather than assumption. Do not report setup, or any task with
a checklist in `docs/checklist.md`, as complete until every applicable box is genuinely checked.
