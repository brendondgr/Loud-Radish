# Live Seminar Transcriber

A single-user local application that transcribes a talk as it happens, and lets you ask a language
model questions about what has been said.

It captures audio from a microphone, from system playback, or from a recording; transcribes it in
near-real-time through a pluggable speech model; keeps a growing timestamped transcript; and serves
that transcript to a browser as it is produced.

> **Current state:** thirteen of the plan's fourteen phases are done. Recording, transcription, the
> settings interface, and the assistant all work end to end, and everything is configurable from
> inside the application. Phase 14 — the soak run, the sessions page, and a final documentation pass
> — remains; see [docs/checklist.md](docs/checklist.md).

## Quick Start

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/). There is no Node toolchain and no
build step.

```bash
uv sync
```

```bash
uv run python app.py
```

Then open <http://127.0.0.1:8395>.

Out of the box this runs a **scripted mock speech model** — it transcribes placeholder text, which
is enough to see the interface work without downloading a model. For real transcription:

```bash
uv sync --extra asr-whisper --extra audio-device
```

Then set `asr.backend` to `faster-whisper` in `data/transcriber-config.json`, or point
`audio.source_type` at a `file` and give it a WAV to replay.

Run the checks:

```bash
uv run pytest && uv run ruff check .
```

## How it fits together

```text
microphone / system loopback / WAV file
    ▼  audio capture — 16 kHz mono, ring-buffered, never blocking
    ▼  voice activity detection — speech flag, hysteresis, pause events
    ▼  streaming engine ◄──► ASR abstraction   (swap the speech model here)
    ▼  transcript store — SQLite, append-only, written through on commit
    ▼  transport — HTTP for operations, WebSocket for the live stream
    ▼  browser — committed text, and a tentative tail shown distinctly
```

The design rests on two swap points: the **ASR interface**, so the speech model is a configuration
value rather than an architectural commitment, and the **LLM interface**, so the assistant can be a
local server or a hosted API. [docs/architecture.md](docs/architecture.md) explains both.

Two properties are worth knowing before reading the code. Committed text is **immutable** — once a
sentence is written to the transcript it never changes — and the tentative tail is a **separate
thing entirely**, replaced wholly on each update rather than appended. Confusing the two is the
bug this design exists to prevent.

## Documentation

`docs/` is the single source of truth for this repository. Start there.

| Read this | For |
|---|---|
| [docs/documentation.md](docs/documentation.md) | Purpose, stack, decision log, current status |
| [docs/workflow.md](docs/workflow.md) | Install, run, test, lint, and env commands |
| [docs/checklist.md](docs/checklist.md) | What is done, what remains, and what is unverified |
| [docs/plans/live-seminar-transcriber.md](docs/plans/live-seminar-transcriber.md) | The build plan, phase by phase |
| [docs/architecture.md](docs/architecture.md) | System design, constraints, component boundaries |
| [docs/structure.md](docs/structure.md) | Repository layout and why each directory exists |
| [docs/routes.md](docs/routes.md) · [docs/api-contract.md](docs/api-contract.md) | HTTP surface and event contract |
| [docs/data-flow.md](docs/data-flow.md) | How data moves through the pipeline |
| [docs/component-map.md](docs/component-map.md) · [docs/design-system.md](docs/design-system.md) | Frontend ownership and the accessibility baseline |

## Privacy

With a local speech model and a local language model, no audio and no text leaves the machine, and
the header says so at a glance. Audio is **not** retained by default. The server binds to
`127.0.0.1` because the application has no authentication — exposing it on a network interface
would publish an unauthenticated transcript of a private room.

## Working in This Repository

Python is managed **only** with `uv`. All web application code lives under `web/`, and all
documentation under `docs/`. `app.py` at the root is a launcher and contains no application logic.

AI agents must read [docs/skills/global-project-rules/SKILL.md](docs/skills/global-project-rules/SKILL.md)
before making changes. Pointer files for **Claude Code** (`.claude/skills/`), **OpenAI Codex**
(`.agents/skills/`), and **Cursor** (`.cursor/rules/`) all route back to it.
