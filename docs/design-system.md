# Design System

*Last updated: 2026-08-14 (repository initialization)*

> **Status: baseline only.** No visual design has been chosen and no frontend exists. This file
> records the non-negotiable quality and accessibility floor every UI in this repository must clear,
> plus the token structure to fill in once a visual direction is set.

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

For this project specifically: upload progress, `queued` / `processing` / `completed` / `failed` job
states, and long-transcript rendering performance all need deliberate treatment.

## Design Tokens

**Not yet defined.** When a visual direction is chosen, tokens live in
`web/frontend/src/styles/` and are recorded here. Fill in this table:

| Token group | Values | Status |
|---|---|---|
| Color — surface, text, border, accent, semantic (success/warning/error/info) | — | Undefined |
| Typography — family, scale, weights, line heights | — | Undefined |
| Spacing — a single consistent scale | — | Undefined |
| Radii | — | Undefined |
| Shadows / elevation | — | Undefined |
| Breakpoints | — | Undefined |
| Motion — durations, easings | — | Undefined |

Rules once defined:

- **No hardcoded values in components.** Colors, spacing, and radii come from tokens.
- Semantic naming (`surface-raised`, `text-muted`), not literal (`gray-200`).
- Light and dark themes both defined, both meeting the contrast floor above.

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
