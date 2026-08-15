# Architecture

*Last updated: 2026-08-14 (Phase 1 — foundations)*

> **Status: in construction.** Phase 1 of `docs/plans/live-seminar-transcriber.md` has landed: the
> application skeleton and the configuration system. The pipeline stages below are documented as the
> agreed design and are built phase by phase. Each section notes what exists.

## What this application is

A **single-user local application** that continuously captures audio from a microphone or from system
playback, transcribes it in near-real-time through a pluggable speech model, maintains a growing
timestamped transcript, and exposes that transcript to a language model so the user can ask questions
about a talk while it is happening.

The driving use case is attending a seminar on an unfamiliar topic. That shapes every decision:
sessions run 45–120 minutes, so memory and compute must be flat over time; the content is jargon-heavy,
so transcription errors on technical terms are the dominant failure mode; the user reads rather than
listens to the output, so 2–4 seconds of latency is acceptable and worth trading for accuracy.

**Superseded model.** This repository was initialized around an *upload a file, poll a job* transcription
service. That model is gone — see Decision **D-010**. There is no upload endpoint, no job queue, and no
job identifier. The unit of work is a live **session**, not a file.

## Application mode

**Mode G — API plus separate frontend**, with one adjustment: the frontend is server-rendered from the
same process rather than being a separately built Node application (Decision **D-011**). The three-way
split of `web/backend/`, `web/frontend/`, and `web/shared/` is unchanged; what changed is that
`web/frontend/` holds Jinja2 templates, CSS, and ES modules with no build step, instead of a React
application with an npm toolchain.

The backend still owns the entire pipeline and publishes a complete HTTP plus WebSocket contract. A
packaged desktop shell could serve exactly the same frontend later without touching the backend — the
contract is what keeps that option open, and choosing the packaging is not urgent.

## The pipeline

```text
   microphone / system loopback / WAV file
                    │
                    ▼
   ┌────────────────────────────┐
   │ 1. AUDIO CAPTURE           │  services/audio/
   │    16 kHz mono float32     │  canonical format enforced here and nowhere else
   └──────────────┬─────────────┘
                  │  fixed-size frames, ring-buffered, never blocking
                  ▼
   ┌────────────────────────────┐
   │ 2. VOICE ACTIVITY          │  services/vad/
   │    speech flag + pauses    │  hysteresis shared across detectors
   └──────────────┬─────────────┘
                  ▼
   ┌────────────────────────────┐        ┌──────────────────────────┐
   │ 4. STREAMING ENGINE        │◄──────►│ 3. ASR ABSTRACTION       │
   │    services/streaming/     │        │    services/asr/  SEAM A │
   │    buffer · commit · trim  │        │    mock · faster-whisper │
   └──────────────┬─────────────┘        └──────────────────────────┘
                  │  committed segments + a single hypothesis tail
                  ▼
   ┌────────────────────────────┐
   │ 5. TRANSCRIPT STORE        │  services/transcript/ — SQLite, append-only
   └──────────────┬─────────────┘
                  ├──────────────────────────► TRANSPORT ──► browser
                  ▼
   ┌────────────────────────────┐
   │ 6. CONTEXT PIPELINE        │  services/context/ — summaries, glossary, chunks
   │    POLISH PASS             │  services/polish/  — a minute at a time, for reading
   │                            │  flatten → rewrite → reconcile → check
   └──────────────┬─────────────┘
                  ├──────────────────────────► TRANSPORT ──► browser
                  ▼
   ┌────────────────────────────┐        ┌──────────────────────────┐
   │ 8. CHAT ORCHESTRATOR       │◄──────►│ 7. LLM ABSTRACTION       │
   │    services/chat/          │        │    services/llm/  SEAM B │
   └──────────────┬─────────────┘        └──────────────────────────┘
                  └──────────────────────────► TRANSPORT ──► browser
```

## The two seams

The whole design rests on two swap points. Everything else is plumbing that can be rewritten later.

| Seam | Location | What it buys |
|---|---|---|
| **A — ASR interface** | `web/backend/app/services/asr/contract.py` | Swapping speech models is a configuration value, not a rewrite. The streaming engine never learns which model it is driving. |
| **B — LLM interface** | `web/backend/app/services/llm/contract.py` | Switching between a local server and a hosted API is a mode setting. One OpenAI-compatible client covers Ollama, llama.cpp, LM Studio, vLLM, LocalAI, and OpenAI itself. |

## Design constraints

These are invariants, not preferences. Each names the failure it prevents.

| # | Constraint | Violation produces |
|---|---|---|
| C1 | The in-memory audio buffer must not grow with session length | Gigabytes consumed; inference time grows without limit |
| C2 | Per-iteration compute is roughly constant | The transcriber falls progressively behind and never recovers |
| C3 | Real-time factor stays above 1 | Unrecoverable lag; the queue grows without bound |
| C4 | Committed text is immutable | The reading position jumps; LLM context contradicts what the user saw |
| C5 | Capture never blocks on transcription | Dropped audio, silently lost words |
| C6 | The streaming engine is model-agnostic | Swapping models means rewriting the hard part |

## Layer responsibilities

| Layer | Location | Owns | Must not |
|---|---|---|---|
| Templates | `web/frontend/templates/` | Page structure, server-rendered markup | Hold behaviour; that is the JS modules' job |
| Static modules | `web/frontend/static/js/` | Client state, event handling, rendering | Duplicate a rule the backend also enforces |
| Contracts | `web/shared/contracts/` | OpenAPI spec, WebSocket event schema | Contain logic |
| Routes | `web/backend/app/routes/` | HTTP shape, validation, status codes | Contain business logic |
| Transport | `web/backend/app/transport/` | WebSocket hub, event fan-out, replay | Know about the pipeline's internals |
| Schemas | `web/backend/app/schemas/` | Request and response validation | Reach into storage |
| Services | `web/backend/app/services/` | The entire pipeline | Know about HTTP |
| Models | `web/backend/app/models/` | Persistence shape | Contain business logic |
| Config | `web/backend/app/config/` | Layered settings, presets, credentials | Store a secret in a file |

The rule that matters: **routes stay thin, services hold the work, and the frontend never
reimplements a backend rule.**

## Concurrency model

Four workers connected by queues. This structure is what satisfies **C5**.

| Worker | Priority | May block? | Queue behaviour |
|---|---|---|---|
| Capture | Highest | **Never** | Writes into a bounded ring buffer; drop-oldest on overflow, and every drop is counted |
| ASR / streaming engine | High | Yes — this is the expensive one | Reads the bounded capture queue |
| Context pipeline | Low | Yes, and interruptible | Must never delay a chat request or contend for the GPU |
| Chat | On demand | Yes | Cancellable in flight |
| Transport | Responsive | Never on the event path | Hypothesis events coalesce to the latest; committed events are never dropped |

Single process, `asyncio` for transport, dedicated threads for capture and inference. Python's global
interpreter lock is tolerable because inference libraries release it during native computation. If
capture stalls are ever observed under soak, the escape hatch is moving ASR into its own process
behind the same queue interface — the interface is designed for that, but the split is not built.

## Configuration

Four layers resolved in order, each overriding the last: **built-in defaults → user config file →
session overrides → runtime UI changes**. Only sparse overlays are stored above the defaults, so a
default that changes in a later release reaches users who never touched that setting.

Every setting is classified by what changing it costs — **live**, **restart-stage**, or
**restart-session** — and the frontend reads that classification to decide whether to warn the user.
See `web/backend/app/config/hotswap.py`.

Three presets ship rather than expecting twenty parameters to be tuned by hand: **Accuracy**,
**Balanced** (the default), and **Low resource**.

## Authentication and multi-user support

**None, deliberately.** This is a single-user local application bound to `127.0.0.1`. There are no user
records, no sessions in the auth sense, and no data isolation, because there is exactly one user and
the data never leaves their machine.

This is a decision, not an omission. If the application is ever exposed beyond loopback, an
authentication model must be designed first — an unauthenticated transcript endpoint on a network
interface publishes the contents of a private room.

## Privacy

Architectural, not cosmetic:

- **Local-only is a genuine option.** With a local ASR model and a local LLM, no audio and no text
  leaves the machine.
- **Data flow is visible.** The frontend shows *fully local* or *sending data to \<provider\>* in the
  header, derived from whether both the ASR model and the LLM provider are local. It is not a buried
  setting.
- **Audio retention defaults to off**, with the disk and consent implications stated in plain language
  at the point of the setting.
- **Transcript content is never written to application logs.**
- **Credentials never enter the config file, the logs, or any response to the frontend.** The frontend
  learns only whether a credential is present.

## Failure handling

The guiding principle: **transcription is the critical path.** An LLM failure must never stop
transcription. A chat error is an inconvenience; a lost transcript is a ruined seminar.

| Failure | Detection | Response |
|---|---|---|
| Audio device disconnected | Stream error, or level flat at zero | Notify, offer reselection, keep the transcript intact |
| Model fails to load | Load exception | Name the likely cause — VRAM, missing file, wrong device — and offer a smaller model |
| Out of GPU memory | Allocation error | Fall back to CPU or a smaller model, and say that it happened |
| Real-time factor below 1 | Rising queue depth | Persistent warning naming a specific remedy, with a control to apply it |
| LLM endpoint unreachable | Connection error | Distinguish *not running* from *wrong URL* from *auth rejected*; transcription is unaffected |
| Polish pass fails, or rewrites rather than tidies | LLM error, or the length guard in `services/polish/guard.py` | Emit no block. The page keeps showing raw segments, which is what it shows with no model at all — the fallback is the ordinary state, not a degraded one |
| Polish pass invents or loses a timestamp | `reconcile_timestamps` against the markers that were actually supplied | Drop the unaccounted-for marker; if none survives, fall back to the block's own start. A wrong timestamp is worse than a missing one — it is indistinguishable from a real one until the reader clicks it |
| Repetition loop | N-gram detector | Truncate, force-commit, log |
| Disk full | Write error | Warn early, keep the transcript in memory, do not crash |

## Open architectural questions

| Question | Status |
|---|---|
| Eventual packaging — browser plus local server, or a desktop shell | Open. The contract keeps both available; the choice is the user's. |
| Default ASR model and compute device | Open. Depends on the user's hardware and must be benchmarked locally. A conservative default ships. |
| Process split for ASR | Deferred. Designed for, not built. Revisit only if soak testing shows capture stalls. |
| Embeddings-based retrieval | Deferred. Keyword search over SQLite FTS5 is expected to be sufficient for single-talk sessions. |
| Speaker diarisation | Out of scope for v1. The segment model reserves an optional `speaker` field so adding it later is not a migration. |

Tracked in `docs/checklist.md`.
