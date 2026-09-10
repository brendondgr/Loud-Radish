# Plan Setup — Recorded Answers

This questionnaire was completed during repository initialization on 2026-08-14. The answers below are
authoritative and are already reflected in `SKILL.md` under "Project Conventions". If they change,
update both files together.

| # | Question | Answer |
|---|---|---|
| 1 | **Plan Granularity:** brief executive outlines, detailed engineering checklists, or full phase-by-phase implementation plans? | Full phase-by-phase implementation plans. Each phase independently verifiable and committable. |
| 2 | **Validation Workflow:** which verification steps should plans prefer? | Unit tests (`uv run pytest`), lint and format (`ruff`), and manual browser QA for any UI-visible change. *(Corrected 2026-09-10: originally listed a frontend type check and build via npm; there is no Node toolchain.)* Integration tests once an API surface exists. |
| 3 | **Git Workflow:** commit-only, push, pull request, or none? | Commit per validated phase on a feature branch. Push only when the user explicitly asks. Message format: `[Plan Name] ([Step] / [Total]) Complete: <what was done>`. |
| 4 | **Audience:** solo implementation, team handoff, stakeholder review, or agentic coding workflows? | Agentic coding workflows and solo implementation. Plans assume a cold-start reader with no conversation history, so file paths, module names, and commands must be explicit. |

## Notes

`planner.md` holds the generic plan format. Where its examples conflict with the answers above, the
answers above win — specifically on git wording and on where plans are stored
(`docs/plans/<short-kebab-topic>.md`).
