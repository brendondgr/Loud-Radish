# TranscriberPrototype — Project Documentation

*Last updated: 2026-08-14 (Phase 5 — streaming engine)*

## Purpose

TranscriberPrototype is the **Live Seminar Transcriber**: a single-user local application that
continuously captures audio from a microphone or from system playback, transcribes it in near-real-time
through a pluggable speech model, maintains a growing timestamped transcript, and exposes that
transcript to a language model so the user can ask questions about a talk while it is happening.

The driving use case is attending a seminar or colloquium on an unfamiliar topic — asking, mid-talk,
*"Summarise the last ten minutes,"* or *"What does the speaker mean by that term?"*

A Python backend owns the entire pipeline and publishes an HTTP plus WebSocket contract. The frontend
is server-rendered from the same process: Jinja2 templates, plain CSS, and vanilla ES modules, with no
build step.

## Status

| Area | Status |
|---|---|
| Repository structure | ✅ Scaffolded |
| Canonical documentation (`docs/`) | ✅ Rewritten for the live-streaming product |
| Implementation plan | ✅ `docs/plans/live-seminar-transcriber.md` — 14 phases |
| Python environment (`uv`) | ✅ Dependencies added; optional groups defined |
| Application skeleton + `GET /api/health` | ✅ Phase 1 |
| Configuration system | ✅ Phase 1 — layers, presets, hot-swap classes, credentials |
| Audio capture | ✅ Phase 2 — formats, resampling, ring buffer, preprocessing, devices, sources |
| Voice activity detection | ✅ Phase 3 — energy and optional Silero detectors, shared hysteresis |
| ASR abstraction (Seam A) | ✅ Phase 4 — contract, registry, mock, faster-whisper, biasing, lifecycle |
| Streaming engine | ✅ Phase 5 — LocalAgreement-2, trimming, rebasing, six guards, segmentation, bypass |
| Transcript store | ⛔ Phase 6 |
| Session manager and metrics | ⛔ Phase 7 |
| Transport (HTTP + WebSocket) | ⛔ Phase 8 |
| LLM abstraction (Seam B) | ⛔ Phase 9 |
| Chat and context pipeline | ⛔ Phase 10 |
| Frontend | ⛔ Phases 11–13 |
| Hardening and soak | ⛔ Phase 14 |

Open work is tracked in [checklist.md](checklist.md); phase status in
[plans/live-seminar-transcriber.md](plans/live-seminar-transcriber.md).

## Tech Stack

| Layer | Choice | Confidence |
|---|---|---|
| Backend language | Python 3.11+ | Confirmed |
| Python env/package manager | `uv` | Confirmed |
| Backend web framework | FastAPI | **Confirmed** — D-004 |
| Frontend | Jinja2 templates + vanilla ES modules + plain CSS, no build step | **Confirmed** — D-011 |
| Transport | HTTP for operations, WebSocket for the live event stream | Confirmed — D-013 |
| Speech recognition | Pluggable. Scripted mock and WAV file source ship; `faster-whisper` optional. | **Confirmed** — D-012 |
| Language model | Pluggable. One OpenAI-compatible client plus a native Anthropic client. | Confirmed — D-014 |
| Persistence | SQLite with FTS5 | **Confirmed** — D-015 |
| Auth | None — single-user, loopback-bound | **Confirmed** — D-016 |
| Deployment target | Runs locally. Desktop packaging deliberately left open. | Open |

## Architecture Overview

Detail in [architecture.md](architecture.md).

```text
microphone / loopback / WAV file
    ▼  audio capture — 16 kHz mono float32, ring-buffered, never blocking
    ▼  voice activity detection — speech flag, hysteresis, pause events
    ▼  streaming engine ◄──► ASR abstraction  (SEAM A: mock, faster-whisper)
    ▼  transcript store — SQLite, append-only, written through on commit
    ├──────────────► transport — WebSocket events ──► browser
    ▼  context pipeline — rolling summaries, glossary, chunk index
    ▼  chat orchestrator ◄──► LLM abstraction  (SEAM B: local, hosted API)
    └──────────────► transport ──► browser
```

The design rests on two swap points. **Seam A** lets the speech model change without touching the
commit logic; **Seam B** lets the language model move between a local server and a hosted API. Both
are narrow interfaces defined early because everything else is replaceable later and these are not.

## Repository Conventions

- **`docs/` is the single source of truth.** Agent folders (`.claude/`, `.agents/`, `.cursor/`) contain
  pointers only.
- **All web application code lives under `web/`.**
- **Python is managed exclusively with `uv`.** No bare `pip`, `poetry`, or `conda`.
- **Files cap at 800 lines**, ideally under 500.
- Full rules: [skills/global-project-rules/SKILL.md](skills/global-project-rules/SKILL.md).

## Supported Agent Environments

Configured: **Claude Code**, **OpenAI Codex**, and **Cursor**.

| Tool | Pointer location |
|---|---|
| Claude Code | `.claude/skills/<skill>/SKILL.md` |
| OpenAI Codex | `.agents/skills/<skill>/SKILL.md` |
| Cursor | `.cursor/rules/<rule>.mdc` |

Antigravity (`.agent/`) and Gemini CLI (`.gemini/`) are not configured.

## Decision Log

| ID | Decision | Rationale |
|---|---|---|
| D-001 | `docs/` is the source of truth; agent folders hold pointers only | Prevents the same rule drifting across three tool-specific copies. |
| D-002 | Mode G layout — API plus separate frontend under `web/` | Keeps the two concerns and their assets cleanly separated. Still holds under D-011; only the frontend's toolchain changed. |
| D-003 | `uv` for Python | The mandated Python manager for this ecosystem. **npm is no longer used** — see D-011. |
| D-004 | **FastAPI confirmed** for the backend | Async-native, which suits a continuous WebSocket event stream alongside ordinary request/response; generates the OpenAPI spec `web/shared/contracts/` needs. The assumption recorded at initialization is now a commitment. |
| D-005 | ~~React + Vite + TypeScript assumed for the frontend~~ | **Reversed by D-011.** |
| D-006 | Only `repository-structure` and `planner` skills adopted | The user selected these two; the others did not exist in this repository. |
| D-007 | Accessibility requirements folded into `docs/design-system.md` | The a11y skills were not adopted, but WCAG 2.1 AA remains a requirement, recorded as documentation. |
| D-008 | No `CHANGELOG.md` | Git history plus this log and `docs/checklist.md` cover it. |
| D-009 | Initializer files deleted after migration | Content migrated into `docs/skills/`; nothing lost. |
| **D-010** | **The product is a live streaming transcriber, not an upload-and-poll job service** | The two design documents specify continuous capture with a persistent event stream. The upload/job model recorded at initialization describes a different application: it has no notion of a live hypothesis tail, a commit policy, or health telemetry, all of which are central here. Keeping both would have meant two contradictory contracts. `architecture.md`, `routes.md`, `api-contract.md`, and `data-flow.md` were rewritten rather than extended. |
| **D-011** | **Frontend is Jinja2 templates + vanilla ES modules + plain CSS, no build step. Reverses D-005.** | The user asked for a heavily compartmentalised template/CSS/JS structure rather than a single file, and for HTML and JavaScript to be the primary artifacts. Server-side includes give that compartmentalisation directly; a framework would add a build step, a lockfile, and a toolchain for a UI that is two panes and a settings modal. It also removes the npm/Node requirement entirely — one runtime, one package manager. The HTTP/WebSocket contract is unchanged, so a framework rewrite later remains possible without touching the backend. |
| **D-012** | **ASR ships as a scripted mock plus a real-time WAV file source; `faster-whisper` lives behind an optional dependency group** | The architecture is explicit that development should happen against recorded audio because live microphone input is not reproducible, and that the commit policy needs a mock backend with scripted outputs to be testable at all. Making the real model optional keeps `uv sync` fast and the test suite free of model downloads, while `uv sync --extra asr-whisper` gives the real thing. Verified: a plain `uv sync` produces an environment with no `faster_whisper`. |
| **D-013** | **HTTP for operations, WebSocket for the live stream** | The transcript is a continuous server-to-client push; everything else is ordinary request/response. Polling a transcript that updates once a second is wasteful and adds latency to the one thing that must feel live. The socket is treated as disposable — reconnection replays from the last segment id, which removes an entire class of bug. |
| **D-014** | **One OpenAI-compatible HTTP client plus a native Anthropic client** | Ollama, llama.cpp's server, LM Studio, vLLM, LocalAI, and OpenAI itself all expose the same chat-completions shape, differing only in base URL. That collapses an apparent provider matrix into two implementations. Anthropic's request/response shape genuinely differs and gets its own client. |
| **D-015** | **SQLite with FTS5 for persistence** | Survives a crash mid-talk, gives full-text search over the transcript for free, and needs no server. A 90-minute session held only in memory is unrecoverable if the process dies at minute 80. |
| **D-016** | **No authentication; bound to `127.0.0.1`** | Single-user local application, so an auth model would be ceremony with no security benefit. The binding is the actual control: an unauthenticated transcript endpoint on a network interface would publish the contents of a private room. Recorded as a decision so it is not mistaken for an oversight. |
| **D-017** | **Credentials go to the OS credential store, never a config file** | Keys in a plaintext config file get committed, backed up, and shared by accident. `keyring` maps to the correct store on every platform; an environment variable is the documented fallback. The frontend is only ever told whether a credential is *present*. |

## Where to Go Next

- Layout and file placement → [structure.md](structure.md)
- Commands and daily workflow → [workflow.md](workflow.md)
- Open work → [checklist.md](checklist.md)
- System design → [architecture.md](architecture.md)
- The build plan → [plans/live-seminar-transcriber.md](plans/live-seminar-transcriber.md)
