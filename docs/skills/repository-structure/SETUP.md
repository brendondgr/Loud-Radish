# Repository Structure Setup — Recorded Answers

This questionnaire was completed during repository initialization on 2026-08-14. The answers below are
authoritative. Re-run this questionnaire only if the project's shape changes fundamentally; if it does,
update this file **and** `docs/structure.md`, `docs/documentation.md`, and
`docs/skills/repository-structure/SKILL.md` together.

| # | Question | Answer |
|---|---|---|
| 1 | **Web Interface Generation:** web-interface-specific structure, or purely backend/CLI? | Web interface. TranscriberPrototype is a web application. Mode G (API plus separate frontend) from `structures/web-interfaces.md` is selected. |
| 2 | **Primary Runtime:** main runtime or language? | Mixed full-stack. Python managed by `uv` for the backend and transcription pipeline; Node/npm for the frontend. |
| 3 | **Repository Shape:** single app, multi-app workspace, or library/tooling repo? | Single application, internally split into `web/backend` and `web/frontend`. |
| 4 | **Shared Code:** need `libs/`, `utils/`, or contract/schema folders? | Yes to all three. `utils/` for standalone helpers, `libs/` for internal packages with more than one consumer, `web/shared/contracts/` for API schemas and types shared across the frontend/backend boundary. |
| 5 | **Test Structure:** which top-level `tests/` sub-directories should exist? | `tests/api/`, `tests/transcription/`, `tests/data/`, `tests/utils/`. Frontend tests live under `web/frontend/` per that toolchain's convention. |
| 6 | **Generated Documentation:** which structure documents beyond `docs/structure.md`? | `docs/documentation.md`, `docs/workflow.md`, `docs/checklist.md`, `docs/architecture.md`, `docs/routes.md`, `docs/component-map.md`, `docs/data-flow.md`, `docs/api-contract.md`, `docs/deployment.md`, `docs/design-system.md`, plus `docs/plans/`. |

## Deferred Decisions

The user explicitly deferred product and framework specifics at initialization time
("just set up structure"). The following were scaffolded under stated assumptions and are tracked as
open items in `docs/checklist.md`:

- Backend web framework (assumed FastAPI).
- Frontend framework and build tool (assumed React + Vite + TypeScript).
- Transcription engine / model choice.
- Persistence layer, authentication model, and deployment target.

Any agent that learns the real answers must update `docs/documentation.md`,
`docs/architecture.md`, and this file, and tick the matching items in `docs/checklist.md`.
