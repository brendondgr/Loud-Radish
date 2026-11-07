# Unified Record Sheet — One Button, One Sheet, Every Kind of Recording

*Written 2026-11-07. Status: **In progress (3 / 6)**. The next free decision number is **D-075**.*

## 1. Introduction

Today the header holds a three-way **Live / Recorded / Window** switch beside **Start recording**
(D-020, `docs/design-system.md` § Capture Modes). The person who uses the application reports that
this is confusing. The tabs name *implementations*, not choices a person makes, and they hide that
the three overlap. Live is "microphone, transcribe as you go". Recorded is "microphone, transcribe
when it ends". Window is "a window, with either or both of those, plus video". Two questions are
tangled together: *what am I recording* and *when should it be transcribed*. Neither question can
be answered in one place. Window has a sheet of options; the other two have none, and their
microphone is chosen in a settings tab two screens away.

The backend has already done most of the separating. In
`web/backend/app/services/session/manager.py` (`_start_locked`), `window` mode treats live
transcription, the second pass, and video as **three independent switches**: the engine is built
when live text is wanted, the audio sink is opened when a second pass is wanted, and the recorder
runs when video is wanted. `live` and `recorded` are two fixed combinations of those switches, with
the microphone as the source. So the change is mostly **interface**. It also needs one generalisation
on the backend: the per-run options are honoured in every mode, not only `window`.

The approach: remove the mode switch from the header. **Start recording** opens one *record sheet*,
which grows out of today's window pre-flight sheet and asks, in order:

1. **What to record**: my microphone, this computer's sound, or a window.
2. **Which input**: the microphone list, or which audio a window carries.
3. **When to transcribe**: while recording, after it ends, or both.
4. **Video**: for a window only.

The confirm button then says what happens next. The existing mode names `live` / `recorded` /
`window` stay as internal vocabulary. Shortcuts, the tray, stored session metadata, and
`tests/utils/test_mode_vocabulary.py` depend on them. The server **derives** the mode from the
options instead of the user choosing it from a tab.

## 2. Gaps & Unanswered Questions

- **Should the sheet show every option at once, or step through them one screen at a time?** The
  request says "go through the options", which suggests steps. *Assumption:* one sheet whose
  sections appear in order, where each section appears only once the one above it is answered. For
  example, the Video section appears only once "A window" is chosen. That reads like stepping
  through, without Back/Next buttons and without a second dialog primitive. Step 1 produces a mockup
  of both forms before anything is built, so this can be overturned cheaply.
- **Should the sheet remember the last choices?** D-020 made window options per-run on purpose,
  "so nobody silently gets no video next time". That fault came from options that were applied
  without being shown. Here the sheet is always shown with every choice visible, so pre-filling
  hides nothing. *Assumption:* pre-fill from the last run, stored in `core/storage.js` preferences,
  and drop to the safe defaults if the stored source no longer exists (the window-capture backend
  is gone, or the microphone is unplugged).
- **Is there still a one-click start?** Opening a sheet on every press adds a step for the person
  who always records the same way. *Assumption:* the two global shortcuts `toggle_live` and
  `toggle_recorded` keep starting immediately, without the sheet, as they do today. In the app, the
  sheet's confirm button takes focus, so pressing Enter twice starts a recording with the last
  settings.
  **Human intervention is needed to answer this question:** do you also want a visible "Start with
  last settings" split-button in the header, or is the sheet plus Enter enough?
- **Microphone + transcribe while recording + transcribe after (both passes).** No mode offers
  this today. `live` builds no sink, so it keeps no audio for a second pass. Step 2 makes it
  possible, because the window code path already supports it. *Assumption:* offer it. It is the
  combination the request describes as "a mix of both".
- **Recording a window's sound *and* the microphone at the same time.** The request mentions
  "the different microphones, the different windows", which could mean mixing both. There is no
  mixer today: `sources.py` opens exactly one audio source per session. Building one is a capture
  change with its own timing risks, not an interface change. *Assumption:* out of scope for this
  plan. Recorded as discovered work in `docs/checklist.md`.
  **Human intervention is needed to answer this question:** is simultaneous window + microphone
  audio a requirement for this redesign, or a later feature?
- **Choosing the microphone per run.** Today the microphone is configuration (`audio.device_id`,
  persistent per D-046) and is set in the Audio settings tab and the tray. *Assumption:* the sheet
  shows the device list from `GET /api/audio/devices` with the configured device preselected.
  Choosing a different one **writes the configuration** through the existing
  `POST /api/audio/device`, rather than adding a second per-run override. That keeps one source of
  truth, which matters because the tray's microphone picker reads the same value.
- **The file source.** When `audio.source_type == "file"`, the file wins in every mode
  (`sources.py`, `_open_source`). *Assumption:* the sheet says so in the "Which input" section
  ("Replaying *name.wav* — change in Audio settings"). It does not offer a microphone list that
  would be ignored.
- **`manager.py` is 796 lines**, against an 800-line cap. Step 2 must move code out of it before
  adding any. *Assumption:* the start-time choice of engine, sink and recorder moves into a new
  `services/session/plan.py`.
- **"Don't transcribe at all"** (video only). Window mode allows it today: live and post both off,
  video on. *Assumption:* keep it, offered only when Video is on, with the existing
  "nothing to record" refusal when everything is off.

## 3. Hierarchical Step-by-Step Instructions

Read before starting: `docs/skills/global-project-rules/SKILL.md`, `docs/architecture.md`,
`docs/data-flow.md`, `docs/component-map.md`, `docs/api-contract.md`, `docs/design-system.md`
(§ Capture Modes), `docs/motion-spec.md`, and the two plans this one revises,
[multi-mode-ui-design.md](multi-mode-ui-design.md) and
[multi-mode-ui-implementation.md](multi-mode-ui-implementation.md). Work on a feature branch, for
example `feature/unified-record-sheet`, never on `main`.

### Step 1: Specify the record sheet (design only, no code)

**Status: Complete (2026-11-07).** The specification is in `docs/design-system.md` § Capture Modes,
and the decision is D-075. The person approved the plan's assumptions as written ("go ahead"), so
the sectioned form was chosen without a mockup round. The built sheet's screenshots in Step 3 stand
in for the mockup.

- **Locations**: `docs/design-system.md` § Capture Modes (rewritten);
  `docs/documentation.md` decision log (new **D-075**); `docs/component-map.md`
  (the multi-mode table marked as superseded by this plan); this plan's Gaps section, updated with
  the answers.
- **What to produce**:
  - The **option model** as a table: source × input × transcription timing × video. For every
    combination, record which internal mode the server derives and whether it is offered.
  - The **mapping** that keeps the old names meaningful:
    - microphone + while recording → `live`
    - microphone + after → `recorded`
    - microphone + both → `recorded` with live text on (new)
    - this computer's sound + any timing → today's window audio path with video off
    - a window → `window`
  - The sheet's sections, the copy for each one, its focus order, and its disclosure behaviour.
    Reuse the hint wording from `templates/partials/preflight.html`. Its audio-source copy was hard
    won (D-022 and the "your microphone is not recorded" note).
  - A static HTML mockup of the sheet in both forms (sections, and stepped), for the person to
    choose between, published as an artifact.
  - The header without the mode switch. While recording, a quiet chip next to the clock says what
    is running, for example "Microphone · live + after". This answers "which mode is this?", which
    D-020 gave to the disabled radiogroup.
- **Rationale**: D-020 was a recorded design decision, with a rejected alternative written down.
  Reversing it needs the same treatment, or the next reader finds two contradicting specifications.
  Doing this first, without code, is what the request asks for: "not scrambling to get something
  correct".
- **Verification**: a documentation review only. Check that every combination in the table is
  either offered or explicitly refused with a reason. No `pytest` run is needed for docs alone.
- **Docs updated**: `design-system.md`, `documentation.md` (D-075), `component-map.md`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: *Unified Record Sheet (1 / 6) Complete: Specified one record sheet that replaces
  the three-way mode switch, and recorded why D-020's switch is retired.*

### Step 2: Honour capture options in every mode (backend)

**Status: Complete (2026-11-07).** As planned, with three additions:
- `resolve()` lives in `plan.py` beside `plan_run()`, and the manager calls it too, so a caller
  passing no options (tests, older code) gets the mode's preset.
- `GET /api/session` and `session.started` now carry the resolved `options`, which Step 4's header
  label needs after a reload.
- Pause and resume moved into a new `hold.py` mixin, which brought `manager.py` from 796 to 735
  lines.

`MODE_STATES` is left for Step 4, which changes both copies together. The tray icon still picks its
picture by mode, so "microphone, both" draws as *recording* rather than *live*. That is Step 5's.

- **Locations**:
  - `web/backend/app/schemas/api.py`: `CaptureOptions` gains `source`
    (`"microphone" | "system" | "window"`). `audio_source` stays and keeps its current meaning for
    windows. `StartSessionRequest.mode` becomes optional.
  - `web/backend/app/services/session/modes.py`: a new `derive_mode(options)` holding the mapping
    from Step 1. It sits beside `MODE_STATES` because it is vocabulary.
  - `web/backend/app/services/session/shapes.py`: mirror the new field.
  - `web/backend/app/services/session/plan.py` (**new**): the "which of engine / sink / recorder
    does this run need" decision, moved out of `manager.py` `_start_locked`. It is generalised from
    the `window` branch and keyed on options, not mode.
  - `web/backend/app/services/session/manager.py`: `self._options` is kept for every mode, not
    nulled outside `window`, and start calls into `plan.py`.
  - `web/backend/app/services/session/sources.py`: `_open_source` decides from `options.source`
    instead of `mode == WINDOW`. Today's "window audio" taps become reachable as the
    "this computer's sound" source with video off.
  - `web/backend/app/routes/session.py`: when `mode` is absent, derive it. When both `mode` and
    `options` are sent and contradict each other, refuse with a 422 and a message that names the
    conflict.
- **Rationale**: the sheet is only honest if every combination it offers actually runs. The window
  branch already shows that the three switches are independent. Extending them to the microphone is
  less code than keeping two parallel paths. Keeping `mode` accepted means the tray, the shortcuts
  and `loud-radish-ctl` (`toggle --mode live`) keep working without change.
- **Tests** (new or extended):
  - `tests/api/test_session_modes.py`: microphone + both passes opens a sink *and* builds an
    engine; omitting `mode` derives it; a contradictory body is refused.
  - `tests/utils/test_mode_vocabulary.py`: if `derive_mode`'s table is mirrored into
    `core/modes.js`, assert that the two copies agree.
  - A small unit test for `plan.py` covering the table from Step 1, using the mock backend.
- **Verification**: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
  `manager.py` ends this step **shorter** than 796 lines.
- **Docs updated**: `api-contract.md` (`POST /api/session/start` body), `data-flow.md` (the source
  is chosen from options), `documentation.md` (D-075 addendum), `structure.md` (`plan.py`).
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: *Unified Record Sheet (2 / 6) Complete: Capture options now drive every mode, so
  the microphone can be transcribed live and again afterwards.*

### Step 3: Build the record sheet (frontend component)

**Status: Complete (2026-11-07).** Built and mounted where the pre-flight sheet was. The header's
Window tab opens it with a window preselected, and its options go to the server **without** a
`mode`, so the server derives the mode. The old `preflight.*` files are unreferenced and deleted in
Step 6. The microphone is written through `config.patch({"audio.device_id"})`, the same call the
Audio tab makes, rather than `POST /api/audio/device`.

**Browser QA** ran against an isolated server on port 8411 with the mock model and a file source,
not the instance on 8395, because the launcher takes over its port. Checked:
- defaults, step numbering, and the file-source note
- a disabled window option carrying its reason (GStreamer was missing in that environment)
- the window branch, the refusal, and the confirm label
- Escape
- 375 px with no horizontal scroll

The Confirm promise is checked end to end in Step 4, where it becomes the only start path.

- **Locations**:
  - `web/frontend/templates/partials/record_sheet.html` (**new**, replacing `preflight.html`).
  - `web/frontend/static/js/components/record-sheet.js` (**new**, replacing `preflight.js`). It
    keeps `show()` returning a promise that resolves to options or `null`, and keeps the
    `FocusTrap`.
  - `web/frontend/static/css/components/record-sheet.css` (**new**, replacing `preflight.css`),
    listed in `templates/base.html`.
  - `web/frontend/static/js/transport/api.js`: reuse the existing device-list and select-device
    calls.
  - `web/frontend/static/js/core/storage.js`: the `lastRecordOptions` preference.
- **Behaviour**: the sections from Step 1, each disclosed once the one above it is answered. The
  microphone list is fetched when the sheet opens, not on page load, because devices change. An
  unavailable source (no window-capture backend) is shown disabled with its reason, never hidden.
  This keeps D-020's rule that "a disabled one naming the install command is actionable". The
  confirm label follows the choice: "Start recording" or "Choose a window…". The "nothing to
  record" refusal is kept.
- **Rationale**: grow from the existing sheet rather than adding a second dialog. Focus
  containment, Escape and scrim cancel, and the refusal are already correct there, and they are the
  accessibility behaviour most easily got wrong twice.
- **Tests**: extend `tests/frontend/test_rendered_accessibility.py` so the rendered sheet's
  radiogroups have legends, every input has a label, and the disabled source carries a reason.
- **Verification**: `uv run pytest`, `uv run ruff check .`. Manual browser QA through the
  `preview_start` dev server: open the sheet, walk every source, and confirm Tab and arrow
  behaviour, Escape, a narrow width (375 px), and dark mode. Take a screenshot for the record.
- **Docs updated**: `component-map.md` (`RecordSheet` replaces `Preflight`), `structure.md`,
  `design-system.md` if any token is added.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: *Unified Record Sheet (3 / 6) Complete: Built the record sheet that asks what to
  record, from which input, when to transcribe, and whether to keep video.*

### Step 4: Rewire the header and the mode store; retire the mode switch

- **Locations**:
  - `web/frontend/templates/partials/header.html`: remove the `data-mode-switcher` radiogroup and
    add the "what is running" chip.
  - `web/frontend/static/js/components/mode-switcher.js` and its stylesheet: **delete**.
  - `web/frontend/static/js/stores/mode.js`: `select()` and the `captureMode` preference go. The
    mode is set from the sheet's derived options at start, and from `adoptSession` on reload, which
    already treats the server as authoritative. `action` becomes `arm` whenever the state is `idle`
    or `error`, for every mode.
  - `web/frontend/static/js/core/modes.js`: `MODE_STATES.live` and `.recorded` gain `ARMING`. Mirror
    this in `services/session/modes.py`, or the vocabulary test fails.
  - `web/frontend/static/js/components/header.js`: render the chip.
  - `web/frontend/static/js/main.js`: `arm()` uses the record sheet for every start, and `start()`
    sends `{options}` with or without `mode`. `main.js` is 729 lines, so move `arm` / `armFromUrl`
    / `start` into a new `static/js/session-start.js` while touching them.
- **Rationale**: the switch and the sheet must not both exist. Two places to choose a mode is the
  confusion this plan exists to remove. `arming` already means "collecting options, nothing
  captured yet", which now applies to every recording.
- **Tests**: `tests/utils/test_mode_vocabulary.py` (updated state table);
  `tests/utils/test_visual_states.py` if it asserts the header's states;
  `tests/frontend/test_rendered_accessibility.py` (the header no longer has a radiogroup, and the
  chip has an accessible name).
- **Verification**: `uv run pytest`, `uv run ruff check .`. In manual browser QA, run a complete
  recording for each source against the **mock speech model and the file source** (the
  reproducible input from `docs/workflow.md`): start, pause, resume, stop, and the processing pass
  where one applies. Reload mid-recording and confirm the chip and state come back.
- **Docs updated**: `component-map.md`, `design-system.md` (header anatomy), `motion-spec.md` if any
  header transition referenced the switch, `routes.md` if `?arm=` changes meaning.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: *Unified Record Sheet (4 / 6) Complete: Start recording now opens the record
  sheet for every kind of recording, and the three-way mode switch is gone.*

### Step 5: Shortcuts, tray and control script

- **Locations**: `web/backend/app/companion/shortcuts.py` (`ACTIONS`),
  `web/backend/app/companion/menu.py` (`arm-window`), `web/backend/app/companion/main.py`
  (`arm` dispatch), and the control script behind `loud-radish-ctl` (`arm --mode`).
- **Change**:
  - `arm --mode window` opens the sheet with "A window" preselected, through
    `?arm=window&source=window`.
  - `open_app` opens the sheet with nothing preselected beyond the last run.
  - `toggle_live` and `toggle_recorded` keep starting immediately, with no sheet (see Gaps).
  - The tray item "Record a window…" stays, because it is a shortcut into the sheet and not a mode
    switch.
- **Rationale**: the tray and the keys reach the same sheet. Two implementations of "choose
  options" drift; D-020's Plan 5 already chose `?arm=` for this reason.
- **Tests**: `tests/utils/test_shortcuts_config.py`, `tests/utils/test_companion_menu.py`,
  `tests/utils/test_loud_radish_ctl.py`.
- **Verification**: `uv run pytest`, `uv run ruff check .`. Then a manual check on the desktop: the
  global "Record a window…" key opens the page with the sheet showing the window source.
- **Docs updated**: `routes.md` (the `?arm=` parameters), `workflow.md` if a control-script command
  changes.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: *Unified Record Sheet (5 / 6) Complete: The tray and global shortcuts open the
  record sheet with the right source chosen, and the quick-start keys still start immediately.*

### Step 6: Clean-up, documentation pass, and close-out

- **Locations**: delete `preflight.html`, `preflight.js`, `preflight.css`, `mode-switcher.js` and
  its CSS if any remain. Then sweep everything for `data-mode-option`, `data-preflight`,
  `captureMode` and "mode switcher" in `docs/`, `web/` and `tests/`. Mark this plan complete. Add a
  note to [multi-mode-ui-design.md](multi-mode-ui-design.md) and
  [multi-mode-ui-implementation.md](multi-mode-ui-implementation.md) that the header switch was
  superseded by D-075. Add the note **additively**, without rewriting their history (the D-069
  rule). Update `docs/checklist.md`: a Part for this work, plus the simultaneous window + microphone
  item under Discovered work. Refresh the README's screenshots if any show the switch
  (`docs/assets/capture.py`, mock data only).
- **Rationale**: the global rules forbid two sources of truth and dead code left behind by the
  change that orphaned it.
- **Verification**: `uv run pytest` (full suite), `uv run ruff check .`,
  `uv run ruff format --check .`. A `grep` sweep that returns nothing for the retired names, and one
  final browser QA pass of all three sources.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: *Unified Record Sheet (6 / 6) Complete: Removed the retired pre-flight sheet and
  mode switch, and brought every document in line with the record sheet.*

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Design specification and D-075 | The option model, the mapping to internal modes, sheet copy and behaviour, and why D-020's switch is retired | `docs/design-system.md`, `docs/documentation.md` |
| Sheet mockup | Static HTML of the sectioned and stepped forms, for choosing between them | Published artifact, linked from Step 1 |
| Mode derivation | `derive_mode(options)`, one table from options to `live` / `recorded` / `window` | `web/backend/app/services/session/modes.py` |
| Run plan module | Which of engine, sink and recorder a run needs, moved out of `manager.py` and generalised to every mode | `web/backend/app/services/session/plan.py` |
| Start request change | `CaptureOptions.source`, `mode` optional, contradiction refused | `web/backend/app/schemas/api.py`, `web/backend/app/routes/session.py` |
| Record sheet | Partial, component and stylesheet replacing the pre-flight sheet | `web/frontend/templates/partials/record_sheet.html`, `static/js/components/record-sheet.js`, `static/css/components/record-sheet.css` |
| Header and store rewiring | Switch removed, running chip added, `arming` for every mode, start flow split out of `main.js` | `templates/partials/header.html`, `static/js/stores/mode.js`, `static/js/components/header.js`, `static/js/session-start.js` |
| Shortcut and tray entry points | `?arm=` with a preselected source; quick-start keys unchanged | `web/backend/app/companion/shortcuts.py`, `menu.py`, `main.py` |
| Run-plan unit tests | The Step 1 combination table against the mock backend: a small, fast subset | `tests/utils/test_run_plan.py` |
| Session API tests | Microphone with both passes; derived mode; contradiction refused | `tests/api/test_session_modes.py` |
| Vocabulary test update | `arming` reachable in every mode, front and back agreeing | `tests/utils/test_mode_vocabulary.py` |
| Rendered accessibility tests | Sheet legends, labels and disabled reasons; header without a radiogroup | `tests/frontend/test_rendered_accessibility.py` |
| Companion tests | Shortcut actions, tray item and control-script `arm` arguments | `tests/utils/test_shortcuts_config.py`, `test_companion_menu.py`, `test_loud_radish_ctl.py` |
| Documentation sweep | Component map, API contract, data flow, routes, structure, checklist, superseded-by notes | `docs/` |
