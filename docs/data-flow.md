# Data Flow

*Last updated: 2026-08-14 (Phase 1 — foundations)*

> **Status: the design is agreed; the stages land phase by phase.** Update this file in the same
> change that alters how data moves between capture, pipeline, storage, transport, or browser.

The upload-and-poll flow recorded at initialization is **superseded** — see Decision D-010. Audio is
never uploaded; it is captured locally and consumed in flight.

## Primary flow — a live session

```text
1. CAPTURE                                    services/audio/
   Device or WAV file ──▶ resample to 16 kHz mono float32 ──▶ fixed-size frames
   Frames go into a bounded ring buffer. If the consumer falls behind, the OLDEST
   audio is overwritten and the drop is counted. Capture never waits. (C5)

2. GATE                                       services/vad/
   Each frame gets a speech flag, with hysteresis so the state does not flicker.
   Silence runs past the threshold emit a PAUSE EVENT — a safe place to cut.

3. ACCUMULATE AND COMMIT                      services/streaming/
   Speech frames append to a growing buffer. Every ~0.75 s the buffer is submitted
   to the ASR backend. This pass's words are compared with the previous pass's:
   the longest common prefix is COMMITTED, the remainder stays HYPOTHESIS.

4. TRIM AND REBASE                            services/streaming/
   Audio for committed words is deleted, preferring a sentence boundary, keeping a
   0.5–1 s acoustic tail. `buffer_start_absolute` advances by the duration removed.
   Every timestamp leaving the engine is session-absolute. (C1, C2)

5. PERSIST                                    services/transcript/
   Committed segments are written through to SQLite as they commit — never held in
   memory alone. A crash 80 minutes in must lose at most the last few seconds.

6. PUBLISH                                    transport/
   `transcript.committed` (append) and `transcript.hypothesis` (replace) go to every
   connected client, alongside audio level, VAD state, and health telemetry.

7. POLISH                                     services/polish/
   Once about a minute of committed transcript has accumulated, the worker waits for
   the speaker to stop for ~2 s and cuts there, at the end of the newest committed
   segment. The chunk goes to the language model with an instruction list — repair
   punctuation, sentences, and paragraphs, change nothing else — and comes back as a
   POLISHED BLOCK, published as `transcript.polished`. The segments it covers are
   left exactly as they are; the block is an additional row, never a replacement.
   With no language model, or on any failure, nothing is emitted and the page keeps
   showing raw segments. That fallback is the design, not the error path.

8. SUMMARISE                                  services/context/
   In the background, at low priority: rolling summaries and glossary terms. Never
   allowed to delay a chat request or contend with transcription for the GPU.

9. ANSWER                                     services/chat/
   A question assembles context in priority order under a token budget, calls the
   provider, and streams `chat.delta` frames back. Transcription continues throughout.
```

## The two timestamp domains

**This is where the bugs are.** The ASR backend returns times relative to the audio array it was
handed. The streaming engine holds `buffer_start_absolute` and converts on the way out:

```text
absolute_time = buffer_start_absolute + relative_time
```

Relative timestamps never leave the engine. Everything in the transcript store, the transport events,
and the frontend is session-absolute.

## Boundaries

| Boundary | Carries | Defined by |
|---|---|---|
| Device ↔ capture | Raw device frames at the device's own rate | `services/audio/formats.py` — canonical format enforced here and nowhere else |
| Capture ↔ engine | 16 kHz mono float32 frames plus a speech flag | Bounded queue, drop-oldest |
| Engine ↔ ASR backend | An audio array and an optional biasing prompt, in; word tokens with **relative** times, out | `services/asr/contract.py` — Seam A |
| Engine ↔ store | Committed segments with **absolute** times | `services/transcript/store.py` |
| Store ↔ transport | Segments and a hypothesis string | `transport/events.py` |
| Transport ↔ browser | JSON event frames over WebSocket; JSON over HTTP | `docs/api-contract.md`, `web/shared/contracts/` |
| Chat ↔ provider | An ordered message list plus generation parameters | `services/llm/contract.py` — Seam B |

Rules:

- Routes never touch the pipeline or storage directly. They call services.
- The frontend never reimplements a backend rule. The backend owns configuration; the frontend reads
  it, presents it, writes changes back, and re-reads.
- **Raw audio never crosses to the browser.** Only text, timings, and telemetry do.

## Queue and backpressure policy

| Queue | Bound | On overflow |
|---|---|---|
| Capture → engine | Bounded | Drop **oldest**. Every drop is counted and surfaced. Capture never blocks. |
| Engine → transport, hypothesis events | Bounded | Coalesce — only the latest matters |
| Engine → transport, committed events | Bounded | **Never dropped.** Backpressure is applied upstream instead. |
| Everything → persistence | Unbounded, batched | Flushed on an interval |

If real-time factor drops below 1 the system will never catch up on its own. That is detected, not
hoped away: the response is to warn plainly and offer a smaller model, with automatic degradation as a
configurable fallback.

## State ownership

| State | Owner | Notes |
|---|---|---|
| Live audio frames | Backend, in memory only | Bounded ring buffer. Never persisted unless audio retention is explicitly on. |
| Committed transcript | Backend, SQLite | Written through on commit. The authoritative record. |
| Hypothesis tail | Backend, in memory | Ephemeral by definition; never persisted |
| Summaries and glossary | Backend, SQLite | Written as produced |
| Chat history | Backend | Survives a browser reload |
| Configuration | **Backend** | The frontend keeps no parallel copy (FE §8.1) |
| Credentials | OS credential store | Never in a config file, a log, or a response |
| View preferences — pane widths, theme, collapsed panels, text size | Browser `localStorage` | The only client-owned state |
| Scroll position and follow state | Browser, in memory | Ephemeral |

## Privacy-relevant paths

Two paths can send data off the machine, and both are explicit:

1. **A hosted LLM provider** — transcript excerpts, when `llm.mode` is `api`.
2. **A hosted ASR backend** — room audio. Deferred to v2; the interface accommodates it.

When both the ASR model and the LLM provider are local, nothing leaves. The frontend derives its
*fully local* indicator from exactly that condition, in the header rather than in settings, because it
is what a user needs to confirm at a glance before pressing record in a room full of people.
