# Plan 2 — Multi-Mode Interface: Implementation

*Created: 2026-08-15 · Status: **not started** (0 / 6 steps)*

Part two of the five-plan expansion. Depends on [Plan 1](multi-mode-ui-design.md) being complete.

## 1. Introduction

Plan 1 decides what the interface says; this plan builds it. The header gains a three-way mode
selector and a record control that cycles six run states instead of flipping a boolean. A pre-flight
sheet appears for the modes that need options chosen before capture starts. A third pane exists in
the markup for the recording monitor. The session store learns about modes, and `POST
/api/session/start` learns to accept one.

The governing constraint is that **live transcription must work exactly as it does today at every
point in this plan.** The `live` mode is the existing behaviour with a name attached, and if
selecting it and pressing record ever behaves differently from pressing record today, the step that
did it is wrong. The other two modes are wired to real endpoints that return a clear "not yet
implemented" while Plans 3 and 4 are outstanding, rather than being hidden — a mode selector with one
working option and two that silently do nothing is worse than one that says what is coming.

---

## 2. Gaps & Unanswered Questions

- **Does the backend need to know the mode in this plan?** *Assumption*: yes, minimally.
  `StartSessionRequest` gains a `mode` field defaulting to `live`, the manager stores it and reports
  it in `state()`, and `session.started` carries it. Modes other than `live` are rejected with the
  existing error envelope until their plan lands. Threading the field through now means Plans 3 and 4
  add behaviour rather than plumbing, and the rejection is a single line each removes.

- **Is the selected mode remembered between page loads?** *Assumption*: yes, in
  `core/storage.js` alongside `activePane` and `transcriptSize`, and it is a *preference*, not
  configuration — it does not belong in `/api/config`, which describes how the pipeline behaves, not
  which button was last pressed. On load the stored mode is re-selected only if it is currently
  available; an unavailable one falls back to `live`.

- **How does the frontend know which modes are available?** *Assumption*: from `GET /api/health`,
  which already reports installed optional dependency groups. `window` mode needs the capture group;
  the health payload gains a `capture` entry in Plan 4 and reports `false` until then, which is
  correct rather than provisional.

- **Does the third pane exist before Plan 4 fills it?** *Assumption*: yes — the template, the
  stylesheet, the tab, and the empty state all land here, and the pane shows "Window capture is not
  available yet". Adding a pane to a three-pane grid is a layout change worth making and verifying
  once, on its own, rather than inside the plan that also has to negotiate a desktop portal.

- **What about the narrow layout, which currently has two tabs?** *Assumption*: the third tab is
  present only when `window` mode is selected. Three permanent tabs on a 320 px screen for a pane
  that is empty in two of three modes spends scarce width on nothing.

---

## 3. Hierarchical Step-by-Step Instructions

### Step 1: Carry the mode through the API and the session manager

- **Locations**: `web/backend/app/schemas/api.py` (`StartSessionRequest.mode`, `SessionResponse`);
  `web/backend/app/services/session/manager.py` (`SessionManager.start`, `state()`);
  `web/backend/app/models/session.py` (`SessionMetadata.mode`);
  `web/backend/app/routes/session.py` (`start_session` validates the mode against `CaptureMode` and
  rejects the unimplemented ones with a `mode-unavailable` error envelope);
  `web/backend/app/transport/events.py` is unchanged — `session.started` already forwards the
  manager's state. Tests: `tests/api/test_session_modes.py`.
- **Rationale**: the mode is a property of the session, so it belongs on the session's metadata and
  in the payload that announces one started. Rejecting unimplemented modes at the route keeps the
  manager free of "not yet" branches that Plan 3 and Plan 4 would then have to find and remove.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/api`, plus `uv run python scripts/generate_contracts.py` and commit the regenerated
  contracts. Once validated, commit stating: `Multi-Mode UI Implementation (1 / 6) Complete: Sessions
  carry a capture mode end to end, and unimplemented modes are refused with a named error.`

### Step 2: The mode store and the record-state machine on the client

- **Locations**: New `web/frontend/static/js/stores/mode.js` — holds the selected mode, the current
  run state, per-mode availability, and the pre-flight options for the pending run; emits
  `MODE_CHANGED`. `web/frontend/static/js/stores/session.js` adopts `mode` from the
  `session.started` payload and from `hydrate`. `web/frontend/static/js/core/storage.js` gains the
  `captureMode` preference. Tests are exercised through the pane checks in step 6; the state machine
  itself is asserted in `tests/utils/test_mode_vocabulary.py`, extended to check that
  `stores/mode.js` only ever assigns states listed for the selected mode.
- **Rationale**: the run state is derived from several sources — the session store, the socket, and
  a pending post-process pass — and every component that renders it needs the same answer. One store
  computing it once is the existing pattern in this frontend and the reason components never talk to
  each other.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/utils`. Once validated, commit stating: `Multi-Mode UI Implementation (2 / 6) Complete: A
  mode store owns the selected mode and the six-state run machine, with the selection remembered
  between loads.`

### Step 3: The header — mode selector and cycling record control

- **Locations**: `web/frontend/templates/partials/header.html` (the mode `radiogroup`, the record
  control rewritten against `data-record-state`);
  `web/frontend/static/js/components/header.js` (renders from `mode` as well as `session`; the
  toggle's click handler asks the mode store what pressing it means rather than checking a boolean);
  new `web/frontend/static/js/components/mode-switcher.js`;
  `web/frontend/static/css/components/header.css` and a new
  `web/frontend/static/css/components/record-control.css`;
  `web/frontend/static/js/main.js` wires the new component and passes the mode into `start()`.
- **Rationale**: this is the change the whole expansion is named for. Splitting the selector into
  its own component keeps `header.js` a renderer rather than a controller, which is the ownership
  rule `docs/component-map.md` already states.
- **Action**: Undergo the verification/tests/validation process for this phase — manual browser QA
  including a keyboard-only pass over the radiogroup (arrow keys move, space selects), a screen-reader
  check that the state announcement fires, and a 320 px pass. Once validated, commit stating:
  `Multi-Mode UI Implementation (3 / 6) Complete: The header offers three capture modes and a record
  control that cycles six run states.`

### Step 4: The pre-flight options sheet

- **Locations**: New `web/frontend/templates/partials/preflight.html`, included from
  `web/frontend/templates/pages/app.html` outside `.app` alongside the settings modal; new
  `web/frontend/static/js/components/preflight.js` reusing `a11y/focus-trap.js`; new
  `web/frontend/static/css/components/preflight.css`; `web/frontend/static/js/main.js` routes
  `arming` through it before calling `api.startSession`.
- **Rationale**: the three window-capture options are per-run and have to be answered before capture
  begins, which makes a sheet at the moment of arming the only correct place for them. Reusing the
  existing focus trap rather than writing a second dialog primitive keeps one implementation of the
  accessibility behaviour that is easiest to get wrong.
- **Action**: Undergo the verification/tests/validation process for this phase — manual QA: the
  refused all-off combination is blocked with a visible reason, focus is trapped and restored,
  `Escape` cancels without starting anything. Once validated, commit stating: `Multi-Mode UI
  Implementation (4 / 6) Complete: Arming a window capture asks for its three options first, and
  refuses the combination that would record nothing.`

### Step 5: The third pane and mode-aware empty states

- **Locations**: New `web/frontend/templates/partials/monitor/pane.html` and
  `web/frontend/templates/partials/monitor/empty_state.html`;
  `web/frontend/templates/pages/app.html` (the third pane and its conditional tab);
  `web/frontend/static/css/layout.css` (three-pane grid) and new
  `web/frontend/static/css/components/monitor.css`;
  `web/frontend/templates/partials/transcript/empty_state.html` and
  `web/frontend/static/js/components/transcript-pane.js` (per-mode empty copy);
  `web/frontend/static/js/main.js` (`showPane` accepts the third name).
- **Rationale**: the layout change is independent of what fills the pane, and doing it here means
  Plan 4 adds a preview to a pane that already exists, is already responsive, and has already had a
  keyboard pass. The transcript's per-mode empty state is what stops `recorded` mode looking broken
  while it records.
- **Action**: Undergo the verification/tests/validation process for this phase — manual QA at
  320 px, at the 900 px breakpoint, and at desktop width, confirming the third tab appears only in
  `window` mode and that tab order remains sane. Once validated, commit stating: `Multi-Mode UI
  Implementation (5 / 6) Complete: A third pane holds the recording monitor, and each mode explains
  its own empty transcript.`

### Step 6: Regression pass on live transcription, and the documentation

- **Locations**: `tests/api/test_session_modes.py` extended with a live-mode end-to-end pass over the
  synthetic source; `docs/component-map.md`, `docs/routes.md`, `docs/api-contract.md`,
  `docs/structure.md`, `docs/design-system.md`, `docs/checklist.md`; `web/shared/contracts/`
  regenerated.
- **Rationale**: the one way this plan can fail invisibly is by changing live transcription while
  renaming it. An explicit end-to-end run in `live` mode over the file source, asserting segments
  commit exactly as before, is the check that catches it.
- **Action**: Undergo the verification/tests/validation process for this phase — the full suite,
  `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, plus a manual live
  recording confirming the transcript still fills. Once validated, commit stating: `Multi-Mode UI
  Implementation (6 / 6) Complete: Live transcription is unchanged under the new interface, and the
  documentation matches what shipped.`

---

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Mode on the session API | `mode` on start, on metadata, and in the state payload | `web/backend/app/schemas/api.py`, `web/backend/app/services/session/manager.py` |
| Mode rejection | Unimplemented modes refused with a `mode-unavailable` envelope | `web/backend/app/routes/session.py` |
| Mode store | Selected mode, run state, availability, pending options | `web/frontend/static/js/stores/mode.js` |
| Mode switcher | The three-way radiogroup | `web/frontend/static/js/components/mode-switcher.js` |
| Cycling record control | Six run states, labels, disabled rules, announcements | `web/frontend/templates/partials/header.html`, `web/frontend/static/js/components/header.js` |
| Pre-flight sheet | Per-run options for window capture, focus-trapped | `web/frontend/templates/partials/preflight.html`, `web/frontend/static/js/components/preflight.js` |
| Monitor pane shell | The third pane, its tab, its empty state, three-pane grid | `web/frontend/templates/partials/monitor/`, `web/frontend/static/css/components/monitor.css` |
| Mode API tests | Mode accepted, rejected, reported, and replayed on reconnect | `tests/api/test_session_modes.py` |
| Live regression test | An end-to-end `live` session over the file source, unchanged | `tests/api/test_session_modes.py` |
| State-machine test | `stores/mode.js` never assigns a state its mode cannot reach | `tests/utils/test_mode_vocabulary.py` |
| Regenerated contracts | OpenAPI and WebSocket event schemas | `web/shared/contracts/` |
