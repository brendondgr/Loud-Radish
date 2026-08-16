# Project Checklist

*Last updated: 2026-08-15 (the multi-mode expansion)*

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

## Part 3d — Suppressing Invented Speech

Tracked in [plans/asr-hallucination-suppression.md](plans/asr-hallucination-suppression.md).
**All four steps are complete.** Reported from real use: the transcript inventing "thank you",
"bye", and stray words over room noise. Reproduced on a synthetic tone fixture that produced a
segment reading "you"; all three non-speech fixtures now commit zero words.

- [x] `no_speech_prob` and `avg_logprob` carried out of the backend instead of discarded
- [x] The filter: model confidence, optional word confidence, and a literal phrase list
- [x] The decoder's own Silero filter, on by default — measured faster, not slower
- [x] Settings in Speech model, the count in the status bar, and the documentation (D-019)

---

## Part 3e — The Multi-Mode Expansion

Five plans, written 2026-08-15, indexed in [plans/README.md](plans/README.md). The application grows
from one capture mode to three, and from a browser tab to a resident desktop application.

- [x] **Plan 1 — [Interface design](plans/multi-mode-ui-design.md)** (5 / 5). The mode and run-state
      vocabulary, mirrored on both sides of the wire and checked identical by test; the header's two
      controls; the pre-flight sheet; the monitor pane. Specification, not implementation.
- [x] **Plan 2 — [Interface implementation](plans/multi-mode-ui-implementation.md)** (6 / 6). Mode
      selector, six-state record control, pre-flight sheet, third pane, and `mode` carried through
      `POST /api/session/start`. Live transcription verified unchanged, in tests and in the browser.
- [x] **Plan 3 — [Recorded transcription](plans/recorded-transcription.md)** (6 / 6). Capture to a
      WAV with no inference; transcribe the whole file in one pass on stop; delete the audio
      afterwards unless retention is on, and keep it when the pass fails. Recorded as **D-021**.
- [x] **Plan 4 — [Window recording](plans/window-recording-transcription.md)** (7 / 7). Portal
      ScreenCast window capture, optional video, live and post-process transcription, the monitor
      pane, and transcript revisions so both passes can be kept. Recorded as **D-022**. One
      measurement is outstanding — see the CPU-contention item below.
- [x] **Plan 5 — [System integration](plans/system-integration.md)** (6 / 6). Autostart, a tray
      companion process drawing the Aperture microphone ([motion-spec.md](motion-spec.md)), global
      keybinds through KGlobalAccel, and a keybind settings tab. Recorded as **D-024**. One hop is
      outstanding — see the StatusNotifierItem item below.

Three things the interface work got wrong on paper and right only once it was run. Recorded because
each was invisible to reading and obvious to using, which is the argument for the manual pass:

- **Requiring a capture device disabled every mode.** A plain `uv sync` has no `sounddevice`, and
  the file source replaces a capture device entirely — so the selector struck out all three modes on
  a machine that transcribes perfectly well. Only `window` has a hard requirement now.
- **The interface stuck in "Stopping…" permanently.** The guard returning to idle named only
  `recording`, excluding the very state a stop passes through.
- **The primary control was pushed off-screen at 320 px.** The header has wrapped since Phase 12,
  but each header *group* was a non-wrapping row — fine at three items, not at four.

- [ ] **A finished transcript is not reloadable until the sessions page is opened.** When a session
      ends the store closes, so `GET /api/session` reports no stats and the page cannot re-fetch its
      segments; a reload shows an empty transcript. This is **pre-existing live-mode behaviour**, not
      something recorded mode introduced — but it lands harder here, because the transcript arrives
      *after* the toggle and a reload a moment later loses text the user has only just seen. The
      record itself is safe on disk and readable from `/sessions`. Worth closing by keeping the last
      session's store open for reading until the next one starts.

- [ ] **The transcription pass is neither resumable nor cancellable.** It runs whole or fails whole.
      A forty-minute recording at RTF ≈ 1.5 takes around twenty-seven minutes, which is long enough
      that resumption is worth wanting — but the right design for it cannot be guessed before anyone
      has watched a real one run. A server restart mid-pass keeps the recording and loses the job;
      the recording then appears in Settings → Storage with a button to run it again.
- [ ] **`recording.batch_window_s` has not been tuned against a real model.** The 30-second default
      matches Whisper's own window, but whether a longer window measurably improves a recorded
      transcript over a live one is the question the mode exists to exploit, and it is unmeasured.

- [ ] **Port takeover is Linux-only.** `app.py` finds the process holding its port through `/proc`;
      on any other platform it degrades to the old "port in use" refusal. Dependency-free was the
      right trade for a launcher, and `psutil` would make it portable if the project ever runs
      somewhere else.

- [x] **One command installs and runs everything (D-023).** No optional dependency groups remain;
      `uv run app.py` is the whole story. GStreamer and a desktop portal are still system packages
      and are named in `docs/workflow.md`.

- [ ] **The tray icon is not yet exported to D-Bus.** Everything behind it is built and tested: the
      Aperture renderer, the frame clock, the (mode, run state) → picture map, the menu model, and
      shortcut registration verified against the real KGlobalAccel. What is missing is the final
      hop — one `org.kde.StatusNotifierItem` object with its icon properties and a `NewIcon` signal,
      so a picture actually appears in the Plasma tray. `Companion.latest_svg` already produces a
      frame per tick; it needs rasterising to ARGB32 and publishing. Recorded here rather than
      claimed, because a tray icon that does not appear is the one part of Plan 5 a user would
      notice immediately.
- [ ] **Whether `jeepney` can export SNI pixmaps at all.** The reason the hop above is separate. If
      it proves unworkable the documented fallback is `PySide6`'s `QSystemTrayIcon`, which speaks
      the same protocol through Qt — decided by trying, not by arguing.

Known blockers and open questions carried by these plans:

- [x] **The tray animation specification.** Supplied 2026-08-15 and extracted into
      [motion-spec.md](motion-spec.md): "The Aperture microphone", Rev A.03 — a thirteen-element
      ribbon grille that is both grille and meter, colourless at rest, with five states plus fault.
      Plan 5 step 5 is rewritten to implement it. Two structural findings came out of reading it
      against D-020, both recorded in `motion-spec.md` §5: the indicator is a function of **(mode,
      run state)**, not run state alone — the spec draws live-mode and recorded-mode recording as
      different pictures — and its *Rewriting* state is the polish pass, which runs concurrently
      with a session rather than as a phase of one.
- [ ] **Whether the Aperture instrument replaces the header's record indicator.** Not adopted by
      default: D-020's motion rule forbids anything that loops in an interface on screen for two
      hours, and a breathing thirteen-bar capsule is exactly that. Recorded as considered rather
      than overlooked. If adopted, the reduced-motion path must hold every bar at rest.
- [ ] **Whether software video encoding and live transcription fit on one CPU — still open, and
      the measurement needs a real recording.** `scripts/measure_capture_cost.py` exists and runs,
      but its synthetic fixture cannot answer the question: Whisper is autoregressive and emits its
      no-speech token after a handful of steps on anything that is not speech, so a tone decodes
      in a fraction of the time a talk would and the real-time factor comes back at 30–150×. That
      figure measures the short-circuit, not the contention. **The script refuses to conclude from
      a run that produced no transcript** and says to re-run with `--audio path/to/a/talk.wav`.
      Do that with a real recording on the machine you will use. Encoding alone measured 13× real
      time at 720p15 with VP8, which is the one half that *is* answered — the encoder has ample
      headroom on its own; what is unknown is what it does to inference running beside it.
- [ ] **Whether the StatusNotifierItem protocol can be spoken directly with `jeepney`.** Plan 5
      avoids pulling a GUI toolkit into the virtualenv for one tray icon. Exporting icon pixmaps as
      D-Bus properties is the part that may not be worth it; the fallback is `PySide6`'s
      `QSystemTrayIcon` behind the same optional group, decided by trying rather than by arguing.
- [ ] **Per-window audio is not available.** The KDE ScreenCast portal carries video only, so window
      capture records the machine's audio, not that window's. Plan 4 says so at the moment of arming
      rather than letting a user discover it in the recording.

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
- [ ] **Tuning the invented-speech thresholds against real audio.** The defaults were calibrated
      against `tiny` on synthetic fixtures, which is not the same thing as a real room. The number
      to watch is `suppressed` in the status bar: climbing while someone is talking means the
      thresholds are too tight and real speech is being deleted, which is the one failure this
      filter can cause. `no_speech_certain` in particular was set at 0.85 because a measured
      hallucination scored 0.901 — a sample of one.
- [ ] **Whether the Silero voice detector should replace the energy one by default.** The optional
      `vad-silero` group already ships and swaps in behind the same interface. The decoder's own
      filter now addresses the same problem inside the model, so this was left alone rather than
      changed blind; it is a one-line setting if the energy detector proves too permissive in a
      noisy room.

---


- [ ] **`tests/api/test_session_toggle.py::test_stopping_ignores_the_mode` fails roughly
      one full-suite run in three, with a SQLite error.** It passes in isolation every time,
      and passed in the two full runs either side of the one that failed, so it is timing and
      not ordering. The test stops a `recorded` session, which is what *starts* a post-capture
      pass and hands that pass the transcript store (D-021) — so the suspicion is the runner's
      thread and the store's close racing under load. `TranscriptionRunner.stop` joins with a
      five-second timeout and then returns regardless, by deliberate design: a server that
      takes half an hour to exit is one nobody will let start automatically. That trade is
      probably right and the abandoned thread is probably the race.

      **Deliberately not fixed by guessing.** A concurrency change made on a hunch is how the
      original "Cannot operate on a closed database" fault was introduced. Reproduce it under
      `pytest -p no:randomly --count` or with the store instrumented to log its close, get the
      full traceback rather than the truncated summary line, and fix what it actually names.
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
- [ ] **A window recording that actually contains the window's sound (D-028).** The fault is
      understood, reproduced, and fixed under measurement — five identically named leaked null
      sinks meant `pw-link` and `pw-record` addressed different nodes, so the capture recorded
      digital silence. Verified by planting the exact fault and clearing it: RMS 0.0 before, 0.071
      after, no sink left behind. What has *not* been observed is a full session end to end: press
      record on a window with a video playing and read the transcript back against what was said in
      it. The tap now refuses to start rather than recording silence, so a failure here should be a
      message rather than an empty transcript.
- [ ] **The new encoder quality on a real portal stream.** Measured on a real capture that had
      already been through the old 256 kbps encoder once: 35.44 dB → 44.80 dB at the default
      setting, so the gap against a pristine stream is wider than that. Worth one recording to
      confirm the picture is what you expect and that `balanced` is the right default for how you
      use it — `capture.quality` also takes `efficient` (≈600 MB/hour) and `high` (≈1200 MB/hour)
      against the default's ≈930 MB/hour.
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
