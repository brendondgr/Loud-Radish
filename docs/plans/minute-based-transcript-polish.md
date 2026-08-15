# Minute-Based Transcript Polish

*Created: 2026-08-15 · Status: **complete** (7 / 7 steps)*

## 1. Introduction

The transcript pane currently shows exactly what the speech model produced: short committed
segments, each a fragment of a sentence, with erratic punctuation and no paragraph structure. That
is the correct *record*, but it is hard to read. This plan adds a background pass that takes the
transcript roughly one minute at a time and rewrites each minute into clean, readable block text —
proper punctuation, sentences that flow, real paragraphs — **without changing what was said**. The
polished block then replaces the raw segments it covers on the page, so the page is incrementally
rebuilt as the talk proceeds.

The pass runs entirely in the backend, on a schedule, with no user action. It is gated on the
language model being reachable: when no model is available, or a call fails, or the model returns
something that fails the content-integrity guard, nothing is emitted and that stretch of transcript
stays exactly as it looks today. The raw segments are never deleted — the polished text is a
derived, additive layer, so the verbatim record and its timestamps survive intact and export is
unaffected.

Chunking is triggered by a natural break rather than a hard cut. The worker accumulates about a
minute of *committed* transcript, then waits for the speaker to stop talking for about two seconds
before cutting. A hard ceiling catches a speaker who never pauses.

---

## 2. Gaps & Unanswered Questions

*Every assumption below was implemented as written. One detail changed during the build and is
recorded here rather than left contradicting the code: the chunk planner shipped as a module-level
`decide_cut()` returning a `CutDecision`, not as a `ChunkPlanner` class — it holds no state, and a
stateless class would have been a namespace pretending to be an object.*

- **Naming collision with the existing rolling summaries.** `ContextWorker` already produces
  `summary.added` records, and those *are* summaries — lossy compression used to fit a two-hour talk
  into a context window. This feature is the opposite: it must preserve every claim. *Assumption*:
  the new feature is named **polish** throughout (`services/polish/`, `polished_blocks`,
  `transcript.polished`), and the two systems coexist without touching each other. Recorded as
  Decision D-018.

- **How "a pause in speech" is detected.** The VAD already tracks a silence run, and
  `SpeechGate.silence_seconds` is exactly the needed quantity. *Assumption*: the polish worker reads
  it through a new `SessionManager.silence_seconds` property rather than subscribing to VAD events,
  because it polls on its own tick anyway. When the VAD is disabled the gate reports zero silence
  forever, so the hard ceiling becomes the only cut trigger — which is correct behaviour, not a bug.

- **What the chunk boundary is measured against.** Cutting at a wall-clock instant risks losing
  words that are still in the hypothesis tail and have not committed yet. *Assumption*: the cut lands
  on the **end time of the newest committed segment**, so a chunk contains only fully committed text
  and nothing is ever polished twice or skipped.

- **Turning reasoning off across providers.** There is no portable field. Different servers accept
  `reasoning_effort`, `chat_template_kwargs.enable_thinking`, or `think`. *Assumption*: send all
  three as extra body fields (nearly every OpenAI-compatible server ignores unknown fields), instruct
  the model in the prompt to answer directly, discard any `reasoning` chunk that arrives anyway, and
  — if a request fails with a client error while the extras are present — retry once without them and
  remember that for the rest of the session.

- **What happens when the model rewrites rather than tidies.** A model asked to clean up text will
  sometimes summarise it instead. *Assumption*: a content-integrity guard compares the returned word
  count against the source; a result under a configurable ratio (default 0.6) is rejected and the raw
  segments stay. This is a guard against over-compression, not a semantic verifier — a model that
  paraphrases at similar length will pass, and the prompt is the only defence against that.

- **Whether polished text should replace raw text in exports.** *Assumption*: no. The export is the
  verbatim record with timestamps; polished text is a reading aid. Noted as deliberate scope in
  `docs/checklist.md` rather than silently omitted.

- **Whether the polished view should be dismissible.** Not requested. *Assumption*: the settings
  toggle (`polish.enabled`) is the control; there is no per-page toggle in this change. Listed as a
  follow-up.

---

## 3. Hierarchical Step-by-Step Instructions

### Step 1: Configuration surface and the durable record

- **Locations**:
  - `web/backend/app/config/schema.py` — new `PolishConfig` class (`enabled`, `chunk_seconds`,
    `pause_seconds`, `max_chunk_seconds`, `disable_reasoning`, `min_retained_ratio`); a `polish`
    field on `AppConfig`.
  - `web/backend/app/config/presets.py` — confirm the three presets still resolve; adjust only if a
    preset should tune `chunk_seconds`.
  - `web/backend/app/models/session.py` — new `PolishedBlock` dataclass with `as_event()`.
  - `web/backend/app/services/transcript/schema.sql` — new `polished_blocks` table
    (`id`, `start`, `end`, `text`, `source_ids`, `created_at`).
  - `web/backend/app/services/transcript/store.py` — `add_polished_block()`, `polished_blocks()`,
    `last_polished_end()`; `SessionStats` gains a polished-block count.
  - `tests/data/test_transcript_store.py` — round-trip test for the new table.
- **Rationale**: everything downstream reads from configuration and writes to the store, so both
  must exist and be tested before any worker can be written. Putting the table in `schema.sql` with
  `CREATE TABLE IF NOT EXISTS` means an older session file opens without migration and simply has no
  polished blocks.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest
  tests/data tests/utils`, `uv run ruff check .`). Once validated, push changes to GitHub stating:
  Minute-Based Transcript Polish (1 / 7) Complete: Added the polish configuration section, the
  polished-block record, and its SQLite table.

### Step 2: The chunk planner and the content-integrity guard

- **Locations**:
  - `web/backend/app/services/polish/__init__.py` — package exports.
  - `web/backend/app/services/polish/chunker.py` — `decide_cut(cursor, last_committed_end,
    silence_seconds, config, final)` returning a `CutDecision`: a cut point or `None`, and the
    reason either way. No clock, no I/O.
  - `web/backend/app/services/polish/guard.py` — `strip_decoration()` (removes bold, italic, heading
    and blockquote markers while leaving list bullets alone) and `preserves_content()` returning a
    pass/fail plus the reason.
  - `tests/assistant/test_polish_chunker.py`, `tests/assistant/test_polish_guard.py`.
- **Rationale**: these are the two pieces of logic most likely to be wrong and the two easiest to
  test in isolation. Extracting them from the worker means the worker's own test does not have to
  drive a clock to prove that "wait a minute, then wait for a pause, then cut" behaves correctly.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest
  tests/assistant`, `uv run ruff check .`). Once validated, push changes to GitHub stating:
  Minute-Based Transcript Polish (2 / 7) Complete: Added the pause-aware chunk planner and the
  content-integrity guard, with unit tests.

### Step 3: The polish worker and its prompt

- **Locations**:
  - `web/backend/app/services/polish/prompts.py` — `POLISH_PROMPT` (a numbered instruction list, not
    an open request: what to strip, what to reformat, what must not change, the no-markup rule, lists
    permitted) and `NO_REASONING_EXTRAS`.
  - `web/backend/app/services/polish/worker.py` — `PolishWorker`: the tick loop, segment collection,
    the LLM call via `LlmBackend.stream`, reasoning discard, the retry-without-extras path, the guard
    check, storage, event emission, and one-time failure reporting.
  - `tests/assistant/test_polish_worker.py` — fake store and fake backend: happy path, model
    unavailable, over-compression rejected, empty answer with `finish_reason == "length"`, and the
    final flush on stop.
- **Rationale**: the worker is where the requirements meet reality — the prompt carries the "do not
  alter the content" instruction, and every failure path must leave the raw transcript untouched
  rather than degrade it. Built after the planner and guard so it can compose them rather than
  reimplement them.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest
  tests/assistant`, `uv run ruff check .`). Once validated, push changes to GitHub stating:
  Minute-Based Transcript Polish (3 / 7) Complete: Added the background polish worker, its explicit
  instruction prompt, and its failure paths.

### Step 4: Pipeline wiring

- **Locations**:
  - `web/backend/app/services/session/manager.py` — a `silence_seconds` property reading the gate;
    a `polish_worker_factory` hook and its start/stop, mirroring `context_worker_factory`; the stop
    path flushing the final partial chunk before the store closes.
  - `web/backend/app/main.py` — `_wire_assistant()` constructs the `PolishWorker` from the same
    `backend_factory`, with a **config provider** rather than a snapshot so a settings change applies
    on the next tick.
  - `tests/transcription/test_session_manager.py` — the worker starts with a session, stops with it,
    and a worker that raises on construction does not prevent recording.
- **Rationale**: the manager owns session lifetime and is the only object that can see both the VAD
  gate and the store. Wiring through a factory keeps `SessionManager` free of any language-model
  import, which is what lets the pipeline run on a machine with no model at all.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest
  tests/transcription tests/assistant`, `uv run ruff check .`). Once validated, push changes to
  GitHub stating: Minute-Based Transcript Polish (4 / 7) Complete: Wired the polish worker into the
  session lifecycle behind a factory, so a machine with no model is unaffected.

### Step 5: Transport and HTTP surface

- **Locations**:
  - `web/backend/app/transport/events.py` — `TRANSCRIPT_POLISHED`, added to `ALL_EVENTS` and to
    `CRITICAL_EVENTS`.
  - `web/backend/app/transport/ws.py` — replay stored polished blocks alongside the segment replay
    in `_handle_hello`.
  - `web/backend/app/schemas/api.py` — `PolishedBlocksResponse`.
  - `web/backend/app/routes/transcript.py` — `GET /api/transcript/polished`, using
    `_optional_store` so a page load before the first session is not a 404.
  - `web/shared/contracts/` — regenerated via `uv run python scripts/generate_contracts.py`.
  - `tests/api/test_transport.py` — the new event is in the vocabulary and the replay includes
    polished blocks.
- **Rationale**: a reload or a dropped socket must not leave the page showing raw text for minutes
  that were already polished. The replay and the HTTP endpoint are the two paths that make the client
  recoverable, and both must exist before the frontend can depend on either.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest
  tests/api`, `uv run python scripts/generate_contracts.py`, `uv run ruff check .`). Once validated,
  push changes to GitHub stating: Minute-Based Transcript Polish (5 / 7) Complete: Published polished
  blocks over the WebSocket and HTTP, including reconnection replay.

### Step 6: The frontend

- **Locations**:
  - `web/frontend/static/js/transport/events.js` — the `TRANSCRIPT_POLISHED` constant.
  - `web/frontend/static/js/transport/api.js` — a `polished()` fetch.
  - `web/frontend/static/js/stores/polish.js` — a new store holding blocks in time order.
  - `web/frontend/static/js/main.js` — route the event into the store; hydrate on load.
  - `web/frontend/static/js/components/transcript-pane.js` — render a polished block in place of the
    raw segments it covers, keeping `scrollToTime()` and `highlight()` working by carrying the
    covered segment ids on the block element.
  - `web/frontend/static/css/components/transcript.css` — styles for the polished block and the
    boundary between polished and still-raw text.
  - `web/frontend/templates/partials/settings/context.html` — a "Tidy the transcript" group exposing
    `polish.enabled`, `polish.chunk_seconds`, and `polish.pause_seconds`.
  - `docs/component-map.md`, `docs/design-system.md` — updated in this step.
- **Rationale**: this is the only step the user actually sees. The pane change is the delicate part:
  the hypothesis tail must stay outside the polished region, the still-raw tail must remain readable
  while the current minute accumulates, and the segment-trimming budget must count blocks as well as
  segments so a long session does not regrow the DOM it was trimming.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest`,
  `uv run ruff check .`, plus manual browser QA at 320 px and a keyboard-only pass per
  `docs/design-system.md`). Once validated, push changes to GitHub stating: Minute-Based Transcript
  Polish (6 / 7) Complete: The transcript pane now shows polished block text for each finished minute
  and raw segments for the minute in progress.

### Step 7: Documentation, full verification, and merge

- **Locations**:
  - `docs/documentation.md` — Decision D-018 (polish is not summarisation), status row.
  - `docs/structure.md` — `services/polish/`, `stores/polish.js`, the new tests.
  - `docs/api-contract.md` — the `transcript.polished` event and the new endpoint.
  - `docs/routes.md` — `GET /api/transcript/polished`.
  - `docs/data-flow.md` — the polish loop as a branch off the transcript store.
  - `docs/architecture.md` — where the worker sits and why it cannot affect transcription.
  - `docs/checklist.md` — close this work, record the follow-ups (export, per-page toggle, real-model
    QA).
  - `docs/plans/README.md` and this file — mark complete.
- **Rationale**: the repository contract is that documentation ships with the code. This step also
  runs the whole suite once rather than per-area, and performs the merge into `main`.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest`,
  `uv run ruff check .`, `uv run ruff format --check .`). Once validated, push changes to GitHub
  stating: Minute-Based Transcript Polish (7 / 7) Complete: Documented the polish pass and merged the
  feature into main.

---

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Polish configuration | Interval, pause threshold, ceiling, reasoning switch, integrity ratio | `web/backend/app/config/schema.py` |
| Polished block record | The dataclass and its event payload | `web/backend/app/models/session.py` |
| Polished block storage | Table, writes, and reads | `web/backend/app/services/transcript/schema.sql`, `store.py` |
| Chunk planner | Decides when a minute is ready to cut, at a pause | `web/backend/app/services/polish/chunker.py` |
| Integrity guard | Strips decoration; rejects over-compression | `web/backend/app/services/polish/guard.py` |
| Polish prompt | The explicit instruction list and the no-reasoning hints | `web/backend/app/services/polish/prompts.py` |
| Polish worker | The background loop and every failure path | `web/backend/app/services/polish/worker.py` |
| Session wiring | Silence exposure and worker lifecycle | `web/backend/app/services/session/manager.py`, `web/backend/app/main.py` |
| Event and endpoint | `transcript.polished`, replay, `GET /api/transcript/polished` | `web/backend/app/transport/{events,ws}.py`, `routes/transcript.py`, `schemas/api.py` |
| Frontend store | Polished blocks held in time order | `web/frontend/static/js/stores/polish.js` |
| Pane rendering | Blocks replacing the segments they cover | `web/frontend/static/js/components/transcript-pane.js`, `static/css/components/transcript.css` |
| Settings controls | Toggle, interval, pause threshold | `web/frontend/templates/partials/settings/context.html` |
| Chunk planner tests | Boundary behaviour, pause waiting, hard ceiling | `tests/assistant/test_polish_chunker.py` |
| Guard tests | Decoration stripping and compression rejection | `tests/assistant/test_polish_guard.py` |
| Worker tests | Happy path and every failure path, against fakes | `tests/assistant/test_polish_worker.py` |
| Store tests | Polished block round-trip | `tests/data/test_transcript_store.py` |
| Transport tests | Event vocabulary and reconnection replay | `tests/api/test_transport.py` |
| Session tests | Worker starts and stops with a session | `tests/transcription/test_session_manager.py` |

---

## 5. Step Status

| Step | Title | Status |
|---|---|---|
| 1 | Configuration surface and the durable record | ✅ Complete |
| 2 | Chunk planner and content-integrity guard | ✅ Complete |
| 3 | Polish worker and prompt | ✅ Complete |
| 4 | Pipeline wiring | ✅ Complete |
| 5 | Transport and HTTP surface | ✅ Complete |
| 6 | Frontend | ✅ Complete |
| 7 | Documentation, verification, merge | ✅ Complete |
