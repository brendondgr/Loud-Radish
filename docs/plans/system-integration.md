# Plan 5 — System Integration: Autostart, Global Keybinds, and the Tray

*Created: 2026-08-15 · Status: **not started** (0 / 6 steps) · **Blocked in part — see section 2**·*

Part five of the five-plan expansion. Independent of Plans 3 and 4 except where noted; it can run
after [Plan 2](multi-mode-ui-implementation.md), but its keybind targets are not all present until
Plans 3 and 4 land.

## 1. Introduction

Everything built so far requires a browser tab to be open and focused before anything can be
recorded, which is the wrong shape for a tool whose value is being ready when a talk unexpectedly
starts. This plan makes the application a resident of the desktop: started with the session, always
listening, reachable by a keystroke from inside whatever application currently has focus, and visible
as a tray icon whose appearance says what it is doing.

The structural fact that drives the whole plan is that **a web page cannot do any of this.** A page
in a browser tab cannot register a system-wide hotkey, cannot place an icon in the tray, and does not
exist when no browser is running. So the application grows a second, small process — a native
companion that owns the tray icon and the global shortcuts and does nothing else, talking to the
existing server over the same loopback HTTP API the browser uses. The server stays the only thing
that records; the companion is a remote control. Keeping it that thin is what stops this plan turning
into a desktop-application rewrite, which `docs/checklist.md` explicitly still lists as an open,
unmade decision.

---

## 2. Gaps & Unanswered Questions

- **The state animations document is missing.** *Human intervention is needed to answer this
  question.* The brief says to "refer to the attached document for the specific animations to use per
  state", and no such document is present in this repository or was supplied with the request. Step 5
  therefore builds the **mechanism** — a frame-cycling tray icon driven by the application's current
  state, with the frame sets loaded from files rather than hard-coded — and ships deliberately plain
  placeholder frames. Dropping in the real animations becomes a matter of replacing image files and
  one manifest, with no code change. **Step 5 may not be reported as complete against the brief until
  that document is supplied.**

- **What owns the tray icon?** *Assumption*: a small helper process implementing
  `org.kde.StatusNotifierItem` directly over D-Bus with `jeepney`, the same pure-Python client Plan 4
  introduces. Wayland has no XEmbed tray at all, so the toolkit-based options (`pystray`, GTK
  AppIndicator) are wrappers around this same protocol; Plasma is a full StatusNotifierItem host, so
  speaking it directly avoids pulling a GUI toolkit into a `uv` virtualenv for one icon. *Recorded
  risk*: exporting icon pixmaps as D-Bus properties is the fiddly part, and if it proves unworkable
  the documented fallback is `PySide6`'s `QSystemTrayIcon` behind the same optional group. The
  fallback must be decided by trying, not by arguing.

- **How are global shortcuts registered under Wayland?** *Assumption*: through KDE's
  `org.kde.KGlobalAccel` D-Bus service, which is present on this machine and is the sanctioned route
  — under Wayland an application cannot grab keys itself, and reading `/dev/input` directly would
  mean a keylogger running as the user, which is not a reasonable thing for a transcription tool to
  install. A `.desktop`-plus-custom-shortcut path is documented as the portable manual alternative
  for desktops that are not KDE.

- **What does a keybind do when the app is not running?** *Assumption*: nothing, and that is
  correct — the shortcut is registered by the companion process, so if it is not running there is no
  shortcut. The autostart unit is what makes this a non-issue in practice.

- **How does a video or window trigger "open a prompt"?** *Assumption*: the companion raises the
  application's window at a URL that arms the mode — `/?arm=window` — and the existing pre-flight
  sheet from Plan 2 opens on load. Building a second, native options dialog would mean two
  implementations of the same three toggles that must be kept in step. If no browser window is open,
  the companion opens one.

- **Is the tray toggle "enable/disable the app" or "start/stop recording"?** *Assumption*: the brief
  asks for both and they are different things, so the menu carries both: a checkable **Listening**
  item that arms or disarms the global shortcuts without stopping the server, and start/stop items
  per mode. Conflating them would mean disabling the app killed a recording in progress.

- **Does the companion need the server's port?** *Assumption*: it reads the same `.env` and the same
  default (8395) `app.py` uses, and reports a clear "server not running" state rather than retrying
  silently. A tray icon that looks fine while the thing behind it is dead is worse than no icon.

- **Is any of this cross-platform?** *Assumption*: no, and the plan says so. Autostart, the tray
  protocol, and global shortcuts are all desktop-specific; this targets the Linux/KDE machine the
  project runs on. The companion is deliberately separable so a Windows or macOS equivalent can be
  added later without touching the server.

---

## 3. Hierarchical Step-by-Step Instructions

### Step 1: The control surface — endpoints and a CLI

- **Locations**: `web/backend/app/routes/session.py` gains `POST /api/session/toggle` (start in the
  named mode if idle, stop if running — one endpoint, because a keystroke has no way to know the
  current state and a round trip to find out is a race). New `utils/transcriber_ctl.py` — a
  dependency-free CLI with `toggle`, `start --mode`, `stop`, `status`, and `arm --mode`, printing a
  one-line human-readable result. `pyproject.toml` exposes it as a console script so `uv run
  transcriber-ctl` works. Tests: `tests/api/test_session_toggle.py` and
  `tests/utils/test_transcriber_ctl.py` against a stubbed HTTP client.
- **Rationale**: every later step in this plan is a way of invoking this CLI, so it exists first and
  is testable on its own. It is also immediately useful without any of the rest — a user can bind it
  to a shortcut by hand in System Settings and have working global keybinds before the companion
  process exists at all, which makes step 4 an improvement rather than a prerequisite.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/api/test_session_toggle.py tests/utils/test_transcriber_ctl.py`, plus a manual `uv run
  transcriber-ctl toggle` against a running server. Once validated, commit stating: `System
  Integration (1 / 6) Complete: A single toggle endpoint and a command-line client can drive
  recording from outside the browser.`

### Step 2: Autostart

- **Locations**: New `scripts/install_autostart.py` — writes
  `~/.config/systemd/user/transcriber.service` pointing at `uv run python app.py` with the repository
  path resolved at install time, runs `systemctl --user daemon-reload` and `enable --now`, and
  supports `--uninstall`. New `scripts/transcriber.desktop.in` — an XDG desktop entry that opens the
  interface in a browser application window, used both for the menu entry and as the documented
  non-systemd autostart alternative. `docs/deployment.md` documents both routes, what each writes
  where, and how to undo them. `app.py` gains a `--systemd` notification-friendly startup log line so
  `systemctl --user status` is informative.
- **Rationale**: a user unit is the right mechanism on this machine — it starts with the session
  rather than the machine, restarts on failure, and is inspectable with `journalctl --user`, none of
  which an XDG autostart entry gives. Writing it from a script rather than documenting a file to
  hand-copy matters because the unit has to embed an absolute path that differs per checkout.
  Uninstall is in scope from the start: a tool that installs a background service and cannot remove
  it is one users are right to distrust.
- **Action**: Undergo the verification/tests/validation process for this phase — a manual install on
  this machine, a logout/login cycle confirming the server comes back, `systemctl --user status`
  showing it healthy, and a clean `--uninstall`. Once validated, commit stating: `System Integration
  (2 / 6) Complete: The server installs as a user service that starts with the desktop session and
  uninstalls cleanly.`

### Step 3: The companion process and its tray icon

- **Locations**: New package `web/backend/app/companion/` — `__init__.py`, `main.py` (the process
  entry point and its poll loop against `GET /api/session`), `tray.py` (the
  `org.freedesktop.StatusNotifierItem` object, its properties, its `NewIcon`/`NewStatus` signals, and
  registration with `org.kde.StatusNotifierWatcher`), and `menu.py` (the `com.canonical.dbusmenu`
  export: **Listening** as a checkable item, start/stop per mode, Open, Settings, Quit).
  `pyproject.toml` gains an optional group `desktop` and a `transcriber-companion` console script.
  Tests: `tests/utils/test_companion_menu.py` (menu structure and enablement per state) and
  `tests/utils/test_companion_state.py` (server state → tray status mapping, including "server not
  running").
- **Rationale**: the tray icon is the plan's user-visible surface and the piece with the most
  protocol detail, so it gets its own step. Keeping the process a *poller* of the existing HTTP API
  rather than a second consumer of the WebSocket keeps it stateless and disposable — it can be
  killed and restarted at any moment without the server noticing, which is the property that makes a
  background helper safe to ship.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/utils/test_companion_*.py`, plus a manual run on this machine confirming the icon appears in
  the Plasma tray, the menu opens, each item does what it says, and killing the server changes the
  icon rather than freezing it. Once validated, commit stating: `System Integration (3 / 6) Complete:
  A companion process shows a tray icon whose menu can arm, start, and stop recording.`

### Step 4: Global shortcuts and the keybind settings tab

- **Locations**: `web/backend/app/companion/shortcuts.py` — registers each configured binding with
  `org.kde.KGlobalAccel`, re-registers on change, and unregisters on exit; falls back to reporting
  "not registered, bind it manually" with the exact command when the service is absent.
  `web/backend/app/config/schema.py` — `ShortcutsConfig` mapping action names
  (`toggle_live`, `toggle_recorded`, `arm_window`, `stop`, `open_app`) to key sequences, with
  defaults in `web/backend/app/config/defaults.py` and hot-swap classification in
  `web/backend/app/config/hotswap.py`.
  New `web/frontend/templates/partials/settings/shortcuts.html`,
  `web/frontend/static/js/components/settings/shortcuts.js`, and entries in
  `web/frontend/templates/partials/settings/nav.html` and
  `web/frontend/static/js/components/settings-modal.js` — a capture field that records a pressed
  combination, shows conflicts the companion reports, and says plainly when the companion is not
  running so the bindings are inert.
  `web/frontend/static/js/main.js` reads `?arm=<mode>` on load and opens the pre-flight sheet.
  Tests: `tests/utils/test_shortcuts_config.py`.
- **Rationale**: the brief's distinction — audio triggers record immediately, video and window
  triggers prompt first — is exactly the difference between the `toggle_*` actions and `arm_window`,
  so it is expressed in the action vocabulary rather than in a branch somewhere. Reusing the browser
  pre-flight via a URL parameter is what keeps one implementation of the three options.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/utils/test_shortcuts_config.py`, plus manual QA on this machine: a bound key starts a
  recording from inside another application, a window binding raises the browser with the sheet
  open, a conflicting binding is reported rather than silently ignored, and the settings tab passes a
  keyboard-only run. Once validated, commit stating: `System Integration (4 / 6) Complete: Global
  shortcuts start audio recording immediately and open the options sheet for window capture, and are
  editable in settings.`

### Step 5: State-driven tray animation

> **Blocked on the missing animations document — see section 2.** Build the mechanism, ship
> placeholders, and do not report this step complete against the brief until the real specification
> arrives.

- **Locations**: `web/backend/app/companion/animation.py` — a frame-cycling driver reading a
  manifest that maps each application state (`idle`, `arming`, `recording`, `stopping`, `processing`,
  `error`, `server-down`) to an ordered frame list and a frame interval, emitting `NewIcon` as it
  advances and holding a single static frame where a state has one. New
  `web/backend/app/companion/icons/manifest.json` and the placeholder frame files.
  `docs/structure.md` documents the icons directory as the drop-in point.
  Tests: `tests/utils/test_companion_animation.py` — every state in the Plan 1 vocabulary has a
  manifest entry, frames cycle at the declared interval, and a missing frame file degrades to the
  static fallback instead of crashing the companion.
- **Rationale**: driving the animation from the same `RecordState` vocabulary Plan 1 fixed means the
  tray cannot show a state the application does not have, and the parity test that already guards the
  vocabulary extends to cover the tray for free. Loading frames from a manifest is what makes the
  missing document a content gap rather than a code gap.
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest
  tests/utils/test_companion_animation.py`, plus a manual pass watching the icon through a full
  idle → recording → processing → idle cycle. **Report explicitly that the animations are
  placeholders pending the specification.** Once validated, commit stating: `System Integration
  (5 / 6) Complete: The tray icon animates per application state from a drop-in manifest, currently
  carrying placeholder frames.`

### Step 6: Documentation and the security review

- **Locations**: `docs/documentation.md` (Decision **D-023** — why a companion process rather than a
  desktop rewrite, and why shortcuts go through KGlobalAccel rather than reading input devices);
  `docs/architecture.md` (the companion as a second process and its one-way dependency);
  `docs/deployment.md` (install, uninstall, what is written where, and how to verify);
  `docs/workflow.md` (the new console scripts and the `desktop` optional group);
  `docs/routes.md` and `docs/api-contract.md` (`POST /api/session/toggle`);
  `docs/structure.md` (`web/backend/app/companion/`, `utils/transcriber_ctl.py`, the scripts);
  `docs/design-system.md` (the shortcuts settings tab); `docs/checklist.md`; `.env.example`;
  `web/shared/contracts/` regenerated.
- **Rationale**: this plan installs a background service, registers system-wide key handlers, and
  adds an endpoint that starts a microphone recording — which together are the most
  security-relevant change the project has made, and the reasoning has to be written down rather than
  inferred from the code. The specific things to state plainly: the toggle endpoint is still bound to
  loopback and still unauthenticated by the D-016 decision, so anything running as this user can
  start a recording; the companion registers shortcuts and does not read input devices; and both are
  removable with one documented command.
- **Action**: Undergo the verification/tests/validation process for this phase — the full suite,
  `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and a read-through
  confirming no document still describes the application as browser-only. Once validated, commit
  stating: `System Integration (6 / 6) Complete: D-023 records the companion-process design and its
  security posture, and every canonical document reflects a resident application.`

---

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Toggle endpoint | One call that starts or stops, because a keystroke cannot know the state | `web/backend/app/routes/session.py` |
| Control CLI | `toggle`, `start`, `stop`, `status`, `arm`, as a console script | `utils/transcriber_ctl.py` |
| Autostart installer | Writes, enables, and removes the systemd user unit | `scripts/install_autostart.py` |
| Desktop entry | Menu entry and browser application window; the non-systemd alternative | `scripts/transcriber.desktop.in` |
| Companion process | Poll loop, lifetime, and server-down handling | `web/backend/app/companion/main.py` |
| Tray item | StatusNotifierItem over D-Bus, properties and signals | `web/backend/app/companion/tray.py` |
| Tray menu | Listening toggle, per-mode start/stop, Open, Settings, Quit | `web/backend/app/companion/menu.py` |
| Global shortcuts | KGlobalAccel registration, conflict reporting, manual fallback | `web/backend/app/companion/shortcuts.py` |
| Animation driver | State → frame set from a drop-in manifest | `web/backend/app/companion/animation.py` |
| Icon manifest and frames | The replaceable content the missing document will specify | `web/backend/app/companion/icons/` |
| Shortcuts settings tab | Capture a combination, show conflicts, say when inert | `web/frontend/templates/partials/settings/shortcuts.html`, `.../settings/shortcuts.js` |
| Arm-on-load | `?arm=<mode>` opens the existing pre-flight sheet | `web/frontend/static/js/main.js` |
| Toggle tests | Start-if-idle, stop-if-running, and the mode argument | `tests/api/test_session_toggle.py` |
| CLI tests | Every subcommand against a stubbed HTTP client | `tests/utils/test_transcriber_ctl.py` |
| Companion tests | Menu structure per state; server state → tray status, including server-down | `tests/utils/test_companion_menu.py`, `tests/utils/test_companion_state.py` |
| Animation tests | Every state has frames; cycling interval; missing-file fallback | `tests/utils/test_companion_animation.py` |
| Shortcut config tests | Defaults, validation, hot-swap classification | `tests/utils/test_shortcuts_config.py` |
| Decision D-023 | Companion process over desktop rewrite; KGlobalAccel over input reading; security posture | `docs/documentation.md` |
