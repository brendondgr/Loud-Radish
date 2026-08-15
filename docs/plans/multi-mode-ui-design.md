# Plan 1 — Multi-Mode Interface: Design

*Created: 2026-08-15 · Status: **not started** (0 / 5 steps)*

Part one of the five-plan expansion. See [README.md](README.md) for the set and the order they run in.

## 1. Introduction

The application today has exactly one thing it can be doing: capturing a live microphone and
transcribing it as it goes. The header says so with a boolean — *Not recording* / *Recording* — and
one button that flips it. Three modes are now wanted instead of one: the existing live transcription,
a toggled recording that is transcribed only after it stops, and a window capture that records a
chosen window with any combination of live transcription, post-process transcription, and video.

A boolean cannot express that, and neither can a single control that cycles through everything. This
plan is design-level: it decides the **vocabulary** — what modes exist, what run states exist, which
control owns which — writes that vocabulary down in one place on each side of the wire so the two
cannot drift, and records the layout and accessibility decisions the next three plans build against.
It deliberately changes nothing a user can see. The one piece of code it lands is the shared
vocabulary module and the test that keeps the backend's copy and the frontend's copy identical,
because a mode name that means one thing in Python and another in JavaScript is the failure this
whole expansion is most likely to produce.

---

## 2. Gaps & Unanswered Questions

- **Should one control cycle through modes *and* run states?** The brief asks for "a toggleable
  switch that cycles between states — not recording, recording, and any other relevant modes."
  *Assumption*: **no — two controls.** A mode selector chooses *what kind of recording this will
  be*, and the record control cycles the *run state* of whichever mode is selected. One control
  doing both means the user cannot tell what pressing it will do without first reading its label,
  and the label changes under them. The mode selector sits immediately left of the record control so
  the pair still reads as one unit, which is what the brief is really asking for.

- **Is mode changeable while recording?** *Assumption*: no. The selector is disabled for the
  duration of a run and re-enabled on stop. Switching capture mode mid-session would mean tearing
  down and rebuilding the pipeline underneath a transcript that is still accumulating, and there is
  no user need for it.

- **What happens to a live transcript when the mode changes?** *Assumption*: nothing. Changing mode
  while idle does not clear the transcript. The transcript is cleared on `session.started` and
  nowhere else, which is an existing rule worth not breaking.

- **Does `recorded` mode show anything while it records?** *Assumption*: elapsed time, an input
  level meter, and a size-on-disk figure — but no transcript, because there is not one yet. The
  transcript pane shows a mode-specific empty state saying so. Showing a blank transcript pane with
  no explanation during a recording reads as a broken transcriber.

- **Where does the window-capture monitor live?** *Assumption*: a third pane, peer to the transcript
  and the assistant, shown only in `window` mode. Not a modal, and not a floating window: the brief
  requires live transcription and AI questions to keep working *during* the recording, and a modal
  over the top of both would prevent exactly that.

- **Are the three modes all always offered?** *Assumption*: yes, always visible, with unavailable
  ones disabled and carrying the reason. `window` mode needs a desktop portal and a capture backend
  that may not be installed. A mode that vanishes when its dependency is missing looks like a feature
  that does not exist; a disabled one naming the install command is actionable. This mirrors how
  `GET /api/health` already reports optional dependency groups.

- **The record control's cycle when a mode has a post-processing stage.** *Assumption*: the control
  is disabled, not hidden, during `processing`, and shows progress. Making it a cancel button is
  tempting and wrong — cancelling a transcription pass after a 40-minute recording discards the only
  copy of the audio's transcript, and the audio is on disk anyway, so the honest recovery is to let
  it finish or to stop the server.

---

## 3. Hierarchical Step-by-Step Instructions

### Step 1: Fix the vocabulary — capture modes and run states

- **Locations**:
  - New: `web/backend/app/services/session/modes.py` — `CaptureMode` and `RecordState` string
    literals, the `MODE_STATES` map naming which run states each mode can reach, and
    `mode_requirements()` describing what each mode needs installed.
  - New: `web/frontend/static/js/core/modes.js` — the same two vocabularies and the same map, as
    frozen objects.
  - New: `tests/utils/test_mode_vocabulary.py` — parses `modes.js` and asserts every name, every
    state, and every mode→states edge matches the Python module exactly.
- **Vocabulary**: modes are `live`, `recorded`, `window`. Run states are `idle`, `arming`,
  `recording`, `stopping`, `processing`, `error`. `live` never enters `processing`; `recorded` always
  does; `window` does only when post-process transcription was enabled.
- **Rationale**: every subsequent step on both sides of the wire names these strings. Defining them
  twice by hand and hoping is how `"window"` becomes `"window_capture"` in one file three weeks
  later. The test is the cheap enforcement, and it is possible only because the frontend has no build
  step — the JavaScript is a file on disk the test can read.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/utils/test_mode_vocabulary.py`, `uv run ruff check .`, `uv run ruff format --check .`. Once
  validated, commit stating: `Multi-Mode UI Design (1 / 5) Complete: Capture modes and run states are
  defined once and checked identical on both sides of the wire.`

### Step 2: Design the header — mode selector, record control, and what each mode shows

- **Locations**: `docs/design-system.md` (new "Capture modes" section); `docs/component-map.md`
  (planned components `mode-switcher` and `record-toggle`, and their ownership). No template or
  stylesheet changes yet.
- **Decisions to record**: the selector is a `radiogroup` of three, not a `<select>` — three options
  that change what the primary control does deserve to be visible without opening anything. The
  record control's label, `aria-label`, and colour for each of the six run states. The disabled rules
  (selector locked while not `idle`; control locked during `stopping` and `processing`). The
  `aria-live="polite"` region that announces state changes, and why it lives on the state label and
  not on the button.
- **Rationale**: the six-state control is where a multi-mode interface either stays legible or turns
  into a guessing game. Deciding the labels and the disabled rules once, in writing, means Plan 2
  implements a specification rather than inventing one control state at a time.
- **Action**: Undergo the verification/tests/validation process for this phase — documentation
  review against the existing design-system conventions and a contrast check on any new state
  colours. Once validated, commit stating: `Multi-Mode UI Design (2 / 5) Complete: The header's mode
  selector and six-state record control are specified, including disabled and announced states.`

### Step 3: Design the pre-flight sheet

- **Locations**: `docs/design-system.md` ("Pre-flight options"); `docs/component-map.md` (planned
  `preflight` component).
- **Decisions to record**: which modes open a pre-flight at all (`window` always; `recorded` only if
  a per-run title is wanted, otherwise never); the three `window` toggles — live transcription,
  post-process transcription, video capture — their defaults, and the one combination that must be
  refused (all three off, which records nothing); that the sheet is a focus-trapped dialog reusing
  `a11y/focus-trap.js` and the existing modal styling rather than a new dialog primitive; and that
  the window is chosen by the desktop's own portal picker *after* the sheet is confirmed, not inside
  it.
- **Rationale**: the options are per-run, not persistent settings, and the difference matters — a
  user who recorded one window without video should not silently get no video the next time. Putting
  them in a sheet at the moment of arming makes the choice explicit each run, and it is the only
  place they can be shown before capture starts.
- **Action**: Undergo the verification/tests/validation process for this phase — review the option
  set against the brief's three named toggles and confirm no fourth has crept in. Once validated,
  commit stating: `Multi-Mode UI Design (3 / 5) Complete: The pre-flight options sheet, its defaults,
  and its one refused combination are specified.`

### Step 4: Design the recording monitor pane and the mode-aware empty states

- **Locations**: `docs/design-system.md` ("The recording monitor"); `docs/component-map.md` (planned
  `recording-monitor` component); `docs/design-system.md` "Required States" table extended with the
  per-mode transcript empty states.
- **Decisions to record**: the monitor is a third pane in `main`, entering the existing narrow-layout
  tab set as a third tab rather than a new layout mechanism; it shows a low-frame-rate preview, the
  elapsed clock, the output file and its growing size, and which of the three options are active; the
  preview degrades to a static card naming the captured window when no preview is available, because
  a black rectangle and a broken preview look identical. Also record the reduced-motion rule for any
  pulsing recording indicator.
- **Rationale**: "a separate UI showing the recording in progress in real time" is the brief's
  requirement, and "alongside a working transcript and a working assistant" is the constraint. A
  pane satisfies both; a modal or a popup window satisfies neither. Deciding the degraded state now
  prevents Plan 4 from being blocked on whether a preview is achievable.
- **Action**: Undergo the verification/tests/validation process for this phase — check the three-pane
  layout against the existing 900 px breakpoint rules and the 320 px floor. Once validated, commit
  stating: `Multi-Mode UI Design (4 / 5) Complete: The recording monitor pane, its degraded state,
  and the per-mode empty states are specified.`

### Step 5: Record the decision and update the canonical documentation

- **Locations**: `docs/documentation.md` (Decision **D-020**, and the Status table gains a
  multi-mode row); `docs/structure.md` (`services/session/modes.py`, `static/js/core/modes.js`);
  `docs/architecture.md` ("Application mode" section, which currently describes a single mode);
  `docs/checklist.md` (a new part tracking the five-plan expansion); `docs/plans/README.md` (index
  entries for all five plans).
- **Rationale**: `docs/` is the source of truth, and D-020 is where the reasoning for two controls
  rather than one, and for the mode vocabulary living in two mirrored files, has to survive. The
  architecture document currently states the application has one mode; leaving that uncorrected while
  building three is the exact drift the repository rules exist to prevent.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest`,
  `uv run ruff check .`, `uv run ruff format --check .`, and a read-through confirming no document
  still describes a single-mode application. Once validated, commit stating: `Multi-Mode UI Design
  (5 / 5) Complete: D-020 records the multi-mode design, and every canonical document reflects three
  capture modes.`

---

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Backend mode vocabulary | `CaptureMode`, `RecordState`, the mode→states map, per-mode requirements | `web/backend/app/services/session/modes.py` |
| Frontend mode vocabulary | The same three vocabularies as frozen objects, no build step | `web/frontend/static/js/core/modes.js` |
| Vocabulary parity test | Parses the JS module and asserts it matches the Python one exactly | `tests/utils/test_mode_vocabulary.py` |
| Header specification | Mode selector, six-state record control, labels, disabled and announced states | `docs/design-system.md` |
| Pre-flight specification | Which modes arm, the three toggles, defaults, the refused combination | `docs/design-system.md` |
| Monitor specification | The third pane, its contents, its degraded state, reduced motion | `docs/design-system.md` |
| Planned component ownership | `mode-switcher`, `record-toggle`, `preflight`, `recording-monitor` | `docs/component-map.md` |
| Decision D-020 | Why two controls rather than one, and why the vocabulary is mirrored | `docs/documentation.md` |
| Architecture correction | "Application mode" rewritten for three capture modes | `docs/architecture.md` |
| Plan index | All five expansion plans listed with status | `docs/plans/README.md` |
