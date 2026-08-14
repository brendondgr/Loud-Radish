# Plans

Implementation and handoff plans live here, one file per topic:
`docs/plans/<short-kebab-topic>.md`.

Follow the format in [../skills/planner/planner.md](../skills/planner/planner.md) and the project
conventions in [../skills/planner/SKILL.md](../skills/planner/SKILL.md).

## Rules

- Any work spanning more than one session, or more than roughly three non-trivial steps, gets a plan
  **before** implementation.
- A plan is a handoff artifact. Another agent must be able to resume from it cold, without the
  conversation that produced it — so name concrete files, modules, and commands.
- Update phase status as work lands. Mark finished plans complete; do not delete them.
- Every phase names its verification step and which `docs/` files it updates.

## Index

| Plan | Topic | Status |
|---|---|---|
| [live-seminar-transcriber.md](live-seminar-transcriber.md) | Full vertical slice — live audio capture, streaming ASR, transcript store, LLM chat, and the Jinja2 + ES module frontend | In progress (9 / 14 phases: 1–8 and 11) |
