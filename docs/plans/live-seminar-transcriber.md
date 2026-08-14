# Live Seminar Transcriber — Implementation Plan

*Created: 2026-08-14 · Status: **In progress** (Phase 0 of 14 complete — planning)*

**Source specifications** (external, read-only inputs to this plan):

- `~/Documents/Plans/Transcription/01-backend-architecture.md` — referenced below as **BE §n**
- `~/Documents/Plans/Transcription/02-frontend-design.md` — referenced below as **FE §n**
- `~/Documents/Plans/Transcription/Local application layout design/Seminar Transcriber.dc.html` — the
  visual design mock. Referenced as **the design mock**. It is a prototype in a proprietary
  template dialect (`sc-if`, `sc-for`, inline styles); it is a *visual and behavioural reference*,
  not source to be copied.

---

## 1. Introduction

TranscriberPrototype is being built into the **Live Seminar Transcriber**: a single-user local
application that continuously captures room or system audio, transcribes it in near-real-time through
a pluggable speech model, maintains a growing timestamped transcript, and exposes that transcript to a
language model so the user can ask questions about a talk while it is happening. The backend is a
FastAPI application that owns the entire audio→text→context pipeline and publishes it over an HTTP
plus WebSocket contract. The frontend is a server-rendered Jinja2 template tree driven by vanilla ES
modules — no build step, no framework — deliberately split into many small template partials, CSS
files, and JS modules rather than a monolith.

The approach follows the two abstraction seams the architecture document identifies as
non-negotiable: **Seam A**, the ASR interface (BE §6.2), and **Seam B**, the LLM interface
(BE §10.2). Everything else is built around them. The build order below mirrors BE §20 (M1–M11) and
FE §16 (F1–F11), collapsed into fourteen independently verifiable phases. The pipeline is developed
and tested against a **scripted mock ASR backend** and a **WAV file source** running at real-time
speed (BE §19.1), so the commit logic — the genuinely hard part — is deterministic and reproducible
before any real model or microphone is involved. A `faster-whisper` adapter ships behind a lazy import
and an optional dependency group, so the real model works when installed and nothing breaks when it
is not.

---

## 2. Gaps & Unanswered Questions

### Resolved by the user at planning time

| Question | Answer |
|---|---|
| Frontend stack | **Jinja2 templates + vanilla ES modules + modular CSS.** Reverses assumed Decision D-005 (React + Vite + TypeScript). Recorded as **D-011**. |
| ASR backends to ship | **Mock + WAV file source now; `faster-whisper` adapter behind an optional dependency group.** Recorded as **D-012**. |
| Scope of this work | **Full vertical slice** — every backend stage and the complete frontend. |

### Gaps resolved by assumption

- **The product changed shape.** `docs/architecture.md`, `docs/routes.md`, `docs/api-contract.md`,
  and `docs/data-flow.md` currently describe an *upload-a-file, poll-a-job* transcription service.
  Both design documents describe a *live streaming* transcriber with a persistent WebSocket.
  *Assumption:* the live transcriber supersedes the upload-job model entirely. The upload surface is
  removed, not kept alongside. Recorded as **D-010**; the four docs are rewritten in Phase 1.
- **Authentication and multi-user support.** *Assumption:* none. This is a single-user application
  bound to `127.0.0.1`. No auth, no user records, no data isolation. Stated explicitly in
  `docs/architecture.md` rather than left implied.
- **Persistence.** *Assumption:* **SQLite** (BE §8.2) — crash-survivable, free full-text search, no
  server. Sessions live under the configured session directory.
- **Async execution model.** *Assumption:* single process, `asyncio` event loop for transport plus
  dedicated worker threads for capture and ASR (BE §14.1). The GIL caveat in BE §14.3 is handled by
  keeping inference in a thread that releases the lock; a process-split escape hatch is designed for
  but not built. Revisit only if capture stalls are observed under soak.
- **Resampling.** *Assumption:* the `soxr` library, per BE §4.2's insistence on a proper resampler.
  A pure-NumPy linear fallback exists solely so the package imports without it; it logs a warning and
  is never the default.
- **VAD implementation.** *Assumption:* an energy-based VAD with hysteresis as the dependency-free
  default, plus a Silero adapter behind the same interface and an optional dependency group. The
  hysteresis and pause-event logic (BE §5.2) lives above the interface so both share it.
- **Credential storage.** BE §10.6 names Windows Credential Manager; this repository develops on
  Linux. *Assumption:* use the `keyring` package when present (it maps to the correct OS store on
  every platform), fall back to an environment variable, and **never** write a key to the config file
  or send one to the frontend. The frontend learns only whether a credential is present.
- **Diarisation.** Out of scope for v1 (BE §1.3), but per BE §21.6 the segment model carries an
  optional `speaker` field from the start so adding it later is not a schema migration.
- **Retrieval sophistication.** *Assumption:* SQLite FTS5 keyword search (BE §9.5). No embeddings.
- **Transcript virtualisation.** FE §4.3 requires it. *Assumption:* a windowed renderer keyed on
  segment id, with scroll-position preservation solved together with it rather than after it.
- **Audio retention.** *Assumption:* off by default (BE §17.3), an explicit setting, and when on,
  written incrementally to disk rather than held in memory.

### Gaps that cannot be closed in this environment

These are honest limits, not deferrals. Each is called out again in the phase that hits it.

- **Live microphone and system-loopback capture cannot be verified here.** This is a headless Linux
  worktree with no audio device and no WASAPI. The device-capture code is written against the same
  `AudioSource` interface as the file source and unit-tested with a fake device, but *confirming that
  real audio arrives from a real microphone is a manual step the user must perform.* BE §20 M1 exists
  precisely because this is where time is lost; it will be reported as unverified, not as passing.
- **`faster-whisper` real-model transcription cannot be verified here.** The optional dependency and
  its model weights are not installed. The adapter is tested for interface conformance and
  import-guard behaviour only. Measuring real-time factor on the user's hardware (BE §20 M2) is a
  manual step.
- **Live LLM endpoints cannot be reached.** Provider clients are tested against a stubbed HTTP
  transport that replays recorded response shapes, including the four error classes BE §10.2
  requires. Pointing at a real Ollama or Anthropic endpoint is a manual step.
- **A 90-minute soak run (BE §19.2) exceeds a reasonable session.** Phase 14 runs an accelerated soak
  — the file source driven faster than real time over a long synthetic input — which exercises flat
  memory and stable RTF but is not the same as ninety wall-clock minutes. The difference will be
  stated, not glossed.

### Requiring human intervention

- **Eventual packaging.** FE §15 leaves open whether this ships as a browser-plus-local-server
  application or is packaged into a desktop shell (Tauri/Electron/Qt). The chosen contract keeps both
  open, so nothing here is blocked — but the decision is the user's and is not made by this plan.
  *Human intervention is needed to answer this question.*
- **Default ASR model and compute device.** BE §21.4 says this depends entirely on the user's
  hardware and must be benchmarked at M2. The configuration system ships a conservative default
  (`faster-whisper` small, int8, CPU) that the user should re-tune after benchmarking.
  *Human intervention is needed to answer this question.*

---

## 3. Hierarchical Step-by-Step Instructions

Fourteen phases. Each is independently verifiable and independently committable. Phase status is
tracked in the table in §5 and must be updated as work lands.

### Step 1: Foundations — dependencies, app skeleton, configuration system, documentation pivot

- **Locations:**
  - `pyproject.toml` — add runtime dependencies (`fastapi`, `uvicorn[standard]`, `jinja2`,
    `python-multipart`, `pydantic`, `pydantic-settings`, `httpx`, `numpy`, `soxr`, `aiosqlite`) via
    `uv add`; dev dependencies (`pytest-asyncio`, `anyio`) via `uv add --dev`; optional dependency
    groups `asr-whisper` (`faster-whisper`), `audio-device` (`sounddevice`), `vad-silero`
    (`onnxruntime`), `credentials` (`keyring`).
  - `web/backend/app/main.py` — `create_app()` factory, lifespan hooks, router mounting,
    `StaticFiles` mount, `Jinja2Templates` binding, `127.0.0.1` binding note.
  - `web/backend/app/config/` — new package: `schema.py` (Pydantic models for the seven configuration
    areas of BE §13.2), `defaults.py`, `presets.py` (Accuracy / Balanced / Low resource, BE §13.4),
    `store.py` (layered resolution per BE §13.1, file persistence, `HotSwapClass` classification per
    BE §13.3), `credentials.py` (keyring/env, never config file, never logged).
  - `web/backend/app/services/__init__.py` — package placeholder for the pipeline sub-packages added
    in later phases.
  - `.env.example` — remove `VITE_API_BASE_URL`; add `TRANSCRIBER_CONFIG_PATH`, `SESSION_DIR`,
    `ASR_BACKEND`, `ASR_MODEL`, `ASR_DEVICE`, `LLM_MODE`, `LLM_LOCAL_ENDPOINT`, `LLM_LOCAL_MODEL`,
    `LLM_API_PROVIDER`, `LLM_API_MODEL`, and commented credential placeholders.
  - `tests/utils/test_config_layers.py`, `tests/utils/test_config_hotswap.py`.
  - **Docs:** rewrite `docs/architecture.md`, `docs/routes.md`, `docs/api-contract.md`,
    `docs/data-flow.md` for the live-streaming product; add Decisions **D-010** (product model),
    **D-011** (Jinja2 + ES modules, reversing D-005), **D-012** (mock + optional faster-whisper) and
    confirm **D-004** (FastAPI) in `docs/documentation.md`; update `docs/structure.md`,
    `docs/workflow.md`, `docs/checklist.md`.
- **Rationale:** every later phase reads configuration and mounts onto the app object, so both must
  exist first. The documentation pivot happens here rather than at the end because leaving four
  canonical docs describing a different product would make every subsequent phase's doc update
  contradictory — and `docs/` is the single source of truth.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv sync`,
  `uv run pytest tests/utils`, `uv run ruff check .`, `uv run ruff format --check .`, and confirm the
  app starts and serves `GET /api/health`. Once validated, push changes to GitHub stating:
  `Live Seminar Transcriber (1 / 14) Complete: Added the FastAPI skeleton, the layered configuration system with presets and hot-swap classification, and pivoted the canonical docs from upload-jobs to live streaming.`

### Step 2: Audio capture layer

- **Locations:** `web/backend/app/services/audio/` — `formats.py` (canonical 16 kHz / mono / f32 /
  ±1.0 format of BE §4.2, downmix, integer normalisation), `resample.py` (`soxr` with the logged
  NumPy fallback), `ring_buffer.py` (fixed-capacity circular buffer, drop-oldest, overwrite counter
  as the health signal of BE §4.4), `level.py` (RMS, peak, clipping flag), `preprocess.py`
  (individually toggleable gain normalisation and ~80 Hz high-pass, BE §4.5), `devices.py` (merged
  input + loopback enumeration with a type tag per BE §4.3), and `sources/` — `base.py`
  (`AudioSource` protocol), `file.py` (WAV read, resample, emit frames at real-time speed —
  BE §19.1), `device.py` (lazy `sounddevice`), `null.py` (synthetic silence/tone for tests).
  Tests: `tests/utils/test_audio_formats.py`, `test_ring_buffer.py`, `test_preprocess.py`;
  `tests/transcription/test_file_source.py`. Script: `scripts/make_fixture_wav.py`.
- **Rationale:** BE §4.2 says the canonical format is enforced at the capture boundary and nowhere
  else, so every downstream stage can assume it. The file source is built here, first, because BE
  §19.1 is explicit that developing against recorded audio rather than a live microphone is what
  makes the commit logic debuggable at all.
- **Known limit:** live device capture is implemented but **cannot be verified in this environment**
  — see §2. It will be reported as unverified.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/utils tests/transcription`, ruff check and format, and a manual run of
  `scripts/make_fixture_wav.py` followed by the file source reading it back at real-time speed. Once
  validated, push changes to GitHub stating:
  `Live Seminar Transcriber (2 / 14) Complete: Built the audio capture layer — canonical format enforcement, resampling, ring buffer, preprocessing, device enumeration, and the real-time WAV file source.`

### Step 3: Voice activity detection

- **Locations:** `web/backend/app/services/vad/` — `base.py` (`VoiceActivityDetector` protocol),
  `energy.py` (dependency-free default), `silero.py` (lazy optional adapter), `hysteresis.py` (the
  3-frame enter / 10-frame leave state machine and the >500 ms pause event of BE §5.2, shared by both
  detectors). Tests: `tests/transcription/test_vad_hysteresis.py`,
  `tests/transcription/test_vad_energy.py`.
- **Rationale:** BE §5.1 gives the VAD three jobs — skipping silence, suppressing the hallucination
  failure mode, and marking safe cut points. The streaming engine in Step 5 consumes pause events as
  preferred commit boundaries, so they must exist first. Hysteresis lives above the detector
  interface because without it the state flickers on every breath, regardless of which detector runs.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/transcription`, ruff check and format; assert specifically that a continuous-speech signal
  never emits a pause event and that flicker-inducing input does not toggle state. Once validated,
  push changes to GitHub stating:
  `Live Seminar Transcriber (3 / 14) Complete: Added voice activity detection with shared hysteresis and pause-event emission, an energy detector by default and an optional Silero adapter.`

### Step 4: The ASR abstraction layer (Seam A)

- **Locations:** `web/backend/app/services/asr/` — `contract.py` (`WordToken` with
  `text`/`start`/`end`/nullable `confidence`, `AsrResult`, `AsrCapabilities` covering the six flags of
  BE §6.3, and the `AsrBackend` protocol), `registry.py` (name → backend factory, capability
  reporting), `mock.py` (scripted backend that replays a fixed word sequence with configurable
  revision behaviour, so the commit policy can be tested deterministically), `faster_whisper.py`
  (lazy import, clear actionable error when the optional group is not installed),
  `prompting.py` (session prompt and rolling context prompt of BE §6.5, both individually
  toggleable), `lifecycle.py` (async load with progress, silence warm-up pass, mid-session swap that
  flushes and marks the switch point, explicit unload). Tests:
  `tests/transcription/test_asr_contract.py`, `test_asr_mock.py`, `test_asr_registry.py`,
  `test_faster_whisper_guard.py`.
- **Rationale:** BE §3.2 and §6.1 identify this as one of two seams that must be designed carefully
  and early because everything else is replaceable and this is not. The mock backend is built here,
  alongside the first real adapter, because BE §20 M3 says so explicitly — it is what makes Step 5
  testable.
- **Critical contract detail:** timestamps returned by a backend are relative to the submitted audio
  array, never wall-clock (BE §6.2). Step 5 owns the translation to absolute time; a test asserts
  that backends do not attempt it themselves.
- **Known limit:** the `faster-whisper` path is verified for conformance and import-guard behaviour
  only — no real model runs here. See §2.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/transcription`, ruff check and format. Once validated, push changes to GitHub stating:
  `Live Seminar Transcriber (4 / 14) Complete: Defined the ASR interface with capability declaration, term biasing, and lifecycle management, behind it a scripted mock backend and an optional faster-whisper adapter.`

### Step 5: The streaming engine

- **Locations:** `web/backend/app/services/streaming/` — `agreement.py` (LocalAgreement-N longest
  common prefix, BE §7.3), `buffer.py` (growing audio buffer, sentence-preferring trim with a
  0.5–1 s retained acoustic tail, and the `buffer_start_absolute` monotonic rebasing of BE §7.5),
  `guards.py` (all six guards of BE §7.6 — minimum buffer, maximum buffer, commit timeout, silence
  gate, repetition filter, queue backpressure), `segmenter.py` (words → segments by punctuation, VAD
  pause, and a ~30 s duration cap, BE §7.7), `passthrough.py` (the `streaming_native` bypass path of
  BE §7.8, producing an identical output contract), `events.py` (committed / hypothesis / segment
  event types), `engine.py` (the orchestrator wiring the above). Tests:
  `tests/transcription/test_local_agreement.py`, `test_timestamp_rebasing.py`, `test_buffer_trim.py`,
  `test_guards.py`, `test_segmenter.py`, `test_engine_commit_policy.py`,
  `test_engine_passthrough.py`. Script: `scripts/run_file_session.py` — console-only pipeline run
  over a WAV file, exactly the "no UI" validation BE §20 M4 demands.
- **Rationale:** BE §7 opens by saying this is the core of the system and the part that is genuinely
  hard, and BE §20 M4 says not to move on until it is correct. Splitting agreement, trimming,
  rebasing, guards, and segmentation into separate modules is what lets each be unit-tested in
  isolation — BE §19.2 names longest-common-prefix logic, timestamp rebasing, trim arithmetic, and
  repetition detection as the four things that must have unit coverage.
- **Test cases required here** (BE §19.3): continuous speech with no pauses exercising the commit
  timeout; long silences exercising the hallucination gate; a speaker resuming mid-word after a
  pause; and the repetition-loop truncation path.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/transcription`, ruff check and format, plus a manual `scripts/run_file_session.py` run whose
  console output is inspected for correct commit/hypothesis behaviour and no duplicated text. Once
  validated, push changes to GitHub stating:
  `Live Seminar Transcriber (5 / 14) Complete: Implemented the streaming engine — LocalAgreement-2 commit policy, buffer trimming, timestamp rebasing, all six guards, segmentation, and the streaming-native bypass path.`

### Step 6: The transcript store

- **Locations:** `web/backend/app/models/segment.py` and `session.py` (the field set of BE §8.1
  including the optional `speaker` field reserved per BE §21.6);
  `web/backend/app/services/transcript/` — `store.py` (SQLite, write-through on commit per BE §8.2,
  append-only), `schema.sql` (tables plus an FTS5 virtual table), `queries.py` (the four queries of
  BE §8.3 — since segment *N*, time range, full-text search, totals), `export.py` (plain text,
  Markdown, SRT, VTT, JSON per BE §17.2). Tests: `tests/data/test_transcript_store.py`,
  `test_transcript_queries.py`, `test_transcript_search.py`, `test_export_formats.py`.
- **Rationale:** BE §8.2 is blunt that a crash eighty minutes into a talk is unrecoverable if the
  transcript is held only in memory. "Everything since segment *N*" is what makes the WebSocket
  reconnection of Step 8 possible, so it must exist before transport.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/data`, ruff check and format, including a test that kills and reopens the store mid-session
  and finds every committed segment intact. Once validated, push changes to GitHub stating:
  `Live Seminar Transcriber (6 / 14) Complete: Added the crash-survivable SQLite transcript store with append-only segments, full-text search, the four-query surface, and five export formats.`

### Step 7: Session manager, workers, and metrics

- **Locations:** `web/backend/app/services/session/` — `manager.py` (wires capture → VAD → engine →
  store; start/stop/state; model swap; source selection), `workers.py` (the four workers and their
  priorities from BE §14.1, bounded drop-oldest capture queue, bounded hypothesis-coalescing output
  queue that never drops committed events, batched persistence — BE §14.2), `metrics.py` (the six
  instrumented metrics of BE §16.3 — real-time factor, queue depth, commit latency median and p95,
  correction rate, forced-commit rate, dropped-audio count), `degradation.py` (the failure responses
  of BE §15). Tests: `tests/transcription/test_session_manager.py`, `test_worker_backpressure.py`,
  `test_metrics.py`.
- **Rationale:** BE §14.1's constraint **C5** — capture must never block on transcription — is a
  property of this wiring, not of any single component, so it needs its own phase and its own tests.
  Real-time factor is described in BE §16.3 as the single most important number in the system; it is
  computed here and surfaced everywhere later.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/transcription`, ruff check and format, including a test that starves the ASR worker and
  asserts capture continues, the queue drops oldest, and the drop is counted rather than silent. Once
  validated, push changes to GitHub stating:
  `Live Seminar Transcriber (7 / 14) Complete: Wired the session manager and its four workers with bounded queues, backpressure handling, degradation paths, and full pipeline metrics.`

### Step 8: The transport layer

- **Locations:** `web/backend/app/transport/` — `events.py` (the twelve server-to-client event
  envelopes of BE §12.2), `hub.py` (connection registry, per-connection replay from a last-seen
  segment id per BE §12.4, hypothesis coalescing, throttled health events), `ws.py` (the WebSocket
  endpoint). `web/backend/app/routes/` — `health.py`, `session.py`, `audio.py`, `asr.py`, `llm.py`,
  `chat.py`, `transcript.py`, `config.py`, `pages.py`, matching the six operation groups of BE §12.3.
  `web/backend/app/schemas/` — one module per route group. `web/shared/contracts/` — generated
  `openapi.json` plus a hand-authored `ws-events.json` describing the WebSocket envelopes, which
  OpenAPI cannot express. Script: `scripts/generate_openapi.py`. Tests: `tests/api/test_health.py`,
  `test_session_routes.py`, `test_transcript_routes.py`, `test_ws_contract.py`,
  `test_ws_reconnect_replay.py`.
- **Rationale:** BE §12 says to treat this section as the API specification, and BE §12.4 says to
  design for reconnection from the start because it removes an entire class of bug. The
  committed-appends / hypothesis-replaces distinction (BE §12.2, FE §9.1) is the single most
  important line in the contract and gets a dedicated test asserting that a replay after reconnect is
  idempotent and produces no duplicated text.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/api`, ruff check and format, regenerate `web/shared/contracts/openapi.json`, plus a manual
  check with a trivial HTML page that logs every event (BE §20 M6). Updates `docs/routes.md`,
  `docs/api-contract.md`, `docs/data-flow.md`. Once validated, push changes to GitHub stating:
  `Live Seminar Transcriber (8 / 14) Complete: Delivered the transport layer — the full HTTP operation surface, the twelve WebSocket events, reconnection replay, and the generated contracts.`

### Step 9: The LLM abstraction layer (Seam B)

- **Locations:** `web/backend/app/services/llm/` — `contract.py` (`Message`, `GenerationParams`, a
  complete-or-streaming response type, reported token usage and actual model id, and the four-way
  structured error taxonomy of BE §10.2 — auth / rate limit / network / model error),
  `openai_compatible.py` (the workhorse client covering Ollama, llama.cpp, LM Studio, vLLM, LocalAI
  and OpenAI itself per BE §10.3), `anthropic.py` (native client), `registry.py` (mode → provider
  resolution over the nested `local` / `api` configuration of BE §10.4), `connection.py` (the
  explicit connection test and model listing of BE §10.4, returning the four specific results FE
  §7.3 requires rather than generic failure text). Tests: `tests/api/test_llm_providers.py`,
  `test_llm_errors.py`, `test_llm_connection_test.py`, all against a stubbed HTTP transport.
- **Rationale:** BE §3.2 makes this the second seam. BE §10.3's observation that most local servers
  speak the OpenAI protocol is what collapses an apparent provider matrix into two implementations —
  encoding that here is what keeps the layer small. The error taxonomy is not cosmetic: FE §7.3 shows
  different guidance per class, so the distinction must survive the boundary.
- **Known limit:** no live endpoint is reachable in this environment. See §2.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/api`, ruff check and format, and confirm no code path can log or return a credential. Once
  validated, push changes to GitHub stating:
  `Live Seminar Transcriber (9 / 14) Complete: Built the LLM abstraction with an OpenAI-compatible workhorse client, a native Anthropic client, structured error classification, connection testing, and OS-credential-store key handling.`

### Step 10: Chat orchestration and the context pipeline

- **Locations:** `web/backend/app/services/context/` — `summariser.py` (interval-triggered rolling
  summarisation fed the running outline for continuity, BE §9.2), `glossary.py` (term extraction with
  one-line definitions and first-use timestamps, feeding back into ASR biasing per BE §9.3),
  `chunks.py` (time-aligned chunk index, BE §9.5), `pipeline.py` (low-priority, interruptible
  background worker that must never delay a chat request). `web/backend/app/services/chat/` —
  `assembly.py` (the seven-tier priority context assembly of BE §11.2 with drop-from-the-bottom token
  budgeting and query-dependent variation), `quick_actions.py` (the configurable parameterised prompt
  templates of BE §11.3), `orchestrator.py` (streaming responses, cancellation, history, and the
  context-timestamp stamping of BE §11.4). Tests: `tests/api/test_context_assembly.py`,
  `test_token_budget.py`, `test_quick_actions.py`, `tests/transcription/test_summariser.py`,
  `test_glossary_extraction.py`.
- **Rationale:** BE §9.1 explains that dumping a raw 12–15 000-word transcript produces *worse*
  answers than a structured context, not merely slower ones — so the assembly policy is a
  correctness concern. BE §9.4's instruction that the model be told its input is imperfect ASR output,
  with the glossary supplied so it can map corrupted terms back, is part of the system instruction and
  is asserted in tests.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/api tests/transcription`, ruff check and format, including a test that an over-budget context
  drops tiers from the bottom in the specified order and never drops the system instruction. Once
  validated, push changes to GitHub stating:
  `Live Seminar Transcriber (10 / 14) Complete: Added rolling summarisation, glossary extraction with ASR feedback, the chunk index, and chat orchestration with priority-ordered context assembly and cancellation.`

### Step 11: Frontend foundation — template tree, design tokens, transport, transcript pane

- **Locations:** `web/frontend/templates/` — `base.html`; `pages/app.html`, `pages/sessions.html`;
  `partials/header.html`, `status_bar.html`, `banners.html`;
  `partials/transcript/{pane,toolbar,segment,hypothesis,jump_to_live,empty_state}.html`;
  `macros/{toggle,slider,field,radio_cards}.html`.
  `web/frontend/static/css/` — `tokens.css` (the palette, type scale, spacing, radii, and motion
  durations read off the design mock), `base.css`, `layout.css`,
  `components/{header,status-bar,banner,transcript,buttons,forms}.css`, `themes.css`.
  `web/frontend/static/js/` — `main.js`; `core/{bus,dom,format,storage,keyboard}.js`;
  `transport/{socket,api,events}.js` (exponential-backoff reconnect sending the last segment id per
  FE §9.2); `stores/{session,transcript,health}.js`;
  `components/{transcript-pane,virtual-list,scroll-controller,hypothesis-tail}.js`.
  Delete `web/frontend/README.md`. **Docs:** `docs/component-map.md` rewritten for the
  template/module ownership model; `docs/design-system.md` token table filled in;
  `docs/structure.md` updated.
- **Rationale:** covers FE §16 F1–F4. The transcript is the document and the chat is a tool (FE §P1),
  so it is built first. FE §4.1 warns that modelling the hypothesis tail as the last array entry
  duplicates text on screen — it is therefore a separate module (`hypothesis-tail.js`) that replaces a
  single element wholly, and a test asserts no duplication over a long run. FE §4.3 and its closing
  note both stress that scroll behaviour is the most-used interaction and the easiest to get wrong,
  and that virtualisation and position preservation must be solved together; `scroll-controller.js`
  and `virtual-list.js` are written and reviewed as one unit.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv run pytest`,
  ruff check and format, plus manual browser QA driving a long recorded session through
  `scripts/run_file_session.py`: confirm auto-follow, immediate disengage on scroll-up, jump-to-live
  with unread count, exact scroll-position preservation while disengaged, and zero text duplication.
  Once validated, push changes to GitHub stating:
  `Live Seminar Transcriber (11 / 14) Complete: Built the frontend foundation — partial-based template tree, tokenised CSS, ES module transport with reconnection, and the transcript pane with virtualised scrolling and a correct hypothesis tail.`

### Step 12: Frontend chrome and settings

- **Locations:** `web/frontend/templates/partials/settings/` —
  `{modal,nav,audio,asr,llm,context,storage}.html`, mapping one-to-one onto FE §7.1–§7.5 and the
  design mock's five settings tabs. `web/frontend/static/css/components/{settings,modal}.css`.
  `web/frontend/static/js/components/` — `header.js`, `status-bar.js`, `banners.js`,
  `settings-modal.js`, `settings/{audio,asr,llm,context,storage}.js`;
  `web/frontend/static/js/stores/config.js`; `web/frontend/static/js/a11y/focus-trap.js`.
- **Rationale:** covers FE §16 F5–F7. FE §1.2's central behavioural requirement — the user looks away
  for two minutes and must know within a second whether the system still works — is delivered by the
  status bar, so RTF below 1.0 turns red *and* carries an icon and text, never colour alone (FE §6.2,
  §13). The local/API switch (FE §7.3) keeps **both** configurations persisted so flipping back
  re-enters nothing, and the connection test reports the four specific outcomes rather than a generic
  failure. Per FE §8.1 the backend owns configuration: the frontend reads, presents, writes back, and
  re-reads, with no parallel client-side notion of settings.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv run pytest`,
  ruff check and format, plus manual browser QA: the three error tiers of FE §6.3 render correctly
  and none of them is a modal; the settings modal traps focus, restores it, and closes on `Escape`;
  the privacy indicator (FE §7.6) reads *fully local* only when both the ASR model and the LLM
  provider are local. Once validated, push changes to GitHub stating:
  `Live Seminar Transcriber (12 / 14) Complete: Added the header, glanceable status bar, tiered error banners, and the five-tab settings modal driven entirely by backend-owned configuration.`

### Step 13: Frontend chat pane, glossary, and cross-pane integration

- **Locations:** `web/frontend/templates/partials/chat/` —
  `{pane,quick_actions,message,composer,collapsed}.html`;
  `partials/transcript/{selection_menu,glossary_panel}.html`.
  `web/frontend/static/css/components/{chat,glossary}.css`.
  `web/frontend/static/js/components/` — `chat-pane.js`, `quick-actions.js`, `composer.js`,
  `selection-menu.js`, `glossary-panel.js`; `stores/{chat,context}.js`;
  `a11y/live-region.js`; `core/keyboard.js` extended with the six shortcuts of FE §12.
- **Rationale:** covers FE §16 F8–F11. FE §4.5 calls "Ask about this" from a text selection the
  highest-value interaction in the application, and FE §5.4 says citation timestamps that scroll the
  transcript are what make the layout more than two independent tools sharing a window — so these are
  built together in one phase rather than left as polish. FE §13 requires the transcript live region
  to announce **committed text only**; announcing the constantly-rewritten hypothesis would be
  unusable, and that distinction is asserted in QA.
- **Action:** Undergo the verification/tests/validation process for this phase — `uv run pytest`,
  ruff check and format, plus manual browser QA covering every state in FE §10 (no session, recording
  with no speech yet, model loading, chat with no LLM configured, chat with an empty transcript,
  connection lost, falling behind), a keyboard-only pass through all six shortcuts, a 320 px viewport
  check, the sub-900 px tab layout of FE §3.3, and a contrast check on the reduced-opacity hypothesis
  text specifically. Once validated, push changes to GitHub stating:
  `Live Seminar Transcriber (13 / 14) Complete: Completed the chat pane with streaming responses and cancellation, the glossary panel, ask-about-selection and citation cross-pane integration, all empty and error states, keyboard shortcuts, and the accessibility pass.`

### Step 14: Hardening, soak, and merge

- **Locations:** `tests/transcription/test_soak_accelerated.py`; `tests/api/test_degradation.py`;
  `scripts/soak_session.py`; `web/backend/app/services/session/degradation.py` (completed);
  `web/frontend/static/js/components/export-menu.js` and
  `web/frontend/templates/partials/export_menu.html`. **Docs:** final pass over
  `docs/documentation.md` (status table, decision log), `docs/structure.md`, `docs/workflow.md`
  (every real command), `docs/checklist.md` (Part 2 reconciled), `docs/architecture.md`,
  `docs/routes.md`, `docs/api-contract.md`, `docs/data-flow.md`, `docs/component-map.md`,
  `docs/design-system.md`, `docs/deployment.md`, and this plan marked complete.
- **Rationale:** BE §20 M11 and BE §15's guiding principle — transcription is the critical path, an
  LLM failure must never stop transcription. The degradation tests assert exactly that. BE §19.2's
  soak requirement is met in accelerated form; the difference between an accelerated run and ninety
  wall-clock minutes is stated in the results, not glossed.
- **Also covers** the remaining BE §19.3 cases not yet exercised: mid-session model swap, frontend
  disconnect and reconnect, LLM endpoint down during a chat request, audio device removed
  mid-session.
- **Action:** Undergo the verification/tests/validation process for this phase — the full
  `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, the accelerated soak with
  memory and RTF sampled throughout, and a final manual browser pass. Then merge the branch into
  `main`, resolve any conflicts, and delete the worktree. Once validated, push changes to GitHub
  stating:
  `Live Seminar Transcriber (14 / 14) Complete: Hardened the degradation paths, ran the accelerated soak, added transcript export, reconciled every canonical document, and merged the feature branch into main.`

---

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Application entry point | FastAPI factory, lifespan, router and static mounting | `web/backend/app/main.py` |
| Configuration system | Layered resolution, presets, hot-swap classes, credential handling | `web/backend/app/config/` |
| Audio capture layer | Canonical format, resampling, ring buffer, preprocessing, devices, sources | `web/backend/app/services/audio/` |
| Voice activity detection | Energy and optional Silero detectors, shared hysteresis, pause events | `web/backend/app/services/vad/` |
| ASR abstraction (Seam A) | Contract, capabilities, registry, mock backend, faster-whisper adapter, lifecycle | `web/backend/app/services/asr/` |
| Streaming engine | LocalAgreement-2, buffer trim, rebasing, six guards, segmentation, bypass path | `web/backend/app/services/streaming/` |
| Transcript store | SQLite append-only store, FTS5 search, query surface | `web/backend/app/services/transcript/` |
| Export formats | Plain text, Markdown, SRT, VTT, JSON | `web/backend/app/services/transcript/export.py` |
| Session manager | Worker wiring, bounded queues, backpressure, metrics, degradation | `web/backend/app/services/session/` |
| LLM abstraction (Seam B) | OpenAI-compatible and Anthropic clients, error taxonomy, connection test | `web/backend/app/services/llm/` |
| Context pipeline | Rolling summaries, glossary extraction, chunk index | `web/backend/app/services/context/` |
| Chat orchestration | Priority context assembly, token budgeting, quick actions, streaming, cancel | `web/backend/app/services/chat/` |
| Transport layer | WebSocket hub with replay, twelve event envelopes, WS endpoint | `web/backend/app/transport/` |
| HTTP route surface | Health, session, audio, ASR, LLM, chat, transcript, config, pages | `web/backend/app/routes/` |
| Request/response schemas | Pydantic validation, one module per route group | `web/backend/app/schemas/` |
| Persistence models | Segment and session models, optional `speaker` field reserved | `web/backend/app/models/` |
| Shared contracts | Generated OpenAPI spec plus hand-authored WebSocket event schema | `web/shared/contracts/` |
| Template tree | Base, pages, and ~25 partials across header, transcript, chat, settings | `web/frontend/templates/` |
| Stylesheet set | Tokens, base, layout, themes, and one file per component | `web/frontend/static/css/` |
| ES module set | Core utilities, transport, six stores, per-component controllers, a11y helpers | `web/frontend/static/js/` |
| **Streaming engine tests** | LCP agreement, timestamp rebasing, trim arithmetic, all six guards, commit policy, passthrough parity | `tests/transcription/test_local_agreement.py`, `test_timestamp_rebasing.py`, `test_buffer_trim.py`, `test_guards.py`, `test_engine_commit_policy.py`, `test_engine_passthrough.py` |
| **Audio and VAD tests** | Format conversion, ring-buffer drop counting, preprocessing, hysteresis, file source timing | `tests/utils/test_audio_formats.py`, `test_ring_buffer.py`, `test_preprocess.py`, `tests/transcription/test_vad_hysteresis.py`, `test_file_source.py` |
| **ASR seam tests** | Contract conformance, scripted mock revision behaviour, registry, optional-import guard | `tests/transcription/test_asr_contract.py`, `test_asr_mock.py`, `test_asr_registry.py`, `test_faster_whisper_guard.py` |
| **Store and export tests** | Append-only persistence, crash-reopen recovery, four queries, FTS, five export formats | `tests/data/test_transcript_store.py`, `test_transcript_queries.py`, `test_transcript_search.py`, `test_export_formats.py` |
| **Transport tests** | Route behaviour, WebSocket contract, idempotent reconnection replay | `tests/api/test_health.py`, `test_session_routes.py`, `test_transcript_routes.py`, `test_ws_contract.py`, `test_ws_reconnect_replay.py` |
| **LLM and chat tests** | Provider clients over a stubbed transport, four-way error classification, connection-test outcomes, priority context assembly, token budget drop order | `tests/api/test_llm_providers.py`, `test_llm_errors.py`, `test_llm_connection_test.py`, `test_context_assembly.py`, `test_token_budget.py` |
| **Configuration tests** | Layer precedence, hot-swap classification, credential never persisted to config | `tests/utils/test_config_layers.py`, `test_config_hotswap.py` |
| **Degradation and soak tests** | LLM failure does not stop transcription, device loss, model swap, accelerated flat-memory soak | `tests/api/test_degradation.py`, `tests/transcription/test_soak_accelerated.py` |
| Fixture generator | Builds small synthetic WAV inputs for the test suite | `scripts/make_fixture_wav.py` |
| Console pipeline runner | Runs a WAV file through the full pipeline with console output — the BE M4 validation | `scripts/run_file_session.py` |
| Soak runner | Accelerated long-run driver sampling memory and real-time factor | `scripts/soak_session.py` |
| Contract generator | Writes the OpenAPI spec into `web/shared/contracts/` | `scripts/generate_openapi.py` |

---

## 5. Phase Status

Update this table as each phase lands. Do not delete completed rows.

| # | Phase | Status |
|---|---|---|
| 1 | Foundations — deps, app skeleton, config, docs pivot | Not started |
| 2 | Audio capture layer | Not started |
| 3 | Voice activity detection | Not started |
| 4 | ASR abstraction (Seam A) | Not started |
| 5 | Streaming engine | Not started |
| 6 | Transcript store | Not started |
| 7 | Session manager, workers, metrics | Not started |
| 8 | Transport layer | Not started |
| 9 | LLM abstraction (Seam B) | Not started |
| 10 | Chat orchestration and context pipeline | Not started |
| 11 | Frontend foundation and transcript pane | Not started |
| 12 | Frontend chrome and settings | Not started |
| 13 | Frontend chat, glossary, cross-pane, states | Not started |
| 14 | Hardening, soak, merge | Not started |
