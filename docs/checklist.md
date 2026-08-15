# Project Checklist

*Last updated: 2026-08-14 (Phase 11 — frontend foundation)*

The active work list for TranscriberPrototype. Update it whenever a task is finished or new work is
discovered.

Phase-level progress lives in [plans/live-seminar-transcriber.md](plans/live-seminar-transcriber.md).
This file tracks everything that is *not* a plan phase, plus the decisions the plan closed.

---

## Part 1 — Setup Definition of Done

Verified on 2026-08-14 by inspecting the repository. **Setup is complete.**

- [x] Intake complete — goal, runtime, deliverables, supported tools, validation workflow
- [x] `docs/` exists and is the single source of truth
- [x] `docs/documentation.md` — purpose, stack, architecture summary, decision log, status
- [x] `docs/structure.md` — matches the actual tree
- [x] `docs/workflow.md` — install, run, test, lint, env, docs, handoff
- [x] `docs/checklist.md` — this file
- [x] `docs/plans/` exists, with a README and an index
- [x] `docs/skills/` exists; every selected skill has a canonical folder
- [x] Web-project docs created: `architecture.md`, `routes.md`, `component-map.md`, `data-flow.md`,
      `api-contract.md`, `deployment.md`, `design-system.md`
- [x] Agent pointers complete and valid for Claude Code, OpenAI Codex, and Cursor
- [x] Top-level directories exist; all web application code is under `web/`
- [x] `pyproject.toml` and `uv.lock` exist; `uv sync` succeeds
- [x] `.env.example` lists every currently-known variable
- [x] `.gitignore` excludes `.env`, `.venv/`, `data/`, `logs/`
- [x] `README.md` points readers to `docs/`
- [x] Initializer artifacts removed; no duplicate sources of truth remain
- [x] `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest` all pass
- [x] **Frontend build** — not applicable. There is no build step (Decision D-011). The item recorded
      as deferred at initialization is now closed rather than outstanding.

---

## Part 2 — Decisions Closed

Each of these was open at initialization and is now settled. Rationale is in the Decision Log in
`docs/documentation.md`.

- [x] **Backend framework** — FastAPI confirmed (D-004)
- [x] **Frontend framework** — Jinja2 + vanilla ES modules; React/Vite/TS assumption reversed (D-011)
- [x] **Product model** — live streaming session, not upload-and-poll jobs (D-010)
- [x] **Transcription engine** — pluggable; mock and WAV file source ship, `faster-whisper` optional (D-012)
- [x] **Persistence** — SQLite with FTS5 (D-015)
- [x] **Progress reporting** — WebSocket push, not polling (D-013)
- [x] **Async execution model** — single process, asyncio transport plus dedicated capture and
      inference threads, with a documented process-split escape hatch (see `docs/architecture.md`)
- [x] **Authentication and multi-user support** — none; single-user, loopback-bound (D-016)
- [x] **Retention policy** — audio retention off by default, explicit setting, plain-language
      implications at the point of use
- [x] **Credential handling** — OS credential store, environment fallback, never a config file (D-017)

---

## Part 3 — Remaining Build Phases

Thirteen of the plan's fourteen phases are complete. The application records, transcribes,
persists, renders a live transcript, and answers questions about it — and every part of that is
configurable from inside the interface, with no config file to edit.

- [x] **Phase 9 — LLM abstraction (Seam B).** OpenAI-compatible client, native Anthropic client,
      the four-way error taxonomy, connection testing, credential handling.
- [x] **Phase 10 — Chat orchestration and the context pipeline.** Priority-ordered context
      assembly under a token budget, quick actions, streaming answers with cancellation, rolling
      summaries, and glossary extraction.
- [x] **Phase 12 — Settings interface.** The five-tab modal over `/api/config`, plus the recording
      library that makes the file source selectable without touching the filesystem.
- [x] **Phase 13 — Chat interface.** Chat pane, quick actions, streaming responses, the glossary
      panel, ask-about-selection, and clickable citation timestamps.
- [ ] **Phase 14 — Hardening.** Degradation paths under test, the accelerated soak, transcript
      export in the UI, the sessions page, and a final documentation pass.

---

## Part 4 — Still Open

- [ ] **Default ASR model and compute device.** Depends entirely on the user's hardware. A
      conservative default ships (`small`, `int8`, auto device); benchmark locally and re-tune. The
      architecture document is explicit that this cannot be decided from published benchmarks.
- [ ] **Desktop packaging.** Whether this stays a browser-plus-local-server application or is packaged
      into a Tauri/Electron/Qt shell. The chosen contract keeps both open, so nothing is blocked.
- [ ] **Deployment documentation.** `docs/deployment.md` still describes a generic web deployment and
      needs rewriting for a locally-run desktop-style application. Due in Phase 14.
- [ ] **Design tokens.** `web/frontend/static/css/tokens.css` is the working source of truth; the
      token table in `docs/design-system.md` still needs to be filled in from it.
- [ ] **Component map.** `docs/component-map.md` still describes React component ownership and needs
      rewriting for the template-and-module model. Overdue — Phase 11 landed without it.
- [ ] **Automated accessibility tooling.** Not selected. Manual keyboard, contrast, and 320 px passes
      are specified per phase in the plan; an automated check would complement them.
- [ ] **Embeddings-based retrieval.** Deferred. Keyword search over FTS5 is expected to suffice for
      single-talk sessions; revisit only if retrieval quality proves inadequate.
- [ ] **Speaker diarisation.** Out of scope for v1. The segment model reserves an optional `speaker`
      field so adding it later is not a schema migration.
- [ ] **Hosted ASR backends.** Deferred to v2. Seam A accommodates them; none is implemented.

---

## Part 5 — Verification Debt

Things the plan's phases cannot verify in a headless environment. Each needs a manual pass on the
user's own machine, and none may be reported as passing until it has had one.

- [ ] **Live microphone capture** — confirm real audio arrives, is not silence, is not clipped, and is
      from the intended device.
- [ ] **System loopback capture** — confirm the loopback device is enumerated and captures playback.
- [ ] **Real-model transcription** — install `--extra asr-whisper`, transcribe a saved recording, and
      measure real-time factor on the actual hardware.
- [x] **A real local LLM endpoint** — done. Verified against an OpenAI-compatible relay on
      `localhost:9090` serving `default-model`: connection test, model listing, streaming answers,
      mid-stream cancellation, rolling summaries, and glossary extraction all confirmed. Five
      integration tests in `tests/assistant/test_llm_live.py` run against it when it is up and skip
      when it is not.
- [ ] **A real hosted LLM provider** — confirm credential storage, auth, and streaming.
- [ ] **A genuine 90-minute soak** — the plan runs an accelerated soak, which exercises flat memory and
      stable real-time factor but is not the same as ninety wall-clock minutes.
