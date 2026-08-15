# TranscriberPrototype — Project Documentation

*Last updated: 2026-08-15 (the multi-mode expansion — D-020 and D-021)*

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
| Transcript store | ✅ Phase 6 — SQLite, FTS5 search, query surface, five export formats |
| Session manager and metrics | ✅ Phase 7 — worker wiring, bounded queues, health telemetry, degradation |
| Transport (HTTP + WebSocket) | ✅ Phase 8 — event hub, reconnection replay, full HTTP surface |
| LLM abstraction (Seam B) | ✅ Phase 9 — OpenAI-compatible and Anthropic clients, error taxonomy |
| Chat and context pipeline | ✅ Phase 10 — context assembly, quick actions, summaries, glossary |
| Frontend foundation | ✅ Phase 11 — templates, tokens, transport, transcript pane |
| Frontend chrome, settings, chat | ✅ Phases 12–13 — settings modal, chat pane, glossary, sessions |
| Hardening and soak | ✅ Phase 14 — degradation tests, accelerated soak, export, docs |
| Minute-based transcript polish | ✅ D-018 — background clean-up pass, optional at every level |
| Polish: dialogue accuracy | ✅ D-018 — spoken code references written out, timestamps retained, continuous prose |
| Invented-speech suppression | ✅ D-019 — the model's own no-speech estimate, a phrase list, and the decoder's speech filter |
| Multi-mode expansion — five plans | ✅ Written. [Interface design](plans/multi-mode-ui-design.md), [interface implementation](plans/multi-mode-ui-implementation.md), [recorded transcription](plans/recorded-transcription.md), [window recording](plans/window-recording-transcription.md), [system integration](plans/system-integration.md) |
| Capture-mode vocabulary | ✅ D-020 — three modes, six run states, mirrored on both sides and checked identical |
| Multi-mode interface | ✅ Three-way mode selector, six-state record control, pre-flight sheet, monitor pane |
| Recorded transcription mode | ✅ D-021 — capture with no inference, one batch pass on stop, and a recovery path for a failed one |
| Window recording mode | ⬜ Not started — [Plan 4](plans/window-recording-transcription.md) |
| Desktop integration | ⬜ Not started — [Plan 5](plans/system-integration.md); its motion spec is [motion-spec.md](motion-spec.md) |

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
    ├──────────────► polish pass — a minute at a time, rewritten for reading ──► browser
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
| **D-018** | **The transcript is polished a minute at a time, and polishing is not summarising** | A speech model emits fragments: two seconds of speech with guessed punctuation, and a page of those is a wall of half-sentences. The clean-up pass rewrites each finished minute into readable prose. Three properties make it safe. **It is not summarisation** — `services/context/` already summarises, which is lossy by design; this must not drop a claim, so the prompt is a numbered instruction list rather than a request, and a length guard rejects a rewrite that came back as a summary. **It is additive** — polished blocks are stored alongside the segments they cover, never in place of them, so the verbatim record, its timestamps, search, and export are untouched and a bad rewrite costs one redundant row. **Its fallback is doing nothing** — with no language model, an unreachable one, a failed call, or a result that fails the guard, no block is produced and the page shows exactly what it showed before the feature existed. Chunks are cut at a pause in speech rather than on a timer, and always at the end of a committed segment, so no chunk ever contains half a sentence or omits words still in the hypothesis tail. **Amended 2026-08-15**, when what the pass produced turned out to be wrong in three ways. It transcribed dictation literally, so a speaker saying "guard dot py" appeared on the page as *guard dot py*; the prompt now writes such references out and is granted exactly enough licence to repair the grammar around a substitution, since a written form is a different number of words from a spoken one. It carried no timestamps, so a polished stretch could not be traced back to when it was said; markers are now placed deterministically *before* the model sees the text and reconciled against that list afterwards, because a model asked to work out timestamps fabricates plausible ones and a fabricated timestamp is worse than none. And it broke to a new line constantly; the chunk is now handed over as one continuous run, one continuous paragraph is what should come back, and the page draws consecutive minutes as consecutive paragraphs with no rule or heading between them. |
| **D-019** | **Invented speech is filtered on the model's own confidence, not on heuristics alone** | Whisper does not go quiet on non-speech audio: it returns the phrases its training data ends with — "thank you", "bye", a bare "you" — confidently and with plausible word timings, and they are indistinguishable from a transcript by reading them. Reported from real use and reproduced on a synthetic tone fixture that produced a segment saying "you". The existing silence gate was working; its *input* was the problem, because an energy detector is cleared by a fan or a keyboard. The fix uses what the model already knew and was discarding: `no_speech_prob` and `avg_logprob` were computed on every pass and never read, because the backend collected `segment.words` and nothing else. Four layers now: the decoder's own Silero filter keeps non-speech out of inference (measured *faster*, not slower — less audio to decode); the model's no-speech estimate condemns a pass, with a second tier above 0.85 where it needs no corroboration; optional word-confidence; and a literal list of the observed boilerplate phrases, matched only when one is the entire output of a short pass. **The governing constraint is that every layer can delete something real**, so defaults are conservative, each layer switches off on its own, discarded text is logged, and the count is published and surfaced in the status bar once non-zero — a filter that silently removes speech is a worse bug than the one it fixes. Absent signals are read as *no opinion* rather than as grounds to drop. |

| **D-020** | **Capture mode and run state are separate vocabularies with separate controls, defined once and mirrored** | The application is growing from one thing it can do to three, and a boolean cannot hold that. Two questions are being asked at once — *what kind of recording is this* and *where has this recording got to* — and the tempting answer, one control that cycles through everything, fails on inspection: a button whose meaning depends on how many times it has already been pressed cannot be understood without reading its label, and the label is the thing that changes. So there are two controls, sitting adjacent so they still read as a pair. The **run states are six, not two**: `arming` exists because window capture must collect options and wait for a desktop picker before anything is captured, and `processing` exists because a recorded pass transcribes after capture ends and can run longer than the recording did — both are states a user will sit and look at, so both need a name, a label, and an announcement rather than being folded into "recording". Which states each mode can reach is data (`MODE_STATES`), not branching, because `live` never processes and only `window` arms, and encoding that as conditionals spread across the interface is how a mode ends up displaying a state it can never be in. **The vocabulary is defined twice on purpose and defended by a test.** It has to exist in Python, which owns the session, and in the browser, which draws it; D-011 removed the build step that would otherwise let one import the other. Two hand-maintained copies drift, and they drift silently — a mode renamed on one side leaves the other sending a string nobody handles, and the interface shows a confidently wrong state rather than an error. `tests/utils/test_mode_vocabulary.py` therefore reads `modes.js` as text and asserts every name, list order, and mode→state edge matches. Parsing it with a regular expression rather than a JavaScript engine keeps the suite free of the Node toolchain D-011 deleted, at the cost of that one file staying simple enough to read that way — a trade its own header states so the next editor knows the constraint is deliberate. |

| **D-021** | **A recorded session does no inference while capturing, and its post-capture pass deliberately bypasses the commit policy** | Live transcription commits two to four seconds behind the speaker because it must decide what is settled while more audio is still arriving. That latency is the price of watching words appear, and when nobody is watching it buys nothing at all — while the inference itself still costs a CPU running flat out for the length of a talk. `recorded` mode inverts the trade: capture writes a WAV and starts no engine, and the whole file is transcribed once, afterwards, with every word of context available. **LocalAgreement is not reused for that pass**, which is the decision most likely to be second-guessed. Agreement answers one question — *what is safe to show now, given the next second might change it?* — and once the file is complete that question no longer exists. Running it anyway would reproduce its latency and its trimming heuristics for nothing and would throw away the context that makes the mode worth having. The **segmenter** *is* reused, and that is what makes the mode cheap to integrate: identical segment shape, id sequence, and boundaries, so the store, the FTS index, export, citations, and the polish pass all work on a recorded transcript without knowing how it was produced. Three consequences are load-bearing. **The sink writes on the capture thread rather than through the drop-oldest queue** — that queue exists to protect inference latency by discarding audio, and audio discarded from a recording is a hole in the only copy of the talk; a buffered kilobyte write is orders of magnitude cheaper than the pass the queue decouples from, so the constraint it guards is not reintroduced. **The pass outlives the session it came from**, so the transcript store is *handed over* rather than borrowed and the runner alone closes it — two owners of one SQLite connection, one tearing down while the other writes, was not hypothetical: it happened, because ownership was a parameter to teardown and `shutdown` reaches teardown by a second path that could not know. **Audio is deleted only once the transcript is safely written, and kept whatever the retention setting says when a pass fails** — at that moment it is the only remaining copy of what was said, and the failure message names its path because `/api/recordings` can run the pass again against it. |

## Where to Go Next

- Layout and file placement → [structure.md](structure.md)
- Commands and daily workflow → [workflow.md](workflow.md)
- Open work → [checklist.md](checklist.md)
- System design → [architecture.md](architecture.md)
- The build plan → [plans/live-seminar-transcriber.md](plans/live-seminar-transcriber.md)
