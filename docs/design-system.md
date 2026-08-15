# Design System

*Last updated: 2026-08-15 (capture modes — D-020)*

> **Status: implemented.** The tokens below are the ones in
> `web/frontend/static/css/tokens.css`, which remains the source of truth — this file is the
> explanation of it. The accessibility floor is a requirement, not an aspiration, and the contrast
> figures here were measured in a browser rather than estimated.

The dedicated `ui-frontend`, `accessibility-mobile`, and `ada-compliance` skills referenced by the
initializer were not adopted (see Decision Log D-006/D-007 in `docs/documentation.md`). Their
substance lives here instead, as documentation rather than as skills.

## Accessibility Baseline — WCAG 2.1 AA

Every interface in this repository must meet these. They are requirements, not aspirations.

### Contrast

- Body and interface text: **at least 4.5:1** against its background.
- Large text (18.66px bold or 24px+): at least 3:1.
- UI component boundaries, icons, focus indicators, and meaningful graphics: **at least 3:1**.
- Never convey state by color alone — pair it with text, an icon, or a shape.

### Keyboard

- Every interactive element reachable and operable by keyboard alone.
- **Visible focus indicator** on every focusable element, meeting 3:1 contrast. Never
  `outline: none` without a stronger replacement.
- Logical tab order matching visual order. No keyboard traps.
- Modals trap focus while open, restore it on close, and close on `Escape`.

### Semantics and Screen Readers

- Native HTML elements first — `<button>`, `<a>`, `<label>`, `<nav>`, `<main>`. Reach for ARIA only
  when no native element fits.
- Every form control has an associated `<label>`. Placeholders are not labels.
- Every meaningful image has alt text; decorative images have `alt=""`.
- One `<h1>` per page; heading levels descend without skipping.
- Status changes — upload progress, job completion, errors — announce via a live region.
- **A live region announces new information, never a restatement of it.** Committed transcript is
  announced; the constantly-rewritten hypothesis is not, and neither are the polished blocks, which
  say again what a screen reader has already read out. All three are equally readable — it is only
  the automatic announcement that is scoped, by keeping the other two outside the live region
  rather than by hiding them.
- Errors identify the field and say how to fix it, in text.

### Motion

- Respect `prefers-reduced-motion`. Nothing essential may depend on animation.
- No content flashing more than three times per second.

## Mobile and Touch Baseline

- Usable at **320 px viewport width** with no horizontal scrolling.
- Touch targets **at least 44×44 CSS pixels**, with adequate spacing between them.
- Content reflows at 200% zoom without loss of function.
- Support both portrait and landscape. Never lock orientation.
- No hover-only interactions — every hover affordance has a tap or focus equivalent.
- Design mobile-first; treat wide layouts as the enhancement.

## Required States

Every view and component defines all four. A component that only handles the success path is
incomplete:

1. **Loading** — with an accessible status announcement.
2. **Empty** — explaining what belongs here and how to get it.
3. **Error** — plain language, actionable, never a raw stack trace or internal identifier.
4. **Success / populated.**

For this project specifically: the live level meter, the real-time-factor indicator, model-loading
progress, and long-transcript rendering performance all need deliberate treatment. The
upload-and-poll job states this paragraph once listed are gone — see Decision D-010.

## Capture Modes

*Specified 2026-08-15 (D-020). The vocabulary is `web/backend/app/services/session/modes.py` and its
mirror `web/frontend/static/js/core/modes.js`; this section is the interface built on it.*

The application has three capture modes and each recording passes through some subset of six run
states. **These are two different questions and they get two different controls.**

| | Answers | Control | Changeable while running |
|---|---|---|---|
| **Capture mode** | What kind of recording this will be | A three-way `radiogroup` in the header | No |
| **Run state** | Where this recording has got to | The primary record control | It *is* the run state |

One control doing both was considered and rejected. A single button that cycles
*idle → recording → stopping → idle → next mode* changes what it means depending on how many times
it has already been pressed, which means a user cannot know what pressing it will do without reading
its current label — and the label is what changes. The two controls sit adjacent so the pair still
reads as one unit.

### The modes

| Mode | What it does | Reachable states |
|---|---|---|
| **Live** | Continuous capture, transcribed as it arrives. The original behaviour. | idle, recording, stopping, error |
| **Recorded** | Captures to a file with no inference; transcribes the whole file on stop. | idle, recording, stopping, **processing**, error |
| **Window** | A chosen window, with live transcription, post-process transcription, and video each switchable first. | idle, **arming**, recording, stopping, processing, error |

**All three are always visible.** A mode whose requirements are missing is *disabled and carries the
reason*, never hidden — a feature that disappears when its dependency is absent is indistinguishable
from a feature that does not exist, whereas a disabled one naming the install command is actionable.
This mirrors how `GET /api/health` already reports optional dependency groups.

**But a mode is only gated on what it genuinely cannot do without.** Only `window` has a hard
requirement. The two audio modes were first gated on the capture-device backend, and running it
showed why that is wrong: a plain `uv sync` has no `sounddevice`, and the file source replaces a
capture device entirely — so all three modes were struck through on a machine transcribing perfectly
well. A missing device is an *input source* problem with an existing remedy path (the audio settings
tab, and a start failure that opens it). Disabling the mode as well tells the same failure twice,
once wrongly.

Unavailable is drawn as dimmed **and** struck through: colour is never the only signal, and the
reason is on the control's `title` as well as in the disabled state.

**The selector is disabled whenever the run state is not `idle`.** Switching capture mode mid-session
would mean rebuilding the pipeline underneath a transcript that is still accumulating, and there is
no user need for it. Changing mode while idle does **not** clear the transcript: the transcript is
cleared on `session.started` and nowhere else.

### The record control's six states

| State | Label | Dot | Enabled | Announced |
|---|---|---|---|---|
| `idle` | *Start recording* | `--text-dim` | Yes | — |
| `arming` | *Choose a window…* | `--accent` | Yes — cancels | "Waiting for a window to be chosen" |
| `recording` | *Stop* | `--danger` | Yes | "Recording" |
| `stopping` | *Stopping…* | `--danger`, dimmed | **No** | "Stopping" |
| `processing` | *Transcribing… NN%* | `--accent` | **No** | "Transcribing, NN percent" |
| `error` | *Start recording* | `--warning` | Yes | The failure, in plain language |

Rules that fall out of the table:

- **`stopping` and `processing` disable the control rather than hiding it.** A hidden control makes
  the header reflow mid-recording; a disabled one keeps the layout still and says why.
- **`processing` is not a cancel button.** Cancelling a transcription pass after a forty-minute
  recording discards the only transcript of audio that is about to be deleted. The honest recoveries
  are to let it finish or to stop the server, and the audio is on disk in the meantime.
- **`error` returns the control to its idle affordance** and puts the message in the banner region,
  which already exists and is already announced. The dot stays warning-coloured so the previous run's
  failure is still visible next to a button that says *Start recording*.
- **Announcement lives on the state label, not the button.** `aria-live="polite"` on a `<button>`
  whose text changes announces the *new label* — "Stop" — which describes what pressing it would do,
  not what happened. The separate `.record-state__label` announces the state itself.
- **No new tokens.** `--danger` for recording, `--accent` for arming and processing, `--warning` for
  error, `--text-dim` for idle, all already measured. Every state pairs its colour with a word, per
  the colour rule above.
- **The recording dot does not pulse.** Existing motion rule: nothing loops or animates in an
  interface that is on screen for two hours. Under `prefers-reduced-motion` there is nothing extra to
  disable, which is the point. The animated instrument in [motion-spec.md](motion-spec.md) is
  therefore **not** adopted in the header — it belongs to the system tray, which is glanced at rather
  than sat in front of. Its six state hues are the same ones this table uses, so the two indicators
  agree on what each state looks like even though only one of them moves.

### Pre-flight options

Some modes need options answered *before* capture begins. They are **per-run**, not settings: a user
who recorded one window without video should not silently get no video next time.

| Mode | Opens a pre-flight | Why |
|---|---|---|
| Live | No | Nothing to choose |
| Recorded | No | Nothing to choose |
| Window | **Always** | Three toggles, and the desktop's window picker follows |

The window pre-flight carries exactly three toggles:

| Toggle | Default | Off means |
|---|---|---|
| **Live transcription** | On | No transcript until the recording ends |
| **Post-process transcription** | On | The live transcript is the only one |
| **Video capture** | On | Audio is recorded and transcribed; no video file |

**All three off is refused** — it would record nothing — with the reason shown next to the confirm
button rather than as an alert. Every other combination is valid, including video with no
transcription and transcription with no video. The refusal is enforced on the server as well; the
client check is a courtesy and the server check is the rule.

The sheet is a focus-trapped dialog reusing `a11y/focus-trap.js` and the existing modal styling — not
a new dialog primitive, because focus trapping and restoration are the accessibility behaviour most
easily got wrong twice. `Escape` cancels and starts nothing.

**The window is chosen after the sheet is confirmed, by the desktop's own picker, not inside the
sheet.** Under Wayland an application cannot enumerate windows (see D-022 and
[plans/window-recording-transcription.md](plans/window-recording-transcription.md)). The interface
must therefore never imply it can: no thumbnail grid, no window list, no pre-selection. The sheet's
confirm button says *Choose a window…* so the next thing that happens is the thing the button
promised.

The sheet also states, in text, that **audio is the machine's, not that window's** — the screen-cast
portal carries video only. A user who assumes per-window audio and records the wrong source has lost
the recording, and that sentence is the entire defence against it.

### The recording monitor

Window capture gets a **third pane**, peer to the transcript and the assistant, shown only in that
mode. Not a modal and not a floating window: live transcription and asking the assistant questions
must keep working *during* the recording, and anything overlaying the other two panes prevents
exactly that.

It shows the preview, the elapsed clock, the output file and its growing size, and which of the three
options are active. In the narrow layout it joins the existing tab set as a third tab — **present
only in window mode**, because three permanent tabs at 320 px spends scarce width on a pane that is
empty in two modes out of three.

**Its degraded state is specified up front**: when no preview is available, the pane shows a static
card naming the captured window and the reason. A black rectangle and a broken preview look
identical, and the difference between "this window is dark" and "capture has failed" is the whole
value of looking at the monitor.

The preview is a still image refreshed on a timer, never frames pushed over the WebSocket — the
socket's backpressure policy makes transcript events critical and undroppable, and video sharing that
channel is the one thing capable of delaying a committed segment. The timer pauses when the pane is
hidden or the tab is backgrounded.

### Per-mode empty states

The transcript pane's empty state is mode-specific, because "no transcript yet" means something
different in each and a blank pane during a recording reads as a broken transcriber.

| Mode and state | What the transcript pane says |
|---|---|
| Any mode, idle | The existing empty state — what this is and how to start |
| Live, recording | Nothing; the transcript is filling |
| Recorded, recording | *"Recording. Nothing is being transcribed yet — the whole recording is transcribed when you stop."* plus the elapsed time and level meter |
| Recorded/window, processing | A progress card: seconds transcribed of seconds recorded |
| Window, recording, live transcription off | *"Recording. Live transcription is off for this run."* |
| Window, recording, live transcription on | Nothing; the transcript is filling |
| Any mode, error | The failure and what to do, per the Required States rule |

## Design Tokens

Every colour, size, radius, and duration lives in `web/frontend/static/css/tokens.css`. No component
hardcodes any of them.

The palette is **dark by default and deliberately so**: a bright screen in a darkened lecture hall is
conspicuous to everyone sitting behind you.

| Token group | Shape | Notes |
|---|---|---|
| Surfaces | `--surface-base` → `--surface-panel`, plus `--surface-control{,-hover}`, `--surface-input` | Four depths. The transcript sits on the lowest so it reads as the document |
| Text | `--text-primary`, `--text-transcript`, `--text-secondary`, `--text-muted`, `--text-faint`, `--text-dim` | `--text-transcript` is warmer and softer than white; it is read for two hours |
| The tentative tail | `--text-hypothesis` | ~7:1 against the transcript surface. "Faded" text is easy to under-contrast |
| Polished blocks | *(no new tokens)* | Same face, size, and colour as a raw segment — it is the same speech. No rule, no heading, no extra gap: one minute is one paragraph and consecutive minutes read as consecutive paragraphs. On a working setup nearly the whole page is polished, so any per-block treatment is a break on every paragraph |
| Inline timestamps in polished text | `--text-faint`, `--text-xs`, `--font-mono` | 5.56:1 on the transcript surface, above the 4.5:1 small-text floor. Not interactive: unlike a chat citation, which points elsewhere, a timestamp in the transcript is already at the moment it names |
| Borders | `--border-subtle` → `--border-strong` | Four weights |
| Accent | `--accent`, `--accent-bright`, `--accent-dim`, `--accent-wash{,-strong}`, `--accent-border` | One hue. The wash variants are translucent and composite over whatever is behind them |
| Semantic | `--danger`, `--warning`, each with `-text`, `-wash`, `-border` | Never used alone — see the colour rule below |
| Type | `--font-ui`, `--font-transcript`, `--font-mono` | Local stacks only. A webfont that fails to load would change the transcript's measure mid-session |
| Type scale | `--text-xs` (10.5px) → `--text-lg` (17px), plus `--transcript-size` | The transcript's size is user-adjustable independently of the chrome |
| Spacing | `--space-1` (2px) → `--space-12` (30px) | One scale, used everywhere |
| Radii | `--radius-sm` → `--radius-xl`, `--radius-pill` | |
| Elevation | `--shadow-pill`, `--shadow-panel`, `--shadow-popover` | |
| Layout | `--header-height`, `--chat-width`, `--glossary-width`, `--transcript-min-width`, … | The frame's fixed dimensions |
| Motion | `--duration-fast` (120ms), `--duration` (180ms), `--duration-slow` (260ms), `--ease` | Almost none by design — see below |

### Rules

- **No hardcoded values in components.** Colours, spacing, and radii come from tokens.
- **Semantic naming** (`surface-raised`, `text-muted`), never literal (`gray-200`).
- **Motion is for state changes only** — a hover, a panel opening — and never for content. Every
  element here is on screen for two hours; anything that loops or pulses becomes noise within
  minutes. `prefers-reduced-motion` reduces all three durations to nothing.

### Contrast

Measured against the surfaces each token actually sits on, and corrected twice:

- `--text-faint` and `--text-dim` were first set from the transcript's surfaces, which are the
  **darkest** in the palette. On `--surface-panel` and `--surface-control` — the settings dialog —
  they measured 4.40:1 against a 4.5:1 floor. Their current values are the lightest-surface floor:
  ≥4.5:1 on `--surface-control-hover`, and therefore ≥4.5:1 everywhere.
- When checking a translucent background such as `--accent-wash`, **composite it over its parent
  first**. Comparing a foreground against the wash's own RGB ignores its alpha and produces a figure
  that is wrong by a factor of six.

### Colour is never the only signal

Every state carries an icon or a word as well as a colour. Real-time factor below 1.0 turns red
*and* says so; a device test result is coloured *and* names what to do; the tentative tail is
italic and labelled as well as dimmer. This is a WCAG requirement and also simply what works in a
darkened room on a projector.

## Quality Brief

The interface should look considered rather than defaulted. Concretely:

- Pick a deliberate type scale and spacing rhythm and hold to them.
- Prefer generous whitespace over dense chrome.
- Treat the transcript itself as the primary content — long-form reading comfort matters more than
  surrounding UI decoration.
- Avoid stacking unmodified component-library defaults into an anonymous dashboard look.

## Verification

Before any UI-visible change is called done:

- [ ] Keyboard-only pass through the changed flow
- [ ] 320 px viewport check, no horizontal scroll
- [ ] Contrast checked on new color pairings
- [ ] All four states present
- [ ] Touch targets at least 44×44 px
- [ ] Automated a11y check (axe or equivalent) — tooling not yet selected, see `docs/checklist.md`
