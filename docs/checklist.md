# Project Checklist

*Last updated: 2026-08-29 (five faults reported against one recording)*

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

- [x] **A finished transcript is not reloadable until the sessions page is opened.** Closed by the
      remedy this item proposed: the last session's store stays open for reading until the next one
      starts. See Part 3h and **D-031** — the same line was refusing every question the assistant
      was asked after a stop, which is how it came to be fixed.

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

- [ ] **`services/session/manager.py` is 1334 lines, against a cap of 800.** It was already 1265
      before this repair and this added 73. The new logic went into the audio modules wherever it
      could, but the decision about which source a session opens belongs to the manager and had
      nowhere else to go. It wants splitting — the source-selection and capture-wiring halves are
      the obvious seam — and that is a refactor, not a repair, so it was not smuggled into one.

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

- [ ] **The developer's `data/sessions` still holds the suite's leavings.** The cause is fixed and
      the files are harmless, but roughly 690 empty databases from before the fix are still in the
      user's own directory and still at the top of the recordings page. Deleting another person's
      data is their call, not ours; the list can be pruned by removing session files whose database
      holds no segments.
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
- [ ] **A restart still hides the last transcript from the live page.** The finished store is held
      only until the next session starts, and not across a process restart — so after restarting
      the application the last talk is reachable through Recordings but not on the main page. That
      is the documented bound of D-031 rather than a regression, and closing it would mean deciding
      what "the current session" means to a process that has just started.

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
- [ ] **A capture that stops producing frames is noticed.** `_watch` polls `process.poll()` and
      asks one question — has it exited — which a stalled pipeline answers "no" to for as long as
      it lasts. Step 2 / 9.
- [ ] **A capture that ends mid-session resumes.** The portal's restore token is persisted on every
      start for exactly this reuse and has never been used for it within a session. Step 3 / 9.
- [ ] **The user's conversation leaves the shared export.** Step 4 / 9.
- [ ] **A re-encode can be measured before it is committed to.** Step 5 / 9.
- [ ] **Exporting is a staged job with real progress.** Step 6 / 9.
- [ ] **One post-recording window: preview, options, projected sizes, stages.** Steps 7–8 / 9.

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

- [ ] **Four tests in `tests/transcription/test_window_audio.py` fail on a quiet machine.**
      `test_window_mode_opens_the_machines_output_not_a_microphone` and three beside it reach the
      real PipeWire graph and raise `MonitorUnavailable` when nothing is playing. Pre-existing and
      unrelated to this plan — confirmed by running them against a clean tree — but they make
      `uv run pytest` red for anyone who is not playing audio, which is most runs. They need either
      a stubbed graph or a skip guard.

---

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
- [ ] **A genuine 90-minute soak** — the accelerated soak in `tests/transcription/test_soak.py`
      drives ninety minutes of transcript through the engine in seconds and holds bounded memory,
      contiguous segment ids, and a clock that has not drifted. It is not the same as ninety
      wall-clock minutes with a real model and a real device, which remains yours to run.
