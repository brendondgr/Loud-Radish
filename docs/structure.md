# Repository Structure

*Last updated: 2026-08-14 (Phase 8 — transport layer)*

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
│   │   └── live-seminar-transcriber.md   # The 14-phase build plan
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
│   │   │   └── transcript/        # The durable record
│   │   │       ├── schema.sql     # SQLite tables, FTS5 index, and its triggers
│   │   │       ├── store.py       # Append-only writes, the four queries, search
│   │   │       └── export.py      # Text, Markdown, SRT, VTT, JSON
│   │   │   └── session/           # Wiring, workers, health, degradation
│   │   │       ├── workers.py     # Drop-oldest queue and the threads draining it
│   │   │       ├── metrics.py     # Pipeline health, gathered in one place
│   │   │       ├── degradation.py # What each failure means and what to do
│   │   │       └── manager.py     # capture → VAD → engine → store → transport
│   │   ├── models/                # Persistence shape
│   │   │   ├── segment.py         # The unit engine, store, and frontend all agree on
│   │   │   └── session.py         # Metadata, summaries, glossary terms, chat turns
│   │   └── schemas/
│   │       └── api.py             # Request/response shapes for every route
│   ├── frontend/                  # Server-rendered UI (templates + static assets)
│   └── shared/contracts/          # Generated: openapi.json, ws-events.json
│
├── libs/                          # Internal packages with more than one consumer
├── utils/                         # Standalone helpers not specific to the web app
├── tests/                         # Python tests, grouped by area
│   ├── api/                       # Routes, transport, LLM and chat behaviour
│   ├── transcription/             # Audio, VAD, ASR, streaming engine, session
│   ├── data/                      # Transcript store, search, export
│   └── utils/                     # Configuration and standalone helpers
├── scripts/                       # Developer and operational scripts
│   ├── make_fixture_wav.py        # Generates synthetic WAV fixtures for pipeline tests
│   ├── run_file_session.py        # Console-only pipeline run over a WAV file (BE M4)
│   └── generate_contracts.py      # Writes openapi.json and ws-events.json
├── data/                          # Sessions, config file, audio (gitignored)
├── logs/                          # Runtime logs (gitignored)
│
├── .claude/skills/ · .agents/skills/ · .cursor/rules/   # Pointers → docs/skills/
├── pyproject.toml                 # Python project, optional groups, tool configuration
├── uv.lock                        # Committed, authoritative Python lockfile
├── .env.example                   # Every environment variable, with safe placeholders
├── .gitignore
└── README.md
```

## Planned additions

Directories that later phases of `docs/plans/live-seminar-transcriber.md` create. Listed here so the
intended shape is legible before the code lands; **none of these exist yet.**

```text
web/backend/app/
├── services/
│   ├── llm/         # contract, openai_compatible, anthropic, registry, connection           Phase 9
│   ├── context/     # summariser, glossary, chunks, pipeline                                Phase 10
│   └── chat/        # assembly, quick_actions, orchestrator                                 Phase 10

web/frontend/
├── templates/       # base.html, pages/, partials/{transcript,chat,settings}/, macros/  Phases 11-13
└── static/
    ├── css/         # tokens, base, layout, themes, components/                         Phases 11-13
    └── js/          # main, core/, transport/, stores/, components/, a11y/              Phases 11-13
```

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
| `web/backend/app/models/` | Persistence shape, separated so storage concerns do not leak into routes. |
| `web/backend/app/schemas/` | Request and response validation — the runtime enforcement of `docs/api-contract.md`. |
| `web/frontend/templates/` | Jinja2 templates, split into many small partials rather than a few large pages. One partial per region of the interface. |
| `web/frontend/static/css/` | Design tokens plus one stylesheet per component. No inline styles. |
| `web/frontend/static/js/` | ES modules: core utilities, transport, six client stores, one controller per component. No bundler. |
| `web/shared/contracts/` | The generated OpenAPI spec and the hand-authored WebSocket event schema, which OpenAPI cannot express. Prevents the two sides drifting apart. |
| `libs/` | Internal packages once code genuinely has more than one consumer. Until then it belongs in `services/`. |
| `utils/` | Small standalone helpers supporting the codebase but not part of the web application. |
| `tests/` | Python tests, grouped by area so the tree stays readable as coverage grows. |
| `scripts/` | Developer and operational scripts — fixture generation, console pipeline runs, soak driving, contract generation. |
| `data/` | Sessions, the user config file, and optionally retained audio. Gitignored: large, local, and often sensitive. |
| `logs/` | Runtime log output. Gitignored. Never contains transcript content. |
| `.claude/`, `.agents/`, `.cursor/` | Tool-specific pointer files. Frontmatter plus a reading list aimed at `docs/`. Never rule content. |

## Test Layout

```text
tests/
├── api/            # Route behaviour, WebSocket contract, LLM providers, chat assembly
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
| `README.md` explaining what belongs there | `web/frontend/`, `web/shared/contracts/`, `docs/plans/` |

`web/frontend/README.md` is deleted in Phase 11, when the template tree replaces it.

## Rules

- **Maximum file length 800 lines; target under 500.** Split rather than grow.
- Every Python sub-package needs an `__init__.py`.
- Documentation lives only in `docs/`. Web application code lives only in `web/`.
- New environment variables go into `.env.example` in the same change that introduces them.
