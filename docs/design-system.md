# Design System

*Last updated: 2026-09-09 (the instruction field's placeholder — D-068)*

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

*Specified 2026-08-15 (D-020); the header switch was replaced by the record sheet on 2026-11-07
(D-075). The vocabulary is `web/backend/app/services/session/modes.py` and its mirror
`web/frontend/static/js/core/modes.js`; this section is the interface built on it.*

The application has three capture modes, and each recording passes through some subset of seven run
states. **These are two different questions and they still get two different controls**, but the
mode is no longer chosen from a tab in the header. It is *derived* from what the person asks for in
the record sheet.

| | Answers | Control | Changeable while running |
|---|---|---|---|
| **What to record, and when to transcribe it** | What kind of recording this will be | The record sheet, opened by *Start recording* | No |
| **Run state** | Where this recording has got to | The primary record control | It *is* the run state |

**Why the three-way switch went (D-075).** The switch named implementations, not choices. *Live*
meant "microphone, transcribe as you go", *Recorded* meant "microphone, transcribe when it ends",
and *Window* meant "a window, with either or both, plus video". Two questions were tangled into
one row of tabs: *what am I recording* and *when should it be transcribed*. Neither could be
answered in one place. Only Window had a sheet of options, and the microphone was chosen in a
settings tab two screens away. Reported as confusing by the person who uses it. The one-control
rejection from D-020 still stands: the record control does not cycle through modes.

### The record sheet

*Start recording* always opens the sheet. Its sections appear in order, and each one appears only
when the section above it has an answer. That reads as stepping through, without Back and Next
buttons and without a second dialog primitive.

| # | Section | Choices | Shown when |
|---|---|---|---|
| 1 | **What to record** | My microphone · This computer's sound · A window | Always |
| 2 | **Which sound** / **Which microphone** | Microphone: the device list, with the configured one selected. A window or this computer's sound: the source's own sound and *My microphone*, either or both (D-081); for a window, *Only this app* or *Everything playing* under its sound | Always, except for a replayed file |
| 3 | **When to transcribe** | While recording · After it ends · Both | Always |
| 4 | **Video** | Record what the window shows (on/off) | A window |

- **Sections 1 and 3 are cards, not radio rows** (revised 2026-11-07, at the user's request). Each
  card is an icon and a one-line title, chosen by clicking the card. The chosen card gets an
  `--accent` outline, a soft glow and an `--accent-wash` fill, so the shape changes as well as the
  colour. The first version gave every choice a hint underneath, and hints of different lengths
  made the row hard to scan; the longer description is now on the card's `title`. The radio is
  still underneath, visually hidden, so the group keeps one tab stop and arrow keys. Its focus ring
  is drawn on the card. A disabled source's reason sits on a line under the row, not inside the
  card, because an install command does not fit a card. On a phone, three cards stay three across,
  and the four timing cards (a window's) go two by two.
- **Which sound is two cards that can both be on** (D-081): the source's own sound, and *My
  microphone*, mixed. They are the same cards as sections 1 and 3, but checkboxes. A box in the
  top-right corner, empty and then ticked on `--accent`, is what says "several may be chosen"
  where the radio cards say "pick one"; the outline and glow alone would read the same for both.
  For *This computer's sound* its own card is **fixed on** (`choice-card--fixed`: ticked, not
  dimmed, not struck through, no pointer). That sound *is* the recording, and only the microphone
  is optional. What a window had is kept aside and restored when a window is chosen again. For a
  window, *Only this app* / *Everything playing* sit under the cards as two small pills, shown
  only while the window's sound is ticked. With both sounds on, one line says to **use
  headphones**, because through speakers the microphone hears the other sound too and records it
  a moment later. No sound at all is refused beside the buttons: *Choose at least one sound to
  record.* On a phone the two cards stay side by side.
- **The microphone question is one row**: the label on the left, the dropdown filling the rest.
  It is also asked whenever *My microphone* is ticked under Which sound, directly under the cards
  and without a step number, because there it is a follow-up rather than a step. Without it,
  narrating over a window could only ever use whichever microphone was last configured.
- **The volumes start where they were left.** Each sound's last volume in the sound panel is
  remembered in the browser (`lastMixLevels`) and sent as the run's starting `levels`.
- **Choosing a microphone here writes the configuration** through `POST /api/audio/device`. It does
  not add a per-run override. The tray's microphone picker reads the same value, so there is one
  answer to "which microphone".
- **The file source wins.** When the audio source is a file, section 2 says which file is being
  replayed and links to Audio settings. It does not offer a microphone list, or a microphone to
  mix in, that would be ignored.
- **An unavailable source is disabled and carries its reason**, never hidden. A disabled option
  naming the install command is actionable; one that has vanished looks like a feature that does
  not exist. Only *A window* has a hard requirement (`window_capture`).
- **The sheet remembers the last run** and opens pre-filled. D-020 made window options per-run so
  that nobody silently got no video. That fault came from options that were applied without being
  shown. Every choice here is on screen at the moment of confirming, so pre-filling hides nothing.
  A remembered source that is no longer available falls back to the microphone.
- **Confirm says what happens next**: *Start recording*, or *Choose a window…* when the desktop's
  window picker comes after. The confirm button takes focus, so pressing Enter twice starts a
  recording the same way as last time.
- **Refused combinations** show their reason beside the confirm button, not as an alert:
  - No transcription and no video records nothing.
  - "Transcribe: neither" is offered only with video, so it can only be chosen for a window.

### How a choice becomes a mode

The server derives the mode (`derive_mode` in `services/session/modes.py`). The names stay because
global shortcuts, the tray, stored session metadata and the vocabulary test all use them.

| What to record | When to transcribe | Video | Derived mode | Notes |
|---|---|---|---|---|
| Microphone | While recording | — | `live` | The original behaviour |
| Microphone | After it ends | — | `recorded` | No inference while capturing |
| Microphone | Both | — | `recorded` | **New.** Live text *and* a second pass, kept as two revisions |
| This computer's sound | Any | — | `live` or `recorded`, by the same rule | Taps every playing stream; never falls back to the microphone |
| A window | Any, or neither | On/off | `window` | The desktop's picker follows if video is on |

A request that still sends `mode` alone gets that mode's preset: `live` is microphone, while
recording; `recorded` is microphone, after; `window` is a window with all three switches on. A
request whose `mode` contradicts its options is refused with a message that names the conflict.

### The modes

| Mode | What it does | Reachable states |
|---|---|---|
| **Live** | Continuous capture, transcribed as it arrives. | idle, arming, recording, paused, stopping, error |
| **Recorded** | Captures to a file; transcribes the whole file on stop, and live as well if asked. | idle, arming, recording, paused, stopping, **processing**, error |
| **Window** | A chosen window, with live transcription, post-process transcription, and video each switchable first. | idle, arming, recording, paused, stopping, processing, error |

**`arming` is reachable in every mode now**, because every recording starts from the sheet.
A global shortcut that starts a recording directly (`toggle_live`, `toggle_recorded`) skips it.

While a recording runs, a quiet chip beside the clock says what is running, for example
*Microphone · live + after*. That answers "which mode is this?", which the disabled radiogroup used
to answer.

### The record control's seven states

| State | Label | Dot | Enabled | Announced |
|---|---|---|---|---|
| `idle` | *Start recording* | `--text-dim` | Yes | — |
| `arming` | *Cancel* | `--accent` | Yes — cancels | "Choosing what to record", or "Waiting for a window to be chosen" once the desktop's picker is up |
| `recording` | *Stop* | `--danger` | Yes | "Recording" |
| `paused` | *Stop* | `--warning` | Yes | "Paused" |
| `stopping` | *Stopping…* | `--danger`, dimmed | **No** | "Stopping" |
| `processing` | *Transcribing… NN%* | `--accent` | **No** | "Transcribing, NN percent" |
| `error` | *Start recording* | `--warning` | Yes | The failure, in plain language |

Rules that fall out of the table:

- **`stopping` and `processing` disable the control rather than hiding it.** A hidden control makes
  the header reflow mid-recording; a disabled one keeps the layout still and says why.
- **Pause is a second control, not a seventh meaning for this one (D-044).** This control already
  serves seven states by asking the store what pressing it means; giving it "stop, and you will be
  asked to confirm" and "pause, which is free" as two meanings that differ only by current state
  puts two different consequences under one finger. The pause control is hidden outside `recording`
  and `paused` rather than disabled — a disabled button invites "why can I not press this", and the
  answer, that there is no recording, is already the whole of what the rest of the header says.
- **`paused` takes `--warning`, not `--danger` and not grey.** It is the one state where what the
  user needs to know is that *nothing is being captured while a session is open*. Red reads as
  recording; grey reads as idle; and idle is the misreading that costs a talk.
- **`processing` is not a cancel button, and the pass's own controls are elsewhere (D-045).**
  Cancelling from the header would sit one pixel from Stop during the most expensive operation the
  application performs. The pass carries its own Pause and Cancel beside its progress bar in the
  transcript pane, where the figure they act on is visible — and that bar **sticks to the top of the
  scroller**, because a pass commits as it goes and used to scroll its own controls out of sight.
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

### Sheet mechanics

The record sheet is a focus-trapped dialog reusing `a11y/focus-trap.js` and the existing modal
styling, not a new dialog primitive: focus trapping and restoration are the accessibility behaviour
most easily got wrong twice. `Escape` and the scrim cancel and start nothing, which is why `arming`
is a run state of its own. The refusal is enforced on the server as well; the client check is a
courtesy and the server check is the rule.

**The window is chosen after the sheet is confirmed, by the desktop's own picker, not inside the
sheet.** Under Wayland an application cannot enumerate windows (see D-022 and
[plans/window-recording-transcription.md](plans/window-recording-transcription.md)). The interface
must therefore never imply it can: no thumbnail grid, no window list, no pre-selection. The sheet's
confirm button says *Choose a window…* so the next thing that happens is the thing the button
promised.

The sheet also states, in text, that **a window's audio is captured separately from its picture**:
the screen-cast portal carries video only. A user who assumes per-window audio and records the wrong
source has lost the recording, and that sentence is the entire defence against it.
### Sound controls (D-081)

A **Sound** button sits beside Pause while a recording is running or held, and opens a popover
under it. A popover rather than a pane, because the header is on screen in every layout and every
run and the monitor pane is not. It also leaves the header as wide as it was. The button carries
`aria-expanded` and `aria-controls`; the popover is a `role="dialog"` labelled *Sound*.

One row per sound the recording has: *The window's sound*, *This computer's sound*, *Your
microphone*. Each row has:

- **The name, and its volume in words** (`80 %`, or *Muted* in the warning colour).
- **A mute button** (`aria-pressed`, named "Mute your microphone" / "Unmute …"). It shows a speaker
  with waves while the sound is heard, and crossed out on the warning wash while muted.
- **A 0–100 % slider** in steps of 5, labelled "<sound> volume", whose `aria-valuetext` is "80
  percent" or "muted".
- **A thin meter** of that sound's own level, before its volume. A turned-down or muted microphone
  still shows whether it hears anything, which is how someone knows it is safe to unmute. Muted, the
  meter dims rather than disappearing.
- **One line of status when it matters:** *Muted — not being recorded.*, or *Stopped — this sound
  is no longer arriving.* (the slider and button are then disabled).

**Keyboard:** opening focuses the first slider; arrows move 5 %; **M** mutes the row the focus is
in; **Escape** closes and returns focus to the button. A click outside closes it. A change applies
at once on screen and reaches the server within 100 ms; every other open page follows
`mix.changed`. Each sound's volume is remembered in the browser and the next recording starts
there. Nothing here is a setting, so closing a recording hides the button.

**The header makes room by shortening the model names**, which ellipsise with the full text on
their title. With Pause, Sound and Stop all showing, the header measured 89 px too wide at 1024 px
until they did. The controls are never what gets pushed off the screen.

### The recording monitor

> Since D-086 the monitor is the **Video** section of the always-present details column (see *The
> Main Screen → The details column*), and its tab is the Details tab. What follows about the
> preview and its degraded states still holds.

Window capture gets a **third pane**, peer to the transcript and the assistant. Not a modal and not
a floating window: live transcription and asking the assistant questions must keep working *during*
the recording, and anything overlaying the other two panes prevents exactly that.

It shows the preview, the elapsed clock, the output file and its growing size, and which of the three
options are active.

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

## The Mark

`web/frontend/static/brand/radish.svg` — a radish shouting through a megaphone, which is where the
name comes from (D-038). One file, used in five places: the browser tab, the application header, the
exported bundle's tab and topbar, the desktop entry, and the README's banner — which embeds these
paths rather than linking the file, because GitHub will not render one SVG inside another. The
banner is generated from the mark by `docs/assets/make_header.py`, so the drawing still has exactly
one source (D-070).

Its palette ships as `--brand-*` tokens:

| Token | Value | What it is |
|---|---|---|
| `--brand-radish` | `#c22d4c` | The body |
| `--brand-leaf` | `#598f3b` | The leaves |
| `--brand-cream` | `#f6f2e8` | The megaphone, and the disc the mark sits on |
| `--brand-blush` | `#d9868a` | The lower body's highlight |
| `--brand-ink` | `#080808` | Outline and limbs |

**These are not the accent, and must not be used as one.** The accent is a lighter radish
(`#e8687f`, D-083) chosen for contrast on graphite; the logo's crimson is darker than the AA floor
allows for text there.
The brand tokens exist so the mark's own treatment does not hard-code hex values.

**The mark always sits on a cream disc in application chrome.** The logo is drawn for a light
ground — its outline and the character's legs are near-black — so on `--surface-raised` it loses
everything but the crimson body and reads as a colour smudge rather than a drawing. Rendered at
28 px against `#0d1011` and against `#f6f2e8` side by side, only the second is legible. The disc
carries 2 px of padding because the megaphone reaches the edge of the viewBox.

The SVG keeps its C2PA provenance metadata. It is 7.7 KB of the file's 75 KB, it is served over
loopback, and stripping provenance to save bytes on a local connection is a poor trade.

Below 700 px the header mark is hidden along with the model identity: the row wraps and every pixel
is spoken for by controls. The tab icon still carries the mark there.

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
| Accent | `--accent`, `--accent-bright`, `--accent-dim`, `--accent-wash{,-strong}`, `--accent-border`, `--accent-line`, `--on-accent` | Radish (D-083), and **only** the accent: the primary action, controls that are on, focus, links, a card's edge. Never a surface — a radish-tinted page was tried in the labs and rejected. The wash variants are translucent and composite over whatever is behind them. `--on-accent` is the dark label on a filled accent |
| Semantic | `--danger`, `--warning`, each with `-text`, `-wash`, `-border`; `--success`, `--success-dim` | Never used alone — see the colour rule below. `--success` is a teal of its own ("healthy": a live input, the level meters, a good realtime factor, local-only privacy); it was the accent's alias until the accent became radish. `--danger` is close to radish, so a destructive control always carries a word and the trash icon |
| Type | `--font-ui`, `--font-transcript`, `--font-mono` | Local stacks only. A webfont that fails to load would change the transcript's measure mid-session |
| Type scale | `--text-xs` (10.5px) → `--text-lg` (17px), plus `--transcript-size` | The transcript's size is user-adjustable independently of the chrome — Settings → Context → Reading, 14–28px, 16.5px by default (redesign Step 6) |
| Spacing | `--space-1` (2px) → `--space-12` (30px) | One scale, used everywhere |
| Radii | `--radius-sm` → `--radius-xl`, `--radius-pill`; `--radius-card` (18px), `--radius-control` (10px) | The redesign's round shapes (D-083): cards and panels, and the buttons and fields inside them |
| Elevation | `--shadow-pill`, `--shadow-panel`, `--shadow-popover`, `--shadow-raised` | `--shadow-raised` lifts a card: a hairline of light on its top edge and a soft drop |
| Layout | `--header-height`, `--chat-width`, `--glossary-width`, `--transcript-min-width`, … | The frame's fixed dimensions |
| Motion | `--duration-fast` (120ms), `--duration` (180ms), `--duration-slow` (260ms), `--ease` | Almost none by design — see below |

### Icons

Drawn icons come from one set (D-083): the `icon(name, size)` macro in
`templates/macros/icons.html` for templates, and `static/js/core/icons.js` (`icon`, `iconNode`) for
components that draw from JavaScript. A test keeps the two path lists identical. Every icon is
decorative (`aria-hidden`); the control around it carries the accessible name.

**An icon sits in a `.icon` box** — `display: inline-flex; line-height: 0` around a block SVG. As
a plain inline box it was a line tall with the SVG at its top, so an icon beside a label sat a pixel
or two high; the dot in *Start recording* was measured 1.5px off in the main-screen lab.

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

### A placeholder that is not a hint

`.field__control--instructions` breaks the usual rule, and the exception is worth stating because
it looks like an oversight otherwise. A placeholder normally suggests what to type and disappears
the moment it stops mattering. Here it carries the instructions **actually in force** whenever the
box is empty, which is the ordinary state (D-068), so it is set at `--text-muted` at full opacity
rather than fading toward the hint colours.

That is not enough on its own — a grey paragraph still reads as inactive — so a line under each
field says in words which of the two texts is being used, and the field is tall and monospaced
because what is being edited is a numbered list of rules rather than a sentence. Colour is never
the only signal, and neither is placement.

### Colour is never the only signal

Every state carries an icon or a word as well as a colour. Real-time factor below 1.0 turns red
*and* says so; a device test result is coloured *and* names what to do; the tentative tail is
italic and labelled as well as dimmer. This is a WCAG requirement and also simply what works in a
darkened room on a projector.

## Recordings and the Main Screen

Specified by [plans/recordings-on-the-main-screen.md](plans/recordings-on-the-main-screen.md)
(D-076, D-077). A past recording can be brought back to the main screen so the assistant can be
asked about it. Nothing is transcribed without the user approving it first.

### What a recording can do

Decided on the server (`SessionMedia.openable`, `.transcribable`, `.reason`) so the card, the
compact window and the main-screen picker can never disagree.

| The recording holds | Result | What the control says |
|---|---|---|
| A transcript (any pass) | **Open** | "Bring to main screen" |
| No transcript, an interrupted pass with its checkpoint | **Resume on approval** | "Transcribe…" — the dialog says it continues from where it stopped |
| No transcript, the WAV (`audio.wav`) | **Transcribe on approval** | "Transcribe…" |
| No transcript, a video with sound (muxed, or a raw video with an audio stream) | **Extract, then transcribe, on approval** | "Transcribe…" — the dialog says the sound is taken from the video first |
| No transcript, a video with no sound track | Refused | "This video has no sound to transcribe." |
| No transcript, no audio, no video | Refused | "There is nothing in this recording to transcribe." |
| Unreadable database | Refused | The existing `problem` text |
| Anything, while a session is recording | Refused (temporarily) | "Stop the current recording first." |
| Anything, while a transcription runs | Refused (temporarily) | "Wait for the running transcription to finish." |

A folder in the recordings directory with **no transcript database** is listed as its own card
("Not transcribed yet"). It follows the same table.

### The Recordings page (D-085)

Chosen in the recordings lab (D-082) and built from it. **Wide cards**, small (`--card-min` 200px,
so a card is at least ~380px) and airy (24px between, 16px inside), **grouped by day** under a
heading with a count ("Today", "Yesterday", "Monday 3 November"); sorted by length, the groups go.

- **The bar** is one slim sticky row: the way back and the heading, the count (its tooltip says
  where recordings are stored), search over titles and what was said, the filters as a radiogroup
  (All, Video, Text only, To transcribe, Empty — each with its count), and sort. **Name *N*
  untitled…** appears only while there are untitled recordings and a model to name them (D-084);
  it asks before anything runs, then reports progress at the foot of the page.
- **The picture** is 40% of the card and as tall as it. A video is a filmstrip — one large frame and
  two small ones, from 15%, 50% and 85% in — and **moving the pointer across it skims the video**
  in the large frame. No video: a colour for the time of day (dawn amber to night blue) with the
  audio or transcript icon. On the picture: the media icons (video · audio · transcript, the
  missing ones faded), the smart date ("Today · 12:11 PM", "Mon · 3:04 PM", "Sep 18 · 12:11 PM"),
  and the length.
- **The words**: the title the owner or the model gave (D-084), or, until there is one, the
  transcript's keywords marked with a spark; a badge for "Recording now" or "On the main screen";
  the first summary or the opening words; then words and size on disk.
- **The buttons appear on hover in the card's bottom-right corner** (the owner's note), always on a
  touch screen: the primary action from the table above (icon only, named by its label), download
  transcript, open folder, and ⋯ with the rest (other downloads, Export web app…, Delete…).
- **Clicking a card opens the compact window.** Dates are read from `started_at`, never from a title.

### The compact window

Replaces the recording window's overview face (D-077). A labelled modal, 880px at most, two
columns: the video (or a "no video" panel) beside the primary action and its reason, six numbers
(length, words, summaries, glossary, questions, on disk), and what was said — keywords, then the
first summary or the opening. Underneath, in one row: **Transcript** (the version when there are two
(D-066), then Markdown, Text, SRT, WebVTT, JSON), **Download** (conversation, video, audio,
**Export web app…**), **Files** (Open folder, Delete…). **Rename** turns the title into a field
(Enter saves, Escape cancels). Escape closes the window and puts focus back on its card. One column
below 860px.

### The web-app export window

What is left of the recording window (D-037, D-077): **choosing** (preview, presets measured against
this recording, size ranges) and **watching** (a bar per stage). Opened from the compact window's
Export web app…, and by the main screen when a recording that can be exported stops. A recording
that cannot be exported does not open it at all.

### The consent dialog

Opened by **Transcribe…**. Says what will run and nothing more: "Take the sound from the video"
(only for a video), then "Transcribe *N* minutes of audio with *model*". When no speech model is
loaded it says so and the confirm is disabled. One checkbox, on by default: **Open on the main
screen when it finishes**. Buttons: **Cancel**, **Transcribe**. Nothing runs until Transcribe is
pressed; progress then shows in the existing transcription status.

### Viewing a recording on the main screen

A card at the top of the transcript pane (a strip above both panes until redesign Step 6): **"Viewing *title* · *date*"**, a second line "Starting a new
recording closes this view.", and **Close**. Not a run state: the record control stays idle and
the header is untouched. The transcript, the earlier conversation and the glossary are re-read from
the server, exactly as on a reload; new questions continue the conversation and are saved with the
recording. The view does not survive a restart.

**Open a recording…** sits in the transcript pane's empty state and in the banner. It lists recent
recordings with the same primary action as their row.

## The Main Screen

Chosen in the main-screen lab (D-082, third round, the owner's "PReferences" combination and
notes) and built by [plans/recordings-and-main-screen-redesign.md](plans/recordings-and-main-screen-redesign.md).

### The header

Left to right: the mark, the record state, the clock, the run chip, Pause and Sound while
recording, and **Start recording** — a radish pill with its dot in an icon box, so the dot and the
words share a centre line (it was 1.5px high). Then **the recording's health as chips**, which were
a status bar along the bottom of the window until redesign Step 5: microphone level with the
source, speaking or silent, the realtime factor (red with a warning icon below 1.0), lag, the
discarded count (only above zero), and the connection. Then privacy, the model names, Recordings and
Settings. Health warnings still arrive as banners, so nothing the bar said is lost.

**It wraps rather than overflowing.** Wrapping is decided on each group's full width, so the model
names are capped at 220px (ellipsised, full text in their tooltip); at 1440px the header is one row
until every chip is showing, then two. At 900px it is three rows. It never scrolls sideways and the
primary control is never pushed off the edge.

### The transcript pane

- **Timestamps sit in a margin** (redesign Step 6): a 64px right-aligned column, 18px from the
  text, with the measure widened by 90px so the text keeps its 68 characters. The column is there
  for every segment, stamped or not (stamps still come every 30 seconds, FE §4.2), so the text
  keeps one left edge — which polished prose and the tentative tail share. Below 700px the stamp
  goes back above the text.
- **Serif at 16.5px** by default, the owner's "smaller". The size is in Settings → Context →
  Reading (a slider, 14–28px, kept by the browser); the toolbar's two A buttons are gone. There is
  no keyboard shortcut of its own: Ctrl + and Ctrl − stay the browser's zoom.
- **The toolbar is icons with names**: the version switch (Live · Final, when there are two), Open
  a past recording, Show the raw transcript (`aria-pressed`, when there is polished prose), search,
  and the glossary — a book with its count as a badge, named "Glossary *N*".
- **Viewing a past recording is a card at the top of the pane** it names: a raised graphite card
  with a radish left edge, under the toolbar, above the scroller. It was a strip under the header.
- On a narrow transcript column (three columns on a small laptop) the pane's label and segment count
  give way first, then the search field's shortcut hint — a container query on the pane, since its
  width, not the window's, is what runs out.

### The details column (D-086)

**Three columns: details, transcript, assistant.** The owner's note: the video and the settings in a
column of their own, with the assistant as the other. 300px wide, 250px below 1280px, scrolling on
its own, in four sections, each a heading over its contents:

1. **Video** — the live preview while a window is recorded (the monitor, unchanged); the picture of
   the past recording on the main screen when one is; otherwise *No video* and how to record one.
   The clock shows only while something is captured.
2. **This recording** — started, length, words, questions: the session being or last recorded, or
   the recording being viewed.
3. **Capture** — source, when it transcribes, and the speech model, with a gear for the capture
   settings. At rest it describes what the last recording was set up to do.
4. **Glossary** — the terms, always there. The Glossary buttons in the transcript's toolbar and the
   assistant's header jump to it and outline it for a moment; they no longer open anything.

**Below 1060px the column folds into a Details tab** in place of the old Monitor tab: the tab bar
appears, the transcript and the assistant stay side by side, and the Details tab shows the column
across the width in their place. Below 900px it is one pane per tab, as before. A Glossary button on
a narrow screen opens the Details tab first.

### The assistant

- **Your questions sit in neutral bubbles** — graphite, never an accent wash — and answers are plain
  text beside them.
- **Suggested questions are in a pop-up** behind a spark icon to the left of the box
  (`aria-haspopup="menu"`), opening upward: one `menuitem` per configured prompt, with its hint.
  Arrow keys, Home and End move; Enter or a click asks it and puts focus in the box; Escape closes
  and returns focus to the button; a click outside or Tab closes it. While an answer streams the
  button is disabled. They were a row of chips that took a third of the pane's height.
- **Ask is an icon** (the send arrow, `aria-label="Ask"`), a 38px radish circle; while an answer
  streams it becomes Stop, its name and picture changing together.
- The header's Glossary button is the book with its count, as in the transcript's toolbar; it jumps
  to the glossary in the details column.

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
- [ ] Automated a11y check — `tests/frontend/` (structural rules over rendered HTML, and WCAG contrast over the tokens). Not axe: it needs a browser and a build step this project does not have. Does not replace the manual keyboard, contrast-in-context and 320 px passes (D-054), see `docs/checklist.md`
