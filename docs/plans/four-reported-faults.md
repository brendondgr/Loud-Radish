# Four Reported Faults

**Status:** 🚧 In progress — Phase 0 of 5.
**Branch:** `main` (Mode B — no worktree).

---

## 1. Introduction

Four faults reported together, unrelated in mechanism. The assistant cannot answer a question at all
because the endpoint rejects the request outright; copied transcript quotes carry a timestamp of
zero; window audio capture now refuses to start with an error this repository added yesterday; and
the streaming engine drops spoken words when a guard forces a commit. Three are defects in shipped
behaviour, one is a regression, and the fourth is a correctness requirement stated absolutely by the
user: **no spoken content may be dropped under any circumstances**.

They are addressed in the order of what they cost. The assistant fault blocks a whole feature and is
a two-line change proven against the live endpoint. The window-audio regression blocks recording and
is this repository's own doing. The forced-commit fault is the largest piece of work, because
"lossless" is a property that has to be *demonstrated* rather than asserted — the phase builds the
harness that can catch it before it changes the code the harness is meant to judge. The copy
timestamp is last because it is cosmetic in comparison, and because it needs browser verification
rather than a unit test.

---

## 2. Gaps & Unanswered Questions

- **Why the endpoint rejects the request.** *Established, not assumed.* `context/assembler.py` emits
  `system(SYSTEM_PROMPT)` and then a second `system(blocks)` whenever any context block survives the
  budget. Reproduced against the user's own server at `localhost:9090`: two system messages return
  `400 System message must be at the beginning`, and the same content merged into one returns `200`.
  *Assumption for the fix:* one system message is acceptable everywhere, since the Anthropic backend
  already concatenates system parts (`llm/anthropic.py`) and the two other callers
  (`context/worker.py`, `polish/worker.py`) send exactly one.

- **Why `carries_audio` refuses a working capture.** The probe treats a run of bit-exact zeros as
  proof the graph is not delivering. Measured with a tone actually playing, it passes at every window
  down to 0.1 s, so it is not a timing fault. **The premise was simply wrong.** The commit that
  introduced it claimed "real audio, even a silent room, never produces a run of samples whose every
  byte is zero" — true of a *microphone*, false of an *application*, which writes literal zeros
  whenever it is between sounds, paused with the stream still open, or in a video's silent lead-in.
  *Assumption:* the user pressed record during such a moment. The fix does not tune the threshold; it
  replaces a signal test with a graph test, which cannot confuse "quiet" with "not connected".

- **Two further defects found in the same path while measuring.** `_open_application_tap` takes a
  `match` parameter, documents it as narrowing to the window's own streams, and **never uses it** —
  both audio choices link everything. And `link_all` is called exactly once, at open, although
  `tap.py`'s own module docstring says the set must be watched because "an application creates and
  destroys playback nodes as the user opens tabs and starts media". So a video started *after*
  recording begins is never captured. Either is independently sufficient to produce "window audio
  records nothing", and both are in scope for Phase 2.

- **Where forced commits lose words.** *Not established, and deliberately not guessed at.* Three
  paths in `engine._forced_commit` discard audio or words: `maximum-buffer` calls
  `_buffer.hard_trim` twice and its own comment says "Discard it rather than resubmitting audio that
  already failed"; `silence-gate` discards the whole buffer on the VAD's word; and `commit-timeout`
  fires **before inference**, so it commits a hypothesis from the previous pass and then trims the
  buffer against it. The last is what the user's log shows. Phase 3 therefore *starts* by building a
  losslessness harness that drives known speech through the engine with each guard forced, and only
  then changes code — the same discipline that caught a leaky-queue regression under D-027. A fix
  aimed at a guessed cause would be indistinguishable from one that works.

- **Whether "lossless" can be absolute.** The user's requirement is unconditional. There is a genuine
  tension: `maximum-buffer` exists because exceeding the model's window produces *silently truncated*
  output, so retaining the audio there risks losing more than discarding it. *Assumption:* lossless
  means **no audio is discarded without having been transcribed at least once**, rather than "the
  buffer is never trimmed" — audio that has been through inference and produced committed words has
  not been lost. Where the current code discards untranscribed audio, it must instead transcribe it
  first. This reading is stated here so it can be corrected before Phase 3 acts on it.

- **Why copied timestamps are zero.** *Not established.* `selection._startOf` walks up from
  `selection.anchorNode` to the first element carrying `data-start`. Four element kinds carry one,
  and one of them — the inline `.polished__at` span built by `transcript-pane.withTimes` — defaults
  to **`parseTimestamp(...) ?? 0`**, so a time string it cannot parse yields a literal zero on an
  element nested *inside* the passage. That is the leading candidate and it is not confirmed. There
  is no Node toolchain in this repository (D-011), so Phase 4 confirms it in the browser against a
  real session before changing anything.

---

## 3. Hierarchical Step-by-Step Instructions

### Phase 0: The assistant can answer a question again

- **Locations:** `web/backend/app/services/context/assembler.py` (`assemble`, around the
  `messages: list[LlmMessage] = [system(...)]` construction);
  `tests/assistant/` — new assertions alongside the existing context tests.
- **Rationale:** This is the shortest path from "a whole feature returns HTTP 400" to "it works", and
  it is already proven at both ends: the failing shape and the working shape were both sent to the
  user's own server. The context blocks and the standing instructions must reach the model as **one**
  system message, ordered instructions-first so the prompt still reads the way it was written. Doing
  this first also means the rest of the plan can be exercised through a working assistant.
- **Validation:** `uv run pytest tests/assistant -q`; a unit test asserting at most one `system`
  message and that it is at index 0, for both the with-context and without-context shapes; one live
  request against `localhost:9090` through the running application.
- **Docs:** none yet — the decision record is written once in Phase 5.
- **Action:** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Four Reported Faults (1 / 5) Complete: the assistant sends one system message, and the endpoint accepts it`

### Phase 1: A losslessness harness, before anything is changed

- **Locations:** new `tests/transcription/test_commit_losslessness.py`;
  exercises `web/backend/app/services/streaming/engine.py` (`_iterate`, `_forced_commit`,
  `_trim_after_commit`) and `web/backend/app/services/streaming/guards.py` through the existing
  synthetic source in `app/services/audio/sources/synthetic.py`.
- **Rationale:** The requirement is absolute, so the test has to be the judge rather than the
  implementation. Drive a known word sequence through the engine with a scripted transcription
  backend, force each guard — `commit-timeout`, `maximum-buffer`, `silence-gate` — and assert that
  the multiset of words in the committed transcript equals the multiset spoken. **Written and shown
  to fail first.** A harness authored after the fix proves only that the fix is self-consistent, and
  this repository has already been burned once by a change that passed every test while dropping 150
  frames to nine.
- **Validation:** `uv run pytest tests/transcription/test_commit_losslessness.py -q` — expected to
  **fail**, and the failure output recorded in the commit message as the specification of the fault.
- **Docs:** none yet.
- **Action:** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Four Reported Faults (2 / 5) Complete: a failing harness that names exactly which words the forced-commit path drops`

### Phase 2: Window audio records the window again

- **Locations:** `web/backend/app/services/audio/sources/monitor.py` (`carries_audio` — replaced);
  `web/backend/app/services/audio/tap.py` (new graph-inspection helper reporting the live links on a
  tap's playback ports; `ApplicationTap.link_all` already exists);
  `web/backend/app/services/session/manager.py` (`_open_application_tap`, `_verify_tap`, and the
  status loop that must re-link);
  `tests/transcription/test_window_audio.py`, `tests/transcription/test_audio_tap.py`.
- **Rationale:** Three faults, one path. **First**, the probe must stop asking a question that
  silence answers wrongly: what it should verify is that the tap sink has at least one live link on
  its playback ports, which is a fact about the graph and is true whether or not anyone is talking.
  That check is deterministic and cannot refuse a legitimate recording. **Second**, `link_all` must
  run again periodically while recording, because a browser creates a playback node per media element
  and the one the user presses play on may not exist when they press record — this is the fault
  `tap.py`'s own docstring warns about and the session never implemented. **Third**, `match=True`
  must actually narrow using the existing `rank`/`score` functions, or the parameter and its
  documentation must go; a setting that silently does nothing is worse than one that is absent. A
  sustained-silence condition may still be reported, but only as a **warning on a running session**,
  never as a refusal to start.
- **Validation:** `uv run pytest tests/transcription -q`; a real-graph test that a tap with no links
  is refused and a tap with links is accepted **while nothing is playing**; a manual capture with a
  video started *after* the recording begins, measured through the sidecar's RMS rather than by ear.
- **Docs:** `docs/checklist.md` — the manual capture item.
- **Action:** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Four Reported Faults (3 / 5) Complete: the tap is verified against the graph rather than against silence, and keeps linking while it records`

### Phase 3: The forced-commit path is made lossless

- **Locations:** `web/backend/app/services/streaming/engine.py` (`_iterate`, `_forced_commit`,
  `_trim_after_commit`); `web/backend/app/services/streaming/guards.py`
  (`before_inference`); possibly `web/backend/app/services/streaming/buffer.py` (`hard_trim`,
  `trim_to`).
- **Rationale:** With the harness from Phase 1 naming the exact words lost, the change is aimed
  rather than guessed. The expected shape, subject to what the harness actually shows: a guard that
  forces a commit must **transcribe before it discards** rather than committing a stale hypothesis
  and trimming against it, and `maximum-buffer` must run a final pass over the audio it is about to
  drop instead of dropping it unread. The guard ordering in `before_inference` is what makes
  `commit-timeout` skip inference, and that is the first thing to test changing.
- **Validation:** the Phase 1 harness now **passes**; `uv run pytest tests/transcription -q` for no
  regression in latency and commit behaviour; the existing accelerated soak in
  `tests/transcription/test_soak.py` still holds bounded memory and a non-drifting clock; the
  forced-commit rate reported by `session/metrics.py` recorded before and after.
- **Docs:** none yet.
- **Action:** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Four Reported Faults (4 / 5) Complete: a forced commit transcribes what it is about to discard, and the harness proves nothing is lost`

### Phase 4: A copied quote carries the time it was said

- **Locations:** `web/frontend/static/js/components/selection.js` (`_startOf`, `_copy`);
  `web/frontend/static/js/components/transcript-pane.js` (`withTimes`, `_buildSegment`,
  `_buildBlock`).
- **Rationale:** Confirm in the browser first — select a passage two minutes into a real session,
  copy it, and read what lands on the clipboard — because the leading candidate (`?? 0` on an inline
  time span nested inside the passage) is a hypothesis and there are three other elements carrying
  `data-start`. Once located, the rule to restore is the one `_startOf`'s own comment already states:
  the time comes from the **enclosing segment or block**, which is the unit the transcript stores
  times for. A nested decoration must not be allowed to answer for it, and a value that cannot be
  parsed must be absent rather than zero — a wrong time shown confidently is worse than no time, the
  same judgement recorded under D-026.
- **Validation:** browser QA against a real session via the preview tools — copy from a segment, from
  a polished block, and from directly on an inline time, checking the clipboard each time; there is
  no Node toolchain (D-011), so this is verified by observation and the observation is recorded.
- **Docs:** `docs/checklist.md`.
- **Action:** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Four Reported Faults (5 / 5) Complete: a copied quote carries the moment it was said`

### Phase 5: The decisions, written down

- **Locations:** `docs/documentation.md` (D-029), `docs/checklist.md`, this plan's status,
  `docs/plans/README.md`.
- **Rationale:** Three of these four faults share a shape worth recording: a check or a parameter
  that *looked* correct and was never exercised against the thing it claimed to govern — an unused
  `match` flag, a probe whose premise held for microphones and not for applications, a guard that
  commits without inferring. That is the same lesson as D-026 and D-028 and it is now cheap enough to
  state once and point at.
- **Validation:** full `uv run pytest -q`; `uv run ruff check .` and `uv run ruff format --check .`;
  `git log --stat` inspected for accidental truncation of `documentation.md`, which has happened once
  under a scripted edit.
- **Action:** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Four Reported Faults (5 / 5) Complete: D-029 records the shape the four faults shared`

---

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| One system message | Context blocks and standing instructions merged into a single `system` message at index 0 | `web/backend/app/services/context/assembler.py` |
| Message-shape tests | At most one system message, at index 0, with and without context blocks | `tests/assistant/test_context_assembly.py` |
| Losslessness harness | Known speech through the engine with each guard forced; asserts the committed word multiset equals the spoken one | `tests/transcription/test_commit_losslessness.py` |
| Lossless forced commit | A guard transcribes before it discards, rather than committing a stale hypothesis | `web/backend/app/services/streaming/engine.py`, `.../guards.py` |
| Graph-based tap check | Verifies the tap has live links rather than that it is currently making a sound | `web/backend/app/services/audio/tap.py`, `.../audio/sources/monitor.py` |
| Continuous re-linking | New playback nodes joined to the tap while the recording runs | `web/backend/app/services/session/manager.py` |
| Window-matched tap | `match=True` narrows via the existing `rank`/`score`, or the parameter goes | `web/backend/app/services/session/manager.py` |
| Tap tests | A linkless tap refused and a linked-but-silent tap accepted, against the real graph | `tests/transcription/test_audio_tap.py`, `.../test_window_audio.py` |
| Enclosing-time copy | The copied timestamp comes from the enclosing segment or block, never a nested decoration, and is absent rather than zero when unknown | `web/frontend/static/js/components/selection.js`, `.../transcript-pane.js` |
| D-029 | The shape these four faults shared, recorded once | `docs/documentation.md` |
| Outstanding manual passes | The window capture and clipboard observations owed on real hardware | `docs/checklist.md` |
