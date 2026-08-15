# Repository Structure

*Last updated: 2026-08-15 (the multi-mode expansion — plans and the mode vocabulary)*

Canonical map of TranscriberPrototype. This file documents **purpose**, not source code. Update it in
the same change that adds, moves, renames, or removes a directory or significant file.

The layout follows **Mode G — API plus separate frontend** from
[skills/repository-structure/structures/web-interfaces.md](skills/repository-structure/structures/web-interfaces.md),
with one adjustment recorded as Decision D-011: `web/frontend/` holds server-rendered templates and
static assets rather than a separately built Node application. There is no npm toolchain.

## Tree — what exists today

```text
TranscriberPrototype/
├── docs/                          # Source of truth for all repository documentation
│   ├── plans/
│   │   ├── README.md              # Plan conventions and index
│   │   ├── live-seminar-transcriber.md   # The 14-phase build plan
│   │   ├── minute-based-transcript-polish.md  # The clean-up pass (D-018)
│   │   ├── transcript-polish-refinements.md   # Dialogue accuracy, timestamps, prose
│   │   ├── asr-hallucination-suppression.md   # Invented speech on silence (D-019)
│   │   ├── multi-mode-ui-design.md            # Expansion 1 — mode vocabulary and interface design
│   │   ├── multi-mode-ui-implementation.md    # Expansion 2 — building that interface
│   │   ├── recorded-transcription.md          # Expansion 3 — toggle to record, transcribe on stop
│   │   ├── window-recording-transcription.md  # Expansion 4 — portal window capture and video
│   │   └── system-integration.md              # Expansion 5 — autostart, tray, global keybinds
│   ├── skills/                    # Canonical skill definitions used by every agent tool
│   │   ├── global-project-rules/SKILL.md
│   │   ├── planner/{SKILL.md,SETUP.md,planner.md}
│   │   └── repository-structure/{SKILL.md,SETUP.md,structures/}
│   ├── documentation.md           # Purpose, stack, architecture summary, decision log
│   ├── structure.md               # This file
│   ├── workflow.md                # Install, run, test, lint, build, env, handoff
│   ├── checklist.md               # Open project work
│   ├── architecture.md            # System design, constraints, component boundaries
│   ├── routes.md                  # HTTP route, page, and WebSocket map
│   ├── component-map.md           # Frontend template and module ownership
│   ├── data-flow.md               # How data moves through the pipeline
│   ├── api-contract.md            # HTTP shapes and the WebSocket event contract
│   ├── deployment.md              # Build, environment, and run targets
│   └── design-system.md           # Design tokens, UI conventions, a11y baseline
│
├── web/                           # ALL web application code
│   ├── backend/app/
│   │   ├── main.py                # FastAPI factory, lifespan, template and static mounting
│   │   ├── paths.py               # Filesystem locations, resolved in one place
│   │   ├── config/                # Layered configuration system
│   │   │   ├── schema.py          # Pydantic models, one per configuration area
│   │   │   ├── defaults.py        # Built-in defaults and the quick-action set
│   │   │   ├── presets.py         # Accuracy / Balanced / Low resource profiles
│   │   │   ├── hotswap.py         # What changing a setting costs the running session
│   │   │   ├── store.py           # Layer resolution, validation, persistence
│   │   │   └── credentials.py     # OS credential store; never a config file
│   │   ├── routes/                # HTTP endpoints — thin, delegate to services
│   │   │   ├── health.py          # Liveness plus installed optional groups
│   │   │   ├── session.py         # Start/stop, devices, model loading, prompts
│   │   │   ├── transcript.py      # Replay, ranges, search, summaries, export
│   │   │   └── config.py          # Read, patch with hot-swap cost, presets, save
│   │   ├── transport/             # The push channel — separate from routes/
│   │   │   ├── events.py          # The event vocabulary and coalescing rules
│   │   │   ├── hub.py             # Fan-out, per-client backpressure, loop marshalling
│   │   │   └── ws.py              # The WebSocket endpoint and reconnection replay
│   │   ├── services/              # The pipeline. Where real work happens.
│   │   │   └── audio/             # Capture — the canonical-format boundary
│   │   │       ├── formats.py     # 16 kHz mono float32; conversion into it
│   │   │       ├── resample.py    # Band-limited rate conversion (soxr)
│   │   │       ├── ring_buffer.py # Fixed-capacity buffer; drop-oldest, counted
│   │   │       ├── level.py       # RMS, peak, clipping for the input meter
│   │   │       ├── preprocess.py  # High-pass filter and gain normalisation
│   │   │       ├── devices.py     # Merged microphone + loopback enumeration
│   │   │       └── sources/       # base, file (real-time WAV), device, synthetic
│   │   │   └── vad/               # Speech detection and pause events
│   │   │       ├── base.py        # The one-question-per-frame detector interface
│   │   │       ├── energy.py      # Dependency-free default, adaptive noise floor
│   │   │       ├── silero.py      # Optional learned detector, same interface
│   │   │       └── hysteresis.py  # Shared debouncing and pause-event emission
│   │   │   └── asr/               # SEAM A — the pluggable speech model
│   │   │       ├── contract.py    # Interface, word tokens, capability declaration
│   │   │       ├── registry.py    # Backend registration and construction
│   │   │       ├── mock.py        # Scripted backend; what makes the engine testable
│   │   │       ├── faster_whisper.py  # The real default, optional dependency
│   │   │       ├── hallucination.py  # Discards text the model invented (D-019)
│   │   │       ├── prompting.py   # Session and rolling-context term biasing
│   │   │       └── lifecycle.py   # Async load, warm-up, swap, unload
│   │   │   └── streaming/         # The core — commit policy and buffering
│   │   │       ├── agreement.py   # LocalAgreement-n, the commit rule
│   │   │       ├── buffer.py      # Growing buffer, trimming, timestamp rebasing
│   │   │       ├── guards.py      # The six guards of BE §7.6
│   │   │       ├── segmenter.py   # Committed words → readable segments
│   │   │       ├── events.py      # The committed / hypothesis output contract
│   │   │       ├── engine.py      # Orchestrator for offline models
│   │   │       └── passthrough.py # Bypass path for streaming-native models
│   │   │   └── polish/            # The minute-by-minute clean-up pass (D-018)
│   │   │       ├── chunker.py     # When a chunk is ready: a minute, then a pause
│   │   │       ├── source.py      # The chunk flattened to one run, timestamps placed
│   │   │       ├── guard.py       # Strips decoration and invented timestamps; catches summaries
│   │   │       ├── prompts.py     # The instruction list, and the no-reasoning hints
│   │   │       └── worker.py      # The background loop and every failure path
│   │   │   └── transcript/        # The durable record
│   │   │       ├── schema.sql     # SQLite tables, FTS5 index, and its triggers
│   │   │       ├── store.py       # Append-only writes, the four queries, search
│   │   │       └── export.py      # Text, Markdown, SRT, VTT, JSON
│   │   │   └── session/           # Wiring, workers, health, degradation
│   │   │       ├── modes.py       # Capture modes and run states (D-020); mirrored in the frontend
│   │   │       ├── workers.py     # Drop-oldest queue and the threads draining it
│   │   │       ├── metrics.py     # Pipeline health, gathered in one place
│   │   │       ├── degradation.py # What each failure means and what to do
│   │   │       └── manager.py     # capture → VAD → engine → store → transport
│   │   │   └── llm/               # SEAM B — the pluggable language model
│   │   │       ├── contract.py    # Interface, streaming chunks, capabilities
│   │   │       ├── openai_compatible.py  # Ollama, LM Studio, llama.cpp, vLLM, OpenAI
│   │   │       ├── anthropic.py   # The one API whose shape genuinely differs
│   │   │       ├── errors.py      # The four-way failure taxonomy
│   │   │       ├── registry.py    # Provider construction from configuration
│   │   │       └── tokens.py      # Budget estimation for context assembly
│   │   │   └── context/           # What the assistant is given when it answers
│   │   │       ├── assembler.py   # Priority-ordered assembly under a token budget
│   │   │       ├── prompts.py     # The chat, summary, and glossary instructions
│   │   │       └── worker.py      # Rolling summaries and glossary extraction
│   │   │   └── chat/              # Question → context → provider → streamed answer
│   │   │       └── orchestrator.py
│   │   ├── models/                # Persistence shape
│   │   │   ├── segment.py         # The unit engine, store, and frontend all agree on
│   │   │   └── session.py         # Metadata, summaries, polished blocks, glossary, chat turns
│   │   └── schemas/
│   │       └── api.py             # Request/response shapes for every route
│   ├── frontend/                  # Server-rendered UI — no build step, no npm
│   │   ├── templates/
│   │   │   ├── base.html          # Document shell; loads the stylesheets and the entry module
│   │   │   ├── pages/app.html     # The live application
│   │   │   ├── macros/icons.html  # Inline SVG icons, inheriting currentColor
│   │   │   └── partials/          # header, status_bar, banners, preflight,
│   │   │                          #   transcript/, chat/, monitor/, settings/
│   │   └── static/
│   │       ├── css/               # tokens, base, layout + one file per component
│   │       └── js/                # main + core/, transport/, stores/, components/, a11y/
│   │           └── core/modes.js  # Mirror of services/session/modes.py; kept identical by test
│   └── shared/contracts/          # Generated: openapi.json, ws-events.json
│
├── libs/                          # Internal packages with more than one consumer
├── utils/                         # Standalone helpers not specific to the web app
├── tests/                         # Python tests, grouped by area
│   ├── api/                       # Routes, transport, audio library
│   ├── assistant/                 # LLM clients, chat, context, and the polish pass
│   ├── transcription/             # Audio, VAD, ASR, streaming engine, session
│   ├── data/                      # Transcript store, search, export, session archive
│   └── utils/                     # Configuration and standalone helpers
├── scripts/                       # Developer and operational scripts
│   ├── make_fixture_wav.py        # Generates synthetic WAV fixtures for pipeline tests
│   ├── run_file_session.py        # Console-only pipeline run over a WAV file (BE M4)
│   └── generate_contracts.py      # Writes openapi.json and ws-events.json
├── data/                          # Sessions, config file, audio (gitignored)
├── logs/                          # Runtime logs (gitignored)
│
├── .claude/skills/ · .agents/skills/ · .cursor/rules/   # Pointers → docs/skills/
├── app.py                         # Launcher: starts everything on port 8395
├── pyproject.toml                 # Python project, optional groups, tool configuration
├── uv.lock                        # Committed, authoritative Python lockfile
├── .env.example                   # Every environment variable, with safe placeholders
├── .gitignore
└── README.md
```

## Planned additions

None outstanding. Every directory the fourteen-phase plan called for now exists; the block that
listed them has been removed rather than left describing work that has landed.

## Top-Level Directory Purposes

| Path | Why it exists |
| :--- | :--- |
| `docs/` | The single source of truth for every durable instruction, decision, and map. Nothing outside it may contradict it. |
| `docs/plans/` | Written implementation plans. Each is a handoff artifact another agent can resume from cold. |
| `docs/skills/` | Canonical skill definitions. The three agent-tool folders point here rather than carrying copies. |
| `web/` | All web application code and assets. Keeps application code out of the repository root. |
| `web/backend/` | The Python side: the pipeline, the HTTP API, and the WebSocket stream. |
| `web/backend/app/config/` | The layered configuration system. Isolated because every stage reads it and nothing else should own it. |
| `web/backend/app/routes/` | HTTP endpoint definitions only. Thin — they delegate to services. |
| `web/backend/app/transport/` | The WebSocket hub and its event envelopes. Separate from `routes/` because it is a push channel with its own reconnection semantics. |
| `web/backend/app/services/` | The pipeline. One sub-package per stage, so each is independently testable. |
| `web/backend/app/services/polish/` | The clean-up pass that rewrites finished minutes for reading. Separate from `context/` because the two do opposite things: `context/` compresses on purpose, and this must not lose a single claim. |
| `web/backend/app/models/` | Persistence shape, separated so storage concerns do not leak into routes. |
| `web/backend/app/schemas/` | Request and response validation — the runtime enforcement of `docs/api-contract.md`. |
| `web/frontend/templates/` | Jinja2 templates, split into many small partials rather than a few large pages. One partial per region of the interface. |
| `web/frontend/static/css/` | Design tokens plus one stylesheet per component. No inline styles. |
| `web/frontend/static/js/` | ES modules: core utilities, transport, the client stores, one controller per component. No bundler. |
| `web/shared/contracts/` | The generated OpenAPI spec and the hand-authored WebSocket event schema, which OpenAPI cannot express. Prevents the two sides drifting apart. |
| `libs/` | Internal packages once code genuinely has more than one consumer. Until then it belongs in `services/`. |
| `utils/` | Small standalone helpers supporting the codebase but not part of the web application. |
| `tests/` | Python tests, grouped by area so the tree stays readable as coverage grows. |
| `scripts/` | Developer and operational scripts — fixture generation, console pipeline runs, soak driving, contract generation. |
| `data/` | Sessions, the user config file, and optionally retained audio. Gitignored: large, local, and often sensitive. |
| `logs/` | Runtime log output. Gitignored. Never contains transcript content. |
| `.claude/`, `.agents/`, `.cursor/` | Tool-specific pointer files. Frontmatter plus a reading list aimed at `docs/`. Never rule content. |
| `app.py` | The entry point: `uv run python app.py`. A launcher only — it loads `.env`, prepares directories, checks the port, and starts the server. It is not an exception to "web application code lives under `web/`", because it contains none: everything it starts lives under `web/`. |

## Test Layout

```text
tests/
├── api/            # Route behaviour and the WebSocket contract
├── assistant/      # LLM providers, chat assembly, context worker, polish chunker/guard/worker
├── transcription/  # Audio sources, VAD, ASR seam, streaming engine, session wiring
├── data/           # Transcript store, queries, search, export formats
└── utils/          # Configuration layers and hot-swap classification
```

Use `tests/<area>/test_<behavior>.py`. Keep shared fixtures close to the area they support.

## Scaffold Status

| Held by | Directories |
|---|---|
| `__init__.py` (required for Python packages) | `libs/`, `utils/`, `tests/` and its four sub-directories, `web/backend/app/{models,schemas}` |
| `.gitkeep` | `data/`, `logs/` |
| `README.md` explaining what belongs there | `web/shared/contracts/`, `docs/plans/` |

## Rules

- **Maximum file length 800 lines; target under 500.** Split rather than grow.
- **`app.py` stays a launcher.** Anything with behaviour belongs under `web/backend/app/`. The
  root file exists so there is one obvious way to start the application, not as a second home for
  application code.
- Every Python sub-package needs an `__init__.py`.
- Documentation lives only in `docs/`. Web application code lives only in `web/`.
- New environment variables go into `.env.example` in the same change that introduces them.
