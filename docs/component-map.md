# Component Map

*Last updated: 2026-08-15 (Phase 14 — final documentation pass)*

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
│   ├── pages/                  one per URL: app.html, sessions.html
│   ├── macros/icons.html       inline SVG, inheriting currentColor
│   └── partials/               one file per region of the interface
│       ├── header.html, banners.html, status_bar.html
│       ├── transcript/         pane, toolbar, hypothesis, empty state, glossary panel…
│       ├── chat/               pane, composer, quick actions, empty state
│       └── settings/           modal, nav, and one file per tab
└── static/
    ├── css/
    │   ├── tokens.css          every colour, size, and duration in the application
    │   ├── base.css            resets and element defaults
    │   ├── layout.css          the application frame and standalone pages
    │   └── components/         one stylesheet per component
    └── js/
        ├── main.js             the application page's entry point
        ├── sessions.js         the sessions page's entry point
        ├── core/               DOM helpers, the event bus, formatting, preferences
        ├── transport/          HTTP client, WebSocket, event names
        ├── stores/             state: transcript, polish, session, health, config, chat
        ├── components/         one class per region, plus settings/ per tab
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
| `Header` | Record control, clock, model and privacy identity | Stopping asks first — an accidental stop mid-talk cannot be undone |
| `TranscriptPane` | Polished blocks, committed segments, the hypothesis tail, search highlighting | Three sibling regions, only the middle one a live region; polished minutes flow as continuous prose with inline timestamps; trims to 300 segments and 240 blocks |
| `ScrollController` | Follow-the-live-edge behaviour | Stops following the instant the user scrolls |
| `TranscriptSelection` | "Ask about this", copy-with-timestamp | Positions from the selection rectangle, reads the time from the enclosing segment |
| `ChatPane` | Conversation, quick actions, composer, streaming | Mutates the streaming message in place rather than re-rendering |
| `GlossaryPanel` | Terms, sorted by first appearance | Each term navigates the transcript |
| `StatusBar` | Level, speech state, real-time factor, connection | Never colour alone — every state carries an icon and a word |
| `Banners` | The three error tiers | Never a modal; a modal during a talk covers the transcript |
| `SettingsModal` | The five tabs, presets, save | The one modal in the application |
| `settings/*` | One tab each, plus `bindings.js` | Controls declare a dotted config path; the binding layer does the rest |
| `FocusTrap` | Modal focus containment and restoration | Recomputes candidates per Tab — the dialog changes shape constantly |

## Stores

| Store | Holds | Published on |
|---|---|---|
| `transcript` | Committed segments and the single hypothesis | `store.transcript.changed` |
| `polish` | Finished minutes rewritten for reading, and which segments they cover | `store.polish.changed` |
| `session` | Whether recording, for how long, resolved config | `store.session.changed` |
| `health` | Level, speech, RTF, latency, connection | `store.health.changed`, throttled to ~4 Hz |
| `config` | The backend's resolved configuration | `config:changed` |
| `chat` | Settled messages plus the streaming one | `store.chat.changed`, `store.chat.stream` |

## Adding a component

1. A partial in `templates/partials/` for its structure, with a `data-` hook on the root.
2. A stylesheet in `static/css/components/`, added to the list in `base.html`.
3. A class in `static/js/components/`, taking its root element in the constructor.
4. Construct it in the page's entry point and pass it any callbacks it needs.

Media queries for a component live in **its own stylesheet**, never in `layout.css`. Component
stylesheets load after `layout.css` and a media query adds no specificity, so a responsive rule
written there is silently overridden by the component's own base rules — a bug that looks exactly
like the media query never matching.
