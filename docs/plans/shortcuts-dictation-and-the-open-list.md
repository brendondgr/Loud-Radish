# Shortcuts That Fire, Dictation to the Clipboard, and Closing the Open List

**Status: Planned (0 / 10)**

Branch: `shortcuts-dictation-and-the-open-list`, cut from `main` at `6ddd428`.
Predecessor: [tray-restart-clutter-and-interruptible-work.md](tray-restart-clutter-and-interruptible-work.md).

---

## 1. Introduction

The tray icon is on the desktop and it works. What it cannot yet do is any of the things a tray icon
exists for: no key on the keyboard reaches this application, the right-click menu cannot change the
microphone, and there is no way to alter a setting without opening a browser tab. This plan closes
that gap and then builds the feature the whole arrangement was heading towards — **press a key,
speak, press it again, and the words appear in whatever you were typing in**.

Three of those four are new surface. The fourth is a correction: `companion/shortcuts.py` has existed
since Plan 5 and **nothing has ever called it**. `register_all` has no call site anywhere in the
repository, so the five configured shortcuts have never been registered with anything, and the
"Listening for shortcuts" checkmark in the tray menu toggles a flag that nothing reads. The settings
panel that edits those keys writes them to a config file no process consults. That is the first
thing to fix, because dictation is a shortcut and the settings window edits shortcuts.

The back half of the plan is `docs/checklist.md` **Part 4 — Still Open**. That list is not one thing.
Some of it is genuinely owed work; some is a decision recorded as a deferral and mis-filed as debt;
and one entry turned out, while surveying for this plan, to be an active fault rather than an open
question. Steps 7 to 10 work through it and leave Part 4 honest.

### Measured before writing, not assumed

Every load-bearing claim below was checked against this machine (Fedora 44, Plasma 6.7.4, Wayland)
before the step that depends on it was written. This is the evidence the plan rests on:

| Question | Answer | How it was established |
|---|---|---|
| Can a settings window be built with no new dependency? | **Yes.** `tkinter` is in the venv and opens under XWayland; a key chord arrives with keysym and modifier mask. | Opened a real window, captured `Control-Alt-r` → `('r', 131076)`. |
| Can we write the clipboard? | **Yes**, two independent ways: `wl-copy` round-trips, and `org.kde.klipper` is on the session bus. | Round-tripped a probe string; the user's own clipboard was saved and restored. |
| Can we paste into the focused window? | **Yes**, two independent ways: `wtype` (exit 0, so KWin exposes the virtual-keyboard protocol) and `ydotool` (`ydotoold` running, `/dev/uinput` reachable). | Modifier-only press through both; then `a`,`b`,`c` typed into a real focused window and received. |
| Does KGlobalAccel accept a binding from `jeepney`? | **Yes.** `doRegister` + `setShortcutKeys(flags=4)`; key read back intact; `globalShortcutsByKey` resolves it to our action. | Registered `Meta+Alt+Shift+F9` = `0x1B000038` and read it back. |
| Does `globalShortcutPressed` reach us over D-Bus? | **Yes.** | `invokeShortcut` on the component delivered the signal to our listener. |
| Does a *physical* key reach our action? | **No — and this is the plan's one real risk.** Our component reports `isActive=False`; `kwin`, `plasmashell` and `org_kde_powerdevil` all report `True`. | Pressed the bound chord with `ydotool`; no signal. Same tool typed successfully into an ordinary window seconds later, so the instrument is sound. |
| How slow is the LLM cleanup step? | **8–10 s warm; 44 s cold.** The local relay routes to a reasoning model — 292 completion tokens for a 20-word answer. | Three timed `POST /v1/chat/completions` calls at `localhost:9090`. |

That last row is why dictation is designed the way it is in Step 4, and the `isActive` row is why
Step 2 ships a fallback rather than a hope.

---

## 2. Gaps & Unanswered Questions

**Simple gaps — an assumption is stated and the plan proceeds.**

- **Whether an in-process shortcut listener can be made to fire.** Our KGlobalAccel component
  registers, binds and can be invoked, but is not `isActive` and so never receives a physical key.
  *Assumption*: this is one missing call or one ordering detail in `kglobalacceld`, findable from its
  source during Step 2. **The plan does not depend on finding it.** Step 2 ships the desktop-file
  route as the floor — an installed `.desktop` entry plus a binding the desktop's own settings
  honour, which is how KDE's "Add Command" shortcuts already work and which needs no listener at all.
  The in-process listener is the improvement, not the mechanism.

- **Whether dictation pastes once or twice.** With cleanup enabled the raw text exists ~8 seconds
  before the tidied text does. *Assumption*: **paste exactly once, never twice.** Pasting raw and
  then pasting a correction would put both into the document, and the second paste would land
  wherever the caret has since moved. Cleanup gets a timeout and falls back to the raw text.

- **Where dictations are stored.** A `recorded` session writes a folder per recording, and this
  repository just deleted 679 empty session databases; fifty dictations a day would rebuild that pile
  in a fortnight. *Assumption*: dictations write to `data/dictations/`, discard their audio on
  success, and are pruned by age and count by the existing `scripts/prune_empty_sessions.py` family.

- **Whether the settings window is a second process.** *Assumption*: **yes.** Tk's main loop must own
  its thread and the companion's D-Bus dispatch loop already owns one. A separate
  `python -m app.companion.settings` also means a crash in the window cannot take the tray icon down.

- **Where the settings window reads and writes settings.** *Assumption*: through the server's
  `GET`/`PATCH /api/config`, never a parallel notion of configuration. That is the existing rule in
  `routes/config.py` and there is no reason for a native window to be the exception.

- **Which Part 4 entries are actually open.** Several ("speaker diarisation", "hosted ASR backends",
  "embeddings-based retrieval") are decisions already taken and recorded as deferrals. *Assumption*:
  Step 10 moves them into a **Deferred by decision** heading rather than pretending they are owed.

**Complex gaps — human intervention is needed to answer these.**

- **Is an 8–10 second wait acceptable before dictated text appears?** Measured on the local relay,
  which routes to a reasoning model. The plan defaults cleanup **on** with a 12-second timeout and a
  raw-text fallback, and makes it a setting — but whether the default should instead be *off*, or
  should point at a smaller model, is a judgement about how you intend to use it.
  *Human intervention is needed to answer this question.*

- **How should `silero_vad.onnx` reach the machine?** Ship the binary in the repository, download it
  on first use, or point `vad.model_path` at the copy already inside the `faster-whisper` package in
  the venv. Each has a different cost and the first is a repository policy question.
  *Human intervention is needed to answer this question.*

- **Should the assistant answer from polished prose instead of raw segments?** `docs/checklist.md`
  states plainly that this is "a decision about what the assistant is allowed to read, not a
  refactor". Citations are segment ids and a polished block has a different id space.
  *Human intervention is needed to answer this question.*

- **Desktop packaging** (Part 4). Whether this stays browser-plus-local-server or becomes a
  Tauri/Electron/Qt shell. Nothing in this plan forces the choice, and Step 10 records it as a
  standing decision rather than leaving it as an open task.
  *Human intervention is needed to answer this question.*

---

## 3. Hierarchical Step-by-Step Instructions

### Step 1: A test suite that finishes

- **Locations**: `pyproject.toml` (`[dependency-groups].dev`, `[tool.pytest.ini_options]`);
  `tests/assistant/test_llm_live.py`; `tests/transcription/test_window_audio.py`
  (`test_the_container_is_raw`); `tests/conftest.py`.
- **Rationale**: `uv run pytest` **does not terminate on this machine**. `test_llm_live.py` skips only
  when nothing answers on `localhost:9090` — and something does answer, so its streaming tests run
  for real against a reasoning model with no per-test deadline and a 180-second-per-read httpx
  timeout. Every step after this one is verified by running the suite, so the suite has to be
  trustworthy first; every full run during the rebrand had to pass `--ignore`, which is not a
  workable baseline for ten steps of work. Two other tests read the developer's live audio graph:
  `test_the_container_is_raw` does a blocking `stdout.read(64000)` with no timeout and asserts on an
  amplitude it does not control. Add `pytest-timeout` and a global deadline, put the live LLM tests
  behind an opt-in marker rather than a reachability probe, replace the blocking read with a deadline
  loop, and assert on absence of `NaN` — the fault that test was written for — instead of on peak
  amplitude.
- **Docs updated**: `docs/workflow.md` (the new marker and how to run the live tests deliberately),
  `docs/checklist.md` (both Discovered-work entries closed).
- **Action**: Undergo the verification/tests/validation process for this phase — `uv run pytest` must
  complete unattended, twice, with no `--ignore`. Once validated, commit stating:
  `Shortcuts & Dictation (1 / 10) Complete: the test suite terminates on its own, and no test asserts on the developer's audio.`

### Step 2: Global shortcuts that actually fire

- **Locations**: `web/backend/app/companion/shortcuts.py` — add a Qt key-sequence codec
  (`parse_sequence`, `format_sequence`), `bind_all`, and `listen`; a new
  `web/backend/app/companion/keys.py` for the Qt keycode table if `shortcuts.py` approaches the cap.
  `web/backend/app/companion/main.py` — `Companion.run` binds and starts the listener, and the
  `listening` flag in `Companion.activate` finally gates something real. `ACTIONS` gains `dictate`.
  New `web/backend/app/companion/desktop_entry.py` for the fallback route. New
  `tests/utils/test_shortcut_codec.py`; extend `tests/utils/test_shortcuts_config.py`.
- **Rationale**: `register_all` has no call site — the shortcuts have never been registered by
  anything, which is why they have never worked. Registration also currently stops at `doRegister`,
  which only *declares* an action; it never calls `setShortcutKeys`, so even a registered action has
  no key. The codec is the substantive piece: KGlobalAccel speaks integers
  (`Meta+Alt+L` is `0x18000000 | 0x4C`), the config file speaks strings, and the settings window in
  Step 6 needs to convert in both directions. Verified against a real binding — KWin's Alt+F4 reads
  back as `150994995`, which is exactly `0x08000000 | Qt::Key_F4`. **The `isActive` question is
  resolved in this step or routed around in this step, not carried forward**: if the listener cannot
  be made active, `desktop_entry.py` installs a `.desktop` file per action and the shortcut is bound
  against that, which requires no listener and no long-lived process.
- **Docs updated**: `docs/documentation.md` (new decision **D-046** — how shortcuts reach the
  application, and which of the two routes is in force), `docs/deployment.md` (what a non-KDE desktop
  must do by hand), `docs/checklist.md`.
- **Action**: Undergo the verification/tests/validation process for this phase — and it is a manual
  one: **press the key and watch the recording start.** A unit test cannot verify a key grab. Once
  validated, commit stating:
  `Shortcuts & Dictation (2 / 10) Complete: a key on the keyboard reaches the application, by a route that was proven rather than assumed.`

### Step 3: The clipboard, the paste, and a word from the desktop

- **Locations**: new package `web/backend/app/desktop/` — `clipboard.py`, `paste.py`, `notify.py`,
  `__init__.py`. New `tests/utils/test_clipboard_backends.py`, `tests/utils/test_paste_backends.py`.
- **Rationale**: Step 4 needs to put text somewhere and tell you it happened, and none of that exists
  anywhere in the repository today — `navigator.clipboard.writeText` in `selection.js` is the only
  clipboard code and it lives in the browser. Each concern gets a small module with **ordered,
  probed backends** rather than one hard-coded tool, because the tool that works differs per desktop:
  clipboard tries `wl-copy`, then `org.kde.klipper` over D-Bus, then `xclip`; paste tries `wtype`,
  then `ydotool`. Both routes of both were proven to work here, which is what makes an ordered list
  worth building instead of a guess. `notify.py` speaks `org.freedesktop.Notifications` through
  `jeepney` — the same session-bus connection pattern `tray.py` already uses — because a dictation
  that silently fails to paste is indistinguishable from one that never recorded. Nothing in this
  package raises; each returns a report naming what it tried and what happened, the way
  `shortcuts.py::describe` already does.
- **Docs updated**: `docs/structure.md` (new directory), `docs/deployment.md` (the optional system
  tools and what is lost without them).
- **Action**: Undergo the verification/tests/validation process for this phase — unit tests against a
  faked `subprocess.run`, plus a real round trip on this machine that saves and restores the existing
  clipboard. Once validated, commit stating:
  `Shortcuts & Dictation (3 / 10) Complete: text can be put on the clipboard, typed into the focused window, and reported on, by whichever backend the desktop actually has.`

### Step 4: Dictation, end to end

- **Locations**: new `web/backend/app/services/dictation/service.py`.
  `web/backend/app/services/recording/runner.py` — `TranscriptionRunner` gains an `on_done` hook
  carrying the job and store, threaded through its **three** construction sites:
  `services/session/passes.py:69`, `routes/recordings.py:185`, `routes/sessions.py:552`.
  `web/backend/app/routes/session.py` — the dictation endpoints. `utils/loud_radish_ctl.py` — a
  `dictate` subcommand. `web/backend/app/config/schema.py` — a `DictationConfig` block.
  `web/backend/app/companion/main.py` and `menu.py` — the tray shows and offers it.
  New `tests/transcription/test_dictation.py`.
- **Rationale**: this is the feature the rest of the plan exists to reach. Three decisions shape it,
  each forced by something measured:
  - **It is not a fourth capture mode.** A dictation is a `recorded` session with a delivery target.
    Adding to the `live`/`recorded`/`window` vocabulary (D-020) would mean touching `modes.py`,
    `modes.js` and the guard test that keeps them in step, for no behaviour that a delivery target
    does not already express. It also keeps `manager.py` — **782 lines against a 800-line cap** —
    out of it; the new work goes in `passes.py` (191 lines) and the new service.
  - **The LLM stage is the whole latency budget.** Transcription of a fifteen-second clip on a warm
    `base` model is a fraction of a second; cleanup measured **8–10 seconds warm**. So cleanup is
    configurable (`dictation.cleanup`), bounded (`dictation.cleanup_timeout_s`, default 12), and
    falls back to the raw transcript when it overruns. The text is pasted **exactly once** either
    way — see the gap above. `LlmBackend.complete()` already exists in `llm/contract.py:228` and has
    no call sites; it is precisely this shape and this is what it was for.
  - **Dictations do not join the recording pile.** They write to `data/dictations/`, discard audio on
    success, and are pruned by age and count. This repository deleted 679 empty session databases one
    plan ago; rebuilding that pile from the other end would be a poor joke.
  There is no existing success-carrying completion hook — `on_released` fires on all five terminal
  paths and takes no arguments — so `on_done` is added rather than `on_released` overloaded.
- **Docs updated**: `docs/routes.md`, `docs/api-contract.md`, `docs/data-flow.md`,
  `docs/structure.md`, `docs/documentation.md` (**D-047**: dictation is a delivery target, not a
  mode; and the paste-once rule), `docs/checklist.md`.
- **Action**: Undergo the verification/tests/validation process for this phase. Unit tests cover
  order of operations, the cleanup timeout falling back to raw, a cancelled dictation pasting
  nothing, and an empty transcript pasting nothing. **Then dictate a real sentence into a real text
  field on this machine and read what lands.** Once validated, commit stating:
  `Shortcuts & Dictation (4 / 10) Complete: a key starts a dictation, a key ends it, and the words arrive in the window you were typing in.`

### Step 5: A microphone picker in the tray menu

- **Locations**: `web/backend/app/companion/tray.py` — `_layout` walks a tree instead of emitting a
  flat list, item ids become stable rather than positional, `_handle_event` resolves an id through
  the tree, and a parent carries `children-display`. `web/backend/app/companion/menu.py` — `build`
  gains a devices submenu with the current device checkmarked. `web/backend/app/companion/main.py` —
  polls `GET /api/audio/devices`, caches the list, and `activate` sends
  `PATCH /api/config` for `audio.device_id`. Extend `tests/utils/test_tray_export.py` and
  `tests/utils/test_companion_menu.py`.
- **Rationale**: the menu is **flat by construction** — `_layout` emits `[]` children for every row
  and `_handle_event` maps a click to `items[item_id - 1]` by position. `MenuItem.children` exists on
  the dataclass and has never been used. Nesting breaks the positional mapping, so the id scheme *is*
  the work here and it is where the bug would be: an off-by-one in a nested menu selects the wrong
  microphone silently. The device list itself is free — `GET /api/audio/devices` already
  exists and already merges microphones and loopback sources with their type.
- **Docs updated**: `docs/documentation.md`, `docs/checklist.md`.
- **Action**: Undergo the verification/tests/validation process for this phase — including opening
  the real menu on the real desktop and switching microphone, then confirming the change in
  `GET /api/config`. Once validated, commit stating:
  `Shortcuts & Dictation (5 / 10) Complete: the tray menu nests, lists the microphones, and switching one takes effect.`

### Step 6: The settings window

- **Locations**: new `web/backend/app/companion/settings_window.py` and
  `web/backend/app/companion/settings_form.py` if the first approaches the cap; a
  `__main__`-style entry point so it runs as `python -m app.companion.settings`.
  `web/backend/app/companion/main.py` — the menu's `settings` item launches the window instead of
  opening a browser tab; `open` keeps opening the web interface. New
  `tests/utils/test_settings_window.py`.
- **Rationale**: this is what was asked for — a place to edit shortcuts that is not the web app.
  `tkinter` is already in the venv and was proven to open a window and capture a chord on this
  machine, so it costs **no new dependency**, which is the same reasoning that produced a hand-written
  rasteriser rather than a Pillow dependency in the previous plan. The window holds three groups: the
  shortcut editor (press-to-capture, with conflicts checked against
  `KGlobalAccel::globalShortcutAvailable` before a key is accepted — taking a key another application
  holds is not ours to do), the microphone chooser, and the dictation options from Step 4. It is a
  **separate process** so that Tk's main loop and the companion's D-Bus loop do not fight over a
  thread, and so a crash in the window leaves the tray icon standing. It reads and writes exclusively
  through `GET`/`PATCH /api/config`. Tests cover the pure functions — the Tk event state to sequence
  string conversion, conflict detection, the patch payload — and not the main loop; note that a
  synthetic `event_generate` reports a different modifier mask than a real press, which is a trap
  worth a comment in the test.
- **Docs updated**: `docs/structure.md`, `docs/workflow.md` (how to open it), `docs/documentation.md`
  (**D-048**: a native settings window, why Tk, and why a second process), `docs/checklist.md`.
- **Action**: Undergo the verification/tests/validation process for this phase — open it from the
  real tray menu, rebind a shortcut, and confirm the new key works without restarting anything. Once
  validated, commit stating:
  `Shortcuts & Dictation (6 / 10) Complete: right-clicking the tray opens a native window that edits the shortcuts, the microphone, and the dictation options.`

### Step 7: The voice detector that was never running

- **Locations**: `web/backend/app/services/vad/__init__.py` (`build_detector`),
  `web/backend/app/services/vad/silero.py` (the `SileroUnavailableError` message),
  `web/backend/app/services/session/manager.py` (`_emit_failure`, so the fallback reaches the banner
  system), `docs/deployment.md`, `tests/transcription/test_vad_detectors.py`.
- **Rationale**: **this is a live fault, not an open question.** `data/loud-radish-config.json` on
  this machine sets `vad.detector = "silero"`; `silero_vad.onnx` does not exist anywhere on the
  machine; `build_detector` catches `SileroUnavailableError` and falls back to the energy detector
  with a `logger.warning` that nobody reads. The application has been reporting one thing and doing
  another. Worse, the error message tells the user to run `uv sync` to install a `vad-silero`
  optional group that **no longer exists** — D-023 removed all optional groups — so following the
  instruction cannot possibly help. A silent fallback becomes an announced one; the message names the
  actual missing thing; and only then is the energy-versus-Silero comparison the checklist asks for
  worth running, because until now it has been comparing energy against energy.
- **Docs updated**: `docs/deployment.md` (the stale `vad-silero` reference), `docs/checklist.md`
  (Part 4 entry rewritten as a closed fault plus a measured decision), `docs/documentation.md`.
- **Action**: Undergo the verification/tests/validation process for this phase — a test that a
  fallback is announced rather than silent, then a real measurement of both detectors against a real
  recording before the default is set. Once validated, commit stating:
  `Shortcuts & Dictation (7 / 10) Complete: the voice detector the config asks for is the one that runs, and says so when it cannot be.`

### Step 8: Defaults measured rather than guessed

- **Locations**: new `scripts/benchmark_asr.py`. `web/backend/app/config/schema.py` (`AsrConfig`
  defaults) and `web/backend/app/config/presets.py`. `web/backend/app/services/polish/worker.py:171`
  (log `check.ratio`, count discards). `web/backend/app/services/asr/lifecycle.py:235` (the debug
  record gains `no_speech_prob` and `avg_logprob`). `web/backend/app/services/session/metrics.py`
  (a per-code suppression breakdown). New `tests/utils/test_benchmark_report.py`.
- **Rationale**: three Part 4 entries — the ASR default, `polish.min_retained_ratio`, and the
  invented-speech thresholds — are all the same shape: a number chosen without data, which cannot be
  fixed by choosing a different number without data. Most of the measuring apparatus already exists
  (`EngineMetrics.real_time_factor`, `PipelineMetrics.summary_lines`, `scripts/run_file_session.py`,
  `scripts/measure_capture_cost.py`), so the benchmark script is a loop and a report rather than new
  instrumentation. The two tuning entries need one thing each before they can ever be tuned:
  `ContentCheck.ratio` says in its own docstring that it is "logged, so a threshold can be tuned from
  real runs" and **the caller drops it**; and the hallucination filter logs its verdict but not the
  two numbers the thresholds are set against. Neither can be tuned until it is recorded. `no_speech_certain`
  is currently 0.85 because one measured hallucination scored 0.901 — a sample of one, and it will
  stay a sample of one until the number is written down on every run.
- **Docs updated**: `docs/deployment.md` (the measured hardware table), `docs/workflow.md` (the new
  script), `docs/checklist.md`, `docs/documentation.md`.
- **Action**: Undergo the verification/tests/validation process for this phase — the report formatter
  is unit-tested against a fixed result set; the benchmark itself is run on this machine and its
  output is what sets the defaults. Once validated, commit stating:
  `Shortcuts & Dictation (8 / 10) Complete: the ASR defaults come from a measurement on this machine, and the two tunable thresholds now record the numbers they would be tuned from.`

### Step 9: The polish view, the assistant's reading, and an accessibility floor

- **Locations**: `web/frontend/templates/partials/transcript/toolbar.html` and
  `web/frontend/static/js/components/transcript-pane.js` (the coverage filter at ~line 219 becomes
  conditional). `web/backend/app/services/context/assembler.py` (the `TranscriptSource` Protocol at
  ~line 55, and the verbatim block). New `tests/frontend/test_rendered_accessibility.py` and
  `tests/frontend/test_token_contrast.py`; `beautifulsoup4` added to `[dependency-groups].dev`.
- **Rationale**: three more Part 4 entries, in descending order of certainty. The per-page polish
  toggle is small and entirely frontend — raw segments are never deleted, only filtered out of the
  DOM, and the existing revision switch (D-022) is the pattern to copy down to its `aria-pressed`
  markup. The assistant reading polished text is **flagged for you** in the gaps above and is
  implemented only if you say so; if you do, the difficulty is not mechanical but that citations are
  segment ids and a polished block has a different id space. Accessibility tooling has been "not
  selected" since the design system was written, and the reason is that every real option assumed a
  browser and an npm build step that this project deliberately does not have. Two things can be done
  without either: assert structural rules over the HTML that `TestClient` already renders (labels,
  accessible names, one `h1`, `lang`, the `aria-live` regions the design system requires), and check
  WCAG contrast over the CSS custom properties in pure Python with no dependency at all. Those cover
  the failure this codebase actually risks — a template edit quietly dropping a label — which a
  browser-driving tool would catch no better.
- **Docs updated**: `docs/design-system.md` (the automated check, and the unchecked box at ~line 350),
  `docs/component-map.md`, `docs/api-contract.md` if the assistant change is taken,
  `docs/checklist.md`.
- **Action**: Undergo the verification/tests/validation process for this phase, including a manual
  keyboard and 320 px pass on the new toggle. Once validated, commit stating:
  `Shortcuts & Dictation (9 / 10) Complete: the polished view can be turned off for one reading, and the rendered page is checked for labels and contrast on every run.`

### Step 10: The race, the dead settings, and an honest list

- **Locations**: `tests/api/test_session_toggle.py::test_stopping_ignores_the_mode`;
  `web/backend/app/services/transcript/store.py` and `services/recording/runner.py` (instrumentation
  only, unless the traceback names something else). `web/backend/app/config/schema.py`
  (`storage.retention_days`). `.env.example`. `docs/checklist.md` (Part 4 restructured).
- **Rationale**: the SQLite flake fails about one full run in three and passes in isolation every
  time. `docs/checklist.md` already says, in terms, that it must not be fixed by guessing — a
  concurrency change made on a hunch is how the original "Cannot operate on a closed database" fault
  was introduced. So: reproduce under repetition with the store's `close` instrumented, get the full
  traceback rather than the truncated summary, and fix what it names. **If it cannot be reproduced,
  the evidence is recorded and the entry stays open** — that is a legitimate outcome and is the only
  honest one. Two dead settings surfaced while surveying: `storage.retention_days` is declared and
  read by nothing, and `.env.example` documents `ASR_BACKEND`, `ASR_MODEL`, `ASR_DEVICE` and the
  `LLM_*` variables that no Python reads, while `docs/workflow.md` makes `.env.example` accuracy a
  project rule. Each gets wired up or deleted. Finally Part 4 is split: what is genuinely owed stays
  under **Still Open**, and the deferrals-by-decision move under **Deferred by decision** with the
  reason, so the list stops reading as a debt pile.
- **Docs updated**: `docs/checklist.md`, `docs/documentation.md`, `docs/workflow.md`,
  `docs/plans/README.md` (index row), and this plan's own status and "What Changed From the Plan".
- **Action**: Undergo the verification/tests/validation process for this phase — the full suite run
  five times unattended, `ruff check` and `ruff format --check` clean, and a browser QA pass. Then
  merge to `main` and delete the branch. **Do not push.** Commit stating:
  `Shortcuts & Dictation (10 / 10) Complete: the flaky race is resolved or evidenced, the dead settings are gone, and Part 4 says only what is true.`

---

## 4. Deliverables Table

| Deliverable | Description | Location |
|---|---|---|
| Suite deadline | `pytest-timeout` and a global limit so a run always terminates | `pyproject.toml` |
| Opt-in live LLM tests | A marker, not a reachability probe, so a running relay cannot hang a run | `tests/assistant/test_llm_live.py` |
| Bounded audio-graph read | A deadline loop replacing a blocking `read`, asserting on `NaN` not amplitude | `tests/transcription/test_window_audio.py` |
| Qt key-sequence codec | `Meta+Alt+L` ↔ `0x1800004C`, both directions | `web/backend/app/companion/shortcuts.py`, `keys.py` |
| Shortcut binding and listening | `setShortcutKeys` plus a `globalShortcutPressed` listener, wired from `Companion.run` | `web/backend/app/companion/shortcuts.py`, `main.py` |
| Desktop-entry fallback | `.desktop` files and a bindable command, for when the listener cannot be made active | `web/backend/app/companion/desktop_entry.py` |
| Codec tests | Every default sequence round-trips; malformed input is rejected, not guessed at | `tests/utils/test_shortcut_codec.py` |
| Clipboard writer | Ordered probed backends: `wl-copy`, `org.kde.klipper`, `xclip` | `web/backend/app/desktop/clipboard.py` |
| Paste driver | Ordered probed backends: `wtype`, `ydotool` | `web/backend/app/desktop/paste.py` |
| Desktop notification | `org.freedesktop.Notifications` over the existing `jeepney` connection pattern | `web/backend/app/desktop/notify.py` |
| Desktop backend tests | Selection, ordering and failure reporting against a faked `subprocess.run` | `tests/utils/test_clipboard_backends.py`, `test_paste_backends.py` |
| Dictation service | Record → transcribe → optional bounded cleanup → clipboard → paste, once | `web/backend/app/services/dictation/service.py` |
| Pass completion hook | `on_done` carrying the job and store, through all three construction sites | `web/backend/app/services/recording/runner.py` |
| Dictation settings | `DictationConfig`: cleanup mode, timeout, audio retention, storage directory | `web/backend/app/config/schema.py` |
| `dictate` control command | So a hand-bound desktop shortcut works with no listener | `utils/loud_radish_ctl.py` |
| Dictation tests | Order of operations, timeout falls back to raw, cancel and empty paste nothing | `tests/transcription/test_dictation.py` |
| Nested tray menu | A layout tree with stable ids, and click resolution through it | `web/backend/app/companion/tray.py` |
| Microphone submenu | Devices listed with the current one checkmarked, switching by `PATCH /api/config` | `web/backend/app/companion/menu.py`, `main.py` |
| Nested menu tests | A nested layout round-trips; a child click resolves to the right item | `tests/utils/test_tray_export.py`, `test_companion_menu.py` |
| Native settings window | Tk, no new dependency, its own process: shortcuts, microphone, dictation | `web/backend/app/companion/settings_window.py` |
| Settings window tests | Chord-to-string, conflict detection, patch payload — the pure parts, not the loop | `tests/utils/test_settings_window.py` |
| Announced VAD fallback | The silent energy-instead-of-Silero substitution becomes visible, with a message that names the real cause | `web/backend/app/services/vad/__init__.py`, `silero.py` |
| ASR benchmark harness | Models × devices × precisions, reporting real-time factor on this machine | `scripts/benchmark_asr.py` |
| Benchmark report tests | The formatter over a fixed fake result set | `tests/utils/test_benchmark_report.py` |
| Tuning instrumentation | The discarded-rewrite ratio and the two hallucination probabilities, recorded | `web/backend/app/services/polish/worker.py`, `asr/lifecycle.py`, `session/metrics.py` |
| Raw-transcript toggle | See the segments under a polished stretch without turning the pass off | `templates/partials/transcript/toolbar.html`, `components/transcript-pane.js` |
| Rendered-page accessibility tests | Labels, accessible names, headings, `lang`, `aria-live`, over real rendered HTML | `tests/frontend/test_rendered_accessibility.py` |
| Contrast checker | WCAG ratios over the CSS custom properties, no dependency | `tests/frontend/test_token_contrast.py` |
| Race evidence or fix | A reproduction with a full traceback, and the fix it names — or the evidence that it would not reproduce | `tests/api/test_session_toggle.py` |
| An honest Part 4 | Owed work separated from decisions already taken | `docs/checklist.md` |

---

## 5. What Changed From the Plan

*Filled in as the work lands. Every departure from the above gets a line here, with the reason.*
