# Capture Hardening

**Status:** Phase 0 complete (measurements). Phases 1–7 not started.
**Mode:** B — no worktree, feature branch `claude/capture-hardening`, merged to `main` at the end.
**Source:** `docs/plans/capture-research-findings.md` (external research, 2026-08-16), answering
`docs/plans/capture-research-brief.md`.

---

## 1. Introduction

External research answered the three open questions from the capture brief and returned a fourth
finding nobody asked for that is worth more than the other three combined. This plan executes all of
it, in the order the research recommends, which is deliberately **not** the order of the original
questions: the highest-damage work is making invisible failures visible, and the highest-value work
is moving video encoding onto hardware that has been sitting idle this whole time.

The through-line is a discipline the last two sessions arrived at the hard way and this plan makes
structural: **an artefact is not correct because a command exited zero — it is correct because it
was opened and measured.** Three separate faults here were well-formed code that failed silently — a
480×16 recording, a `pw-record` AU header decoding to `NaN`, and a 1291-test suite passing while the
feature was broken in every user-visible respect. Phase 2 moves those checks out of the test suite
and into the recorder at runtime, where they see what the machine actually produced.

---

## 2. Gaps & Unanswered Questions

### Settled by measurement in Phase 0 — no longer open

- **Is there a hardware encoder?** **Yes.** `vaav1enc` and `vapostproc` are registered against the
  Radeon 8060S. A real encode produced a decodable 1280×720 AV1 file. H.264/HEVC entrypoints are
  absent — Fedora strips them and `mesa-va-drivers-freeworld` is not installed — so **AV1 is the
  path**, and it needs no RPM Fusion. Measured cost for 150 frames at 720p: **0.90 s user CPU for
  software VP8 against 0.14 s for hardware AV1**, a 6.4× reduction in the CPU this competes with the
  speech model for.
- **Does the transcriber use batched inference?** **No.** Nothing in the tree references
  `BatchedInferencePipeline`. The sequential path is in use, so `compression_ratio_threshold`,
  `log_prob_threshold`, `no_speech_threshold` and `condition_on_previous_text` are live settings and
  worth pinning (research §5.3).
- **Does the portal report a usable stream size?** **No, and it never will** (research §2). `size` is
  optional *and* specified in compositor logical coordinates, explicitly not pixels. Question
  abandoned; the ceiling is rebuilt on negotiated caps.

### Open, with an assumption stated

- **Does the capture chain deliver intelligible speech?** The one P0 item needing a human: recording
  a window in which someone is visibly talking. The transcriber itself is proven — `large-v3-turbo`
  returns 54 correct words from `data/audio/seminar-speech-real.wav` — so this tests the *capture*,
  not the model. *Assumption: it does, and the 2-segments-from-322-seconds result was an honest
  reading of music.* Phase 3's sidecar makes the answer self-evident from then on, whichever way it
  falls, which is why it is not a blocker.

- **Whether `pipewiresrc` on this machine exposes `target-object`.** Research §6.3 says to prefer it
  over `path` where present. *Assumption: check at runtime and fall back to `path`* — this is one
  `gst-inspect` call in Phase 7 and costs nothing if absent.

- **Whether hardware AV1 survives a real portal stream.** It is proven against `videotestsrc`. A
  portal node carries DMA-BUF buffers and possibly exotic modifiers, which is a different negotiation
  (research §6.3 documents real failures in exactly this configuration). *Assumption: it works; the
  design keeps software VP8 as an automatic fallback selected by probing, so a failure degrades
  rather than breaks.* Phase 5's acceptance test is a real window capture, not a test pattern.

- **Whether additive PipeWire linking leaves the user's audio audible on this machine.** Research
  §3.4 is emphatic that this must be confirmed by a human before code is written, because the whole
  tier-1 design rests on it. **Human intervention is needed to answer this question** — it is the
  gate on Phase 6, and Phase 6 is the only phase that cannot be completed without the user.

---

## 3. Hierarchical Step-by-Step Instructions

### Phase 0 — Settle the premise ✅ COMPLETE

- **Locations**: none — measurement only. Results recorded in §2 above and in the commit message.
- **Rationale**: The research document blocks every other phase on three measurements, because two
  of them could invalidate whole phases. Both were decisive: hardware AV1 exists (making Phase 5
  worth doing), and inference is sequential (making Phase 7's threshold pinning meaningful rather
  than dead code).
- **Validated**: `gst-inspect-1.0 va` lists `vaav1enc`; a real encode produced a decodable file;
  `/usr/bin/time` measured the CPU difference; no `BatchedInferencePipeline` reference exists.

### Phase 1 — Caps-driven geometry

- **Locations**: `web/backend/app/services/capture/pipeline.py` (`build`, `_record_scaler`);
  new `web/backend/app/services/capture/geometry.py`;
  `tests/transcription/test_capture_output.py`.
- **Rationale**: The recording's size is currently decided by refusing to scale whenever the portal
  reports nothing, which is safe but abandons the resolution ceiling entirely. Research §2.3 gives
  the correct source of truth — the **negotiated caps** — and the bounds discipline that would have
  caught the 480×16 file: reject `< 16` or `> 16384`, guard the divisor before dividing, round to
  even, and be idempotent under renegotiation. Even dimensions become mandatory rather than lucky
  the moment Phase 5 switches to a hardware encoder (research §6.2).
- **Validation**: `uv run pytest tests/transcription/test_capture_output.py`; a real
  `gst-launch-1.0` run whose output `ffprobe` reports at the expected even dimensions; a unit test
  provoking a zero source size and asserting it refuses rather than producing a degenerate file.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Capture Hardening (1 / 7) Complete: the recording's size is bounded, even, and
  computed from negotiated caps rather than from a portal property that does not exist.`

### Phase 2 — Assertions in the recorder, not only in the tests

- **Locations**: `web/backend/app/services/audio/sources/monitor.py` (`_read`);
  `web/backend/app/services/capture/recorder.py`; new `tests/transcription/test_runtime_guards.py`.
- **Rationale**: Research §9.1 is the most important paragraph in the document: *tests that check
  what your code does cannot catch what the environment does.* The `NaN` check, the byte-count
  check and the dimension bounds check belong where they see real output on a real machine. Three
  checks, each three lines, each corresponding to a fault that actually happened here: assert the
  first second of audio contains no `NaN`; assert the byte count matches `rate × channels × 4 ×
  seconds` within 2 %; assert the recorded dimensions are within bounds and even.
- **Validation**: `uv run pytest tests/transcription/test_runtime_guards.py`; feed the monitor
  source a deliberately mis-specified format and confirm it fails loudly at runtime rather than
  producing a plausible file.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Capture Hardening (2 / 7) Complete: the recorder asserts on its own output at
  runtime, where it can see what the machine produced.`

### Phase 3 — The audio-characterisation sidecar and a truthful terminal state

- **Locations**: new `web/backend/app/services/recording/characterise.py`;
  `web/backend/app/services/recording/runner.py`; `web/backend/app/services/session/manager.py`;
  `web/backend/app/models/session.py` (the `done_no_speech` state);
  `web/frontend/static/js/stores/recording.js`; new `tests/data/test_audio_sidecar.py`.
- **Rationale**: `state: done, transcribed_seconds: 322.46, 2 segments` is unfalsifiable from
  outside — it cannot be told apart from a broken transcriber. Research §5.4 promotes the ad-hoc
  measurements from the brief into a permanent artefact written beside every recording: duration,
  RMS, peak, clipping, the band-energy table, the dominant frequency, and — carrying most of the
  value — `speech_ratio` and a distinct **`done_no_speech`** terminal state. With both, two segments
  over five minutes stops looking like a crash and starts looking like an accurate reading. Research
  §9.2 notes the second benefit: once every recording carries its own measurements, a regression
  becomes a diff between two JSON files.
- **Validation**: `uv run pytest tests/data/test_audio_sidecar.py`; re-characterise the existing
  5 m 20 s recording and confirm the sidecar reproduces the brief's own band table (17.9 % below
  100 Hz, peak at 52 Hz) and reports `done_no_speech`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Capture Hardening (3 / 7) Complete: every recording carries its own measurements
  and an empty transcript says why it is empty.`

### Phase 4 — The Monitor pane: state plus event

- **Locations**: `web/backend/app/routes/capture.py` (new `GET /api/capture/state`, cache headers);
  `web/backend/app/services/session/manager.py` (heartbeat emit);
  `web/backend/app/services/capture/recorder.py` (atomic preview rename);
  `web/frontend/static/js/components/recording-monitor.js`;
  `web/frontend/static/js/main.js`; `tests/api/test_capture_routes.py`.
- **Rationale**: The single `capture.state` emit is a fact broadcast once into a lossy channel with
  no reconciliation (research §4.2). Four ordinary events break it permanently: a page loaded late,
  a socket reconnect, a second tab, or the emit racing the recorder. The standard fix is
  state-plus-event — an authoritative `GET`, fetched on every socket open, with the event kept as a
  latency optimisation and a heartbeat so a missed one self-heals. Research §4.3 adds the
  browser-side items that bite afterwards: `Cache-Control: no-store` so a cached 404 does not stick,
  a cache-busted `src`, an `onerror` retry rather than giving up, a refresh on `visibilitychange`,
  and an atomic `os.replace` of the JPEG so a torn half-written frame is never served.
- **Validation**: `uv run pytest tests/api/test_capture_routes.py`; manual browser QA against the
  four scenarios in research §4.3 — hard reload, second tab, 30 s backgrounded, socket restart.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Capture Hardening (4 / 7) Complete: the monitor pane reconciles against an
  authoritative state instead of trusting one broadcast it may never have received.`

### Phase 5 — Hardware AV1 encoding, with software fallback

- **Locations**: `web/backend/app/services/capture/probe.py` (`ENCODERS`, `choose_encoder`);
  `web/backend/app/services/capture/pipeline.py` (`encoder_args`, `build`);
  `tests/transcription/test_capture_pipeline.py`; `tests/transcription/test_capture_output.py`.
- **Rationale**: Measured in Phase 0 at **6.4× less CPU** than software VP8. This application
  transcribes and records simultaneously, so every core `vp8enc` occupies is a core the speech model
  does not get — the same asymmetry that made the GPU worth 33× against 8.3× for transcription.
  Selection is by probing which `va*enc` elements registered, so a machine without them silently
  keeps VP8; the choice is logged rather than assumed. Research §6.3's defensive pipeline shape
  arrives with it: an early format capsfilter restricting negotiation to packed 8-bit RGB, keeping
  it away from the 10-bit and exotic-modifier formats that `pipewiresrc` is documented to mishandle;
  a fixed capsfilter before the encoder so a window resize is absorbed by `videoscale` and never
  reaches an encoder that cannot handle mid-stream resolution changes; and `keepalive-time=1000` so
  a static window cannot starve the muxer.
- **Validation**: `uv run pytest tests/transcription/test_capture_pipeline.py
  tests/transcription/test_capture_output.py`; a real capture that `ffprobe`s clean and plays;
  `/usr/bin/time -v` showing the CPU drop; resizing the source window repeatedly during a 60 s
  recording and confirming the file is 60 s at fixed dimensions throughout.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Capture Hardening (5 / 7) Complete: video encodes on the GPU's media engine, and
  a window resize no longer reaches the encoder.`

### Phase 6 — Per-window audio through an additive PipeWire tap

- **Locations**: new `web/backend/app/services/audio/tap.py`;
  `web/backend/app/services/audio/monitor.py`; `web/backend/app/services/audio/sources/monitor.py`;
  `web/backend/app/config/schema.py`; new `tests/transcription/test_audio_tap.py`.
- **Rationale**: The feature the brief was really asking for, with a shipping reference
  implementation to copy (OBS's Application Audio Capture, research §3.3). The mechanism is
  PipeWire's graph: a node's output ports may be linked to more than one destination, so the target
  application's audio can be tapped **additively** into a hidden `Audio/Sink/Internal` sink without
  taking it off the user's speakers. That retires the brief's own objection to this feature.
  Three constraints are load-bearing and each corresponds to a documented failure: link the **set**
  of the application's nodes and keep linking new ones, because a browser opens a node per media
  element; set **`node.dont-fallback`** on the capture stream, because PipeWire's default on a
  failed link is to connect you to the default target and record the wrong audio successfully; and
  destroy the tap explicitly, because leaked null sinks accumulate across crashes and are
  user-visible. **Tier 2 — moving the stream with `pactl move-sink-input` — is not implemented at
  any priority, behind any flag**: it is the only option that can leave a user unable to hear the
  thing they are recording.
- **Gate**: the shell prototype in research §3.4 must be run by a human first, specifically its step
  4 — confirming the application is still audible after the additive link. *Human intervention is
  needed.* If additive linking does not hold on this machine, the phase stops and tier 0 remains.
- **Validation**: `uv run pytest tests/transcription/test_audio_tap.py`; with two applications
  playing different audio, a recording of one containing only that one — **verified by spectrum, not
  by ear**; both applications audible throughout; `pactl list short modules | grep null-sink` clean
  after the recording ends.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Capture Hardening (6 / 7) Complete: one application's audio is tapped without
  taking it off the user's speakers.`

### Phase 7 — Robustness, documentation, and merge

- **Locations**: `web/backend/app/services/capture/portal.py` (`pipewire-serial`, restore-token
  rotation); `web/backend/app/services/asr/faster_whisper.py` (threshold pinning);
  new `web/backend/app/services/asr/hallucination.py` additions (denylist);
  `docs/documentation.md` (decision **D-027**); `docs/deployment.md`; `docs/checklist.md`;
  `docs/plans/README.md`.
- **Rationale**: The remaining research items, each small and each closing a known hazard. The
  portal's `pipewire-serial` (v6) is absent on this v5 backend but reading it when present and
  falling back costs six lines and removes a class of "bound to the wrong window after docking" bug,
  since node ids are reused and serials never are. The **restore token is single-use** — a new one
  comes back on every `Start` and the old is invalidated, so persisting once and replaying it
  reproduces the dialog-every-time symptom that was just fixed. Thresholds are pinned explicitly
  now that Phase 0 established inference is sequential and they are therefore live. D-027 records
  why the encoder is AV1 rather than H.264 (Fedora's patent stripping), why the ceiling is built on
  caps rather than the portal, and why tier 2 audio capture is permanently out of scope.
- **Validation**: full `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, a
  final end-to-end window session, and the merge into `main`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Capture Hardening (7 / 7) Complete: D-027 records the encoder, geometry and audio
  decisions, and the branch is merged into main.`

---

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Geometry resolver | Bounds-checked, even-rounded, idempotent target size from negotiated caps | `web/backend/app/services/capture/geometry.py` |
| Geometry tests | Zero/absurd source sizes refuse rather than producing a degenerate file | `tests/transcription/test_capture_output.py` |
| Runtime guards | NaN, byte-count and dimension assertions inside the recorder, not the suite | `web/backend/app/services/audio/sources/monitor.py`, `web/backend/app/services/capture/recorder.py` |
| Runtime guard tests | A mis-specified format fails loudly instead of producing a plausible file | `tests/transcription/test_runtime_guards.py` |
| Audio characterisation | RMS, peak, clipping, band energy, dominant frequency, speech ratio | `web/backend/app/services/recording/characterise.py` |
| Recording sidecar | The measurements written beside every recording as JSON | `web/backend/app/services/recording/runner.py` |
| `done_no_speech` state | A terminal state distinct from `done`, so an empty transcript explains itself | `web/backend/app/models/session.py` |
| Sidecar tests | Reproduces the brief's band table from the existing recording | `tests/data/test_audio_sidecar.py` |
| Capture state endpoint | `GET /api/capture/state` as the authoritative source, plus a heartbeat emit | `web/backend/app/routes/capture.py` |
| Monitor pane reconciliation | Fetch on connect, cache-busted src, retry on error, refresh on visibility | `web/frontend/static/js/components/recording-monitor.js` |
| Atomic preview write | `os.replace` so a torn half-written JPEG is never served | `web/backend/app/services/capture/recorder.py` |
| Capture route tests | 404-while-idle, no-store headers, state shape | `tests/api/test_capture_routes.py` |
| Hardware AV1 encoding | Probed at startup, logged, software VP8 fallback | `web/backend/app/services/capture/probe.py`, `pipeline.py` |
| Defensive pipeline shape | Early format capsfilter, fixed pre-encoder caps, `keepalive-time` | `web/backend/app/services/capture/pipeline.py` |
| PipeWire application tap | Hidden `Audio/Sink/Internal`, additive links, `node.dont-fallback`, explicit teardown | `web/backend/app/services/audio/tap.py` |
| Tap tests | Link set grows with new nodes; teardown leaves no null sink | `tests/transcription/test_audio_tap.py` |
| Portal robustness | `pipewire-serial` preferred, restore-token rotated on every `Start` | `web/backend/app/services/capture/portal.py` |
| Hallucination denylist | Drops the stock phrases Whisper emits on non-speech | `web/backend/app/services/asr/hallucination.py` |
| Decision D-027 | Why AV1, why caps-driven geometry, why tier 2 is out of scope | `docs/documentation.md` |
