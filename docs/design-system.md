# Design System

*Last updated: 2026-08-15 (Phase 14 — final documentation pass)*

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
