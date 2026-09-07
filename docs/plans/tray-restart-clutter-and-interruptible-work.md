# The Tray Icon, the Transcript After a Restart, the Clutter, and Work That Can Be Interrupted

*Written 2026-09-06. Status: **In progress (1 / 10 steps)**. Branch: `interruptible-work`.*

> **Rebased onto the Loud Radish rebrand.** This plan was written against `main` at `97de92e` and the
> rebrand (D-038) landed while it was being written. It has been re-based rather than re-planned:
> decision numbers moved up to start at **D-039**, `utils/transcriber_ctl.py` is now
> `utils/loud_radish_ctl.py`, and every user-visible name a new module needs comes from
> `web/backend/app/branding.py` rather than a literal — `tests/utils/test_no_legacy_brand.py` fails
> the build otherwise, and it is right to. The measurements in §1 were taken before that merge and
> were re-checked after it: `manager.py` is still 1782 lines, and the session directory has grown
> from 968 databases to 970, so Step 2 re-measures rather than trusting the figure below.

Four things asked for together. Three are bounded repairs of gaps this project has already written
down and left open. The fourth — pausing, resuming and cancelling a recording or a transcription —
is new capability, and it is most of this plan.

---

## 1. Introduction

The four requests, in the words they were made in:

1. **The tray icon.** Everything behind it is built and tested; the picture does not appear.
2. **Restart the application and the last transcript disappears** from the main page.
3. **~690 files of clutter** at the top of the Recordings page. Remove them.
4. **A long transcription or a live recording should be pausable, resumable and cancellable.**

Items 1–3 are already recorded as open in [../checklist.md](../checklist.md) and each has a known
cause. Item 4 is genuinely new, and it changes the meaning of the session clock, so it is planned in
five steps rather than one and lands behind the other three.

The state of each was measured before this plan was written, not assumed:

| Measured | Value |
|---|---|
| `services/session/manager.py` | **1782 lines**, against a cap of 800 — every one of the four items edits it |
| Session databases in `data/sessions/` | **968 files, 69.6 MB**; **706 hold zero segments**, 262 hold a transcript |
| Empty databases that nevertheless own a recording folder | **28** — these are *failed transcriptions of real audio*, not test leavings |
| Recording folders in `data/recordings/` | **160 folders, 1.1 GB** |
| SVG rasteriser among the project's dependencies | **none** — no `cairosvg`, no `Pillow`, no `PyGObject`, no Qt |
| `numpy`, already a dependency | present, and enough to rasterise a 22 px instrument |
| `org.kde.StatusNotifierWatcher` on this machine | **running**, with `IsStatusNotifierHostRegistered = true` and another item already registered |
| `jeepney` marshalling an SNI `a(iiay)` pixmap | **works** — a 22 × 22 ARGB32 buffer serialised into a `Properties.Get` reply and parsed back **byte-identical** |
| Where a transcription pass can already be interrupted | `batch.py:170` — `should_stop()` is consulted **between windows** |

Two of those measurements decide the shape of the work before any code is written.

**The 28 empty databases with recordings beside them are the reason a prune cannot be "delete every
database with no segments."** That rule would destroy the only remaining record of 28 real
recordings whose transcription failed — precisely the recordings a user would most want back. The
criterion has to be *no segments **and** no media*.

**`should_stop()` already exists and already returns partial work.** Cancelling a transcription is
therefore nearly free; *resuming* one is the part that needs building, and what it needs is a
window offset, a segment id to continue from, and somewhere to write those down that survives the
process.

**And the tray's long-standing open question is now answered.** The checklist has carried "whether
`jeepney` can export SNI pixmaps at all" as an unknown since Plan 5, with `PySide6` named as the
fallback. It was measured rather than argued, before this plan was written: `jeepney` 0.9.0
serialises a 22 × 22 ARGB32 buffer into a `Properties.Get` reply of signature `a(iiay)` and parses
it back byte-identical, it constructs `NewIcon` signals and `GetAll` replies, and its blocking
connection exposes `receive` / `send` / `filter` alongside `new_method_return` and `new_error` —
which is every primitive a served D-Bus object needs. This machine is also already running an
`org.kde.StatusNotifierWatcher` with a host registered, so there is something to register with.
**The GUI-toolkit fallback is very unlikely to be needed**, and Step 5 is scoped accordingly.

The approach, in order:

- **Make room first** (Step 1). Split `manager.py` along the seams the checklist already named. A
  pure move, no behaviour change, so the four features that follow land in files that are within
  the cap rather than pushing a 1782-line file toward 2000.
- **Clear the three known gaps** (Steps 2–5). The prune, the restart, and the two tray steps. Each
  is independent of the others and independently committable.
- **Then make work interruptible** (Steps 6–9). The vocabulary and the control first, then capture,
  then the window mode's video, then the transcription pass.
- **Write it down** (Step 10).

---

## 2. Gaps & Unanswered Questions

### Resolved by assumption — proceeding

**What a pause does to time.** This is the load-bearing decision of Steps 6–9 and everything else
follows from it. **A pause removes time from the recording entirely.** Frames are dropped rather
than buffered, so the WAV stops growing, the engine's `session_seconds` stops advancing, and the
header clock freezes. A ten-minute talk paused for five minutes produces ten minutes of audio and a
transcript whose last timestamp is `10:00`.

The alternative — recording the pause as silence — was rejected on two grounds. It makes the
transcript claim time in which nothing was said, and it makes "pause" cost exactly as much disk and
exactly as much transcription as not pausing, which is not what anyone means by the word. The
consequence to accept is that transcript timestamps no longer correspond to wall-clock time of day
once a session has been paused. That is the right trade: every consumer of those timestamps — the
polish pass, the assistant's citations, the exported web application's video sync — is
*recording*-relative, and none is clock-relative.

**What cancel does with what was captured.** **Cancel keeps everything and transcribes nothing.**
It stops capture, skips the post-capture pass, marks the session cancelled in its metadata, and
leaves the audio, the video and any committed segments exactly where they are. Deleting is a
separate, explicit act from the Recordings page. A control that discards a recording is one that
will eventually discard the wrong one, and this application's whole disposition — D-027, D-036, the
delete-only-when-verifiably-contained rule in `mux.py` — is that it does not destroy evidence to
tidy up.

**Which session is reopened at startup.** The newest session database **that holds at least one
segment**, chosen by the timestamp in its filename stem rather than by `st_mtime`. Mtime is wrong
here: an export or a late polish write touches a file long after its talk ended, so the newest by
mtime is not the newest talk. Zero-segment databases are skipped because reopening one restores an
empty page, which is indistinguishable from the bug being fixed.

**Whether reopening at startup is optional.** Yes — `storage.reopen_last_session`, defaulting to
**on**. It costs one SQLite connection and it is what the user asked for, but a setting exists
because "the last talk is on screen when I start the application" is a preference and not everyone
shares it.

**The prune's criterion.** A session database is a leaving if it holds **zero segments** *and* its
matching recording folder either does not exist or contains no media. The script defaults to
reporting only; `--apply` is required to delete; and it never touches `data/recordings/`. Orphaned
recording folders are a different problem and are reported, not removed.

**Where a transcription pass writes its checkpoint.** A `transcription_passes` table in the
session's own transcript database, created through `TranscriptStore._migrate` so the 25 known
pre-migration databases are unaffected. Not a file in the recording folder: D-037's report asked
for *fewer* files there, and a checkpoint that travels with the transcript it is a checkpoint of
cannot be separated from it by a move or a partial copy.

This supersedes part of D-021's reasoning, which says the job is "deliberately not persisted"
because re-running is cheap. Re-running a forty-minute recording costs twenty-seven minutes of CPU;
that argument was made about crash recovery and does not survive contact with a user who pressed
pause on purpose.

**How the tray icon is rasterised.** Directly from the Aperture's own geometry into an ARGB32
buffer with `numpy`, at 4× and box-downsampled for antialiasing — **not** by rendering the SVG.
`aperture.py` already returns the bar amplitudes alongside the SVG text for exactly this reason. No
SVG rasteriser can be added without either a system library (`cairosvg` needs cairo) or a GUI
toolkit, and both contradict D-023's promise that `uv sync` is the whole install.

**Where pause lives in the interface.** A **second control** beside the primary one, not a new
meaning for the primary one. The primary control already serves six states by asking
`stores/mode.js` what pressing it means; making it also mean "pause" would give one button under
the user's finger two destructive-adjacent meanings that differ only by current state. Pause is a
separate button, shown only in `recording` and `paused`.

### Human intervention is needed to answer these

**If the served-object loop defeats `jeepney`, may `PySide6` be added as a dependency?** The pixmap
half of this question is now answered — see the measurement above — so what remains is only whether
a hand-written dispatch loop can serve an SNI host's property reads reliably enough. That is much
smaller, and the expected answer is yes. But if it is not, the fallback is not free: PySide6 is a
large binary dependency, and D-023 promises that one `uv sync` installs everything with no optional
groups, so adding it makes every install carry a GUI toolkit for one 22-pixel picture. **Human
intervention is needed to answer this question** *if it arises*. Step 5 stops at the decision point
and reports what it found rather than adding the dependency on its own authority. A cheaper third
option to weigh first: a **file-backed** SNI, with `IconName` pointing at a PNG this process
rewrites per frame — uglier, but it needs no property serving at all.

**Should pause be offered in `window` mode at all, given the portal picker?** Pausing a window
capture means stopping the video pipeline and reopening it on resume. D-036 records that the
compositor's answer to a restore token it cannot honour is to **put its picker on screen** — over
the talk being recorded. For an automatic resume after a stall that was judged unacceptable, and
the resume was made silent-or-not-at-all. A pause is different: the user asked for it and is
looking at the screen, so a picker is an annoyance rather than an ambush. Step 8 proceeds on that
reading, and the honest position is that **whether the picker actually appears cannot be known
without a real desktop** — it is listed in Verification Debt, not asserted here. If it appears every
time, the alternative is to leave the video running through a pause and accept that paused sessions
lose A/V sync, which is worse; or to withdraw pause from `window` mode. **Human intervention is
needed to choose between those two if the picker proves unavoidable.**

### Noted, not resolved here

- **`tests/api/test_session_toggle.py::test_stopping_ignores_the_mode` fails about one full-suite
  run in three.** Step 9 changes `TranscriptionRunner`'s stop path, which is the prime suspect. The
  flake is **not** a target of this plan and must not be "fixed" by guessing while nearby code is
  being edited — the checklist is explicit that a hunch is how the original closed-database fault
  was introduced. If Step 9's checkpointing happens to remove it, say so with evidence; if it
  persists, leave it.
- **`segments_in_range` still spans passes** (Part 3g). Untouched here.

---

## 3. Hierarchical Step-by-Step Instructions

### Step 1 — Split the session manager, changing nothing it does ✅ **Done**

- **Locations.** `web/backend/app/services/session/manager.py` (1782 lines) splits into:
  - `services/session/sources.py` — audio source and tap selection (currently manager lines
    ~1084–1300): `_open_source`, `_open_sink`, the application-tap wiring, the loopback checks.
  - `services/session/window_capture.py` — the portal half (currently ~863–1082):
    `_start_window_capture`, `_resume_window_capture`, `_stop_window_capture`, `_on_recorder_stopped`.
  - `services/session/passes.py` — the post-capture pass launch (currently ~1333–1429):
    `_start_transcription`, `_on_transcription_released`.
  - `manager.py` keeps the lifecycle, the state machine, the frame path and teardown.
- **Rationale.** Every remaining step in this plan edits this file. Splitting afterwards would mean
  splitting a file that has grown further; splitting first means each feature lands inside the cap.
  These are the seams the checklist itself named ("the source-selection and capture-wiring halves
  are the obvious seam"). This step adds **no behaviour** — it is a move, and the proof that it is
  a move is that the existing suite passes untouched.
- **Verification.** `uv run pytest` — the full suite, with **no test file edited in this step**; any
  test that needed changing means behaviour moved and the split is wrong. `uv run ruff check .`,
  `uv run ruff format --check .`. `wc -l` on all four files: every one under 800.
- **Docs.** `docs/structure.md` (three new modules), `docs/architecture.md` (component boundary).
- **Action.** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Interruptible Work (1 / 10) Complete: split the session manager into
  source-selection, window-capture and transcription-pass modules, with no behaviour change.`

### Step 2 — Remove the clutter, without removing anything real

- **Locations.** New `scripts/prune_empty_sessions.py`. Reads through
  `services/transcript/archive.py` (`list_sessions`, `describe`) and
  `services/recording/layout.py` (`key_for`, the folder's media names) so the script and the
  Recordings page agree on what a session *is* rather than reimplementing it.
- **Behaviour.** Reports by default; `--apply` deletes; `--older-than <date>` narrows. A database is
  a candidate only when it holds **zero segments at every revision** and its recording folder is
  absent or holds no `audio.wav`, `video*.??`, `video-with-audio.*` or `preview.jpg`. Prints the
  count, the total bytes, the oldest and newest candidate, and — separately and never deleted — the
  empty databases it is **keeping because they own media**, with their folder sizes.
- **Rationale.** 706 of 968 databases hold nothing, they sort to the top of the Recordings page, and
  the cause was fixed in Part 3i without the leavings being cleared. The media check is the whole
  safety of the step: 28 of those 706 are failed transcriptions of real recordings, and a rule of
  "zero segments" alone would delete the last record of them.
- **Real-world check.** Run against the developer's actual `data/` — first with no flags, and
  confirm the reported count matches the measured 706 minus the 28 protected. Then `--apply`. Then
  start the application and open the Recordings page: the first row must be a real talk. Record the
  before/after file count and directory size in the plan.
- **Verification.** `uv run pytest tests/data/test_prune_empty_sessions.py` — new tests over a
  temporary directory holding a real session, an empty one, an empty one *with* a recording folder,
  and an unreadable file; assert only the second is a candidate and that `--apply` is required.
  Full suite, ruff. `tests/utils/test_data_isolation.py` must still pass, proving the script's own
  tests write nothing into `data/`.
- **Docs.** `docs/structure.md` (the script), `docs/workflow.md` (how to run it),
  `docs/checklist.md` (close the item, with the measured numbers).
- **Action.** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Interruptible Work (2 / 10) Complete: a prune script that removes empty session
  databases and refuses to touch the ones that own a recording.`

### Step 3 — The last transcript survives a restart

- **Locations.** `services/session/manager.py` — a `reopen_last_session()` called from the lifespan
  in `web/backend/app/main.py`, setting `_last_store` through the existing `_retain()`.
  `services/transcript/archive.py` — a `newest_with_segments(config)` helper, so the selection rule
  lives with the code that already enumerates sessions. `config/schema.py` — a
  `storage.reopen_last_session` flag, defaulting true. `web/frontend/static/js/main.js` needs no
  change: `hydrate` → `loadTranscript` already reads whatever `manager.store` offers.
- **Rationale.** D-031 retains a finished session's store so the assistant and the page can still
  read it, but `_last_store` is instance state and a new process starts with `None`. The transcript
  is on disk the whole time; nothing reopens it. Doing the selection in `archive.py` rather than in
  the manager keeps one definition of "the sessions on disk" — the same reason the prune in Step 2
  reads through it.
- **Real-world check.** Record a short session. Stop it. Confirm the transcript is on screen. Kill
  the server and `uv run app.py` again. Reload the page: the same transcript, the same segment
  count, and the assistant answers a question about it rather than saying there is nothing to ask
  about. Then set the flag off, restart, and confirm the page comes up empty.
- **Verification.** New `tests/transcription/test_reopen_last_session.py`: a directory holding three
  sessions reopens the newest with segments and not the newest empty one; an empty directory
  reopens nothing and does not raise; the flag off reopens nothing; a corrupt database is skipped
  with a log line rather than preventing startup. Full suite, ruff.
- **Docs.** `docs/documentation.md` (**D-040**, extending D-031 across a process boundary),
  `docs/architecture.md`, `docs/data-flow.md`, `.env.example` if a variable is added,
  `docs/checklist.md`.
- **Action.** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Interruptible Work (3 / 10) Complete: the last finished transcript is reopened at
  startup, so a restart no longer empties the main page.`

### Step 4 — Draw the Aperture as pixels, with no new dependency

- **Locations.** New `web/backend/app/companion/raster.py`. Reads `VisualState` from
  `companion/visual_states.py` and the amplitudes from `companion/aperture.py`'s `Frame`, and
  produces an ARGB32 buffer plus its width and height. Rendered at 4× into a `numpy` array and
  box-downsampled. `aperture.py` is not rewritten — `Frame.amplitudes` already exists and is
  already asserted on by `tests/utils/test_aperture_render.py`.
- **Rationale.** StatusNotifierItem wants `a(iiay)` — width, height, and ARGB32 bytes. The project
  has nothing that turns SVG into pixels and cannot gain one without a system library or a GUI
  toolkit, both of which break D-023's single-command install. The instrument is thirteen rounded
  bars, two rings, a read head and four stubs; drawing those into an array directly is less code
  than adopting a rasteriser, and it keeps the SVG path — which the tests and any future web use
  depend on — untouched.
- **Verification.** New `tests/utils/test_aperture_raster.py`: the buffer is exactly
  `width * height * 4` bytes; a bar's column is brighter at its centre than at its edge (proving the
  downsample antialiases rather than aliasing); the `idle` and `recording` visuals differ; the
  `fault` visual has four bars' worth of ink and no more; every one of the (mode, run state) pairs
  from `visual_states.py` rasterises without raising. Full suite, ruff.
- **Docs.** `docs/structure.md`, `docs/motion-spec.md` §6 (that §3's closed-form amplitudes now have
  a second consumer).
- **Action.** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Interruptible Work (4 / 10) Complete: the Aperture renders to an ARGB32 pixmap
  from its own geometry, adding no dependency.`

### Step 5 — Put the icon in the tray

- **Locations.** New `web/backend/app/companion/tray.py`, wired into `companion/main.py`'s existing
  loop beside `_on_frame`. Uses `jeepney` — already a dependency, already used by
  `companion/shortcuts.py` for KGlobalAccel. Every name it puts on the bus — the item's `Id`,
  `Title` and bus name — comes from `web/backend/app/branding.py` (`APP_SLUG`, `APP_TITLE`), never a
  literal, because `tests/utils/test_no_legacy_brand.py` scans `web/` for exactly that.
- **Sub-steps, in order, because the first may end the step.**
  1. **The spike.** Export one `org.kde.StatusNotifierItem` object with a static pixmap and register
     it with `org.kde.StatusNotifierWatcher` — which is confirmed running on this machine with a
     host attached. Marshalling is already proven (see §1), so the only open part is the dispatch
     loop: `RequestName`, then serve `Properties.Get`/`GetAll`/`Introspect` off
     `DBusConnection.receive` and reply with `new_method_return`, answering anything unrecognised
     with `new_error` rather than silence — a host that gets no reply blocks. **If this cannot be
     made to work**, stop here, write down what failed, and put the file-backed-icon / PySide6
     choice to the user. Do not add PySide6 unasked.
  2. **The live icon.** `NewIcon` emitted when `Companion.latest_svg` changes — throttled, because
     the frame clock runs at 10 fps and a tray does not need ten signals a second. Honour the
     existing `hold_still` setting by emitting once and stopping.
  3. **The menu and activation.** `com.canonical.dbusmenu` built from `companion/menu.py`'s existing
     model, and `Activate` mapped to `Companion.activate`. If the menu protocol proves as costly as
     the pixmaps, `ItemIsMenu=false` with left-click activation is a shippable intermediate and is
     recorded as such rather than left unmentioned.
- **Rationale.** This is the one part of Plan 5 a user notices immediately, and everything behind it
  is already built and tested. It is staged so that the risky, unknown part is answered first and
  cheaply, rather than discovered after the menu has been written against an API that cannot be
  served.
- **Real-world check.** This step is not done until **a picture appears in the Plasma tray on the
  user's own desktop** and changes when a recording starts. Screenshot it. Then kill the companion
  and confirm the icon disappears and the server does not notice — the companion is a remote
  control and must stay disposable.
- **Verification.** New `tests/utils/test_tray_export.py` against a fake bus connection: the item
  registers, `GetAll` returns the properties an SNI host requires, the pixmap has the shape from
  Step 4, `NewIcon` is emitted on a visual change and **not** on an identical frame, and a bus that
  refuses the connection leaves the companion running rather than crashing it. Full suite, ruff.
- **Docs.** `docs/documentation.md` (**D-041**), `docs/structure.md`, `docs/deployment.md`,
  `docs/checklist.md` (close the two tray items, or record precisely what the spike found).
- **Action.** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Interruptible Work (5 / 10) Complete: the tray icon is exported over D-Bus and
  appears in the system tray.`

### Step 6 — `paused` enters the vocabulary, and the interface grows a second control

- **Locations.** `services/session/modes.py` — add `PAUSED`, add it to `RECORD_STATES`, and add it
  to `MODE_STATES` for all three modes. `web/frontend/static/js/core/modes.js` — the identical
  change, because `tests/utils/test_mode_vocabulary.py` parses that file and asserts the two match
  edge for edge. `stores/mode.js` — `paused` in the `action` switch and in `adoptSession`.
  `components/header.js` — a `PRESENTATION` row for `paused`, and a second button.
  `templates/partials/header.html`, `static/css/components/header.css`.
- **Rationale.** The vocabulary is mirrored and test-enforced, so the state has to be added on both
  sides in one change or the suite fails — which is the mechanism working. The second control is
  separate from the primary one for the reason given in §2: one button cannot safely mean both
  "stop, and you will be asked to confirm" and "pause, which is free".
- **Verification.** `uv run pytest tests/utils/test_mode_vocabulary.py` first, then the full suite,
  ruff. Manual browser pass: the pause control is absent in `idle`, present in `recording`, reads
  "Resume" in `paused`, and is absent in `stopping` and `processing`. Keyboard: reachable by Tab,
  activated by Enter and Space. **A 320 px pass**, because the header has been pushed off-screen by
  a fourth item once already (Part 3e) and this adds a fifth.
- **Docs.** `docs/design-system.md` (the `paused` presentation), `docs/component-map.md`,
  `docs/api-contract.md` (the state appears in `SessionResponse`).
- **Action.** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Interruptible Work (6 / 10) Complete: paused is part of the run-state vocabulary
  on both sides of the wire, with its own control in the header.`

### Step 7 — Pause, resume and cancel a live or recorded capture

- **Locations.** `services/session/manager.py` — a paused flag consulted at the top of `_on_frame`,
  after the gate check and **before** `sink.write` and `queue.put`; `pause()`, `resume()` and
  `cancel()` alongside `start()` and `stop()`; `state()` reports the new run state.
  `routes/session.py` — `POST /api/session/pause`, `/api/session/resume`, `/api/session/cancel`.
  `schemas/api.py`. `transport/events.py` — `session.paused`, `session.resumed`,
  `session.cancelled`, and `session.paused` added to the `INVALIDATES` entry that already retracts
  `recording.progress` and `transcript.hypothesis`. `stores/session.js` — freeze the clock on pause
  the way `stoppedAt` already freezes it on stop. `utils/loud_radish_ctl.py` and
  `companion/menu.py` — pause and resume from the tray, since a tray that can start and stop but
  not pause is the one place a pause is most wanted.
- **Rationale.** `_on_frame` is the single choke point: it is where the recording is written *and*
  where the engine is fed, so one flag there stops both in step and no other code needs to know.
  Dropping frames rather than buffering them is what makes the WAV and the transcript timeline agree
  — the decision recorded in §2. The level meter keeps emitting during a pause, deliberately: a
  paused recording with a dead meter looks like a broken one, and a moving meter says "we can still
  hear you, and we are not writing it down."
- **Real-world check.** Start a live session. Say a sentence. Pause. Say a **distinctive** sentence
  that must not appear. Resume. Say a third. Confirm: the middle sentence is absent, the transcript
  runs continuously with no gap in its timestamps, the header clock froze while paused, and the
  recorded WAV's duration equals the transcript's last timestamp rather than wall-clock elapsed.
  Repeat in `recorded` mode and confirm the post-capture pass transcribes the shortened file.
- **Verification.** New `tests/transcription/test_pause_resume.py` driving the manager with the
  synthetic source: frames during a pause reach neither the sink nor the queue; `session_seconds`
  does not advance while paused; resume continues the same session id and the same store; pause on
  an idle session is refused rather than silently accepted; cancel leaves the audio file, leaves
  committed segments, marks the metadata cancelled and starts **no** transcription pass. Full suite,
  ruff. `uv run python scripts/generate_contracts.py` and commit the regenerated contracts.
- **Docs.** `docs/api-contract.md`, `docs/routes.md`, `docs/data-flow.md`, `docs/architecture.md`,
  `docs/documentation.md` (**D-042** — pause removes time, and why).
- **Action.** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Interruptible Work (7 / 10) Complete: a live or recorded capture can be paused,
  resumed and cancelled, with the clock and the recording stopping together.`

### Step 8 — Pause a window capture without losing the picture's place

- **Locations.** `services/session/window_capture.py` (from Step 1) — stop the `WindowRecorder` on
  pause and reopen it on resume through the existing restore-token path that
  `_resume_window_capture` already uses for D-036. `services/capture/stitch.py` — **no change
  expected**: it takes `CaptureSegment(path, starts_at_s)` and inserts filler only for gaps above
  `MIN_GAP_S`, so a piece whose `starts_at_s` is derived on the *audio* clock — which did not
  advance during the pause — joins the previous piece with a gap of roughly zero and no filler.
  Confirm that by measurement rather than by reading.
- **Rationale.** Step 7 stops the audio clock, so the video must stop too or the two drift apart by
  exactly the pause. Reusing the resume path means pause inherits machinery already tested against a
  real `ffmpeg`. Deriving the piece's start from the audio clock is what lets the existing stitcher
  do the right thing without being told about pausing at all — the gap it would otherwise fill
  simply is not there.
- **Real-world check.** Record a window with a video playing. Pause for thirty seconds. Resume.
  Stop. Then: does the combined file play continuously with no thirty-second frozen frame; does the
  sound stay in step with the picture after the pause; and **does the portal picker appear on
  resume?** That last one is the open question from §2 and its answer decides whether this step
  ships as planned. Record what happened either way.
- **Verification.** New `tests/transcription/test_window_pause.py` against a stubbed portal and a
  real `ffmpeg` (following the existing D-036 tests): two pieces separated by a pause stitch into
  one file whose duration is the sum of the pieces, not the sum plus the pause; a reopen that fails
  leaves the session recording audio and raises a banner rather than ending it. Full suite, ruff.
- **Docs.** `docs/architecture.md`, `docs/data-flow.md`, `docs/documentation.md` (**D-043**),
  `docs/checklist.md` — including the picker's real behaviour, whatever it turns out to be.
- **Action.** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Interruptible Work (8 / 10) Complete: pausing a window recording stops the video
  with the audio, and the pieces rejoin with no gap.`

### Step 9 — A transcription pass that can be paused, cancelled, and picked up after a restart

- **Locations.**
  - `services/recording/batch.py` — `plan_windows` gains a `start_s`; `transcribe_file` gains
    `start_s` and reports the position it stopped at, so a resume begins on a window boundary
    rather than mid-window.
  - `services/recording/job.py` — `JobState` gains `PAUSED` and `CANCELLED`; `TranscriptionJob`
    carries `next_start_s` and `last_segment_id`.
  - `services/recording/runner.py` — a pause event distinct from the existing `_stop` event, so
    shutdown and pause are not the same signal; the checkpoint written on every window through
    `_on_window`, not only on pause, because a crash gets no chance to write one.
  - `services/transcript/schema.sql` and `store.py` — a `transcription_passes` table plus
    `record_pass`, `pass_state` and `incomplete_passes`, created through the existing `_migrate`.
  - `services/session/passes.py` (from Step 1) — resume an incomplete pass rather than starting one.
  - `routes/recordings.py` — `POST /{name}/transcribe/pause`, `/resume`, `/cancel`, following the
    shape `routes/sessions.py` already uses for `POST /{key}/export/cancel`.
  - `transport/events.py` — `transcription.paused` and `transcription.cancelled`, both added to
    `INVALIDATES` against `transcription.progress`. Without that, a page opened after a pause is
    replayed a stale "running" frame for the life of the process — the exact bug D-033 fixed twice
    and D-037 fixed a third time.
  - `components/recording-monitor.js`, `stores/recording.js`, and the Recordings page — the
    controls, and a "Resume transcription" affordance on a recording with an incomplete pass.
- **Rationale.** The interruption point already exists: `batch.py:170` consults `should_stop()`
  between windows and returns what it has. What is missing is somewhere to write down *where* it
  got to, and the ability to start again from there. Windows are 30 seconds, so a resume loses at
  most one window of work — small enough that sub-window checkpointing is not worth the complexity.
  Checkpointing on every window rather than only on pause is what makes a killed process resumable,
  which is the half of this the user cannot ask for by pressing a button.
- **Real-world check.** This needs a genuinely long recording, not a fixture. Take a recording of at
  least twenty minutes, start its pass, let it run a few minutes, and pause it. Confirm the partial
  transcript is on screen and the progress figure stopped. **Kill the server.** Start it again. The
  Recordings page must offer to resume. Resume it, let it finish, and then read the joined
  transcript across the seam: no duplicated sentence, no missing one, and timestamps that run
  continuously through the point where it was interrupted. Separately: cancel a pass and confirm the
  audio is still there and the recording is still listed as transcribable.
- **Verification.** New `tests/data/test_pass_checkpoint.py` (the table, the migration against a
  database created without it) and `tests/transcription/test_pass_resume.py` (a pass stopped after
  two windows resumes from window three; segment ids continue rather than restarting; the segment
  text across the seam appears exactly once — the assertion Part 3g's regression test established as
  the right shape for this class of bug; cancel commits nothing further and retains the audio).
  Full suite, ruff, `scripts/generate_contracts.py`. Note explicitly whether
  `test_stopping_ignores_the_mode` still flakes, with evidence either way.
- **Docs.** `docs/api-contract.md`, `docs/routes.md`, `docs/data-flow.md`,
  `docs/component-map.md`, `docs/documentation.md` (**D-044**, and an amendment to D-021 recording
  that "deliberately not persisted" no longer holds and why), `docs/checklist.md`.
- **Action.** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Interruptible Work (9 / 10) Complete: a transcription pass can be paused,
  cancelled, and resumed from its checkpoint across a server restart.`

### Step 10 — Write down what was done, and what is still owed

- **Locations.** `docs/checklist.md` — close the four items this plan addresses, and add what it
  discovered; every real-world check above that could not be run goes into **Part 5, Verification
  Debt**, named, rather than being reported as passing. `docs/plans/README.md` — index this plan,
  and add the missing `silent-recordings-and-quality.md` row while there. This plan file — mark
  10 / 10 and add a "What Changed From the Plan" section.
- **Rationale.** The project's rule is that documentation ships with the code, and the per-step doc
  updates above satisfy that. This step is for the things that only exist once all ten are done: the
  checklist's shape, the plan's own record of where it departed from itself, and the honest list of
  what a headless environment could not confirm.
- **Verification.** `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and
  `uv run python scripts/generate_contracts.py` producing no diff. Read `docs/structure.md` against
  the actual tree.
- **Action.** Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Interruptible Work (10 / 10) Complete: documentation, decision log and checklist
  brought up to date with the tray, the restart, the prune and the interruptible work.`

---

## 4. Deliverables Table

| Deliverable | Description | Location |
|---|---|---|
| Session source selection | Audio source and tap wiring, split out of the manager | `web/backend/app/services/session/sources.py` |
| Window capture control | Portal start, resume, pause and stop | `web/backend/app/services/session/window_capture.py` |
| Transcription pass launch | Starting, resuming and releasing a pass | `web/backend/app/services/session/passes.py` |
| Prune script | Reports, and on `--apply` removes, session databases with no segments and no media | `scripts/prune_empty_sessions.py` |
| Newest-session lookup | The selection rule for what to reopen at startup | `web/backend/app/services/transcript/archive.py` |
| Startup reopen | Reopens the last transcript so a restart does not empty the page | `web/backend/app/services/session/manager.py`, `web/backend/app/main.py` |
| Aperture rasteriser | The instrument as an ARGB32 pixmap, from `numpy`, no new dependency | `web/backend/app/companion/raster.py` |
| Tray item | The StatusNotifierItem object, its properties, `NewIcon`, and the menu | `web/backend/app/companion/tray.py` |
| `paused` run state | Added to the vocabulary on both sides of the wire | `web/backend/app/services/session/modes.py`, `web/frontend/static/js/core/modes.js` |
| Pause control | The second header button and its presentation | `web/frontend/static/js/components/header.js`, `templates/partials/header.html` |
| Capture pause / resume / cancel | The flag in `_on_frame`, the three methods, the three routes | `web/backend/app/services/session/manager.py`, `web/backend/app/routes/session.py` |
| Pass checkpointing | The table, its migration, and the queries that read it | `web/backend/app/services/transcript/schema.sql`, `store.py` |
| Resumable batch pass | `start_s` on the planner and the driver; pause distinct from shutdown | `web/backend/app/services/recording/batch.py`, `job.py`, `runner.py` |
| Pass controls | Pause, resume and cancel a transcription over HTTP | `web/backend/app/routes/recordings.py` |
| New events | `session.paused/resumed/cancelled`, `transcription.paused/cancelled`, with retraction | `web/backend/app/transport/events.py` |
| Prune tests | Only a database with no segments **and** no media is a candidate | `tests/data/test_prune_empty_sessions.py` |
| Restart tests | The newest session with segments is reopened; empty and corrupt ones are skipped | `tests/transcription/test_reopen_last_session.py` |
| Rasteriser tests | Buffer size, antialiasing, per-state distinctness, every (mode, state) pair | `tests/utils/test_aperture_raster.py` |
| Tray tests | Registration, properties, `NewIcon` only on change, a refused bus does not crash | `tests/utils/test_tray_export.py` |
| Pause tests | Paused frames reach neither sink nor queue; the clock holds; cancel keeps everything | `tests/transcription/test_pause_resume.py` |
| Window pause tests | Two pieces rejoin with no filler and no drift | `tests/transcription/test_window_pause.py` |
| Checkpoint tests | The table migrates onto a database created without it | `tests/data/test_pass_checkpoint.py` |
| Pass resume tests | Ids continue, no sentence appears twice across the seam, cancel retains the audio | `tests/transcription/test_pass_resume.py` |
| Vocabulary mirror | The existing test now also holds `paused` identical on both sides | `tests/utils/test_mode_vocabulary.py` |

---

## 5. What Changed From the Plan

**Step 1 — five new modules, not three, and the "no tests edited" rule did not survive.**

*Three modules were not enough.* The plan named `sources.py`, `window_capture.py` and `passes.py`.
Extracting exactly those left `manager.py` at **851 lines** — still over the cap, so the split would
have failed its own purpose. Two more came out: `frames.py` (what happens to one captured frame, on
both the threads that touch one) and `background.py` (the status ticker and the context and polish
workers). A sixth, `shapes.py`, holds the dataclasses and tuning constants and imports no sibling,
which is what lets the other five import them without a cycle. Final count: `manager.py` **1782 →
672**, and every file in the package is now under 500.

*The split is by file, not by interface, and the code says so.* The five modules are mixins on
`SessionManager`, not collaborators. Recorded as **D-039** with the reasoning, including the part
that is a cost rather than a benefit: coupling is unchanged, and the seam is a filename.

*Tests were edited, and the rule that forbade it earned its place by catching that.* The plan said
no test file may be touched in Step 1, on the grounds that a test needing a change means behaviour
moved. Roughly thirty patch sites named `app.services.session.manager` as the module to
monkeypatch — in two forms, `monkeypatch.setattr(manager_module, ...)` and the string path
`"app.services.session.manager.X"`. Monkeypatching binds to a *module namespace*, so where a name
lives is observable behaviour, and the targets had to move with the code.

That mattered more than tidiness, because four of those sites are the autouse fixtures in
`tests/conftest.py` that keep the entire suite out of the developer's PipeWire graph and away from
the screen-share dialog. Left pointing at `manager`, they would have kept passing while protecting
nothing — the precise failure the commit "No test may read the developer's audio graph either" was
written to prevent. So they were verified the same way that commit verified them: every real
`ApplicationTap`, `playback_streams`, `default_sink`, `default_monitor` and `PortalSession` made to
raise, and the resulting failure set compared before the split against after. **Identical — 24
failures on each side**, all of them tests that legitimately construct those objects themselves. The
one extra failure on the "after" side is `test_stopping_ignores_the_mode`, the SQLite flake Part 4 of
the checklist already documents, and it is left alone.

*Two tests fail on this machine for reasons that predate the split.*
`test_a_real_tap_reports_its_links_without_listening_to_them` and
`test_a_capture_produces_finite_audio_at_the_canonical_rate` are `@pipewire`-gated and read the live
graph, so whether they pass depends on what the developer happens to be playing. Confirmed by
stashing the whole split and running them twice against the untouched baseline: they fail there too.
Not caused here, not fixed here, and recorded so the next person does not spend the afternoon on it.
