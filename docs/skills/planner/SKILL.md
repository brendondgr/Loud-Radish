---
name: planner
description: Use this skill when asked to create, refine, or evaluate an implementation plan, roadmap, migration plan, or structured sequence of work before writing code — including any multi-step or multi-session task in Loud Radish that needs a written handoff artifact.
---

# Plan Creation

Use this skill to produce explicit, hierarchical implementation plans that another agent can execute
without re-asking setup questions.

## Core Reference

Follow the planning format and quality rules in [planner.md](planner.md). That file defines the
required section order: Introduction → Gaps & Unanswered Questions → Hierarchical Steps →
Deliverables Table.

## When To Use

- The user asks to "create a plan", "plan this out", "make a roadmap", or "break this into steps".
- Work spans more than one session, or more than roughly three non-trivial steps.
- A staged approach is needed before code changes.
- The plan must be validated, committed, or handed off phase by phase.

## Project Conventions

These conventions are specific to Loud Radish and take precedence over the generic examples
in `planner.md` where they conflict.

- **Granularity:** full phase-by-phase implementation plans. Each phase must be independently
  verifiable and independently committable.
- **Audience:** agentic coding workflows and solo implementation. Assume the reader is an AI agent
  starting cold with no conversation history — spell out file paths, module names, and commands.
- **Storage:** save plans as `docs/plans/<short-kebab-topic>.md`. Never leave a plan only in chat.
- **Status tracking:** mark each phase's status in the plan as work completes. Do not delete completed
  plans; mark them complete so they remain a record.
- **Validation:** every phase names the concrete verification it requires, drawn from
  `docs/workflow.md` — Python tests via `uv run pytest`, lint/format via `ruff`, plus manual browser
  QA for UI-visible changes. There is no frontend build or type check to run.
- **Git workflow:** each completed and validated phase ends with a commit on a feature branch. Push
  only when the user asks. Use the wording:
  `[Plan Name] ([Current Step] / [Total Steps]) Complete: <sentence describing what was done>`
- **Documentation duty:** every plan must state which `docs/` files each phase updates, per the
  maintenance table in `docs/skills/global-project-rules/SKILL.md`.

## Output Expectations

- Clean Markdown with an introduction, gaps and unanswered questions, hierarchical steps, and a
  deliverables table.
- Concrete locations — files, classes, functions, scripts, directories — for every step.
- State assumptions for simple gaps and proceed; explicitly flag complex gaps as
  *"Human intervention is needed to answer this question."*
- No large code blocks. Name the parts involved; do not write the implementation.
- The deliverables table must include tests that exercise a small subset of the behavior.
