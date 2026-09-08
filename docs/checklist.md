# Project Checklist

*Last updated: 2026-09-08 (the batch pass cut at pauses — D-062)*

The active work list for Loud Radish. Update it whenever a task is finished or new work is
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

- [x] **A finished transcript is not reloadable until the sessions page is opened.** Closed by the
      remedy this item proposed: the last session's store stays open for reading until the next one
      starts. See Part 3h and **D-031** — the same line was refusing every question the assistant
      was asked after a stop, which is how it came to be fixed.

- [x] **The transcription pass is neither resumable nor cancellable — it is both now (D-045).**
      The item said the right design "cannot be guessed before anyone has watched a real one run",
      and that turned out to be exactly right: watching one run is what found the window-boundary
      defect. A server restart mid-pass now keeps the recording *and* the job.
- [ ] **`recording.batch_window_s` has not been tuned against a real model.** Since D-062 it is the
      *longest* chunk, cut shorter at a pause wherever one exists, and the model walks anything over
      thirty seconds itself. Whether a longer cap measurably improves a recorded transcript over a
      live one is the question the mode exists to exploit, and it is still unmeasured; what a longer
      cap certainly does is make a pause or a cancel wait longer for the chunk in flight.

- [ ] **Port takeover is Linux-only.** `app.py` finds the process holding its port through `/proc`;
      on any other platform it degrades to the old "port in use" refusal. Dependency-free was the
      right trade for a launcher, and `psutil` would make it portable if the project ever runs
      somewhere else.

- [x] **One command installs and runs everything (D-023).** No optional dependency groups remain;
      `uv run app.py` is the whole story. GStreamer and a desktop portal are still system packages
      and are named in `docs/workflow.md`.

- [x] **The tray icon is not yet exported to D-Bus — it is now.** The final hop is written and
      verified against the real Plasma watcher: `Companion.latest_svg` still produces a frame per
      tick, and beside it `companion/raster.py` produces the same frame as ARGB32 for the icon
      property. See Part 3l, **D-042** and **D-043**.
- [x] **Whether `jeepney` can export SNI pixmaps at all — it can.** Measured 2026-09-06 rather than
      argued, which is what this item asked for. `jeepney` 0.9.0 serialises a 22 x 22 ARGB32 buffer
      into a `Properties.Get` reply of signature `a(iiay)` and parses it back **byte-identical**; it
      builds `NewIcon` signals and `GetAll` replies; and `DBusConnection` exposes
      `receive`/`send`/`filter` beside `new_method_return` and `new_error`, which is every primitive
      a served object needs. This machine is already running an `org.kde.StatusNotifierWatcher` with
      a host registered. **`PySide6` is therefore very unlikely to be needed**, and the remaining
      unknown is only the dispatch loop, not the protocol. Planned in
      [plans/tray-restart-clutter-and-interruptible-work.md](plans/tray-restart-clutter-and-interruptible-work.md).

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
- [x] **Whether the StatusNotifierItem protocol can be spoken directly with `jeepney` — yes.**
      The same measurement as above, recorded twice because the question was asked twice. Pixmap
      marshalling was the part feared not worth it and it is a non-issue; what is left is a dispatch
      loop, which Step 5 of the plan builds.
- [ ] **Per-window audio is not available.** The KDE ScreenCast portal carries video only, so window
      capture records the machine's audio, not that window's. Plan 4 says so at the moment of arming
      rather than letting a user discover it in the recording.

---

## Part 3g — The Final Result Said Everything Twice

Reported: stop a window session, wait for the pass that runs at the end, and the finished result
showed the rewritten prose followed immediately by the entire raw transcript again. Planned in
[plans/duplicate-final-transcript.md](plans/duplicate-final-transcript.md) and closed (4 / 4).

- [x] **Nothing was duplicating text — the rule in the contract was never applied.** Both passes are
      supposed to exist (**D-022**), and `docs/api-contract.md` already said a client showing
      revision 1 must *replace* the transcript rather than merge. Four call sites merged.
- [x] **The browser holds one pass at a time.** `stores/transcript.js` drops a committed segment
      whose revision is not the one on screen, so a post-capture pass streaming in over the live
      socket can no longer append the whole talk under the transcript already there. Enforced in the
      store, for the same reason the hypothesis is its own field: the shape is the safeguard.
- [x] **The second pass appears by replacing, not by joining.** `transcription.done` now switches
      the pane to the newest revision, which resets and refetches. `recorded` mode, whose only pass
      is revision 0, is unaffected — the switch is a no-op when the revision has not changed.
- [x] **Three read paths served the union.** `GET /api/transcript/export`,
      `GET /api/sessions/{key}` and `GET /api/sessions/{key}/export` used `all_segments()`. They use
      `latest_segments()` now, so the file a reader keeps holds the talk once in all five formats.
- [x] **A regression test that fails on the old code.** `tests/data/test_two_pass_output.py` builds
      a two-pass session and asserts each sentence appears exactly once in every export format and
      on the sessions page — verified failing before the fix, seven of thirteen.

- [ ] **Export cannot name a revision.** `/export` serves the latest pass and there is no way to ask
      for the live one. The sessions page has no revision control to drive such a parameter, so
      adding it would have widened a bug fix into a feature. Worth doing alongside a revision
      control on that page.
- [ ] **`segments_in_range` still spans passes.** The context, polish, and chat paths read through
      it. Not reachable today — the post-capture pass runs after those workers have stopped and
      closes the store when it finishes — but it is the same union, and it would surface the moment
      a session with two passes is reopened for reading.

---

## Part 3f — A Tap That Is Linked, Active, and Silent

Reported: window recording "not working again" — the video recorded perfectly and the transcript
stayed empty. Recorded as **D-030**. Reproduced against the real graph and fixed under measurement.

- [x] **The fault, located.** The tap is built exactly as designed and delivers bit-exact digital
      silence: sink created, browser ports linked, every link `active`, both nodes `running`, every
      gain 1.0, 43 seconds of capture at the right sample rate with not one non-zero sample.
- [x] **Why neither guard saw it.** `live_links` counts links and there were two; the silence probe
      it replaced was removed for the sound reason that zeros are also what a paused video looks
      like. Each question was right and neither was sufficient alone.
- [x] **The mechanism is not broken.** `pw-play` linked into the *identical* tap in the same second
      came back at 440.4 Hz / RMS 0.35 while the browser stayed at 0.000000 — measured four times
      alternating, and again against a sink created with the browser's own rate and layout.
- [x] **The dead-tap test, and the widening.** Tap silence and the machine's own output are probed
      together; only *tap silent **and** speakers audible* means a dead tap. The capture then widens
      to the whole output and says so, rather than refusing.
- [x] **`-1.0` is unknown, not silent.** A probe that could not run changes nothing.
- [x] **One name is not one node.** `link` keyed by `node.name`, and a browser gives every tab's
      node the same one, so the second tab was marked already-linked and skipped — exactly one tab
      was ever captured. It links by port object id and keys by node id now.

Two things measured along the way, recorded so they are not re-litigated:

- **The `<name>.monitor` target must not be used for a null sink.** It silently records the
  *microphone*, at RMS 0.065 whether or not anything is linked — and because the microphone hears
  the speakers, level alone cannot tell it from a real capture. The discriminator is an unlinked
  tap: a true tap read is 0.0, a microphone fallback is not.
- **`node.dont-fallback` is unusable on PipeWire 1.6.8.** It rejects valid targets by name and by
  id alike with `defined target not found`, so it cannot be the thing that makes a bad target
  visible. Verifying the achieved capture replaces it.

- [x] **`services/session/manager.py` is 1334 lines, against a cap of 800 — split at 1782.** It
      kept growing after this was written. The seam named here, source-selection against
      capture-wiring, is two of the six files it became; see Part 3l and **D-039**.

Still open, and deliberately not guessed at:

- [ ] **Why a `pipewire-pulse` client's output does not reach an additive second sink.** Every
      observable says it should: the tap joins the browser's driver domain (`node.driver-id` goes
      from `None` to 63 on linking), all four links read `active`, all gains read 1.0. A native
      client through the same tap works. Ruled out by measurement: sink volume and the hidden
      `volume` scalar, mute and `softMute`, monitor volumes, the null sink's rate and channel
      layout, and driver-domain separation. This is the reason per-application audio is unavailable
      for browsers, and the widening is a workaround rather than a fix.
- [ ] **`module-combine-sink` as the real fix for per-application audio.** The standard PulseAudio
      approach for this: a combined sink over the real sink plus the tap, with the application moved
      onto it, keeps the audio audible *and* captures it. It is a larger change and it moves a
      stream the user is listening to, which is the risk D-027 refused for `move-sink-input` — worth
      trying deliberately rather than smuggling into a repair.
- [x] **End-to-end confirmation that words appear — done, on a real window recording with consent
      given at the picker.** 55 s of a narrated video: 22 segments, 240 words, coherent and matching
      the video's own on-screen captions; video 1080x910 VP8 muxed with an Opus track; the sidecar
      reads `likely_speech`, speech-band ratio **0.73**, dominant **164.5 Hz**. That last figure is
      what closes the earlier ambiguity — the audio used during the repair characterised as
      `likely_music_or_game` at ratio 0.29 and dominant 29.7 Hz, so the empty transcripts then were
      the audio, not the pipeline.

- [x] **The widening recorded the microphone, and did so for most of a day.** Found only because
      a second real recording tripped it: `default_monitor()` returns `<sink>.monitor`, and that
      target **does not resolve on either sink on this machine** — an unresolved target falls back
      to the default *source*. Measured against a simultaneous microphone capture: the Bluetooth
      output's `.monitor` and the USB dock's both correlate with the microphone at **+1.000**,
      identical to five decimals. It looked plausible throughout because a microphone hears the
      speakers. This is D-028's fault reintroduced by the repair meant to prevent an empty
      transcript, and it is the worse of the two — an empty transcript is visibly empty, a room
      recording reads as a working one. The widening now addresses the sink with
      `stream.capture.sink` (`default_sink()`, not `default_monitor()`), verified live at
      **-0.005** correlation against the microphone on the Bluetooth output.
- [x] **`node.dont-fallback` cannot be the guard on a device sink.** It is right on the tap's null
      sink and refused outright by a device sink on PipeWire 1.6.8 — by name and by node id — so a
      capture carrying it never starts. Without it a *bad* target still falls back silently
      (measured, RMS 0.076), so the safety comes from provenance instead: that path is only given
      a sink `pactl get-default-sink` just named, and the test doubles assert the two sink kinds
      never swap property sets.

- [ ] **The tap delivered on the first successful run, so D-030's fault did not reproduce there
      and the widening never fired.** Stated plainly because it matters for what is actually known: the repair is verified
      to *detect and widen* (its own tests, plus a live run earlier the same day where it fired in
      2.1 s), but this successful recording did not exercise it. Nor can the change be attributed
      with confidence. The browser's stream state differed — two playback nodes during the repair,
      one here — and with a single node the link-by-port-id fix and the old name-keyed code behave
      identically. Against that: linking *only* the video's node by explicit port id during the
      repair still measured bit-exact zeros, which the browser-state explanation does not cover.
      **So the silent-tap condition is intermittent and its trigger is not known.** Both guards
      stay. If it recurs, capture `pw-dump` and the playback-node set at the moment it happens —
      that is the observation the repair never got. **It has since reproduced twice more**, both
      times with more than one playback node linked (6 ports across two LibreWolf nodes and
      `speech-dispatcher-dummy`), against one node on the run that worked — so "several linked
      nodes" is now the strongest lead, though linking only the video's node by port id during the
      repair also measured zeros, which that lead does not explain.

---

## Part 3h — Questions After a Session Ends

Tracked in [plans/questions-after-a-session-ends.md](plans/questions-after-a-session-ends.md).
**All three steps are complete.** Reported: after stopping a transcription, *"Summarise the last 10
minutes"* answered **"There is no transcript to ask about yet"** while the finished transcript was
plainly on screen. Recorded as **D-031**.

- [x] The finished session's store is retained, open for reading, until the next session starts
- [x] `state()` and `session_seconds` read through the `store` property rather than `_store`
- [x] A batch pass reopens the store on release, so `recorded` and `window` are covered too
- [x] Released on the next start and on shutdown, so exactly one is ever held
- [x] Thirteen tests, eleven of which fail on the previous code

Three faults, one line. Worth separating because only the first was reported and the other two were
each sufficient on their own to keep the feature broken:

- **The assistant refused every question.** `ChatService` is wired to `lambda: manager.store`, and
  teardown set that to `None`. The transcript on screen is the *client's* copy, accumulated over the
  socket during the session — it outlives the store, which is exactly why the interface and the
  server disagreed and why the message read as nonsense to the user.
- **A reload showed an empty transcript**, because `state()` read `_store` directly and so reported
  no stats to re-fetch from. Carried above as its own item since the multi-mode expansion.
- **The clock fell to `0.0`**, because `session_seconds` also read `_store` directly — so *"the last
  ten minutes"* would have resolved to the range `[0, 0]` and selected nothing **even once the store
  was retained**. Its own docstring promised the opposite ("so a question asked after a session ends
  is still positioned correctly"); the promise was defeated one line below where it was written.

Deliberately unchanged, recorded so the absence is not mistaken for an oversight:

- **"There is no transcript to ask about yet" still exists, and still fires.** It is correct for an
  application that has never recorded, and two tests hold it there. Retention must not convert an
  honest refusal into an answer invented from nothing.
- **Nothing about the assistant, the quick actions, or the context assembler changed.** They were
  already right; they were being handed `None`. A fix inside them would have been a workaround for a
  fault one layer down.

---

## Part 3i — Recording Folders, the Completion State, and the Web-App Export

Five reported problems, planned in
[plans/recording-folders-and-web-export.md](plans/recording-folders-and-web-export.md) and closed
(6 / 6). Recorded as **D-032**, **D-033**, and **D-034**.

- [x] **One directory per recording.** `data/recordings/` held every artefact from every session in
      one flat list. Each recording now owns `<YYYYMMDD-HHMMSS>-<session-id>/` with fixed names
      inside it, owned by `services/recording/layout.py`; nothing else builds a path in there. The
      folder name is the transcript database's stem, which is what joins a recording to its
      transcript without moving an open SQLite file.
- [x] **Files written before the layout are migrated once, at start-up.** Grouped by the key their
      names already carry. Nothing deleted, nothing overwritten, and anything that does not parse
      left where it is — a recordings directory is a user directory.
- [x] **A finished pass no longer wedges the interface.** The hub replayed the last
      `transcription.progress` frame — which says `running` — to every client that connected
      afterwards. Terminal events retract the coalescing events they end.
- [x] **The clock freezes when the session does.** `stores/session.js` adopts the server's
      `ended_at` rather than a stop the page may never have witnessed, which is the other half of
      the same report.
- [x] **A client that arrives after a pass ended finds its way back to idle.** `adoptSession`
      protects `processing` from a stop, so the `session.state` handler is the only route out.
- [x] **The suite stopped writing into `data/`.** 949 session files, 690 empty, written by test
      runs into the developer's own session directory — which is why the past-sessions page opened
      on "0 words, 0 segments". An autouse fixture patches the bottom configuration layer;
      `tests/utils/test_data_isolation.py` asserts a run creates nothing under `data/`.
- [x] **The route to past recordings is a labelled button.** It was a borderless hamburger icon
      beside the settings cog. It reads "Recordings" and the page is titled the same.
- [x] **Every past session says what it holds.** Video, audio, and transcript as three chips, all
      three always drawn so a column of rows can be scanned. "Has a transcript" means the database
      holds segments, not that the file exists.
- [x] **A recording with all three exports as a self-contained web application.** A ZIP holding the
      video, a transcript that follows it, and a Q&A panel with the model settings built in. Opens
      with no server: classic scripts and a data bundle beside the JSON, because browsers refuse
      both `import` and `fetch` across `file://`. Verified in a browser, both ways.
- [x] **No credential leaves in the archive.** `llm.api_key` ships present and empty; the page keeps
      what the user types in that browser alone.

- [x] **The developer's `data/sessions` still holds the suite's leavings — cleared on request.**
      679 removed, 291 kept. The rule turned out to need a second clause: 28 of the empty databases
      own a recording folder and are failed transcriptions rather than leavings. See Part 3l and
      `scripts/prune_empty_sessions.py`.
- [ ] **The export cannot be scoped to a revision.** It ships the latest pass, like every other
      export. Same gap as the one recorded in Part 3g, and the same remedy — a revision control on
      the recordings page.
- [ ] **The exported page speaks OpenAI-compatible endpoints only.** Anthropic's shape genuinely
      differs (D-014) and a static page cannot hold a key safely, so the export deliberately points
      at a local model. Worth revisiting only if someone asks for it.

---

## Part 3j — Five Faults in One Recording

Reported together against one window session, planned in
[plans/five-recording-faults.md](plans/five-recording-faults.md) and closed (5 / 5). Recorded as
**D-035**. None of them had lost anything: the folder held a complete video, both transcription
passes, and a muxed file with sound in it.

- [x] **A stopped session stopped calling itself live.** The badge asked which session was
      recording by reading `SessionManager.store`, which since D-031 keeps answering after a
      session ends. It asks `is_running` now, which is the question it always meant.
- [x] **Audio means sound you can play.** The chip tested for `audio.wav`, which a successful pass
      deletes — so it read false exactly when the recording was healthiest, and hid the web-app
      export from the only session that qualified for it. `audio_file` reports the narrower fact.
- [x] **The counts describe one pass.** 46 segments and 479 words for a talk of 26. The listing was
      the last reader still counting both passes rather than the latest (D-022).
- [x] **A pre-migration database still lists.** The narrowed query is guarded on the column
      existing: this connection is read-only and 25 of the 965 databases predate the column.
- [x] **"The transcription was never saved" was the page never asking.** `hydrate` loaded the
      transcript only while a session was running, so a reload after a pass showed an empty pane.
- [x] **Polished blocks follow the pass on screen.** A block is built from the segment ids of one
      pass and the post-capture pass writes different ids for the same audio, so loading both
      unfiltered renders the talk twice.
- [x] **One video file per recording.** The silent original goes once the combined file is probed
      and holds both streams — verified, rather than kept for ever or deleted on faith.
- [x] **The picture is aligned with the sound.** The capture offset is measured and applied, and
      the output keeps the audio's timeline because that is the one the transcript is in.

- [ ] **Recordings made before this cannot be re-aligned.** The two start times are not in any
      existing artefact, so files already on disk keep whatever offset they have. Correcting one by
      hand is `ffmpeg -itsoffset <seconds> -i video ...`, with the offset found by eye.
- [ ] **The offset is logged, not stored.** It goes to the log at INFO when a recording is
      combined. Giving it a file of its own was rejected: the same report asked for *fewer* files
      in a recording folder.
- [x] **A restart still hides the last transcript from the live page — closed (D-041).** The
      question it raised, what "the current session" means to a process that has just started, is
      answered: the newest session that actually holds segments, by the timestamp in its name.

## Part 3k — A Seminar That Stopped Recording, and the Export Window

Reported after a Zoom seminar on 2026-09-04, planned in
[plans/capture-resilience-and-export.md](plans/capture-resilience-and-export.md).

The evidence is still in `data/recordings/20260904-155600-08ab28c2d732/`, and it is unambiguous.
Audio and transcript run the full **3925.7 s**. Video frames run at a steady 15.0 fps from 0.000 s
to **882.542 s**, then stop entirely for 882.6 s, then two final frames at 1765.16 s — the EOS
flush. The combined file ends **both** streams at 1765.227 s. And the session's own config snapshot
says `max_height: 720` against a recording made at **2560 × 1532**.

- [x] **The mux keeps the whole of what it was given.** `-shortest` truncated the sound to the
      broken picture; the result still held one stream of each kind, so it passed verification and
      the sources went. Thirty-six minutes of the talk survived the recording and were destroyed by
      the tidy-up. The output now runs as long as its longer input, and a source is deleted only
      when the output demonstrably *contains* it — same duration, not merely the same kinds of
      stream. Step 1 / 9.
- [x] **A short combined file keeps the audio whatever Settings says.** Retention stops being a
      preference at the moment the WAV becomes the only complete copy of the talk. Step 1 / 9.
- [x] **A capture that stops producing frames is noticed.** `_watch` polled `process.poll()` and
      asked one question — has it exited — which a stalled pipeline answers "no" to for as long as
      it lasts. It samples the output file's size on the same tick now, drops `-q` so GStreamer's
      bus messages survive, and sends stderr to a file rather than to a pipe nobody drains — which
      blocks its writer at 64 KB and is a way of *causing* the stall. Step 2 / 9.
- [x] **A capture that ends mid-session resumes.** Reopened on the stored consent token, into
      `video.002.<ext>` and up, bounded at five attempts with a backoff — and silently or not at
      all, because the compositor's answer to a token it cannot restore is to put its picker across
      the talk. A stall is acted on rather than only reported: waiting for the stalled pipeline to
      end on its own cost this recording fifteen minutes of picture. The pieces are joined at
      teardown, each placed by the same derivation the mux's offset uses, with the last frame held
      across every gap — dropped instead, the video would be exactly as much shorter as the
      recording had failed for, and every frame after the gap would sit that far ahead of its own
      transcript line. Step 3 / 9.
- [x] **The user's conversation leaves the shared export.** Six chat messages in the reported
      recording, and every one of them rode into `transcript.json`, into the `bundle.js` beside it,
      into the Markdown export and into the JSON export — then seeded the exported page's assistant,
      so a recipient opened the ZIP mid-conversation with someone else's questions about a talk they
      had not watched. It is a document of its own now (`GET /{key}/chat`), the flag that puts it
      back defaults to off, and the listing reports `chat_messages` so it is offered only where
      there is one. Verified against the real session: nought of six turns in the default archive,
      six of six with `include_chat=true`. Step 4 / 9.
- [x] **A re-encode can be measured before it is committed to.** `ffprobe` says what the recording
      is; five named plans say what it could become; and the estimate is anchored to the
      recording's *own* bits per pixel per frame rather than to a table, because a flat "720p costs
      N MB an hour" is wrong by a factor of three depending on what is on screen. It returns a
      range and says so. Verified with `scripts/calibrate_export_estimate.py` against the reported
      recording: five of five predictions contained the measurement, and the time predictions came
      within a second or two. Step 5 / 9.
- [x] **Exporting is a staged job with real progress.** Four stages — measure, encode, transcript,
      package — each with its own bar and its own estimate, and an overall figure weighted by
      predicted cost rather than by stage count. `export.done` and `export.failed` retract
      `export.progress`, without which a window opened after an export finished would be replayed a
      stale "running" frame for the life of the process, which is the bug D-033 fixed twice for
      transcription. Verified end to end against the reported recording: **129.2 MB to 28.9 MB in
      14 seconds**, against 29.7 MB predicted. Step 6 / 9.
- [x] **One post-recording window: preview, options, projected sizes, stages.** It opens when a
      recording stops — the old flow returned silently to an idle screen and left the user to find
      the Recordings page, pick the right row, and choose between two download links with no idea
      what either would produce — and from the Recordings page, which is where an export is retried.
      Preview, five presets each carrying what it would produce for *this* file, a size that moves
      with the choice, and then one row per stage with its own bar and estimate. Verified in the
      browser against the reported recording: **123 MB to 18 MB in 14 seconds**, against 19 MB
      predicted; focus stays inside the dialog under Tab; and at 320 px nothing scrolls sideways.
      Three faults were found by running it and are fixed: a stray click reaching an unopened
      controller issued `POST /api/sessions//export/start`, a refusal was left standing beside a
      successful export, and a finished job's elapsed time kept climbing — "took 14 s" to the
      window that watched it and "took 2 min" to one opened later, about the same file. Steps
      7–8 / 9.

- [x] **The plan is complete (9 / 9) and recorded as D-036 and D-037.** What was departed from, and
      why, is written down in the plan's own "What Changed From the Plan" section rather than left
      to be rediscovered.

- [ ] **`max_height` is advisory whenever the portal reports no geometry**, which
      `_record_scaler`'s own comment calls "the normal case on this desktop" — so a ceiling of 720
      recorded at 2560 × 1532, roughly four times the intended pixel work, competing with the
      speech model for the same cores. Not fixed at capture: the caps history in that function is
      bad enough that changing it blind is how a recording came out 480 × 16. The remedy taken is
      to make resolution an **export-time** decision, where the real dimensions are known from the
      finished file. Whether capture should also enforce it needs a real portal stream to test
      against.
- [ ] **Why the PipeWire node stalled rather than ended is not recoverable** from what survives.
      `logs/capture.log` still holds an unrelated `amdgpu` line from 2026-08-16, because
      `_write_log` writes only when stderr was non-empty and this run produced none — `gst-launch`
      is invoked with `-q`. Step 2 drops `-q` so the next occurrence is diagnosable.

- [x] **Four tests in `tests/transcription/test_window_audio.py` failed on a quiet machine.** Fixed
      in `tests/conftest.py`, which is where it belonged: the suite-wide guard stopped tests
      *creating* a sink in the developer's PipeWire graph and never stopped them *reading* it, so
      `_open_application_tap`'s query for what is currently playing was live. The four had also been
      left behind by D-030 — they patch `MonitorSource`, which is still where the "window's sound"
      path ends but is no longer where it starts, so the graph query in front of it was suddenly
      real and whether they passed depended on what the developer happened to have open. Three names
      on the session manager are now answered suite-wide with the ordinary healthy answer, and a
      test wanting a different one overrides them as several already did. Verified by making every
      real PipeWire call raise: the only tests that reach the graph are the two `@pipewire`-gated
      ones written to.

---

---

## Part 3l — The Tray, the Restart, the Clutter, and Work That Can Be Interrupted

Four things asked for together on 2026-09-06, planned in
[plans/tray-restart-clutter-and-interruptible-work.md](plans/tray-restart-clutter-and-interruptible-work.md)
and closed (10 / 10). Recorded as **D-039** to **D-045**. Three of them were open items already
carried above; the fourth is new capability. The items they close are left in place rather than
moved, so nothing is lost by reading this file top to bottom.

Every step that could be run *was* run, against the real desktop and real recordings rather than
fixtures, and five faults came out of that which no test would have found:

- **"Nothing recorded yet" stood over a pane full of prose.** The empty state ignored polished
  blocks and was not re-rendered when every segment was already covered by one. Reachable before
  this work and never looked at.
- **A `setAttr` used without importing it took the whole page down**, so nothing on it wired up.
- **The pass's progress bar hid itself** after its first window, because it lived inside the empty
  state — a fault present since D-021 — and its new controls went with it. Both moved out and made
  sticky, after finding them scrolled off-screen at 8000 px.
- **A held pass refused its own resumption**, because `is_busy` counted waiting as working.
- **A held pass reported itself 100 % complete**, because the tail flush reports the whole file's
  length whether the loop finished or was cut short.

- [x] **The tray icon's D-Bus export — done, and the icon appears.** `companion/raster.py` draws
      the instrument as ARGB32 with `numpy` alone (**D-042**), and `companion/tray.py` serves one
      `org.kde.StatusNotifierItem` plus its `com.canonical.dbusmenu` from a dispatch loop over
      `jeepney`'s primitives (**D-043**). Verified against the real desktop: registered with the
      live watcher, `GetAll` returning fifteen properties and a 22 x 22 icon, the picture animating
      between reads, an eleven-item menu — and on killing the server, the tooltip becoming "Loud
      Radish is not running", the menu collapsing to four items, and the picture becoming the fault
      one. No `PySide6`. Start it with `uv run --no-sync python -m app.companion.main` from `web/backend/`,
      or `--no-tray` to run the shortcuts without one.
- [x] **The last transcript after a restart — closed.** The newest session holding segments is
      reopened once at start-up, chosen by the timestamp in its filename rather than by mtime.
      `storage.reopen_last_session` turns it off. Verified against a real server both ways: on, the
      page hydrates 4 segments and the assistant answers with a `context_timestamp` of 16.5 rather
      than zero; off, `/api/transcript/revisions` says no session is open and the assistant gives
      D-031's honest refusal. **Running it found a second fault**: with a transcript restored the
      pane showed "Nothing recorded yet" over the prose, because the empty state ignored polished
      blocks and was not re-rendered when every segment was already covered by one. Both halves
      fixed. Recorded as **D-041**.
- [x] **The empty session databases — cleared, 970 files down to 291.** `scripts/prune_empty_sessions.py`
      reports by default and needs `--apply` to delete. Run against the real directory: 679 removed,
      47.1 MB reclaimed, `data/sessions` 103 MB → 58 MB, and the Recordings page now opens on a real
      talk instead of a wall of empty rows. **28 empty databases were kept**, every one of them
      owning a recording folder — failed transcriptions of real audio, whose empty database is the
      only thing naming the audio. Every candidate was re-checked straight from SQLite before
      deleting: 679 of 679 genuinely held zero segments.
- [x] **Pause, resume and cancel a capture — done (D-044).** `paused` is in the run-state
      vocabulary on both sides of the wire, with its own control in the header beside the primary
      one. A pause **removes time from the recording**: verified in the browser against a real
      server, nine seconds of wall clock across a three-second hold produced 3.01 s → 3.01 s →
      6.02 s of recorded audio, with the header clock frozen throughout the hold. `window` mode
      stops its video with the audio so the two cannot drift. Cancel keeps every artefact and runs
      no pass. 320 px and keyboard passes done.
- [x] **Pause, resume and cancel a transcription pass — done (D-045).** The checkpoint lives in
      the session's own transcript database and is written on every window, so an interruption the
      user did not choose is recoverable too. **Verified against a real 39-minute recording**: the
      pass was killed with `SIGKILL` at 1885 s, the process restarted, and
      `POST /api/sessions/{key}/transcribe/resume` picked it up from its checkpoint — unique
      increasing ids, non-decreasing timestamps, and a seam that reads continuously from 1885.2 s to
      1886.2 s with no gap and no overlap. Running it found a defect reading would not have: a
      resume snaps back to the window boundary at or before the point asked for, so trimming at the
      requested second and transcribing from the earlier one duplicated eighteen seconds. Fixed, and
      the regression test was confirmed failing against the old behaviour.
- [ ] **`services/session/manager.py` is back to 782 lines and the cap is 800.** Split from 1782,
      then grown again by pause, resume and cancel. It is inside the limit and has roughly twenty
      lines of headroom, which is not much: the next thing added to a session's lifecycle will need
      another seam rather than another method. The frame path, the background workers, the window
      capture, the passes and the source selection have already gone; what is left is genuinely the
      lifecycle and the state machine, so the next split is a real design decision rather than a
      move.
- [x] **`services/session/manager.py` had reached 1782 lines — split, and was 672.** Six files:
      `shapes.py` (the dataclasses and tuning constants, importing no sibling so nothing cycles),
      `frames.py`, `background.py`, `window_capture.py`, `passes.py`, `sources.py`. Every file in
      the package is now under 500. Mixins rather than collaborators, and **D-039** says why and
      what that costs. No behaviour changed — 1671 passed / 12 skipped, identical to before — but
      about thirty test patch sites had to follow the code, including the four autouse guards that
      keep the suite out of the developer's PipeWire graph; those were re-verified by making every
      real call raise and diffing the failure set, which came back identical at 24 each side.

---

## Part 4 — Still Open

Everything here is work somebody still owes. Entries that were *decisions* — deferrals with a
reason, not tasks nobody got to — moved to **Part 4c — Deferred by decision** below, because a list
that mixes the two reads as a debt pile and stops being read at all.


- [x] **Default ASR model and compute device.** Done, by measurement — `scripts/benchmark_asr.py`
      now exists and runs every combination of model, device and precision in its own process, after
      a warm pass, reporting speed alongside how far each transcript diverges from the others.

      What it found was not a tuning answer but a fault. **The shipped default crashes on this
      hardware**: `small` at `int8` with `device="auto"` resolves to the GPU on any AMD machine and
      aborts the *process* with "Memory access fault by GPU node-1" — no exception, nothing to
      catch, the server and any recording in progress gone. That combination is now refused before
      the load, with an override for hardware where it is not broken (D-053).

      The CPU figures are stable and repeatable: `base`/`int8` measured 37.2×, 37.9×, 38.9× and
      40.2× across four runs; `small`/`int8` 14.5–15.6×. This machine's own configuration is now
      `base` on the CPU — about 2.2× real time through the streaming pipeline, against roughly 1.3×
      on the GPU, and without the fault.

- [ ] **The GPU path on this machine no longer produces coherent output.** Discovered while doing
      the benchmark above, and left open because it needs someone who can bisect the ROCm stack.
      With each combination isolated in its own process and warmed first, `large-v3-turbo` at
      float16 returned **99 words for a 54-word clip**, `base` at float16 returned **none**, and the
      same combination that transcribed correctly through the running server minutes earlier
      returned zero words under the benchmark. It is not simply slow; it is wrong, and differently
      wrong each time. `docs/deployment.md` now marks its GPU column unverified.

- [x] **Deployment documentation.** Done. `docs/deployment.md` now describes installing and running
      it locally, what ends up on disk, and measured hardware expectations.
- [x] **Design tokens.** Done. `docs/design-system.md` documents every token group, the two
      contrast corrections, and the rule about compositing translucent backgrounds before measuring.
- [x] **Component map.** Done. `docs/component-map.md` describes the template-and-module tree, the
      three ownership rules, and every component and store.
- [x] **Automated accessibility tooling.** Done, and deliberately not axe. Every browser-driving
      option assumes a build step this project does not have, so the check is two test modules over
      what the server already renders: `tests/frontend/test_rendered_accessibility.py` asserts the
      structural rules (one `h1`, `lang`, accessible names on buttons, a label of some kind on every
      field, `alt` on images, the transcript's polite live region) using `html.parser` from the
      standard library, and `tests/frontend/test_token_contrast.py` computes WCAG ratios over the
      design tokens with no dependency at all.

      It found two real faults on its first run, which is the argument for it: the application page
      had **no `h1` at all** — every heading on it was an `h2` or lower — and it caught its own
      author's parser bug reporting two named buttons as unnamed. Both are fixed. This does not
      replace the manual keyboard, contrast-in-context and 320 px passes; it catches the regressions
      that can be stated as rules.



- [x] **A per-page control for the polish view.** Done. A **Show raw** toggle in the transcript
      toolbar, next to the revision switch it copies, appears only once a polished block exists.
      Turning it on renders the segments under the prose rather than instead of it, and it is not
      persisted: somebody who wants raw text permanently wants the pass off, and Settings → Context
      already does that. Verified in the browser against a session holding a real polished block —
      three segments with it off, six with it on, the prose untouched throughout.
- [ ] **Tuning `polish.min_retained_ratio`.** *The measurement it needs now exists.* `ContentCheck`
      always carried the ratio and its docstring said it was "logged, so a threshold can be tuned
      from real runs" — and the caller dropped it. Every discard now logs the ratio, the floor it
      was compared against, and a running count, so the number can be gathered from real use. The
      value itself still needs that use before it is changed.

      *Original note:* The 0.6 default is a starting point chosen without a
      real model behind it. It is a length check, not a meaning check, and the right value can only
      come from watching what a real model actually returns. Writing out spoken code references now
      shortens a rewrite legitimately ("guard dot py" is three words and `guard.py` is one), which
      pushes in the same direction as a summary would — another reason the floor needs real data.
- [x] **Whether the assistant should answer from polished text.** **Decided: no — it keeps reading
      the raw segments.** This was flagged for the user and they asked for the whole list to be
      worked through, so it is answered rather than left open.

      The argument for polished text is that it is cleaner input. The argument against is what the
      assistant is *for*: it answers with citations, and a citation is a claim that the speaker said
      something. The polish pass is checked by `preserves_content`, which compares **word counts** —
      it is a length check, not a meaning check, and its own threshold is still an untuned guess.
      Feeding the assistant a rewrite means it can quote, with a timestamp, words the speaker did
      not use; feeding it the raw segments means it sometimes quotes clumsy speech, which is what
      was actually said. The second failure is much better than the first.

      Two mechanical costs would also be paid for the worse of the two: citations are segment ids
      and a polished block has a different id space, and `offered_seconds` — which gates citation
      validation — would have to be derived from the `[MM:SS]` markers inside the prose rather than
      from segment starts. If the decision is ever reversed, that is where the work is: the
      `TranscriptSource` protocol in `services/context/assembler.py`, its verbatim block, and
      `resolve_citations`. **Reversing it should require a reason about accuracy, not tidiness.**

      The reader-facing half of the same question is now answered the other way: Show raw (below)
      lets a person see the segments under any polished stretch, which is where wanting the raw
      text actually bites.
- [ ] **Tuning the invented-speech thresholds against real audio.** *The measurement it needs now
      exists.* The filter logged its verdict but never the two numbers the thresholds are compared
      against, so there was nothing to tune from; `no_speech_prob` and `avg_logprob` are now in the
      debug record alongside the clip length, and suppressions are counted per code so a climbing
      total can be attributed rather than merely noticed.

      *Original note:* The defaults were calibrated
      against `tiny` on synthetic fixtures, which is not the same thing as a real room. The number
      to watch is `suppressed` in the status bar: climbing while someone is talking means the
      thresholds are too tight and real speech is being deleted, which is the one failure this
      filter can cause. `no_speech_certain` in particular was set at 0.85 because a measured
      hallucination scored 0.901 — a sample of one.
- [x] **Whether the Silero voice detector should replace the energy one by default.** Done, and it
      was not the question it looked like. **Silero was never running at all**: `silero_vad.onnx`
      existed on no machine, so `build_detector` fell back to the energy detector with a
      `logger.warning` nobody reads — while the status bar reported "silero". The configuration on
      this machine asked for it and got energy for months. Nothing needs downloading: the model is
      already inside `faster-whisper`, and it is now used when no path is configured. The fallback,
      when it does happen, is a banner rather than a log line.

      With both detectors actually runnable, the comparison could finally be made. Same clips, same
      sensitivity: on real speech Silero found **83%** of frames against energy's **62%**; on a tone
      fixture containing no speech it fired on **1%** against energy's **62%**. Better on both axes,
      so Silero is the default (D-052).

---


- [ ] **`tests/api/test_session_toggle.py::test_stopping_ignores_the_mode` fails roughly
      one full-suite run in three, with a SQLite error.** **It did not fail once while this plan was
      executed, and it was deliberately provoked.** Recorded as evidence rather than as a fix,
      because "I could not make it happen" is not "it cannot happen":

      - **18 full-suite runs** across the ten steps, including five consecutive runs at the end.
      - **40 consecutive runs** of `tests/api/test_session_toggle.py` in isolation.
      - **Three full `tests/api` + `tests/transcription` suites running concurrently**, to reproduce
        the load the original diagnosis blamed. No SQLite error appeared in any of the three.

      That last attempt did produce two failures, and they are worth naming so nobody repeats the
      experiment and misreads them: `test_audio_tap.py::test_a_tap_opens_and_closes_without_leaving_a_sink_behind`
      counts null sinks in the developer's live PipeWire graph, and three suites running at once
      each see the others' taps. That is an artifact of the provocation, not a fault — and the test
      is right to be strict, because a leaked sink is what made an afternoon of recordings silent
      (see the second lesson in `tests/conftest.py`).

      **Something changed.** The likeliest candidates are the transcription runner's terminal paths,
      which D-045 reworked, and `pytest-timeout`, which alters when a slow test gives up. Neither is
      a diagnosis. The entry stays open; the next person to see it should reproduce first and
      instrument the store's `close`, and if it stays quiet for another few months it can be closed
      as gone rather than as fixed.

---

## Part 4c — Deferred by decision

**Not open work.** Each of these was decided, with a reason, and none is waiting on anyone. They sat
under "Still Open" for months, which made the list longer than the actual debt and easier to ignore.

- **Desktop packaging.** Whether this stays a browser-plus-local-server application or is packaged
  into a Tauri/Electron/Qt shell. Undecided *deliberately*: the chosen contract keeps both open, so
  nothing is blocked either way, and the native settings window (D-051) removed the main reason to
  want a shell — the application can now be configured without a browser tab. Revisit when there is
  a concrete reason, not on a schedule.

- **Embeddings-based retrieval.** Keyword search over FTS5 is expected to suffice for single-talk
  sessions; revisit only if retrieval quality proves inadequate.

- **Speaker diarisation.** Out of scope for v1. The segment model reserves an optional `speaker`
  field, so adding it later is not a schema migration.

- **Hosted ASR backends.** Deferred to v2. Seam A accommodates them; none is implemented.

---

## Part 4b — Shortcuts, dictation, and the tray's own settings

- [x] **Global shortcuts are registered and fire.** Done. `register_all` had never been called by
      anything; it now runs when the companion starts. The route is one `.desktop` entry per action
      registered as a KGlobalAccel *service* — the portal refuses unsandboxed callers, and an
      in-process component only works while the companion lives (D-047). Verified by pressing
      `Meta+Alt+V` and watching a session start, and `Meta+Alt+X` and watching it stop.
- [x] **The shipped shortcut defaults collided with KDE's own.** Done. `Meta+Alt+L`, `Meta+Alt+R`
      and `Meta+Alt+S` were already taken by the keyboard layout switcher, Spectacle and the screen
      reader. They had never been checked against a real desktop. The defaults are now
      V/C/W/X/T/D, all verified free, and a conflict is reported rather than stolen.
- [x] **Dictation.** Done, and verified twice on this machine. Press `Meta+Alt+D`, speak, press it
      again: the words are transcribed, tidied, put on the clipboard and pasted into whatever window
      has focus (D-049). Over 25 s of real speech: 2.0 s to transcribe on the GPU, 0.9 s to tidy.
      In a silent room it says "nothing was said" and pastes nothing, which is the behaviour that
      matters most.
- [x] **A microphone picker in the tray menu.** Done (D-050). One level of nesting, the current
      input ticked and named in the parent row, disabled while recording. Verified over D-Bus on the
      real desktop: clicking a child switched the microphone and wrote it to the config file.
- [x] **Choosing a microphone did not survive a restart.** Done, and it took two goes. `PATCH
      /api/config` was fixed first (D-046) — but Settings → Audio posts to `/api/audio/device`,
      which was still writing to the discarded runtime layer, so the reported fault was untouched
      until that route was fixed too.
- [x] **A native settings window.** Done (D-051). Tk, no new dependency, its own process,
      launched from the tray's "Settings…" item. Three tabs: shortcuts with press-to-capture and
      conflict checking, the microphone list, and the dictation options. Verified by clicking the
      real menu item over D-Bus.

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
- [ ] **Copy a quote from polished prose and check the timestamp.** Verified in the browser
      against the real `selection.js` on a reconstruction of a real block — prose 95 s into a block
      starting at 0.0 now stamps `00:01:35` where it stamped `00:00:00`. What is owed is the same
      check on a live session, selecting with the mouse rather than a scripted Range, since the
      anchor node a hand-drawn selection produces is the one variable a script cannot reproduce.
- [ ] **A window recording where the audio starts *after* you press record.** The session now
      re-links playing streams on every status tick, because an application creates a playback node
      when media starts and the old code linked once at open — so pressing record and then pressing
      play captured nothing. Verified against the real graph in isolation; not yet through a whole
      session.
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
- [ ] **A window recording that has to resume (D-036).** The stall detector, the reopen, and the
      stitching are covered by tests against a real `ffmpeg` and a stubbed portal, and the pieces
      have been joined into a correct timeline under measurement. What has **not** been observed is
      the whole thing on a real desktop: close the captured window mid-talk, or drop the network
      under a video call, and confirm that the portal restores without a picker appearing, that the
      video that comes out plays continuously with a held frame across the gap, and that the sound
      after the gap is still in step with the transcript. This is the one behaviour in the repair
      whose failure mode is silent, since a capture that does not resume looks exactly like a
      capture that ended.
- [ ] **The export estimator against your own content and your own CPU (D-037).** Both constants are
      local: content decides the size and the processor decides the time, and the ones shipped were
      fitted to a slide-heavy seminar on a 32-core machine. Run
      `uv run scripts/calibrate_export_estimate.py data/recordings/<key>/video-with-audio.webm`
      against a recording of a *different* kind — one that cuts between cameras rather than sitting
      on a slide — and see whether the measurement still lands inside the range.
- [ ] **The tray icon on a desktop that is not KDE.** The `StatusNotifierItem` export is verified
      against the real `org.kde.StatusNotifierWatcher` on this machine, end to end: registered,
      fifteen properties read back, the picture animating, an eleven-item menu, and the whole thing
      following the server as it was killed. What has **not** been tried is GNOME (which needs an
      extension for SNI at all), or a panel that reads `IconName` and ignores `IconPixmap`. The
      failure mode there is no icon rather than a wrong one, and the companion says so once and
      carries on.
- [ ] **Pausing a `window` recording on a real portal stream.** The video is stopped with the audio
      and the pieces rejoin on the audio clock, which is covered against a stubbed portal and a real
      `ffmpeg`. What is owed is the whole thing on a real desktop: pause a window capture, wait,
      resume, and confirm both that **the portal picker does not appear** — the open question this
      plan flagged for the user and could not answer headlessly — and that the joined video plays
      continuously with the sound still in step.
- [ ] **A paused capture over a long talk.** Verified at nine seconds of wall clock across a
      three-second hold. Nobody has yet held a capture for twenty minutes mid-seminar and read the
      result back, which is the case the feature exists for.
- [ ] **A genuine 90-minute soak** — the accelerated soak in `tests/transcription/test_soak.py`
      drives ninety minutes of transcript through the engine in seconds and holds bounded memory,
      contiguous segment ids, and a clock that has not drifted. It is not the same as ninety
      wall-clock minutes with a real model and a real device, which remains yours to run.

## Part 6 — After the Loud Radish rebrand (D-038)

- [x] **The rebrand itself.** The name lives in `web/backend/app/branding.py`; every user-visible
      string, every machine identifier, the documentation, the three agent pointer sets, and the
      mark are done. `tests/utils/test_no_legacy_brand.py` fails if a pre-rename name reappears
      anywhere it is not migrating from.
- [ ] **The migrations against a real pre-rename install.** All four are covered by tests, and the
      config-file adoption was watched happening on this machine. What has **not** been exercised
      is the keyring move against a real Secret Service, the pre-rename systemd unit being removed
      by a real `systemctl`, or the shortcut component being re-registered with a real KGlobalAccel
      — none of the three was installed here to migrate. If you have another machine with the old
      build on it, that is the one to try.

## Discovered work

- [x] **A dictation came back with "..." where a window boundary fell mid-sentence.** Done
      (D-061). The recording is now cut where the speaker paused — `vad/pauses.py` finds the
      silences, `recording/chunks.py` ends every chunk inside one — and each chunk goes to the
      model whole and is tidied on its own. The cap is thirty minutes, up from five, and reaching
      it delivers what was said instead of silently dropping everything after it.

- [x] **Recorded mode's batch pass still cuts on a clock.** Done (D-062). `transcribe_file` now
      plans the whole file into pause-bounded chunks with the configured detector, hands each to
      the model whole, and checkpoints before every chunk; both sides of a resume plan the same
      boundaries, so the resume point is a real one. `batch_overlap_s` is retired, and retiring it
      found that an unknown key made the loader ignore the user's entire config file — retired
      keys are now dropped with a log line instead.

- [ ] **A dictation's transcribe and tidy could overlap.** The chunks are transcribed and then
      tidied in sequence. The speech model and the language model are different resources, so
      tidying chunk one while chunk two transcribes would roughly halve the wait on a long one.

- [x] **The native settings window saved nothing but the microphone.** Done (D-060). Every shortcut
      and every dictation option went to the layer that is discarded on exit, so a rebound key
      reverted to its default on the next start and the user pressed it to no effect. Whole
      families persist now — `audio.`, `shortcuts.`, `dictation.` — rather than a list of paths a
      new setting can fall off.

- [x] **A dictation was invisible in the tray, and every keypress showed a loading window.**
      Done (D-059). The icon now shows an open microphone while dictating and the decoding sweep
      afterwards; the menu says which key ends it. The `.desktop` entries set
      `StartupNotify=false`, so the desktop stops waiting for a window that never comes.

- [x] **The export window opened after every session, including ones with nothing to export.**
      Done (D-058). A live transcription ended with a dialog offering five video qualities over
      "This recording has no video". `session.stopped` now says whether the recording folder holds
      any media, and the window opens only then.

- [x] **The shortcut editor said "Meta" and meant the Windows key.** Done (D-057). Every label now
      says `Win`; the stored and registered form stays `Meta`, because that is what KDE's own
      settings show. The web panel, whose input *is* the stored value, explains it instead.

- [x] **A terminal command to start it in the background.** Done (D-056). `radish` starts the
      server and the tray icon detached and returns in about a second; `radish stop`,
      `radish status`, `radish logs`, `radish dictate`. Install with
      `ln -sf "$PWD/scripts/radish" ~/.local/bin/radish`.

- [x] **Two settings that configured nothing.** Done (D-055). `.env.example` listed eleven
      environment variables no Python reads, and `storage.retention_days` drew a control saying
      "Delete sessions after N days" that deleted nothing. The variables are gone from the file and
      the control is replaced by a pointer to `scripts/prune_empty_sessions.py` — deleting
      recordings on a timer is not something this application should do quietly.

- [ ] **`uv run` outside `app.py` silently breaks GPU transcription.** *Partly mitigated:* the
      documented companion command now passes `--no-sync`, because starting the tray icon was
      swapping the wheel out from under the running server. `app.py` calls
      `acceleration.repair_kept_wheel()` on launch, which puts the ROCm CTranslate2 build back after
      `uv` has replaced it with the PyPI CPU/CUDA one — so starting the application normally
      self-heals. **Any other `uv run` does not.** A bare `uv run python some_script.py` re-syncs,
      swaps the wheel, and the next model load fails with "the installed CTranslate2 cannot use it";
      running the same script again through `uv run` re-breaks what a manual
      `uv pip install --reinstall` just fixed. Encountered while testing dictation from a script.
      Either the repair belongs somewhere every entry point passes through, or the scripts under
      `scripts/` need the same call `app.py` makes.

- [x] **`tests/assistant/test_llm_live.py` can hang the suite.** Done. The tests are now opt-in by
      flag (`--run-live-llm`) rather than by reachability, which is what "skips when nothing
      answers" was mistaken for. `pytest-timeout` gives the whole suite a 120-second per-test
      deadline, the two draining tests get 300 seconds when deliberately asked for, and the server
      probe moved into a fixture so collection no longer makes an HTTP request on every run.
      `uv run pytest` completes unattended in about 95 seconds with no `--ignore`.
- [x] **`tests/transcription/test_window_audio.py::test_the_container_is_raw` is environment-
      dependent.** Done, and it was worse than reported. The amplitude assertion is gone from it and
      from `test_a_capture_produces_finite_audio_at_the_canonical_rate`; both now assert that the
      samples are finite, which is the fault the flag was added for. The blocking
      `stdout.read(64000)` — which would never return on a machine whose default monitor is silent,
      and so would never reach the `finally` that kills `pw-record` — is now a bounded read with a
      ten-second deadline.

      A **third** test in the same file was failing on every full-module run and had not been
      reported: `test_a_real_tap_reports_its_links_without_listening_to_them` threw away the count
      `link_all` returns and asserted `live_links > 0` regardless, so it failed whenever `pw-dump`
      listed a node that could not actually be linked. It now skips on that, which is a fact about
      the machine, and still asserts the counter whenever there is something to count.
