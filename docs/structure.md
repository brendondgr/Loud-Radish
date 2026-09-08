# Repository Structure

*Last updated: 2026-09-08 (pauses, chunks, and the dictation pipeline — D-061)*

Canonical map of Loud Radish. This file documents **purpose**, not source code. Update it in
the same change that adds, moves, renames, or removes a directory or significant file.

The layout follows **Mode G — API plus separate frontend** from
[skills/repository-structure/structures/web-interfaces.md](skills/repository-structure/structures/web-interfaces.md),
with one adjustment recorded as Decision D-011: `web/frontend/` holds server-rendered templates and
static assets rather than a separately built Node application. There is no npm toolchain.

## Tree — what exists today

```text
TranscriberPrototype/                # the checkout keeps its old name; the product does not (D-038)
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
│   │   ├── system-integration.md              # Expansion 5 — autostart, tray, global keybinds
│   │   └── recording-folders-and-web-export.md  # Per-recording folders, the completion-state
│   │                                            #   repair, and the self-contained export
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
│   ├── design-system.md           # Design tokens, UI conventions, a11y baseline
│   └── motion-spec.md             # The Aperture microphone — state indicator motion, Rev A.03
│
├── web/                           # ALL web application code
│   ├── backend/app/
│   │   ├── main.py                # FastAPI factory, lifespan, template and static mounting
│   │   ├── branding.py            # The product name, and every identifier derived from it —
│   │   │                          #   each paired with the legacy value it migrates from (D-038)
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
│   │   │   ├── recordings.py      # List, re-run, and delete captured audio (D-021)
│   │   │   ├── sessions.py        # Past sessions: list, read, export, web-app export, delete
│   │   │   ├── chat.py            # Ask, cancel, history, the read mark
│   │   │   ├── llm.py             # Language-model probing and credentials
│   │   │   ├── capture.py         # The window capture's preview frame and state (D-022)
│   │   │   ├── pages.py           # The two server-rendered pages
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
│   │   │       ├── hysteresis.py  # Shared debouncing and pause-event emission
│   │   │       └── pauses.py      # The silences in a finished recording — where to cut (D-061)
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
│   │   │   └── capture/           # Recording a window's video, via the desktop portal (D-022)
│   │   │       ├── probe.py       # Five distinct verdicts on whether this machine can
│   │   │       ├── portal.py      # ScreenCast over D-Bus: consent, and a PipeWire node
│   │   │       ├── pipeline.py    # The GStreamer launch line, built from what is installed
│   │   │       ├── recorder.py    # The subprocess, its lifetime, and its finalisation
│   │   │       ├── mux.py         # Combines the video with the session's audio, correcting the
│   │   │                          #   capture-start offset between them (D-035)
│   │   │       └── stitch.py      # Joins the pieces of a capture that had to be restarted onto
│   │   │                          #   one timeline, holding a frame across each gap (D-036)
│   │   │   └── recording/         # Capture to disk, and transcribe it whole (D-021)
│   │   │       ├── layout.py      # One directory per recording, named for when it started (D-032)
│   │   │       ├── sink.py        # Incremental WAV writer; crash-safe header, duration cap
│   │   │       ├── batch.py       # Whole-file pass, one pause-bounded chunk at a time; bypasses
│   │   │       │                  #   agreement, checkpoints on every chunk (D-021, D-045, D-062)
│   │   │       ├── chunks.py      # Cuts a finished file into pieces that each end in a pause,
│   │   │       │                  #   so no boundary falls mid-word (D-061)
│   │   │       ├── job.py         # One pass's state, progress, and the one-at-a-time rule
│   │   │       └── runner.py      # Runs it on a thread, outliving the session that made the file
│   │   │   └── polish/            # The minute-by-minute clean-up pass (D-018)
│   │   │       ├── chunker.py     # When a chunk is ready: a minute, then a pause
│   │   │       ├── source.py      # The chunk flattened to one run, timestamps placed
│   │   │       ├── guard.py       # Strips decoration and invented timestamps; catches summaries
│   │   │       ├── prompts.py     # The instruction list, and the no-reasoning hints
│   │   │       └── worker.py      # The background loop and every failure path
│   │   │   └── transcript/        # The durable record
│   │   │       ├── schema.sql     # SQLite tables, FTS5 index, its triggers, and where a
│   │   │                          #   transcription pass got to (D-045)
│   │   │       ├── store.py       # Append-only writes, the four queries, search
│   │   │       └── export.py      # Text, Markdown, SRT, VTT, JSON
│   │   │   └── session/           # Wiring, workers, health, degradation
│   │   │       ├── modes.py       # Capture modes and run states (D-020); mirrored in the frontend
│   │   │       ├── workers.py     # Drop-oldest queue and the threads draining it
│   │   │       ├── metrics.py     # Pipeline health, gathered in one place
│   │   │       ├── degradation.py # What each failure means and what to do
│   │   │       ├── shapes.py      # The small shapes and tuning numbers the rest of it shares.
│   │   │                          #   A leaf: imports no sibling, so nothing here forms a cycle
│   │   │       ├── frames.py      # What happens to one captured frame, on both threads
│   │   │       ├── background.py  # The status ticker, the context worker, the polish worker
│   │   │       ├── window_capture.py  # The portal, the recorder, the resume, the join
│   │   │       ├── passes.py      # Starting a pass over a finished recording; store retention
│   │   │       ├── sources.py     # What a session listens to, and proving it is listening
│   │   │       └── manager.py     # capture → VAD → engine → store → transport. Owns the
│   │   │                          #   lifecycle; the five modules above are the rest of this one
│   │   │                          #   class, split across files (D-039)
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
│   │   │   └── export/            # A recording as a folder that opens on its own (D-034)
│   │   │       ├── webapp.py      # Builds the ZIP; streams the video rather than reading it in
│   │   │       ├── payload.py     # transcript.json and settings.json; never a credential, and
│   │   │                          #   never the conversation unless asked (D-037)
│   │   │       ├── profile.py     # What a recording measurably is, and what a re-encode would be
│   │   │       ├── presets.py     # The five named plans, mirrored into the frontend
│   │   │       ├── estimate.py    # Size and time, predicted before anything is encoded
│   │   │       ├── encode.py      # The ffmpeg invocation, with progress read from `-progress`
│   │   │       ├── job.py         # One export as stages, weighted by predicted cost
│   │   │       ├── runner.py      # Driving those stages on a background thread
│   │   │       └── template/      # The exported page, copied verbatim: index.html, two
│   │   │                          #   stylesheets, and five classic scripts (not modules —
│   │   │                          #   `import` is refused across file:// URLs)
│   │   ├── services/dictation/    # Press a key, speak, press again: the words arrive in
│   │   │   │                      #   whatever window has focus (D-049)
│   │   │   ├── service.py         # One dictation at a time: record, deliver in the background,
│   │   │   │                      #   paste exactly once, finish at the cap
│   │   │   ├── pipeline.py        # The recording cut at pauses; each chunk transcribed whole
│   │   │   │                      #   and tidied on its own, bounded (D-061)
│   │   │   └── prompts.py         # What the language model is asked to do, and not do
│   │   ├── desktop/               # Talking to the desktop the user is sitting in front of:
│   │   │   │                      #   clipboard, a keystroke into the focused window, and a
│   │   │   │                      #   notification. Ordered, probed backends (D-048)
│   │   │   ├── outcome.py         # What was tried and what happened — never an exception
│   │   │   ├── clipboard.py       # wl-copy → klipper → xclip
│   │   │   ├── keystroke.py       # ydotool → wtype. That order was measured, not assumed
│   │   │   └── notify.py          # org.freedesktop.Notifications, for when nobody is looking
│   │   │                          #   at the browser
│   │   ├── companion/             # The desktop presence — a remote control, not a rewrite (D-024)
│   │   │   ├── visual_states.py   # (capture mode, run state) → which picture the instrument shows
│   │   │   ├── aperture.py        # Draws one frame of it, from docs/motion-spec.md
│   │   │   ├── raster.py         # The same frame as ARGB32 pixels, for the tray's D-Bus
│   │   │   │                     #   icon property. numpy only — no rasteriser (D-042)
│   │   │   ├── animation.py       # The frame clock, and the hold-still setting
│   │   │   ├── menu.py            # The tray menu's structure, as data
│   │   │   ├── tray.py            # The StatusNotifierItem and its dbusmenu, served from a
│   │   │   │                      #   dispatch loop over jeepney's primitives (D-043)
│   │   │   ├── keys.py            # "Meta+Alt+D" ↔ the integer KGlobalAccel speaks, and the
│   │   │   │                      #   "Win+Alt+D" a person reads (D-047, D-057). Its own
│   │   │   │                      #   module because the encoding is the part that is wrong in
│   │   │   │                      #   a way nothing else notices (D-047)
│   │   │   ├── desktop_entry.py   # One .desktop file per shortcut — what the desktop binds to
│   │   │   ├── shortcuts.py       # Registration through the desktop's own service
│   │   │   ├── settings_form.py   # A keypress → a sequence string; conflicts; the patch body.
│   │   │   │                      #   Pure functions, because Tk's loop cannot be tested (D-051)
│   │   │   ├── settings_window.py # The Tk window itself: shortcuts, microphone, dictation
│   │   │   ├── settings.py        # `python -m app.companion.settings` — its own process
│   │   │   └── main.py            # Polls the server; killable and restartable at any moment
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
│   │       ├── brand/radish.svg   # The mark — tab icon, header, export, desktop entry (D-038)
│   │       ├── css/               # tokens, base, layout + one file per component
│   │       └── js/                # main + core/, transport/, stores/, components/, a11y/
│   │           └── core/modes.js  # Mirror of services/session/modes.py; kept identical by test
│   └── shared/contracts/          # Generated: openapi.json, ws-events.json
│
├── libs/                          # Internal packages with more than one consumer
├── utils/                         # Standalone helpers not specific to the web app
│   └── loud_radish_ctl.py         # Drive recording from outside the browser (D-024)
├── tests/                         # Python tests, grouped by area
│   ├── api/                       # Routes, transport, audio library
│   ├── assistant/                 # LLM clients, chat, context, and the polish pass
│   ├── transcription/             # Audio, VAD, ASR, streaming engine, session
│   ├── data/                      # Transcript store, search, export, session archive
│   └── utils/                     # Configuration and standalone helpers
├── scripts/                       # Developer and operational scripts
│   ├── loud-radish.desktop.in     # Desktop entry template, filled in by install_autostart.py
│   ├── make_fixture_wav.py        # Generates synthetic WAV fixtures for pipeline tests
│   ├── measure_capture_cost.py    # Encoding vs. inference contention (D-022)
│   ├── prune_empty_sessions.py    # Clears session databases that hold nothing, and
│   │                              #   refuses the ones that own a recording (D-040)
│   ├── run_file_session.py        # Console-only pipeline run over a WAV file (BE M4)
│   └── generate_contracts.py      # Writes openapi.json and ws-events.json
├── data/                          # Sessions, config file, audio, recordings (gitignored)
│   ├── loud-radish-config.json    # User settings; adopted from transcriber-config.json (D-038)
│   ├── sessions/<stamp>-<id>.db   # One transcript per session
│   └── recordings/<stamp>-<id>/   # One folder per recording — audio.wav, video.<ext>,
│                                  #   video-with-audio.<ext>, preview.jpg, audio.json,
│                                  #   exports/ once one has been exported (D-037).
│                                  #   The folder name is the session database's stem (D-032).
│                                  #   video.002.<ext> and up appear only while a capture that
│                                  #   was restarted is still being joined together (D-036)
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
| `web/backend/app/branding.py` | The product name and every machine identifier built from it, each beside the legacy value it replaces. One file, so the next rename is one diff rather than a hunt through eleven — and so the migration policy is readable in one place instead of scattered across six subsystems. |
| `web/backend/app/config/` | The layered configuration system. Isolated because every stage reads it and nothing else should own it. |
| `web/backend/app/routes/` | HTTP endpoint definitions only. Thin — they delegate to services. |
| `web/backend/app/transport/` | The WebSocket hub and its event envelopes. Separate from `routes/` because it is a push channel with its own reconnection semantics. |
| `web/backend/app/services/` | The pipeline. One sub-package per stage, so each is independently testable. |
| `tests/frontend/` | Accessibility over what the server actually renders: structural rules via `html.parser`, and WCAG contrast over the design tokens. No browser, no build step, no dependency (D-054). |
| `scripts/radish` | The `radish` terminal command: start the server and tray icon in the background, stop them, say what they are doing. Symlinked into `~/.local/bin` (D-056). |
| `scripts/benchmark_asr.py` | Every model, device and precision measured on *this* machine, each in its own process. Exists because the default cannot be chosen from published benchmarks (D-053). |
| `web/backend/app/companion/` | The tray icon and the global shortcuts — the two things a browser page cannot do. Deliberately a *remote control*: it polls the HTTP API, holds no state, and can be killed and restarted without the server noticing. |
| `web/backend/app/services/capture/` | Recording a window's picture. Separate from `recording/` because they solve unrelated problems: one negotiates with a compositor for pixels, the other writes and transcribes audio. |
| `web/backend/app/services/recording/` | Capturing audio to a file and transcribing it once whole. Separate from `streaming/` because the two answer opposite questions: `streaming/` decides what is safe to show while audio is still arriving, and this runs only when it has stopped. |
| `web/backend/app/services/polish/` | The clean-up pass that rewrites finished minutes for reading. Separate from `context/` because the two do opposite things: `context/` compresses on purpose, and this must not lose a single claim. |
| `web/backend/app/models/` | Persistence shape, separated so storage concerns do not leak into routes. |
| `web/backend/app/schemas/` | Request and response validation — the runtime enforcement of `docs/api-contract.md`. |
| `web/frontend/templates/` | Jinja2 templates, split into many small partials rather than a few large pages. One partial per region of the interface. |
| `web/frontend/static/brand/` | The logo, in one place. The application header, the browser tab, the desktop entry and every export reference this single file rather than carrying copies. |
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
| `app.py` | The entry point: `uv run app.py`. A launcher only — it loads `.env`, prepares directories, checks the port, and starts the server. It is not an exception to "web application code lives under `web/`", because it contains none: everything it starts lives under `web/`. |

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
