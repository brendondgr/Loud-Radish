# Workflow

*Last updated: 2026-08-14 (Phase 5 — streaming engine)*

Every command needed to work in TranscriberPrototype. If a command here is wrong, fix this file in the
same change — do not work around it silently.

## Environment Manager

| Side | Manager | Non-negotiable |
|---|---|---|
| Everything | **`uv`** | Never use bare `pip`, `poetry`, `pipenv`, or `conda`. `uv` owns `.venv/`. |

There is **no Node toolchain**. The frontend is served by the backend as Jinja2 templates plus plain
CSS and ES modules, with no build step — see Decision D-011 in `docs/documentation.md`. `uv.lock` is
committed and authoritative; never hand-edit it.

## Prerequisites

| Tool | Version |
|---|---|
| Python | 3.11+ |
| `uv` | 0.11+ |

## Install

```bash
uv sync
```

This gives a lean environment: the API, the pipeline, and the test suite, with no model weights and no
audio device bindings.

### Optional dependency groups

Each adds one capability. Install only what you need.

| Group | Adds | Install |
|---|---|---|
| `asr-whisper` | Real transcription via `faster-whisper` | `uv sync --extra asr-whisper` |
| `audio-device` | Live microphone and loopback capture via `sounddevice` | `uv sync --extra audio-device` |
| `vad-silero` | The Silero voice-activity detector | `uv sync --extra vad-silero` |
| `credentials` | OS credential store for API keys via `keyring` | `uv sync --extra credentials` |

Several at once:

```bash
uv sync --extra asr-whisper --extra audio-device --extra credentials
```

`GET /api/health` reports which groups are present, so a missing one surfaces there rather than as a
confusing failure at record time.

## Adding Dependencies

```bash
uv add <package>
```

```bash
uv add --dev <package>
```

```bash
uv add --optional <group> <package>
```

## Run

The application server — API, WebSocket, and the frontend, from one process:

```bash
uv run uvicorn app.main:app --reload --app-dir web/backend --port 8000
```

It binds to `127.0.0.1`. That is deliberate: the application is single-user and unauthenticated, so
exposing it on a network interface would publish an unauthenticated transcript of a private room.

Any Python command runs inside the project environment via `uv run`:

```bash
uv run python -c "import sys; print(sys.version)"
```

## Running the pipeline without a UI

The streaming engine can be driven end to end over a recorded file, with console output only. This
is the validation the architecture calls for before any interface exists — and it is how the commit
policy's behaviour, including its inherent 2–4 second latency, is inspected directly.

```bash
uv run python scripts/make_fixture_wav.py --out data/fixtures
```

```bash
uv run python scripts/run_file_session.py data/fixtures/alternating-20s.wav
```

With a real model, once the optional group is installed:

```bash
uv run python scripts/run_file_session.py talk.wav --backend faster-whisper --model small
```

Replay faster than real time for a long recording:

```bash
uv run python scripts/run_file_session.py talk.wav --speed 10
```

## Regenerating the shared contracts

Run this whenever a route or a WebSocket event changes, and commit the result:

```bash
uv run python scripts/generate_contracts.py
```

## Test

```bash
uv run pytest
```

A single area:

```bash
uv run pytest tests/transcription
```

Tests live in `tests/<area>/test_<behavior>.py`. Add them alongside features, not afterwards.

## Lint and Format

```bash
uv run ruff check .
```

```bash
uv run ruff format .
```

Check formatting without writing changes:

```bash
uv run ruff format --check .
```

## Build

There is no build step. The backend runs from source, and the frontend is served as-is.

## Environment Variables

- `.env.example` lists every variable the project uses, with safe placeholder values.
- Copy it to `.env` locally. `.env` is gitignored and must never be committed.
- **Every new variable goes into `.env.example` in the same change that introduces it.**
- **API keys never go into the config file.** They live in the OS credential store, with an
  environment variable as the documented fallback.

```bash
cp .env.example .env
```

## Verification Before Declaring Work Done

Run what applies to the change:

1. `uv run pytest` — Python behaviour.
2. `uv run ruff check .` — lint.
3. `uv run ruff format --check .` — formatting.
4. Manual browser QA for any UI-visible change, including a 320 px viewport and a keyboard-only pass —
   see `docs/design-system.md`.

**If a command was not run, say so and explain why.** Never imply a check passed when it was skipped.
Report failures with the actual output.

### What cannot be verified in a headless environment

State these explicitly rather than implying coverage:

- **Live microphone and system-loopback capture.** No audio device is present. Capture code is unit
  tested against a fake device; confirming real audio arrives is a manual step on the user's machine.
- **Real-model transcription.** `faster-whisper` and its weights are not installed by default.
  Measuring real-time factor on real hardware is a manual step.
- **Live LLM endpoints.** Provider clients are tested against a stubbed HTTP transport. Pointing at a
  real Ollama or Anthropic endpoint is a manual step.

## Documentation Maintenance

Documentation updates ship with the code, not after it. The full trigger table is in
[skills/global-project-rules/SKILL.md](skills/global-project-rules/SKILL.md) §3. In short:

| Change | Update |
|---|---|
| Directory or significant file added/moved/removed | `docs/structure.md` |
| Stack, architecture, or notable decision | `docs/documentation.md` |
| Any command on this page | `docs/workflow.md` |
| Task finished or new work found | `docs/checklist.md` |
| Route, page, or WebSocket event | `docs/routes.md` |
| API or event shape | `docs/api-contract.md` + `web/shared/contracts/` |
| Template or module ownership | `docs/component-map.md` |
| Data movement | `docs/data-flow.md` |
| Design tokens or UI conventions | `docs/design-system.md` |
| Build or run procedure | `docs/deployment.md` |

## Planning and Handoff

- Multi-step or multi-session work gets a plan in `docs/plans/<short-kebab-topic>.md` **before**
  implementation, following `docs/skills/planner/planner.md`.
- The active plan is [plans/live-seminar-transcriber.md](plans/live-seminar-transcriber.md). Update its
  phase status table as work lands.
- Handoff = the plan plus current `docs/checklist.md` state. Nothing important lives only in chat.

## Git Workflow

- Work on a feature branch. Do not commit directly to `main`.
- Commit per validated phase. Push only when the user explicitly asks.
- Phase commit message format:
  `[Plan Name] ([Step] / [Total]) Complete: <sentence describing what was done>`
- Keep documentation updates in the same commit as the code they describe.

## Supported Agent Environments

Configured: **Claude Code**, **OpenAI Codex**, **Cursor**.

| Tool | Pointer location | Format |
|---|---|---|
| Claude Code | `.claude/skills/<skill>/SKILL.md` | `SKILL.md` + `name`/`description` frontmatter |
| OpenAI Codex | `.agents/skills/<skill>/SKILL.md` | `SKILL.md` + `name`/`description` frontmatter |
| Cursor | `.cursor/rules/<rule>.mdc` | `.mdc` + `description`/`globs`/`alwaysApply` frontmatter |

Adding a canonical skill under `docs/skills/` requires adding the matching pointer to **all three**
tools in the same change.
