# Recordings and Main Screen Redesign: From the Labs to the Real Pages

*Written 2026-11-07. Status: **In progress (6 / 9)**. Source of truth for the target design: the
two labs, `/labs/sessions/` and `/labs/main/` (D-082), at commit `e76a389` on branch
`labs/sessions-cards`, and the owner's three rounds of exported feedback quoted below. This plan
takes decisions **D-083 to D-086**. `feature/mixed-audio-steady-video` already holds D-080 and
D-081, and the labs hold D-082. If any of D-083 to D-086 are taken by the time this lands,
renumber here.*

## 1. Introduction

The owner judged the Recordings page and the main screen wrong in shape. The Recordings page has
rows that read poorly, an "Open…" window that is mostly blank space, a header that takes a third
of the screen, titles that all say "Session on <date>", and dates that look hours off. The main
screen is the right set of parts in the wrong arrangement. Rather than guess at a fix, we built
two labs. `/labs/sessions/` shows the owner's real recordings as cards in six layouts.
`/labs/main/` draws the main screen in four arrangements. Each lab has a panel of options and a
feedback export. The owner went through three rounds and settled on a design for each page. This
plan builds those designs into the real pages, then deletes the labs.

The work is in four groups:

1. **Shared foundation (Step 1).** Radish becomes the accent, but only the accent: surfaces stay
   graphite. Corners get rounder, panels get raised, and the icon set is extended. The icon box is
   also fixed, which is what left the record button's dot off centre.
2. **Backend the cards need (Steps 2–3).** Frames from a video, named downloads, opening a
   folder, and short titles the language model writes once a transcript is finished.
3. **The Recordings page rebuilt (Step 4).**
4. **The main screen rebuilt (Steps 5–8).** One step each for the header, the transcript pane,
   a new details column, and the assistant.

Step 9 removes the labs. Each step is independently verifiable and committable. Step 4 and Steps
5–8 can be built in either order, but Steps 5–8 must start after
`feature/mixed-audio-steady-video` is merged, because both change the header and `main.js`.

### The target, as the owner chose it

**Recordings page** (round one, "My Actual Preference", plus a note; built on in round two):

- **Wide cards**: picture on the left, words on the right.
  - Small (`--card-min` 200px, so a wide card is at least about 380px).
  - Airy spacing (24px gap, 16px padding).
  - Grouped by day, under a heading per day.
- **Header:** one slim bar, sticky. It holds the title and count, search, the filter chips
  (All, Video, Text only, To transcribe, Empty), and sort.
- **Title:** a short title written by the model (Step 3). Until one exists, the transcript's
  keywords are shown, marked with a spark icon. The date uses the smart format ("Today · 12:11
  PM", "Mon · 3:04 PM", "Sep 18 · 12:11 PM") and sits **on the picture**.
- **Picture:**
  - Video recordings use a filmstrip, drawn in wide cards as one large frame plus two small ones.
  - Moving the pointer across the picture skims through the video.
  - With no video, a colour for the time of day.
  - Media icons (video, audio, transcript) sit on the picture, with missing ones drawn faded.
- **Numbers:** length, words, and size on disk.
- **Buttons appear on hover in the card's bottom-right corner** (the owner's note). The quick
  ones are download transcript and open folder. Everything else is in a ⋯ menu, and the main
  button says "Bring to main screen".
- **Clicking a card opens a compact window.** It is a two-column window (video beside the main
  button and the numbers, what was said beside them), with download and file actions underneath.
  It replaces today's tall "Open…" window.
- **Look:** radish accent, raised cards, round corners (18px).

**Main screen** (round three, "PReferences", plus notes):

- **Arrangement:** classic, restyled. Header on top; panes edge to edge, divided by rules.
- **Header:**
  - The record button is a radish pill with a dot and the words "Start recording", vertically
    centred.
  - **Microphone level, speed, lag and connection become chips in the header.** The status bar
    goes.
- **A details column on the left of the transcript** (the owner's note). It holds the video
  monitor, "This recording" facts, the capture settings with a gear, and the glossary list.
- **Transcript:**
  - Serif, smaller (16.5px), timestamps in the left margin.
  - The toolbar uses icons only. The **text-size button is removed**, and the **glossary button
    carries its count in a badge**.
  - "Viewing a past recording" is a **card at the top of the transcript**, not a strip under the
    header.
- **Assistant:**
  - Your questions in bubbles, on a neutral surface.
  - **Quick questions are hidden behind an icon button beside the box, in a pop-up.**
  - **Ask is an icon**, not a word.
- **Look:** radish accent only, graphite surfaces (the owner rejected radish-tinted backgrounds
  in round two), raised, round.

## 2. Gaps & Unanswered Questions

- **Neither design has been formally approved.** Both labs now end with an approve / changes /
  not-yet decision, and no round has used it yet. *Assumption:* the owner's latest saved
  combination and notes are the target, as listed above. If another round changes them, edit the
  matching step before building it.
- **Wide cards were rated "It's OK", not "Love it."** *Assumption:* the owner saved wide cards
  as "My Actual Preference", so they are the choice.
- **"Picture shape: 3:2" does nothing in wide cards**, because the picture stretches to the
  card's height. *Assumption:* drop the option. The picture is 40% of the card's width and as tall
  as the card.
- **Radish as the application's accent collides with two existing tokens.**
  - `--success` is defined as `var(--accent)` in `tokens.css`, so "healthy" would turn red.
    *Assumption:* un-alias it. `--success` keeps today's teal `#6fb0a6`; `--accent` becomes
    radish `#e8687f`.
  - `--danger` (`#d9564b`) is close to radish. *Assumption:* keep it, and give every destructive
    control a label and the trash icon so it never depends on colour alone. Record stays radish,
    which is conventional.
  - *Human intervention is needed to answer this question* only if the owner wants a visibly
    different danger colour.
- **Where the text size goes once its button is removed.** The size is user-adjustable today
  (FE §13, `--transcript-size`). *Assumption:*
  - It moves to Settings, as a "Transcript" group on an existing tab (Context or Storage,
    whichever `settings/nav.html` places nearer reading).
  - Ctrl + / Ctrl − keep working as shortcuts.
  - The default becomes 16.5px, the owner's "Smaller".
- **The status bar holds more than the lab drew.** It shows the source label, level meter,
  voice-activity dot, speed (realtime factor), latency, the suppressed-hallucination count, the
  connection, and health. *Assumption:* all of them become header chips. Suppressed shows only
  when it is above zero. Health warnings keep using the existing banners
  (`partials/banners.html`), so nothing is lost by removing the bar.
- **Automatic titles use the language model, and the GPU is shared.** A local model and the
  speech model on one iGPU caused the 2026-09-18 crashes (D-071 to D-074). *Assumption:*
  - A title is requested **once**, after a transcript is complete: when a live session stops, or
    when a post-capture pass finishes. It is queued through the GPU arbiter in `services/gpu/`
    (D-072).
  - If no model is configured, nothing is stored, and the card shows keywords.
  - Naming *older* recordings is a separate **approval-gated** action ("Name untitled
    recordings…"), consistent with the rule that nothing is processed without the owner's
    approval.
- **The "dates are off" report is already fixed in code.** `archive._default_title` builds the
  "Session on …" title from `started_at`, and it has used local time since D-076. The owner's
  instance on :8395 is a process from before that change. No step is needed, but Step 4 shows
  dates from `started_at` directly, as the lab does.
- **Merge order.** `feature/mixed-audio-steady-video` changes `partials/header.html`,
  `css/components/header.css`, `js/components/header.js`, `js/main.js`, `templates/base.html` and
  adds a sound panel. *Assumption:* it merges to `main` first (its plan is complete). This plan's
  branch is cut from `main` after that merge, and Steps 5–8 start from the merged header.
- **Narrow windows.** The details column needs room. *Assumption:*
  - It is 300px wide, slimming to 250px below 1280px.
  - Below 1060px it leaves the grid and becomes a "Details" tab beside Transcript and Assistant
    in the existing narrow-window tab bar (`data-pane-tabs` in `pages/app.html`), replacing today's
    "Monitor" tab.

## 3. Hierarchical Step-by-Step Instructions

Work on a feature branch, `feature/redesign-from-labs`, cut from `main` after
`feature/mixed-audio-steady-video` and `labs/sessions-cards` have both merged. The labs stay
available until Step 9 so every step can be compared against them side by side. Every step runs:

- `uv run pytest`
- `uv run ruff check web/backend tests` and `uv run ruff format --check` on the files it touched
- browser QA on an isolated server: port 8411, mock ASR, a copy of real data — see
  `docs/workflow.md` and the "Isolated QA server" note. Never on the owner's :8395.

#### Step 1: The shared look — accent, corners, surfaces, icons

- **Locations**:
  - `web/frontend/static/css/tokens.css`:
    - `--accent` and its washes become radish, with the values from
      `static/css/labs/theme.css`, `.lab[data-accent="radish"]`.
    - `--success` gets its own teal value instead of `var(--accent)`.
    - New tokens: `--accent-line`, `--on-accent`, `--radius-card: 18px`, `--radius-control: 10px`,
      and `--shadow-raised`, taken from the lab's raised-card shadow.
  - `web/frontend/static/css/components/buttons.css`:
    - `.button--primary` uses `--accent` and `--on-accent`.
    - A new `.icon` rule: inline-flex, `line-height: 0`, with a block SVG. This is the lab's
      `.lab-icon` fix.
  - `web/frontend/templates/macros/icons.html`: add the icons the designs use, ported from
    `static/js/labs/icons.js`: video, audio, transcript, main, folder, download, webapp, more,
    trash, spark, book, record, mic, shield, grid, send.
  - A new `web/frontend/static/js/core/icons.js`: the same set for components that draw from
    JavaScript. It is `labs/icons.js` moved, so both pages share one source.
- **Rationale**: Every later step draws with these, and the accent change must land once,
  everywhere, rather than page by page. The icon box fix is what left the record button's dot
  1.5px high (measured in round three).
- **Docs**:
  - `docs/design-system.md`: palette, radii, elevation, and the icon rule.
  - `docs/documentation.md`: **D-083**, radish is the accent only and surfaces stay graphite,
    with the rejected tinted background recorded.
- **Tests**:
  - `tests/frontend/test_token_contrast.py` gains pairs for `--on-accent` on `--accent` and for
    the accent as text on `--surface-panel`, both at 4.5:1 or better.
  - A test that `--success` is not the accent.
- *Action: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recordings and Main Screen Redesign (1 / 9) Complete: Radish is the
  accent and only the accent, with rounder raised panels and one shared icon set.*

> **Status: ✅ Complete.** Built on `feature/redesign-from-labs`, cut from `labs/sessions-cards`
> with `feature/mixed-audio-steady-video` merged in (rather than waiting on `main`, which serves the
> owner's live instance). Beyond the plan: the healthy indicators that read `--accent` directly
> (level meters, the live dot, a good realtime factor, the sound panel's meters, local-only privacy)
> now read `--success`; every `.button` takes `--radius-control`; Stop keeps the danger treatment
> over the radish fill; and `tests/frontend/test_shared_icons.py` keeps the macro and the module
> identical.

#### Step 2: What a card needs from the sessions API

- **Locations**:
  - `web/backend/app/routes/sessions.py` takes over the lab's routes from `routes/labs.py`, under
    the sessions prefix:
    - `GET /api/sessions/{key}/thumbnail?t=&w=`: the stored `preview.jpg`, or a frame by the
      second via `services/recording/frames.py:frame_jpeg`.
    - `GET /api/sessions/{key}/file/{video|audio}`: a named download.
    - `POST /api/sessions/{key}/reveal`: opens the folder through `_open_in_file_manager`.
    - `GET /api/sessions/glance?keys=`: `services/transcript/glance.py`.
    If `sessions.py` would pass 500 lines, put them in a new `routes/session_media.py` and
    register it in `routes/__init__.py` before `pages`.
  - `services/transcript/archive.py:SessionMedia`: add `preview: bool` (whether `preview.jpg`
    exists), so a card asks for a picture only when one exists.
  - `web/frontend/static/js/transport/api.js`: add `thumbnailUrl`, `fileUrl`, `revealFolder` and
    `glances`.
- **Rationale**: The cards and the details column (Step 7) both need these. Moving them out of
  `/api/labs/` now means Step 9 deletes only lab code. The `/api/labs/` routes stay as thin aliases
  until then.
- **Docs**:
  - `docs/routes.md`: the four routes move from "Labs" to "Past sessions".
  - `docs/api-contract.md`.
  - Regenerate the contracts with `uv run python scripts/generate_contracts.py`.
- **Tests**: `tests/api/test_labs.py` becomes `tests/api/test_session_media.py`, against the new
  paths, keeping the hostile-key, missing-video and no-file-manager cases.
- *Action: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recordings and Main Screen Redesign (2 / 9) Complete: Recording
  pictures, frames, named downloads and opening the folder are part of the sessions API.*

> **Status: ✅ Complete.** In a new `routes/session_media.py` (`sessions.py` is already 613
> lines), registered before `sessions` so `/api/sessions/glance` is not read as a key. The lab
> routes are hidden aliases of the new handlers. The lab's page tests moved into the same test file
> under a section Step 9 deletes.

#### Step 3: Short titles written by the model

- **Locations**:
  - A new `web/backend/app/services/transcript/titles.py`:
    - `suggest_title(store, llm)` asks for four to six words from the summaries, or from the
      opening of the transcript, and cleans the answer: no quotes, no trailing full stop, at most
      60 characters.
    - `keyword_title(glance)` is the fallback, which is not stored.
  - `services/transcript/store.py`: `TranscriptStore.set_title(title)` writes `session.title`.
    `metadata()` already reads it.
  - The prompt goes beside the others in `services/context/prompts.py`.
  - Where it runs:
    - After a live session stops, in `services/session/manager.py` beside the existing post-stop
      work.
    - After a pass finishes, in `services/recording/runner.py`'s completion path.
    - Both only when the title is empty and a model is configured.
    - Queued through the GPU arbiter in `services/gpu/`, never alongside the speech model.
  - Approval-gated backfill:
    - `GET /api/sessions/untitled/plan` returns how many and which model.
    - `POST /api/sessions/untitled/name` names them one at a time in the background, reporting
      progress over the existing job events.
  - Renaming: `PATCH /api/sessions/{key}` with `{title}`, so the owner can correct any title.
    This is used by the compact window in Step 4.
- **Rationale**: 316 recordings all titled "Session on <date>" cannot be told apart. The lab's
  keywords proved the idea and also showed its limits ("Operators · Commute"). The owner asked
  for a four-to-five-word title on transcription. Running it once, after the speech model is done,
  respects D-071 to D-074. Older recordings are named only when the owner says so.
- **Docs**:
  - `docs/documentation.md`: **D-084**, model-written titles, when they run, and why the backfill
    asks first.
  - `docs/data-flow.md`: the post-transcript title step.
  - `docs/routes.md`.
  - `docs/api-contract.md`.
- **Tests**:
  - `tests/assistant/test_titles.py` uses a scripted language-model client:
    - the cleanup rules;
    - empty and garbage answers stored as nothing;
    - an existing title never overwritten.
  - `tests/api/test_session_titles.py`:
    - the plan starts nothing;
    - rename round-trips;
    - the backfill names an untitled session.
- *Action: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recordings and Main Screen Redesign (3 / 9) Complete: A finished
  transcript is given a four-to-six-word title by the model, older ones are named on approval,
  and any title can be renamed.*

> **Status: ✅ Complete.** Two departures from the plan, both recorded in D-084: the trigger is a
> server-side hub listener on `session.stopped` and `transcription.done` rather than code inside
> `manager.py` and `runner.py`; and background naming also requires rolling summaries to be on, as
> the owner's existing consent for background model work. `suggest_title` takes the text rather than
> the store, so reading the database stays off the event loop. The routes are in
> `routes/session_titles.py`; the new events are `session.titled`, `titles.progress`, `titles.done`.

#### Step 4: The Recordings page, rebuilt as wide cards

- **Locations**:
  - `web/frontend/templates/pages/sessions.html` is rewritten to the lab's skeleton: slim sticky
    toolbar, list, compact-window host, transcribe consent. It keeps
    `partials/recording_window.html` for the web-app export's preset and progress faces.
  - `web/frontend/static/js/sessions.js` becomes the page's entry point only. Its work moves into
    a new `static/js/recordings/` folder, promoted from `static/js/labs/` with the option
    machinery removed and the owner's settings fixed in place:
    - `card.js`: the wide card, filmstrip as one large frame plus two small, skimming, time-of-day
      colour, icons on the picture, date on the picture, numbers, hover buttons bottom-right;
    - `describe.js`: smart dates and titles, using the stored title, then the keyword fallback;
    - `actions.js`;
    - `menu.js`;
    - `detail.js`: the compact window, with a rename field (Step 3) and "Export web app…" opening
      `RecordingWindow` on its choose face;
    - `toolbar.js`: search, filter chips, sort;
    - `glances.js`: batched fetching, including the eager first screenful.
  - `static/js/components/recording-window.js` loses its overview face, which the compact window
    replaces. It keeps choose and progress. `components/recording-overview.js` is deleted.
  - CSS: a new `static/css/components/recordings.css` holds the cards and toolbar, and
    `recording-detail.css` holds the compact window. Both are ported from `css/labs/` with the
    `.lab[data-*]` variants removed. The old `components/sessions.css` row styles are deleted.
- **Rationale**: This is the design the owner chose, built on the components every lab round
  already exercised against their real recordings. Fixing the settings in place removes about
  half the lab code: only the chosen variant of each option survives.
- **Docs**:
  - `docs/design-system.md` § Recordings: replace the rows and window description.
  - `docs/component-map.md`.
  - `docs/structure.md`.
  - `docs/documentation.md`: **D-085**, the Recordings page is cards, and D-077's single window
    becomes the compact window plus the export faces.
- **Tests**:
  - `tests/frontend/test_recording_window.py`: the overview tests become compact-window tests.
    Keep the scroll test (`.page` scrolls) and the focus tests.
  - A new `tests/frontend/test_recordings_page.py`:
    - every import resolves;
    - the toolbar has a labelled search and filter radiogroup;
    - each card action has an accessible name.
  - `tests/frontend/test_viewing_markup.py`: the `/sessions` dialog cases.
  - **Browser QA:**
    - group by day;
    - skim a video;
    - hover buttons at bottom-right;
    - ⋯ menu;
    - compact window;
    - bring to main screen;
    - transcribe on approval;
    - rename;
    - delete on a copy;
    - 375px width.
- *Action: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recordings and Main Screen Redesign (4 / 9) Complete: The Recordings
  page is wide cards grouped by day, with pictures, model titles, hover actions and a compact
  window in place of the old one.*

> **Status: ✅ Complete.** Built as planned, with these differences: the bar's heading is in the
> template (the page needs one server-rendered `h1`) and the rest is drawn; skimming moves the large
> frame of the filmstrip; buttons are always shown on a touch screen; the compact window also
> carries the conversation download and the transcript version choice the overview had; a
> recording that cannot be exported no longer opens the export window when it stops (it had only
> the overview to show); the picker's media-chip styles moved to `viewing.css` when
> `components/sessions.css` was deleted; and "Name *N* untitled…" in the bar is the backfill's
> approval (Step 3). Browser QA on the scratch copy: day groups, skimming, hover buttons
> bottom-right, the ⋯ menu, the compact window, export, transcribe approval (cancelled), rename,
> bring to main screen, delete, and 375px.

#### Step 5: The main screen's header absorbs the status bar

- **Locations**:
  - `web/frontend/templates/partials/header.html` and `static/js/components/header.js`:
    - The record pill uses Step 1's icon box and the radish accent.
    - A health group of chips: source and level meter, voice activity, realtime factor, latency,
      suppressed (only when above zero), connection. Each keeps its existing `data-*` hook from
      `status_bar.html`, so `static/js/components/status-bar.js` can render into the header
      without changing what it reads.
  - `partials/status_bar.html` is deleted, and so is its include in `pages/app.html`.
  - `static/css/components/header.css` takes the chip styles. `status-bar.css` is deleted.
  - `static/css/layout.css`: the `.app` column loses its last row.
- **Rationale**: The owner chose "chips in the header". One fewer bar gives the transcript the
  height back. Keeping the hooks means the event handling is untouched and only placement
  changes.
- **Docs**:
  - `docs/design-system.md` § header.
  - `docs/component-map.md`: the status bar is folded into the header.
- **Tests**:
  - `tests/frontend/test_rendered_accessibility.py` still passes: the level meter keeps
    `aria-label="Input level"`.
  - A test that every status-bar hook (`data-rtf`, `data-latency`, `data-level-meter`,
    `data-connection-dot`, `data-suppressed`, `data-vad-dot`) is present in the rendered header.
  - **Browser QA:**
    - a mock-ASR session updates every chip;
    - the record button's dot and words are centred (measure as in round three);
    - at 900px the header wraps instead of overflowing.
- *Action: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recordings and Main Screen Redesign (5 / 9) Complete: Level, speed,
  lag and connection are chips in the header, and the status bar is gone.*

> **Status: ✅ Complete.** The chips are a labelled group (`role="group"`, "Recording health") that
> keeps the `data-status-bar` hook, so `main.js` and `status-bar.js` are unchanged apart from a
> comment. The header now always wraps, and the model names are capped at 220px: a one-row rule
> above 1240px was tried and pushed Settings off the edge at 1440px once every chip was showing.
> The record button's label and dot are separate spans (`data-session-toggle-label`,
> `data-session-toggle-icon`); the dot shows only when idle. Measured: dot and label centred to the
> pixel; one row at 1440px idle, two with every chip, three at 900px, no overflow at any width.

#### Step 6: The transcript pane — margin timestamps, icon toolbar, viewing card

- **Locations**:
  - `partials/transcript/toolbar.html`:
    - icon-only buttons with `aria-label`s;
    - `data-text-size` removed;
    - the glossary toggle carries its count as a badge (`data-glossary-count` already exists;
      restyle it as `.badge` inside the button).
  - `static/js/components/transcript-pane.js`:
    - drop the text-size handler;
    - render timestamps into a margin column.
  - `static/css/components/transcript.css`:
    - the timestamp grid: a 64px right-aligned column, with the measure widened by 90px;
    - `--transcript-size` defaults to 16.5px in `tokens.css`.
  - The text size moves to Settings: a "Transcript" group in the chosen `settings/*.html` tab,
    wired in `components/settings/`. Ctrl +/− stay.
  - `partials/viewing_banner.html` moves inside `partials/transcript/pane.html`, above the
    scroller, styled as a card with a radish left edge.
  - `static/js/viewing.js` is unchanged, because it finds the banner by `data-viewing-banner`.
- **Rationale**: These are the owner's round-three picks and notes. The text size must not
  disappear, because FE §13 makes it the one size a reader adjusts, so it moves rather than
  going.
- **Docs**:
  - `docs/design-system.md` § transcript.
  - `docs/component-map.md`.
- **Tests**:
  - `tests/frontend/test_viewing_markup.py`: the banner is inside the transcript pane, still
    labelled and closable.
  - The settings form test for the new size control.
  - **Browser QA:**
    - long sessions scroll smoothly with margin timestamps;
    - "jump to live" still works;
    - the glossary badge updates as terms arrive.
- *Action: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recordings and Main Screen Redesign (6 / 9) Complete: The transcript
  reads with timestamps in the margin, an icon toolbar with a glossary count, and the viewing card
  at its top.*

> **Status: ✅ Complete.** Differences from the plan: **there was no Ctrl +/− shortcut** to keep —
> the size had only its two buttons — and none was added, because those keys are the browser's
> zoom; the size is a slider in Settings → Context → Reading, applied by `core/text-size.js` and
> stored by the browser (the field says so, since the dialog's header says settings are stored by
> the recorder). **A pre-existing bug was fixed:** the toolbar's glossary button carried
> `data-toggle-glossary`, which nothing read, and `main.js` passed `GlossaryPanel` the first
> `[data-glossary-count]` only, so the assistant's badge never counted; both buttons now drive the
> panel and both badges count. "Show raw" became an icon toggle whose name stays and whose
> `aria-pressed` changes. Margin timestamps are CSS only (`transcript-pane.js` was unchanged apart
> from that toggle). QA: a 363-segment recording with stamps in the margin, the viewing card in the
> pane, both badges at 4, the toolbar's button opening the glossary, and the slider applying and
> storing the size.

#### Step 7: A details column — video, recording, capture, glossary

- **Locations**:
  - `partials/monitor/pane.html` becomes `partials/details/pane.html`, a left column always
    present (not only in window mode). It holds:
    1. the video monitor: the live preview while recording a window, the viewed recording's
       picture (Step 2's thumbnail) when viewing one, otherwise an empty state;
    2. "This recording": started, length, words, questions;
    3. "Capture": source, when it transcribes, the model, and a gear that opens the record sheet;
    4. the glossary list, moved here from `partials/transcript/glossary_panel.html`. The
       transcript toolbar's glossary button scrolls to and highlights this section.
  - `static/js/components/recording-monitor.js` keeps the preview logic. A new
    `static/js/components/details-pane.js` renders sections 2 and 3 from the session store.
    `glossary-panel.js` renders into section 4.
  - `static/css/layout.css`:
    - `.app__main` becomes a three-column grid: details, transcript, assistant.
    - `--details-width: 300px`, 250px below 1280px.
    - Below 1060px the column leaves the grid and the tab bar's "Monitor" tab becomes "Details".
  - `pages/app.html`: the include and the tab.
- **Rationale**: The owner wants the video and the settings visible at once in their own column,
  with the assistant as the other sidebar. Today's monitor pane exists only in window mode and
  the glossary hides inside the transcript.
- **Docs**:
  - `docs/design-system.md` § layout.
  - `docs/component-map.md`: monitor pane becomes details pane.
  - `docs/structure.md`.
  - `docs/documentation.md`: **D-086**, the main screen has three columns, and why the details
    column folds into a tab below 1060px.
- **Tests**:
  - Accessibility: the column is a labelled `aside`, and the tab bar has three tabs with the right
    `aria-selected`.
  - The window-mode monitor tests in `tests/frontend/` move to the new pane.
  - **Browser QA:**
    - window mode shows the live preview;
    - microphone mode shows the empty state;
    - viewing a past recording shows its picture and facts;
    - glossary terms appear as they arrive;
    - 1440px, 1100px and 900px widths.
- *Action: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recordings and Main Screen Redesign (7 / 9) Complete: The video,
  the recording's facts, capture settings and the glossary sit in a column of their own.*

#### Step 8: The assistant — bubbles, quick questions in a pop-up, an icon Ask

- **Locations**:
  - `partials/chat/composer.html`:
    - a spark icon button to the left of the input, with `aria-haspopup="menu"`, opening the quick
      questions;
    - the send button becomes the send icon with `aria-label="Ask"`.
  - `partials/chat/quick_actions.html` becomes the pop-up's content, a `role="menu"` list with
    each action's shortcut hint. It is hidden until opened.
  - `static/js/components/chat-pane.js`:
    - open and close the menu (Escape and outside click close it, focus returns to the button);
    - choosing an item sends its prompt exactly as a chip does today;
    - ⌘1–⌘5 keep working without opening it.
  - `static/css/components/chat.css`: neutral bubbles (`--surface-control`, no accent wash), the
    pop-up, the 38px icon buttons.
  - The assistant header's glossary count uses the Step 6 badge.
- **Rationale**: These are the owner's round-three notes. The chips took a third of the pane's
  height, and the shortcuts already make them reachable without being on screen.
- **Docs**:
  - `docs/design-system.md` § assistant.
  - `docs/component-map.md`.
- **Tests**:
  - Accessibility: the menu button and Ask have names, and the menu items are `menuitem`s.
  - **Browser QA:**
    - open the menu with mouse and keyboard;
    - choose an item;
    - ⌘2 without opening it;
    - Escape closes and returns focus;
    - send by icon and by Enter.
- *Action: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recordings and Main Screen Redesign (8 / 9) Complete: Quick questions
  live in a pop-up beside an icon-only Ask, and questions sit in neutral bubbles.*

#### Step 9: Retire the labs

- **Locations**:
  - Delete:
    - `web/backend/app/routes/labs.py` and its registration in `routes/__init__.py`;
    - `web/frontend/templates/pages/labs/`;
    - `web/frontend/static/js/labs/`;
    - `web/frontend/static/css/labs/`;
    - `tests/frontend/test_labs_page.py`.
  - Keep `services/recording/frames.py` and `services/transcript/glance.py`, now used by Step 2.
  - Remove the temporary `labs-qa` entry from the main checkout's `.claude/launch.json` if it is
    still there.
- **Rationale**: D-082 says a lab is deleted, not kept beside the page it prototyped, once its
  design ships. Two copies of a design drift.
- **Docs**:
  - `docs/documentation.md`: D-082 marked done, pointing at D-083 to D-086.
  - `docs/routes.md`: the Labs section and lab pages removed.
  - `docs/structure.md`.
  - `docs/component-map.md`.
  - `docs/checklist.md`: Part 12 closed.
  - Regenerate the contracts.
- **Tests**:
  - The full suite.
  - A last browser pass over both pages against the screenshots in the labs' final round.
- *Action: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Recordings and Main Screen Redesign (9 / 9) Complete: The labs are
  removed now that both designs are the real pages.*

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Accent and shape tokens | Radish accent, separate teal `--success`, card and control radii, raised shadow | `web/frontend/static/css/tokens.css` |
| Shared icons | One icon set for templates and modules; the centred icon box | `templates/macros/icons.html`, `static/js/core/icons.js`, `static/css/components/buttons.css` |
| Session media routes | Thumbnail and frames, named downloads, open folder, glances | `web/backend/app/routes/sessions.py` (or `session_media.py`) |
| Model-written titles | Title after a transcript, approval-gated backfill, rename | `web/backend/app/services/transcript/titles.py`, `store.py:set_title`, `routes/sessions.py` |
| Recordings page | Wide cards grouped by day, compact window, slim toolbar | `templates/pages/sessions.html`, `static/js/recordings/`, `static/css/components/recordings.css`, `recording-detail.css` |
| Header with health chips | Status bar folded into the header | `partials/header.html`, `components/header.js`, `components/status-bar.js`, `header.css` |
| Transcript pane | Margin timestamps, icon toolbar with glossary badge, viewing card, size in Settings | `partials/transcript/`, `components/transcript-pane.js`, `transcript.css`, `partials/settings/` |
| Details column | Video, recording facts, capture, glossary in a left column | `partials/details/pane.html`, `components/details-pane.js`, `layout.css` |
| Assistant | Quick-question pop-up, icon Ask, neutral bubbles | `partials/chat/composer.html`, `quick_actions.html`, `components/chat-pane.js`, `chat.css` |
| Contrast tests | Accent, on-accent and success pairs at AA | `tests/frontend/test_token_contrast.py` |
| Media route tests | Hostile keys, missing video, no file manager, glances | `tests/api/test_session_media.py` |
| Title tests | Cleanup, never overwriting, plan starts nothing, rename, backfill | `tests/assistant/test_titles.py`, `tests/api/test_session_titles.py` |
| Recordings page tests | Imports resolve, labelled toolbar, named card actions, compact window | `tests/frontend/test_recordings_page.py`, `tests/frontend/test_recording_window.py` |
| Main screen markup tests | Health hooks in the header, banner in the transcript, details pane labelled, menu names | `tests/frontend/test_rendered_accessibility.py`, `tests/frontend/test_viewing_markup.py` |
| Labs removed | Lab routes, pages, modules, styles and tests deleted | `routes/labs.py`, `templates/pages/labs/`, `static/js/labs/`, `static/css/labs/` |
