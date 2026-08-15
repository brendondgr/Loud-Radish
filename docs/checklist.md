# Project Checklist

*Last updated: 2026-08-15 (polish: dialogue accuracy, timestamps, continuous prose)*

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

**All fourteen phases are complete.** The application records, transcribes, persists, renders a
live transcript, answers questions about it, and keeps past sessions retrievable and exportable —
and every part of that is configurable from inside the interface, with no config file to edit.

- [x] **Phase 9 — LLM abstraction (Seam B).** OpenAI-compatible client, native Anthropic client,
      the four-way error taxonomy, connection testing, credential handling.
- [x] **Phase 10 — Chat orchestration and the context pipeline.** Priority-ordered context
      assembly under a token budget, quick actions, streaming answers with cancellation, rolling
      summaries, and glossary extraction.
- [x] **Phase 12 — Settings interface.** The five-tab modal over `/api/config`, plus the recording
      library that makes the file source selectable without touching the filesystem.
- [x] **Phase 13 — Chat interface.** Chat pane, quick actions, streaming responses, the glossary
      panel, ask-about-selection, and clickable citation timestamps.
- [x] **Phase 14 — Hardening.** Degradation paths under test, the accelerated soak, transcript
      export in the UI, the sessions page, and the documentation pass.

---

## Part 3b — Minute-Based Transcript Polish

Tracked in [plans/minute-based-transcript-polish.md](plans/minute-based-transcript-polish.md).
**All seven steps are complete.** Each finished minute of transcript is rewritten into readable
prose in the background, and the page keeps showing raw segments whenever that cannot happen.

- [x] Configuration (`polish.*`), the `PolishedBlock` record, and the `polished_blocks` table
- [x] The pause-aware chunk planner and the content-integrity guard
- [x] The background worker, its instruction-list prompt, and every failure path
- [x] Session wiring behind a factory, so a machine with no language model is unaffected
- [x] `transcript.polished` over the socket, reconnection replay, `GET /api/transcript/polished`
- [x] The transcript pane, the settings controls, and the 320 px and keyboard passes
- [x] Documentation: D-018 plus `structure`, `architecture`, `data-flow`, `api-contract`,
      `routes`, `component-map`, `design-system`

Deliberately out of scope, recorded so the absences are not mistaken for oversights:

- **Export is unchanged.** `/api/transcript/export` emits the verbatim record with timestamps.
  Polished text is a reading aid, and an export that quietly substituted a model's rewrite for what
  was said would be the wrong document to keep.
- **The session archive does not count polished blocks.** `services/transcript/archive.py` reads
  older session files directly and must open one written before this table existed.

---

## Part 3c — Polish: Dialogue Accuracy, Timestamps, Continuous Prose

Tracked in [plans/transcript-polish-refinements.md](plans/transcript-polish-refinements.md).
**All five steps are complete.** The pass now aims at accurate dialogue rather than literal
transcription.

- [x] The chunk is flattened into one timestamped run before the model sees it (`polish/source.py`)
- [x] Invented, reversed, and duplicated markers are reconciled away; line breaks are collapsed
- [x] The prompt writes out spoken code references and repairs the grammar around them
- [x] Polished minutes flow as consecutive paragraphs with quiet inline timestamps
- [x] Documentation: D-018 amended, plus `structure`, `architecture`, `data-flow`, `api-contract`,
      `routes`, `component-map`, `design-system`

---

## Part 4 — Still Open

- [ ] **Default ASR model and compute device.** Depends entirely on the user's hardware. A
      conservative default ships (`small`, `int8`, auto device); benchmark locally and re-tune. The
      architecture document is explicit that this cannot be decided from published benchmarks.
- [ ] **Desktop packaging.** Whether this stays a browser-plus-local-server application or is packaged
      into a Tauri/Electron/Qt shell. The chosen contract keeps both open, so nothing is blocked.
- [x] **Deployment documentation.** Done. `docs/deployment.md` now describes installing and running
      it locally, what ends up on disk, and measured hardware expectations.
- [x] **Design tokens.** Done. `docs/design-system.md` documents every token group, the two
      contrast corrections, and the rule about compositing translucent backgrounds before measuring.
- [x] **Component map.** Done. `docs/component-map.md` describes the template-and-module tree, the
      three ownership rules, and every component and store.
- [ ] **Automated accessibility tooling.** Not selected. Manual keyboard, contrast, and 320 px passes
      are specified per phase in the plan; an automated check would complement them.
- [ ] **Embeddings-based retrieval.** Deferred. Keyword search over FTS5 is expected to suffice for
      single-talk sessions; revisit only if retrieval quality proves inadequate.
- [ ] **Speaker diarisation.** Out of scope for v1. The segment model reserves an optional `speaker`
      field so adding it later is not a schema migration.
- [ ] **Hosted ASR backends.** Deferred to v2. Seam A accommodates them; none is implemented.
- [ ] **A per-page control for the polish view.** Settings → Context turns the pass on and off, but
      there is no way to see the raw segments for a stretch that has been polished without turning
      it off entirely. Worth adding once the pass has been used against a real model and it is
      clear how often anyone wants to.
- [ ] **Tuning `polish.min_retained_ratio`.** The 0.6 default is a starting point chosen without a
      real model behind it. It is a length check, not a meaning check, and the right value can only
      come from watching what a real model actually returns. Writing out spoken code references now
      shortens a rewrite legitimately ("guard dot py" is three words and `guard.py` is one), which
      pushes in the same direction as a summary would — another reason the floor needs real data.
- [ ] **Whether the assistant should answer from polished text.** It currently assembles context
      from raw segments, which are the verbatim record. Now that polished text carries the same
      `[MM:SS]` markers the assistant cites, feeding it the polished version instead would give it
      cleaner input — but it is a decision about what the assistant is allowed to read, not a
      refactor, and it was deliberately not smuggled into the polish work.
- [ ] **ASR hallucination on silence and noise.** Reported from real use: the transcript picks up
      room noise and invents speech that was never said — "thank you", "bye", and stray single
      words are the recurring ones. Reproduced on a synthetic tone fixture, which produced a
      segment reading "you". This is the well-known Whisper failure on non-speech audio and it
      needs its own plan; the likely levers are the model's own `no_speech_prob`, gating inference
      on the VAD rather than only preferring its pauses, and a blocklist of the specific phrases.

---

## Part 5 — Verification Debt

Things the plan's phases cannot verify in a headless environment. Each needs a manual pass on the
user's own machine, and none may be reported as passing until it has had one.

- [x] **Live microphone capture** — done, and it found four bugs. Verified against real hardware:
      frames arrive at the correct rate and level, our conversion matches raw PortAudio, and the
      device now has a stable identity so a saved selection cannot resolve to a different
      microphone after a re-plug. Settings → Audio → **Test this device** does this check on demand.
      *Still worth doing yourself:* speak into your own microphone and confirm the words appear.
- [ ] **System loopback capture** — partially. Loopback sources are now correctly *classified*
      rather than presented as microphones, but on a PipeWire desktop PortAudio does not expose the
      sink monitors at all, so what is offered is whatever the JACK bridge surfaces. Capturing a
      remote talk this way needs confirming on your own setup.
- [x] **Real-model transcription** — done. `faster-whisper` `small` at `int8` on a 32-core CPU:
      **RTF ≈ 1.5**, commit latency ≈ 1.4 s, transcript recognisably correct. Measured on this
      machine; measure on yours, which is what the status bar exists for.
- [x] **A real local LLM endpoint** — done. Verified against an OpenAI-compatible relay on
      `localhost:9090` serving `default-model`: connection test, model listing, streaming answers,
      mid-stream cancellation, rolling summaries, and glossary extraction all confirmed. Five
      integration tests in `tests/assistant/test_llm_live.py` run against it when it is up and skip
      when it is not.
- [ ] **A real hosted LLM provider** — confirm credential storage, auth, and streaming.
- [ ] **The polish pass against a real language model.** Every failure path is covered by tests
      against a scripted backend, and the happy path is verified end to end in the browser with a
      block written directly into the session database. What has *not* been observed is a real
      model obeying the instruction list: whether it strips filler without dropping content,
      whether it honours the no-markup rule, whether the no-reasoning fields are accepted by the
      user's server, and whether the length guard's default floor is right. Run a talk with a local
      model and read the result against the raw transcript.
- [ ] **A genuine 90-minute soak** — the accelerated soak in `tests/transcription/test_soak.py`
      drives ninety minutes of transcript through the engine in seconds and holds bounded memory,
      contiguous segment ids, and a clock that has not drifted. It is not the same as ninety
      wall-clock minutes with a real model and a real device, which remains yours to run.
