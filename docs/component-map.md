# Component Map

*Last updated: 2026-08-14 (repository initialization)*

> **Status: no components exist.** `web/frontend/` is not yet scaffolded. This file defines the
> ownership rules that will govern it, plus the components anticipated at initialization. Update it in
> the same change that adds, moves, or renames a component.

## Ownership Rules

Where a component lives is determined by what it knows about, not by what it looks like.

| Location | Holds | Test |
|---|---|---|
| `web/frontend/src/pages/` | Route-level views | Is it addressable by a URL? |
| `web/frontend/src/components/ui/` | Reusable visual primitives — button, input, spinner, dialog | Would it make sense in a different app? |
| `web/frontend/src/components/layout/` | App chrome — header, nav, sidebar, footer, shells | Does it wrap other content? |
| `web/frontend/src/features/` | Domain-specific UI — uploader, transcript editor, job list | Does it know what a transcript is? |
| `web/frontend/src/hooks/` | Reusable hooks | Is it used by more than one feature? |
| `web/frontend/src/lib/` | Non-visual helpers, the API client | Does it render nothing? |
| `web/frontend/src/styles/` | Design tokens, global styles | See `docs/design-system.md` |

The line that matters: **`components/ui/` must never know what a transcript is.** Domain knowledge
lives in `features/`. A hook used by exactly one feature stays inside that feature.

## Anticipated Components

Proposed only — none implemented.

| Component | Location | Purpose |
|---|---|---|
| `AudioUploader` | `features/upload/` | File selection, validation, upload with progress |
| `JobStatus` | `features/jobs/` | Poll and display transcription job state |
| `JobList` | `features/jobs/` | Table of past jobs |
| `TranscriptViewer` | `features/transcript/` | Render a completed transcript |
| `TranscriptEditor` | `features/transcript/` | Correct and export a transcript |
| `AppShell` | `components/layout/` | Header, nav, and page frame |
| `Button`, `Input`, `Spinner`, `Dialog` | `components/ui/` | Visual primitives |
| `useTranscriptionJob` | `features/jobs/` | Job polling; feature-local, not in `hooks/` |
| `apiClient` | `lib/` | Typed fetch wrapper over the contracts in `web/shared/contracts/` |

## Third-Party Component Libraries

**None selected.** If a library is adopted later, record it here with its ownership model:

- **shadcn/ui** — copied source; components live in `components/ui/` and are owned by this repository.
- **Radix UI** — wrapped primitives; document which wrapper owns which primitive.
- **Headless UI or native HTML** — for headless and custom controls.

Do not install overlapping libraries without recording the reason in `docs/documentation.md`.

## Requirements Every Component Must Meet

From `docs/design-system.md`:

- Keyboard operable, with a visible focus indicator.
- Interactive targets at least 44×44 CSS pixels.
- Text contrast at least 4.5:1; UI component and graphic contrast at least 3:1.
- Usable at a 320 px viewport width without horizontal scrolling.
- State conveyed by more than color alone.
- Loading, empty, and error states defined — not just the success path.
