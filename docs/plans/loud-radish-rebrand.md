# Loud Radish — The Rebrand

*Status: **Complete (8 / 8 steps)*** · Created and executed 2026-09-06

## 1. Introduction

This project has been called two things at once for its whole life: `TranscriberPrototype` on disk
and in the documentation, and **Live Seminar Transcriber** in every string a person actually reads.
Neither is a name anyone chose to keep. This plan replaces both with one name — **Loud Radish** —
carried by the descriptor **Live Audio & Video Transcriber**, which is a truer description of what
the application became than "seminar" ever was: it records windows and video now, not only talks in
rooms.

The rebrand goes all the way down. User-visible strings change, and so do the machine identifiers
the application writes into the operating system: the configuration filename, three environment
variables, the OS credential-store service, the systemd unit, the desktop entry, the KDE global
shortcut component, the PipeWire tap sink prefix, and the control script's own filename. Because
those identifiers name *state that already exists on the developer's machine*, every one of them
ships with a migration path — a legacy value the code still recognises, adopts, and then stops
using. A rebrand that silently orphaned a keyring entry or a config file would be indistinguishable
from data loss.

The name lives in exactly one module afterwards. Today "Live Seminar Transcriber" is typed out in
eleven files; after this it is a constant, and the eleven files ask for it.

## 2. Gaps & Unanswered Questions

- **Common noun versus product name.** Much of the codebase uses the word "transcriber" as an
  ordinary English noun — "what a broken transcriber looks like", "the transcriber will never catch
  up". *Assumption*: these stay exactly as they are. Only the **product name** is being replaced. A
  blind find-and-replace across the repository would corrupt roughly forty comments and docstrings
  into nonsense, so every step below names its files rather than a pattern.
- **Repository folder name.** *Answered by the user*: the checkout stays at
  `/home/bdgr/Projects/TranscriberPrototype`. Documentation stops saying `TranscriberPrototype` as a
  product name, but no directory is moved and no git remote is renamed. The folder can be moved by
  hand later; nothing in the code depends on its name.
- **Already-exported bundles.** Web-app exports written before this change use the `localStorage`
  key prefix `transcriber-export:`. *Assumption*: they are left alone. Each export is a
  self-contained folder with its own copy of the scripts, it is never upgraded in place, and the key
  is per-export view state (a scroll position, a chosen tab), not content. New exports use the new
  prefix; old ones keep working with theirs.
- **Registered global shortcuts.** KDE stores accelerators under a component name, and this plan
  changes that component. *Assumption*: the installer unregisters the old `transcriber` component
  when it registers the new one, so key bindings survive. A shortcut the user wired up **by hand**
  to `utils/transcriber_ctl.py` will break, because that file is renamed; `docs/workflow.md` gains a
  line saying so.
- **`uv run pytest` does not finish on this machine.** `tests/assistant/test_llm_live.py` skips
  itself when nothing is listening on `LLM_TEST_ENDPOINT` (default `http://localhost:9090/v1`) — but
  a server *is* listening here, so the tests run for real and
  `test_a_real_answer_streams_back_as_content` waits on a full model generation with no timeout. It
  is unrelated to this plan and predates it. *Assumption*: every step below reads "`uv run pytest`"
  as `uv run pytest --ignore=tests/assistant/test_llm_live.py`, which is 1632 passed and 4 skipped in
  about 82 seconds. Giving the live tests a timeout is separate work; it belongs in
  `docs/checklist.md`, not in a rebrand.
- **Visual identity.** *Answered by the user*: `docs/radish.svg` is the logo — an angry radish
  shouting through a megaphone, which is where the name comes from. It carries five colours:
  `#C22D4C` crimson, `#598F3B` leaf, `#F6F2E8` cream, `#D9868A` blush, and `#080808` ink.
  *Assumption*: the interface is **not** repainted around it. The application's accent is currently
  teal `#6fb0a6`, and that same token is aliased as `--success`; making crimson the success colour
  would be actively wrong. The logo's palette is added as separate `--brand-*` tokens used by the
  mark itself, and `--accent` is left alone. Recolouring the whole interface is a design project,
  not a rename.
- **Python distribution name.** `pyproject.toml` declares `transcriber-prototype`. Nothing is
  published to an index and nothing imports the project by distribution name. *Assumption*: rename
  it to `loud-radish` and re-lock; the lockfile change is expected to be a single line.

## 3. Hierarchical Step-by-Step Instructions

### Step 1 — Clear the desk

- **Locations**: `tests/conftest.py` (uncommitted); the stale worktree at
  `.claude/worktrees/coding-session-process-efcceb` and its branch
  `claude/coding-session-process-efcceb`; a new feature branch `rebrand-loud-radish`.
- **Rationale**: an unrelated test fix is sitting uncommitted in the working tree — the PipeWire
  graph doubles that stop four `test_window_audio.py` tests from depending on what the developer
  happens to be playing. It must land as its own commit, or it will be buried inside a rebrand diff
  where nobody will ever find it again. The abandoned worktree violates the cleanup rule in
  `docs/skills/global-project-rules/SKILL.md` §8 and must be pruned. Only then is there a clean base
  for the rename, on a branch, because §9 forbids committing directly to `main`.
- **Validation**: `uv run pytest` passes before the conftest commit is made, proving the fix is
  sound rather than assumed; `git worktree list` shows one entry afterwards.
- **Docs updated**: none.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit (do not push) stating: `Loud Radish Rebrand (1 / 8) Complete: landed the PipeWire test
  doubles, pruned the stale worktree, and branched for the rename.`

### Step 2 — One place for the name

- **Locations**: new `web/backend/app/branding.py`, holding `APP_NAME` ("Loud Radish"),
  `APP_TAGLINE` ("Live Audio & Video Transcriber"), `APP_TITLE` (the two joined by an em dash),
  `APP_SLUG` ("loud-radish"), `APP_DESCRIPTION`, and the identifier constants Steps 4 and 5 consume
  — `CONFIG_FILENAME`, `KEYRING_SERVICE`, `TAP_SINK_PREFIX`, `SHORTCUT_COMPONENT`, `SERVICE_UNIT`,
  `DESKTOP_ENTRY`, `EXPORT_STORAGE_PREFIX`, `ENV_PREFIX` — each paired with its `LEGACY_*`
  counterpart. `web/backend/app/main.py` (`APP_TITLE`) becomes its first consumer.
- **Rationale**: the reason this rebrand touches eleven files is that the last one was never
  centralised. Introducing the module *before* changing anything means every later step is a
  deletion of a hardcoded string rather than a substitution of one for another, and a third rename
  would be a one-line change. Putting the legacy values here too keeps the whole migration surface
  readable in one screen instead of scattered across six subsystems.
- **Validation**: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`. New test
  `tests/utils/test_branding.py` asserts `APP_TITLE` composes from `APP_NAME` and `APP_TAGLINE`, and
  that every `LEGACY_*` constant differs from its current counterpart.
- **Docs updated**: `docs/structure.md` (the new module), `docs/documentation.md` (Decision
  **D-038** — the rebrand, why the name is centralised, and the rule that legacy identifiers are
  recognised but never written).
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit (do not push) stating: `Loud Radish Rebrand (2 / 8) Complete: the product name and every
  brand-derived identifier now live in one module.`

### Step 3 — Everything a person reads

- **Locations**: `web/frontend/templates/base.html` (the `title` block and the `description` meta),
  `web/frontend/templates/pages/sessions.html` (title block),
  `web/frontend/templates/partials/transcript/empty_state.html` (prose),
  `web/backend/app/main.py` (FastAPI title), `app.py` (module docstring, the startup banner around
  line 310, the `argparse` description), `web/backend/app/companion/menu.py` (the disabled status
  row), `web/backend/app/companion/visual_states.py` (the idle caption),
  `web/backend/app/companion/shortcuts.py` (`COMPONENT_LABEL`, the "Open the transcriber" action
  label), `web/backend/app/companion/main.py` (`argparse` prog),
  `web/backend/app/services/export/webapp.py` (the exported bundle's README line),
  `scripts/install_autostart.py` (unit `Description=` and the printed guidance), and
  `scripts/transcriber.desktop.in` (`Name=`, `Comment=`, `Keywords=`).
- **Rationale**: these are the strings that make the application *feel* renamed, and they carry no
  migration risk whatsoever — nothing keys off them. Landing them as one commit separates the
  reversible cosmetic change from the irreversible identifier change in Step 4, so a bisect can tell
  the two apart. Where a template or a menu currently hardcodes the title, it takes it from
  `branding.py` instead; templates receive it through the existing template globals in
  `web/backend/app/main.py`.
- **Validation**: `uv run pytest`; `uv run ruff check .`; and a manual browser pass at
  `http://127.0.0.1:8395` confirming the tab title, the sessions page title, and the empty state
  read "Loud Radish". Regenerate `web/shared/contracts/openapi.json` with
  `uv run scripts/generate_contracts.py` rather than hand-editing its `title` field.
- **Docs updated**: `docs/component-map.md` (the templates that now consume the branding global),
  `docs/api-contract.md` if the regenerated spec's title is quoted there.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit (do not push) stating: `Loud Radish Rebrand (3 / 8) Complete: every user-visible string now
  reads Loud Radish — Live Audio & Video Transcriber.`

### Step 4 — The identifiers that name existing state, with their migrations

- **Locations**:
  - `web/backend/app/config/store.py` — `DEFAULT_CONFIG_FILENAME` becomes `loud-radish-config.json`;
    the resolver reads `LOUD_RADISH_CONFIG_PATH`, falls back to `TRANSCRIBER_CONFIG_PATH` with a
    warning, and — when neither is set and no new-named file exists but `transcriber-config.json`
    does — adopts the old file by renaming it once, logging what it did.
  - `web/backend/app/config/credentials.py` — `SERVICE_NAME` becomes `loud-radish`; a read that
    misses falls back to the legacy service, and on a hit re-writes the secret under the new service
    and deletes the old entry. Writes only ever target the new service.
  - `web/backend/app/services/audio/tap.py` — `TAP_SINK_PREFIX` becomes `loud-radish-tap`, and
    `LEGACY_SINK_NAME` grows into a tuple of legacy prefixes that still includes `transcriber-tap`,
    so the leaked-sink sweep documented in that module keeps removing sinks left by older builds.
  - `web/backend/app/services/asr/acceleration.py` — `REPAIR_OFF` becomes
    `LOUD_RADISH_NO_GPU_REPAIR`, honouring the old variable as well.
  - `web/backend/app/companion/main.py` — the root override becomes `LOUD_RADISH_ROOT`, with the
    same fallback.
  - `web/backend/app/companion/shortcuts.py` — `COMPONENT` becomes `loud-radish`; registration
    unregisters the legacy `transcriber` component first so bindings are not duplicated.
  - `web/backend/app/services/capture/portal.py` — the request-token prefix becomes `loud_radish_`.
    No migration: the token is per-request and lives for seconds.
  - `web/backend/app/services/export/template/util.js` — the `localStorage` prefix becomes
    `loud-radish-export:`. No migration, for the reason recorded in §2.
  - `.env.example` — the three renamed variables, with the old names noted as deprecated.
- **Rationale**: this is the only step that can lose something. Each identifier names a thing that
  already exists outside the repository — a file, a secret in the login keyring, a systemd-adjacent
  registration, a PipeWire node — and renaming a constant without teaching the code to recognise the
  old value is how a working install turns into a blank configuration and a lost API key. Doing all
  of them in one commit is deliberate: the migration policy is a single idea, and reviewing it in
  one place is the only way to check it is applied consistently.
- **Validation**: `uv run pytest` plus a new `tests/utils/test_brand_migration.py` covering four
  cases with a temporary data directory and a fake keyring — a legacy config file is adopted and
  renamed; a legacy environment variable is honoured and warned about; a legacy credential is read,
  re-homed, and removed; and a legacy tap sink name is still recognised by the sweep. Manual check:
  start the application and confirm the existing `data/transcriber-config.json` is adopted with all
  settings intact, and that the configured LLM credential still resolves.
- **Docs updated**: `docs/workflow.md` (the renamed environment variables and the deprecation note),
  `docs/documentation.md` (D-038 extended with the migration policy), `docs/structure.md` (the
  `data/` entry naming the new config file), `docs/data-flow.md` if it names the config path.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit (do not push) stating: `Loud Radish Rebrand (4 / 8) Complete: configuration, credentials,
  shortcuts, the audio tap, and three environment variables carry the new name and adopt the old.`

### Step 5 — The files and the package

> **Deviation as executed.** The control script and its test were renamed in **Step 4**, not here.
> `branding.CONTROL_SCRIPT` already named the new path, so committing the two apart would have
> shipped a printed shortcut command pointing at a file that did not exist yet. Step 5 as committed
> covers the desktop entry, the systemd unit, the distribution name and the launch config.

- **Locations**: `utils/transcriber_ctl.py` → `utils/loud_radish_ctl.py` (with its `argparse` prog
  becoming `loud-radish-ctl` and its usage docstring updated);
  `tests/utils/test_transcriber_ctl.py` → `tests/utils/test_loud_radish_ctl.py` and its import;
  `scripts/transcriber.desktop.in` → `scripts/loud-radish.desktop.in`;
  `scripts/install_autostart.py` — `UNIT_NAME` becomes `loud-radish.service`, install first stops
  and removes a stale `transcriber.service`, and `--remove` handles both names;
  `web/backend/app/companion/shortcuts.py` and `web/backend/app/companion/main.py` — the paths they
  build to invoke the control script; `pyproject.toml` — the distribution name becomes `loud-radish`,
  followed by `uv lock`; `.claude/launch.json` — the configuration name.
- **Rationale**: renaming a file is the one part of this that git records as a move rather than a
  diff, so it is kept out of Step 4 where the content changes need reading line by line. The systemd
  unit is the sharp edge: leaving the old unit enabled alongside a new one would start the server
  twice on the same port, so the installer removes it rather than assuming the user will.
- **Validation**: `uv run pytest`; `uv run ruff check .`; `uv sync` succeeds against the re-locked
  file; `uv run utils/loud_radish_ctl.py status` answers; `uv run scripts/install_autostart.py
  --remove` followed by an install and `systemctl --user status loud-radish` confirms the unit is
  the only one present.
- **Docs updated**: `docs/structure.md` (both renamed files, in the tree and the tables),
  `docs/workflow.md` (every command naming the control script or the unit), `docs/deployment.md`
  (the unit and desktop entry names).
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit (do not push) stating: `Loud Radish Rebrand (5 / 8) Complete: the control script, desktop
  entry, systemd unit, and Python distribution renamed, with the old unit removed on install.`

### Step 6 — The documentation, and the guard that keeps it true

- **Locations**: `README.md` (title, opening paragraph, quick start); `docs/documentation.md`
  (heading, Purpose, and the D-038 entry finalised); `docs/structure.md`, `docs/workflow.md`,
  `docs/checklist.md`, `docs/architecture.md`, `docs/routes.md`, `docs/component-map.md`,
  `docs/deployment.md`, `docs/design-system.md`, `docs/api-contract.md`; the three skill sources
  under `docs/skills/*/SKILL.md` and `docs/skills/repository-structure/SETUP.md`; the three pointer
  sets `.claude/skills/*/SKILL.md`, `.agents/skills/*/SKILL.md`, and
  `.cursor/rules/global-project-rules.mdc`, which §10 of the global rules requires to move together;
  `docs/plans/README.md` (this plan added to the index). Historical plans under `docs/plans/` are
  **not** rewritten — they are a record of what was decided when, and
  `live-seminar-transcriber.md` keeps its name and its sixteen mentions.
  New `tests/utils/test_no_legacy_brand.py`.
- **Rationale**: documentation is the source of truth in this repository, and a rebrand that leaves
  it saying `TranscriberPrototype` has not happened. The guard test is what stops this decaying: it
  asserts that the old product name and the old identifier strings appear nowhere in `web/`,
  `utils/`, `scripts/`, or `app.py` **except** on the `LEGACY_*` constants in `branding.py` and the
  modules that consume them, which it allows by name. Without it, the next feature to hardcode
  "Live Seminar Transcriber" will do so unnoticed.
- **Validation**: `uv run pytest` including the new guard; `uv run ruff check .`; a manual read of
  `README.md` and `docs/documentation.md` top to bottom, because a guard test cannot tell whether a
  sentence still makes sense after a name has been swapped into it.
- **Docs updated**: all of the above, plus `docs/checklist.md` recording the rebrand as done and the
  visual-identity question from §2 as open.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit (do not push) stating: `Loud Radish Rebrand (6 / 8) Complete: documentation, skills, and
  agent pointers renamed, with a test that keeps the old name from creeping back.`

### Step 7 — The mark

- **Locations**: `docs/radish.svg` moves to `web/frontend/static/brand/radish.svg` — it is an asset
  the application serves, and `docs/` holds documentation, not application assets; a `<link
  rel="icon">` in `web/frontend/templates/base.html`; five `--brand-*` custom properties in
  `web/frontend/static/css/tokens.css`; the mark placed beside the title in
  `web/frontend/templates/partials/header.html` and in the exported bundle's shell at
  `web/backend/app/services/export/template/index.html`, with `web/backend/app/services/export/webapp.py`
  copying it into the export; `README.md` opening with it; `scripts/loud-radish.desktop.in` pointing
  `Icon=` at an installed copy rather than the stock `audio-input-microphone`.
- **Rationale**: the application currently ships no favicon at all, so every browser tab shows a
  blank glyph — with the app and the sessions page usually both open, they are indistinguishable.
  The logo fixes that and is the one thing that makes the rename feel like it happened. It goes into
  the export too, because an exported recording is the artefact that leaves this machine and is the
  only place the name is seen by anyone else. The file keeps its C2PA metadata block: it is
  provenance, it costs 7.7 KB, and stripping it to save bytes over a loopback connection would be a
  poor trade.
- **Validation**: `uv run pytest` (the static-mount route test, and the export test asserting the
  bundle contains the mark); a manual browser pass confirming the tab icon renders on both pages and
  the header mark sits correctly at the dark theme's contrast; an export opened from `file://`
  showing the mark without a network request.
- **Docs updated**: `docs/design-system.md` (the mark, the `--brand-*` tokens, and the explicit note
  that `--accent` is unchanged), `docs/structure.md` (the new `static/brand/` directory),
  `docs/component-map.md` (the header partial).
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit (do not push) stating: `Loud Radish Rebrand (7 / 8) Complete: the radish mark ships in the
  browser tab, the header, the README, the desktop entry, and every export.`

### Step 8 — Prove it end to end, then merge

- **Locations**: whole repository; the branch `rebrand-loud-radish` merged into `main`.
- **Rationale**: every prior step validated its own slice. This one asks the question none of them
  can: does a real install, with real existing state, still work after the rename? The migration
  paths in Step 4 are only meaningfully tested against the developer's actual
  `data/transcriber-config.json` and actual keyring entry, once, in the order a user would hit them.
- **Validation**: `uv run ruff check .`; `uv run ruff format --check .`; `uv run pytest`;
  `uv run scripts/generate_contracts.py` leaves no diff; `uv run app.py` starts, adopts the existing
  configuration, and serves the interface; a short live-mode recording is started and stopped and
  produces a transcript; the settings modal reports the new config path; an export is produced and
  opened from `file://`.
- **Docs updated**: `docs/checklist.md` — mark the rebrand complete; this plan's status header set to
  **Complete (8 / 8)** and its row in `docs/plans/README.md` updated.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit (do not push) stating: `Loud Radish Rebrand (8 / 8) Complete: full-suite and live
  verification passed, and the rebrand merged to main.`

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Branding module | The single source of the product name, its tagline, and every brand-derived identifier, each paired with its legacy counterpart | `web/backend/app/branding.py` |
| Config-file migration | Adopts `data/transcriber-config.json` by renaming it; honours the deprecated environment variable | `web/backend/app/config/store.py` |
| Credential migration | Reads through to the legacy keyring service, re-homes the secret, deletes the old entry | `web/backend/app/config/credentials.py` |
| Legacy tap sweep | Still recognises and removes `transcriber-tap` sinks leaked by older builds | `web/backend/app/services/audio/tap.py` |
| Shortcut re-registration | Registers under `loud-radish` and unregisters the legacy `transcriber` component | `web/backend/app/companion/shortcuts.py` |
| Renamed control script | The out-of-browser control CLI, under its new name and prog | `utils/loud_radish_ctl.py` |
| Renamed desktop entry | Desktop file template carrying the new name, comment, and icon | `scripts/loud-radish.desktop.in` |
| Autostart installer | Installs `loud-radish.service` and removes a stale `transcriber.service` | `scripts/install_autostart.py` |
| Application mark | The radish logo, serving as favicon, header mark, README hero, desktop icon, and export branding | `web/frontend/static/brand/radish.svg` |
| Brand tokens | The logo's five colours as `--brand-*` custom properties; `--accent` deliberately unchanged | `web/frontend/static/css/tokens.css` |
| Branding unit tests | Title composition, and that no legacy constant equals its replacement | `tests/utils/test_branding.py` |
| Migration tests | Four cases over a temporary data directory and a fake keyring: config adoption, deprecated env var, credential re-homing, legacy sink sweep | `tests/utils/test_brand_migration.py` |
| Regression guard | Asserts the old product name and old identifiers appear nowhere outside the named legacy-compat sites | `tests/utils/test_no_legacy_brand.py` |
| Renamed CLI tests | The control-script tests, following their subject | `tests/utils/test_loud_radish_ctl.py` |
| Regenerated contract | OpenAPI spec carrying the new title, produced rather than edited | `web/shared/contracts/openapi.json` |
| Decision record | **D-038** — the rebrand, why the name is centralised, and the recognise-but-never-write migration policy | `docs/documentation.md` |


## 5. What Was Actually Verified

Recorded here rather than implied, because the difference matters to whoever picks this up next.

**Proven on this machine, end to end.** The application starts, the banner reads *Loud Radish — Live
Audio & Video Transcriber*, and the log line is `Configuration resolved from
data/loud-radish-config.json` — **the config migration ran for real**: the user's actual settings
(Samson GoMic, `faster-whisper` `base`/`int8`, a local LLM on `localhost:9090`) survived the rename,
and `data/transcriber-config.json` is gone rather than duplicated. A live session was started,
transcribed real speech through `faster-whisper` on ROCm, and stopped in 7 ms with 4 segments and 29
words; the transcript persisted, appeared at the top of the Recordings page, and exported as
Markdown. A real 129 MB web-app export was built and unpacked: 15 files, the mark intact at 75 338
bytes, `README.txt` reading "exported from Loud Radish — Live Audio & Video Transcriber", and the
`localStorage` prefix changed. `uv run scripts/generate_contracts.py` leaves no diff.

**Proven by test, not by observation.** The keyring move, the systemd unit removal, and the shortcut
re-registration. None of the three was installed on this machine — no unit, no desktop entry, no
registered component, and no credential under either service — so there was nothing here to migrate.
This is carried in `docs/checklist.md` as open rather than reported as done.

**Not verified: the exported page opened from `file://`.** The browser tooling available refused to
navigate to a `file://` URL, so the export's markup and stylesheet were read directly instead. The
reference-integrity test already fails if the page names a file the archive lacks.

**Two pre-existing faults found and recorded, not fixed** — both in `docs/checklist.md`:
`tests/assistant/test_llm_live.py` hangs the suite against the live relay on this machine (every run
here used `--ignore`), and `test_the_container_is_raw` passes or fails depending on what is playing
through the speakers.
