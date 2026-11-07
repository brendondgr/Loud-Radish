# Component Map

*Last updated: 2026-11-07 (the record sheet replaces the mode switch — D-075)*

> **Status: implemented.** Update this file in the same change that adds, moves, or renames a
> component.

This file described React component ownership until Phase 14. It was written at initialization,
before Decision D-011 replaced the React/Vite/TypeScript assumption with Jinja2 templates and plain
ES modules — so it described a directory tree that never existed. What follows is the tree that does.

## The shape of the frontend

There is no build step and no bundler. The browser resolves the ES module graph itself, and the
server renders the page shell from Jinja2 partials. A change to a stylesheet or a module is live on
reload; there is nothing to compile and no second toolchain to keep in step with the first.

```text
web/frontend/
├── templates/          server-rendered structure — the shell, never the data
│   ├── base.html               <head>, stylesheet list, module entry point
│   ├── pages/                  one per URL: app.html, sessions.html, and labs/ —
│   │                           prototypes beside a real page, not instead of it (D-082)
│   ├── macros/icons.html       inline SVG, inheriting currentColor
│   └── partials/               one file per region of the interface
│       ├── header.html, banners.html, status_bar.html
│       ├── record_sheet.html   the record sheet: what to record, which input, when to
│       │                       transcribe, and video (D-075)
│       ├── recording_window.html  one window per recording (D-077): an overview of what it
│       │                       holds and every export, with the web-app export's preview and
│       │                       presets on one face and the pipeline's stages on another
│       ├── transcript/         pane, toolbar, hypothesis, empty state, glossary panel…
│       ├── chat/               pane, composer, quick actions, empty state
│       ├── details/            the details column: pane (video, this recording, capture),
│       │                       the monitor's empty state, and the glossary section (D-086)
│       └── settings/           modal, nav, and one file per tab, including
│                               rewriting.html — the two instruction editors (D-068)
└── static/
    ├── css/
    │   ├── tokens.css          every colour, size, and duration in the application
    │   ├── base.css            resets and element defaults
    │   ├── layout.css          the application frame and standalone pages
    │   ├── components/         one stylesheet per component
    │   └── labs/               theme (accent and corners, shared), then each lab's sheets
    │                           (D-082)
    └── js/
        ├── main.js             the application page's entry point
        ├── session-start.js    opening the record sheet and starting what it chose (D-075)
        ├── sessions.js         the Recordings page's entry point — groups the cards by day,
        │                       opens the compact window, runs actions, polls an export and
        │                       the title backfill (the page has no socket) (D-085)
        ├── recordings/         the Recordings page's parts: card.js (wide card, filmstrip,
        │                       skim, hover buttons), detail.js (the compact window, rename),
        │                       actions.js (one list of actions per recording), menu.js (⋯),
        │                       toolbar.js (search, filters, sort), describe.js (smart dates,
        │                       titles), glances.js (batched fetching)
        ├── viewing.js          a past recording on the main screen: banner, picker, approval
        │                       (D-076)
        ├── mixing.js           the server's word about the mix, routed into its store (D-081)
        ├── core/               DOM helpers, the event bus, formatting, preferences, modes,
        │                       export presets — the last two mirrored from the backend and
        │                       held identical by a test
        ├── transport/          HTTP client, WebSocket, event names
        ├── stores/             state: transcript, polish, session, mode, health, config, chat,
        │                       capture, export, mix
        ├── components/         one class per region, plus settings/ per tab
        ├── labs/               the labs (D-082): review.js (settings, verdicts, notes, the
        │                       decision) and the customize panel and export, shared; then
        │                       each lab's options as data and its renderers — sessions.js
        │                       for the recordings lab, main.js for the main screen
        └── a11y/               the focus trap
```

## Ownership rules

Where something lives is determined by **what it knows about**, not by what it looks like.

| Location | Holds | The test |
|---|---|---|
| `templates/pages/` | One file per URL | Is it addressable by a URL? |
| `templates/partials/` | Structure for one region | Does it render markup and no data? |
| `static/js/core/` | Helpers that know nothing about this application | Would it work unchanged in a different app? |
| `static/js/transport/` | Talking to the server | Does it touch `fetch` or `WebSocket`? |
| `static/js/stores/` | State, and the events announcing it changed | Does more than one component read it? |
| `static/js/components/` | One region of the interface | Does it own DOM nodes? |
| `static/css/components/` | Styles for one component | Does it match a component file by name? |

Three rules make the whole thing hold together, and each exists because breaking it caused a real
bug during the build:

**A component never talks to another component.** Stores publish on the bus; components subscribe.
The transcript pane does not know the chat pane exists, and neither knows about the socket. Where
one region must drive another — a citation scrolling the transcript, "ask about this" filling the
composer — the entry point passes a callback, which keeps the dependency one-way and visible in one
file.

**`core/` must never know what a transcript is.** Domain knowledge lives in stores and components.

**The backend owns configuration.** `stores/config.js` is a cache of what the server last said, not
a parallel notion of the truth: every write goes to the server and the response replaces the cache
wholesale, so a value the backend clamps shows up in the field immediately. The only state the
browser owns is genuinely its own — pane widths, text size, which pane is showing.

## Components

| Component | Owns | Notes |
|---|---|---|
| `Header` | The mark, record control, **the pause control**, clock, model and privacy identity | Stopping asks first — an accidental stop mid-talk cannot be undone. The mark is decorative (`alt=""`): the page title already carries the name, and announcing it again at the top of every view is noise. Hidden below 700 px, where the tab icon carries it instead. Pause is a *second* control, not a seventh meaning for the primary one (D-044) — hidden outside `recording` and `paused`, because there is nothing to explain about pausing a session that does not exist |
| `TranscriptPane` | Polished blocks, committed segments, the hypothesis tail, search highlighting, **the transcription pass's progress and its controls** | Three sibling regions, only the middle one a live region; polished minutes flow as continuous prose with inline timestamps; trims to 300 segments and 240 blocks. The pass's progress bar sits *outside* the empty state and sticks to the top of the scroller: a pass commits as it goes, so the pane stops being empty after its first window, which used to take the bar and its Pause button with it (D-045) |
| `ScrollController` | Follow-the-live-edge behaviour | Stops following the instant the user scrolls |
| `TranscriptSelection` | "Ask about this", copy-with-timestamp | Positions from the selection rectangle, reads the time from the enclosing segment |
| `ChatPane` | Conversation, quick actions, composer, streaming | Mutates the streaming message in place rather than re-rendering |
| `GlossaryPanel` | Terms, sorted by first appearance | Each term navigates the transcript. Takes every `[data-glossary-toggle]` and `[data-glossary-count]` — the transcript toolbar's and the assistant's — as lists; given one of each, the toolbar's button did nothing and its badge was the only one that counted (fixed in redesign Step 6) |
| `StatusBar` | Level, speech state, real-time factor, lag, discarded, source, connection — drawn as chips in the header (`[data-status-bar]`), no longer a bar | Never colour alone — every state carries an icon and a word. Moved into the header by redesign Step 5 with every hook kept, so the class is unchanged |
| `Banners` | The three error tiers | Never a modal; a modal during a talk covers the transcript |
| `SettingsModal` | The seven tabs, presets, save | The one modal in the application |
| `settings/*` | One tab each, plus `bindings.js` and `combobox.js` — the assistant's model field, a text input that is also a dropdown (D-067) | Controls declare a dotted config path; the binding layer does the rest. `shortcuts.js` adds key capture and reports on the *companion process*, since keys can be set and still do nothing |
| `RewritingSettings` | The two instruction editors, their Reset controls, and the line saying which text is in force | The one thing the binding layer cannot do: a blank field means the instructions that shipped, so the shipped text is the field's **placeholder** and never its value — seeding it would store a copy on the next change event and freeze this installation on today's wording. Reset writes `""` for the same reason (D-068) |
| `FocusTrap` | Modal focus containment and restoration | Recomputes candidates per Tab — the dialog changes shape constantly |
| `RecordingWindow` | The web-app export: choosing a size, then watching the encode (D-037) | One modal on both pages: opened from the compact window's Export web app…, and on its own when a recording that can be exported stops — one that cannot never opens it. Its overview face became the compact window (D-077 → D-085) |
| Recordings page (`sessions.js`, `recordings/`) | Every recording as a wide card grouped by day; the compact window | `actionsFor` is the one list of what a recording can do, so the hover buttons, the ⋯ menu and the compact window cannot disagree; the primary action reads `media.openable` / `.transcribable` / `.reason` as the picker does. Titles are the stored one when `named`, else the glance's keywords with a spark. No socket: an export's progress and the title backfill are polled while they run (D-085) |
| `wireViewing` (`viewing.js`) | The main screen's view of a past recording: the banner, and the glue between the picker, the approval dialog and a reload | Opening a recording is a reload with another store loaded, so `main.js` hands it `reloadForView`, which empties the transcript, conversation and glossary and re-runs `hydrate()`. Every change of view arrives as `session.viewing`; the HTTP response is adopted too and a repeat is ignored. Not a run state and not in the header (D-076) |
| `mix` (`stores/mix.js`), wired by `wireMix` (`mixing.js`) | The running recording's mix: each sound's volume, mute, level and whether it is still delivering (D-081) | Authoritative from the server except while a slider is dragged: a change is applied locally at once and sent at most every 100 ms, the last value always sent, and a reply that predates a waiting change does not move the slider back. Two bus topics, settings and meters, so four meter frames a second never rebuild a slider under a pointer. Reconciled with `GET /api/session/mix` on every connect; cleared on `session.capture_ended`. A change that fails sets `error`, because a mute that did not arrive is a microphone still being recorded |
| `SoundPanel` | The header's Sound button and popover: per sound, a meter, a 0–100 % slider and a mute (D-081) | Created by `wireMix`. Rows are cloned from a `<template>` and rebuilt only when the set of inputs changes; a settings change updates them in place, so a slider is never replaced under the pointer. Meters follow `MIX_LEVELS_CHANGED` on their own. Writes each volume to the `lastMixLevels` preference that the record sheet starts the next run from |
| `RecordingPicker` | "Open a recording…": recordings with the one action each can take | Opened by any `[data-open-recording]` control — the transcript toolbar, the empty state, the banner. The action and its refusal come from `media.openable` / `.transcribable` / `.reason`, through `primaryAction`; the Recordings page's `actionsFor` answers from the same three fields. A refusal is shown as text, not only as a disabled button's title (D-076) |
| `TranscribeConsent` | Approving a transcription of a recording with no transcript | Reads `GET …/transcribe/plan` and lists exactly what will run; nothing starts until Transcribe. One checkbox: open it on the main screen when it finishes, on by default. Used on both pages (D-076) |

### The multi-mode expansion (D-020), and the record sheet (D-075)

> The header's `ModeSwitcher` and the window-only `Preflight` were replaced by one `RecordSheet`
> behind *Start recording* ([plans/unified-record-sheet.md](plans/unified-record-sheet.md)). The
> mode is derived from the sheet's choices rather than picked. `static/js/session-start.js` connects
> the two: it opens the sheet, then asks the server to start what it chose.

Specified in `docs/design-system.md` § Capture Modes; built by
[plans/multi-mode-ui-implementation.md](plans/multi-mode-ui-implementation.md).

| Component | Owns | Notes |
|---|---|---|
| `RecordSheet` | The record sheet: source, microphone or window audio, transcription timing, video | Replaced `Preflight` (D-075). Reuses `FocusTrap` and the modal styling. `show(preset)` returns a promise of the start request's `options`, or `null` on cancel. Opens pre-filled from the last run (`lastRecordOptions`), focuses Confirm, and writes a changed microphone to `audio.device_id` rather than holding a per-run override. A disabled source carries its reason. Refuses nothing-to-record; the server refuses it again |
| `DetailsPane` | The details column's "This recording" and "Capture" sections (D-086) | Drawn from the stores, so it says what the header and the transcript say; told the viewed recording by `wireViewing`'s `onChange`, and hands its picture to the monitor. The gear opens Settings → Audio. Below 1060px the column is the Details tab |
| `RecordingMonitor` | The details column's Video section: preview, elapsed, output size, active options, and a viewed recording's picture (`showPoster`) | Pulls the preview as a still image on a timer, stopped whenever nobody is looking. The socket never carries video, because transcript events on it are undroppable. Distinguishes its degraded states — no capture, no encoder, window closed, recorder failed — because a black rectangle and a broken capture look identical |

`Header` keeps the record control but no longer derives its appearance from a boolean: it renders
the seven run states from a presentation table and asks `stores/mode.js` what pressing the control
means — from `idle`, always "arm", which opens the record sheet. Beside the clock it shows what is
running, for example *Microphone · live + after*, the job the mode tabs used to do by being visibly
selected. That label is the one element in the row allowed to truncate. The error message goes to the banner and the `title`, never into the state label — a sentence
there stretches the header until the primary control is pushed off a narrow screen.

`TranscriptPane` grows a mode-specific empty state, because `recorded` mode genuinely has no
transcript while it records and a blank pane reads as a transcriber that has stopped working. It
keys on the run's *options* as well as its mode: a `recorded` run with live text on says what live
says, and idle has one message whatever the last run was. Once a
session can hold two transcription passes it also grows a Live/Final revision switch (Plan 4).

## Stores

| Store | Holds | Published on |
|---|---|---|
| `transcript` | Committed segments from **one** transcription pass, and the single hypothesis. A segment from another pass is dropped rather than appended — merging the post-capture pass into the live one shows the talk twice (D-022) | `store.transcript.changed` |
| `polish` | Finished minutes rewritten for reading, and which segments they cover | `store.polish.changed` |
| `session` | Whether recording, for how long, resolved config, and the options the server resolved | `store.session.changed` |
| `health` | Level, speech, RTF, latency, connection | `store.health.changed`, throttled to ~4 Hz |
| `config` | The backend's resolved configuration | `config:changed` |
| `chat` | Settled messages plus the streaming one | `store.chat.changed`, `store.chat.stream` |
| `mode` | The current or last run's mode (derived, never selected — D-075), current run state, per-mode availability, and the record sheet's options for that run | `store.mode.changed` |
| `recording` | Bytes and duration captured, and the post-capture pass's progress. Authoritative from the server, because the pass outlives the page (D-021) | `store.recording.changed` |
| `capture` | The window recording's state. Also server-authoritative: the recorder is a subprocess the browser has no view of (D-022) | `store.capture.changed` |

## Adding a component

1. A partial in `templates/partials/` for its structure, with a `data-` hook on the root.
2. A stylesheet in `static/css/components/`, added to the list in `base.html`.
3. A class in `static/js/components/`, taking its root element in the constructor.
4. Construct it in the page's entry point and pass it any callbacks it needs.

Media queries for a component live in **its own stylesheet**, never in `layout.css`. Component
stylesheets load after `layout.css` and a media query adds no specificity, so a responsive rule
written there is silently overridden by the component's own base rules — a bug that looks exactly
like the media query never matching.
