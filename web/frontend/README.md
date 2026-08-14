# Frontend — not yet scaffolded

This directory will hold the browser client. It is intentionally empty.

**Assumed stack:** React + Vite + TypeScript, managed with `npm`. This was a default chosen during
repository initialization so structure work could proceed, not a confirmed decision — see Decision Log
D-005 in [../../docs/documentation.md](../../docs/documentation.md). **Confirm it before scaffolding.**

## To scaffold

Run from this directory, then record the actual commands in
[../../docs/workflow.md](../../docs/workflow.md):

```bash
npm create vite@latest . -- --template react-ts
```

## Expected layout

Per [../../docs/structure.md](../../docs/structure.md) and the ownership rules in
[../../docs/component-map.md](../../docs/component-map.md):

```text
web/frontend/
├── src/
│   ├── pages/       # Route-level views
│   ├── components/
│   │   ├── ui/      # Reusable primitives — must not know what a transcript is
│   │   └── layout/  # App chrome
│   ├── features/    # Domain UI — uploader, job list, transcript editor
│   ├── hooks/       # Hooks used by more than one feature
│   ├── lib/         # API client and non-visual helpers
│   ├── styles/      # Design tokens and global styles
│   └── assets/
├── public/
└── package.json
```

## Before writing components

Read [../../docs/design-system.md](../../docs/design-system.md). Its WCAG 2.1 AA and mobile/touch
baselines are requirements, not suggestions. Delete this file once the frontend exists.
