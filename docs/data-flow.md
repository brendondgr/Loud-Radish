# Data Flow

*Last updated: 2026-08-29 (one folder per recording, the web-app export, and the capture offset)*

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

2b. FILTER INVENTED SPEECH                    services/asr/hallucination.py
   A speech model given non-speech audio does not go quiet — it returns the phrases
   its training data ends with. The decoder's own Silero filter strips non-speech
   before it is decoded; what survives is then judged on the model's own no-speech
   estimate, on word confidence, and against a literal list of observed boilerplate.
   A discarded pass returns empty, which is what genuine silence produces anyway.
   Every layer here can delete real speech, so the count is published rather than
   the discards being silent.

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
   `transcript.committed` appends *within one transcription pass*. A post-capture pass
   publishes over the same channel while the browser is still showing the live one
   (D-022), so the segment store keeps the revision it is displaying and drops the
   rest; on `transcription.done` the pane switches to the newest pass by replacing
   its contents. Appending across passes writes the whole talk out twice.

7. POLISH                                     services/polish/
   Once about a minute of committed transcript has accumulated, the worker waits for
   the speaker to stop for ~2 s and cuts there, at the end of the newest committed
   segment. Four steps, of which only the second involves a model:

     a. FLATTEN     the chunk becomes ONE continuous run of text with [MM:SS] markers
                    already placed in it, every ~15 s, from the segments' own start
                    times. It reads as nonsense here — that is what concatenated
                    speech-model output is.
     b. REWRITE     the model rewrites that whole run at once, not fragment by
                    fragment: repair punctuation and sentences, write out anything
                    dictated aloud ("guard dot py" is guard.py), carry the markers
                    through, return one continuous paragraph, change nothing else.
     c. RECONCILE   markers the model invented, reversed, or repeated are removed;
                    line breaks it inserted anyway are collapsed away.
     d. CHECK       a rewrite far shorter than the source is a summary, and discarded.

   The result is a POLISHED BLOCK, published as `transcript.polished`. The segments it
   covers are left exactly as they are; the block is an additional row, never a
   replacement. With no language model, or on any failure at any step, nothing is
   emitted and the page keeps showing raw segments. That fallback is the design, not
   the error path.

8. SUMMARISE                                  services/context/
   In the background, at low priority: rolling summaries and glossary terms. Never
   allowed to delay a chat request or contend with transcription for the GPU.

9. ANSWER                                     services/chat/
   A question assembles context in priority order under a token budget, calls the
   provider, and streams `chat.delta` frames back. Transcription continues throughout.
```

## Secondary flow — a recorded session (D-021)

```text
device ──► capture ──► VAD (meter only, gates nothing)
                 │
                 └──► WavSink ──► data/recordings/<stamp>-<id>/audio.wav
                                   │  (toggle off)
                                   ▼
                        batch pass, overlapping windows
                                   │
                        ASR ──► segmenter ──► transcript store ──► transport ──► browser
                                   │
                                   └──► audio deleted, unless retention is on or the pass failed
```

Three differences from the live flow, each deliberate:

- **The streaming engine is absent.** No inference runs while capturing. That is the mode.
- **The sink is fed from the capture thread, not from the drop-oldest queue.** The queue discards
  audio to protect inference latency, and audio discarded from a recording is a hole in the only
  copy of the talk.
- **The pass outlives the session.** The transcript store is handed to the runner, which alone
  closes it; the manager keeps a read-only reference so `/api/transcript/...` still serves during
  the pass, and reopens it for reading when the runner says the store is gone.
- **And the pass outlives the process.** It writes where it has got to into the session's own
  database on every window, so a pause — or a `SIGKILL` — leaves enough to pick it up again: which
  file, how far in, which segment id comes next, what prompt it was started with. A resume trims
  everything at or after the window it actually restarts on and re-derives it from the audio, which
  makes it idempotent whatever the process was doing when it stopped (**D-045**).
- **The transcript outlives the session too.** The store stays open for reading until the next
  session starts, so a question asked *after* a talk — a summary, a definition, what was missed —
  reaches the same store the live ones did, and a reload still finds segments to re-fetch. Asking
  after the fact is the ordinary case rather than the edge one, and closing the store on stop broke
  it three separate ways (**D-031**).
- **And it outlives the process.** That store is held in an attribute, so a restart lost it: the
  database sat on disk and nothing reopened it, and the main page came up empty while Recordings
  showed the same talk. The application now reopens the newest session that holds segments, once,
  at start-up — chosen by the timestamp in the filename rather than by mtime, because a polish pass
  or an export writes into a database long after its talk ended. `storage.reopen_last_session`
  turns it off (**D-041**).

## Where a recording's pieces end up (D-032)

```text
data/sessions/<stamp>-<id>.db        the transcript, summaries, glossary, conversation
data/recordings/<stamp>-<id>/        everything the capture itself produced
    ├── audio.wav                    deleted by a successful pass, unless retention is on
    ├── audio.json                   what the audio measured as, written by the pass
    ├── video.<ext>                  window mode only
    ├── video-with-audio.<ext>       the muxed copy; both sources are kept
    └── preview.jpg                  the monitor pane's frame
```

**The two names are the same string on purpose.** The transcript is an open SQLite file for the
whole of a session and is not moved into the folder to tidy a listing; sharing the stem joins them
without moving anything. Every consumer that has to answer "what does this session hold" — the
past-sessions list, the media indicators, the web-app export — does it by listing one directory.

## The two capture clocks, and the offset between them (D-035)

```text
press record
   │
   ├─ t0  audio source starts ──────────────► WavSink, and the engine. Transcript time zero.
   │
   │      (the screen-cast portal asks which window, and waits for a human)
   │
   └─ t1  GStreamer's first encoded frame ──► video.<ext>. Video time zero.
```

`t1 − t0` is the capture offset. It is **not drift** — it is fixed from the first frame — and it is
the price of an ordering that is correct: audio starts before the portal is asked, so the first
words of a talk are not lost while someone chooses a window. Measured at 2.1 s on the recording that
prompted this, and bounded only by how long that dialog stays open.

The mux corrects it by delaying the video, never by trimming the audio, because **the transcript is
in the audio's clock** and the exported web application syncs the transcript against the muxed file.
The offset is derived rather than observed: nothing marks the first encoded frame arriving, so the
video's start is taken as the moment it was told to stop, minus its own encoded duration. An offset
that cannot be measured is zero — never a guess.

## Export flow — a recording as a web application (D-034)

```text
GET /api/sessions/{key}/webapp
    ├──► transcript store  ──► transcript.json   (latest revision only — never the union)
    ├──► resolved config   ──► settings.json     (local endpoint; api_key present and empty)
    ├──► both again        ──► bundle.js         (the file:// fallback; fetch is refused there)
    ├──► template/         ──► index.html + 2 stylesheets + 5 scripts, copied verbatim
    └──► recording folder  ──► media/<video>     (streamed in, stored uncompressed)
                                   ▼
                              one ZIP, built on a worker thread
```

Nothing in the archive reaches outside it, and nothing in it is a credential. The video is streamed
a megabyte at a time rather than read whole: a talk is measured in hundreds of megabytes and this
runs in the same process as the speech model.

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
