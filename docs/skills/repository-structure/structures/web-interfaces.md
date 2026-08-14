# Web Application Structure

This reference defines approved structures for website projects. Before creating files, the agent must use `website-architecture` to select one mode, define the framework stack, map routes, map data flow, document frontend/backend boundaries, and define the design-quality brief.

All website-specific code, assets, and runtime files live under the root `web/` directory. Repository documentation lives under `docs/`.

## Required Planning Files

Every website project must create or update:

- `docs/architecture.md`
- `docs/structure.md`
- `docs/routes.md`
- `docs/component-map.md`
- `docs/data-flow.md`
- `docs/deployment.md`
- `docs/design-system.md`
- `docs/api-contract.md` when backend/API contracts exist

## Stack Selection

Do not assume one frontend stack. Record the selected framework and optional libraries before scaffolding.

Common choices:

- Astro content or marketing site
- SvelteKit app
- React/Vite SPA
- React/Next.js App Router app
- React/Next.js marketing or product site
- Flask/Jinja or Flask/HTMX monolith
- Flask/FastAPI/Django API plus separate frontend
- Django application
- Dockerized full-stack app

Optional frontend libraries should be selected by job:

- shadcn/ui for copy-owned React component scaffolds
- Radix UI for accessible React primitives
- Headless UI or native HTML for headless/custom controls
- TanStack Query for API-backed server state
- TanStack Router or React Router for client-side routing
- TanStack Table for dense tables
- React Hook Form plus Zod for complex React forms
- Framer Motion/Motion, GSAP, CSS transitions, or View Transitions for purposeful motion

Avoid installing overlapping libraries without a documented reason.

## Mode A: Static Site

Use for simple portfolios, landing pages, documentation pages, and non-authenticated informational sites.

```text
root/
|-- docs/
|-- web/
|   |-- pages/
|   |-- layouts/
|   |-- components/
|   |-- assets/
|   |-- styles/
|   `-- index.html
|-- scripts/
`-- README.md
```

## Mode B: Astro Site

Use when content, performance, static generation, and islands architecture are central.

```text
root/
|-- docs/
|-- web/
|   |-- src/
|   |   |-- pages/
|   |   |-- layouts/
|   |   |-- components/
|   |   |-- content/
|   |   |-- styles/
|   |   `-- assets/
|   |-- public/
|   |-- astro.config.mjs
|   `-- package.json
`-- README.md
```

## Mode C: SvelteKit App

Use when Svelte routing, SSR, actions, stores, and app-style interactivity are desired.

```text
root/
|-- docs/
|-- web/
|   |-- src/
|   |   |-- routes/
|   |   |-- lib/
|   |   |   |-- components/
|   |   |   |-- server/
|   |   |   |-- stores/
|   |   |   `-- styles/
|   |   `-- app.html
|   |-- static/
|   |-- svelte.config.js
|   |-- vite.config.ts
|   `-- package.json
`-- README.md
```

## Mode D: React/Vite Frontend

Use for a client-heavy SPA or frontend shell, usually backed by APIs.

```text
root/
|-- docs/
|-- web/
|   |-- src/
|   |   |-- app/
|   |   |-- pages/
|   |   |-- routes/
|   |   |-- layouts/
|   |   |-- components/
|   |   |   |-- ui/
|   |   |   |-- layout/
|   |   |   `-- feature/
|   |   |-- features/
|   |   |-- hooks/
|   |   |-- lib/
|   |   |-- styles/
|   |   `-- assets/
|   |-- public/
|   |-- vite.config.ts
|   `-- package.json
`-- README.md
```

## Mode E: React/Next.js App

Use for Next.js App Router projects, SSR/SSG/ISR, server actions, route handlers, product sites, dashboards, and full-stack React apps.

```text
root/
|-- docs/
|-- web/
|   |-- app/
|   |   |-- (marketing)/
|   |   |-- (app)/
|   |   |-- api/
|   |   |-- layout.tsx
|   |   `-- page.tsx
|   |-- components/
|   |   |-- ui/
|   |   |-- layout/
|   |   `-- feature/
|   |-- features/
|   |-- hooks/
|   |-- lib/
|   |-- styles/
|   |-- public/
|   |-- next.config.js
|   `-- package.json
`-- README.md
```

For shadcn/ui, keep copied components in `web/components/ui/`. For Radix UI wrappers, document ownership in `docs/component-map.md`. For TanStack Query, tables, routers, or React Hook Form, document provider and hook ownership.

## Mode F: Flask/Jinja or Flask/HTMX Monolith

Use when Flask renders server-side templates directly.

```text
root/
|-- docs/
|-- web/
|   |-- app/
|   |   |-- __init__.py
|   |   |-- routes/
|   |   |-- services/
|   |   |-- models/
|   |   |-- schemas/
|   |   |-- templates/
|   |   |   |-- base.html
|   |   |   |-- pages/
|   |   |   |-- layouts/
|   |   |   `-- components/
|   |   `-- static/
|   |       |-- css/
|   |       |-- js/
|   |       `-- images/
|-- tests/
|-- scripts/
|-- pyproject.toml
|-- .env.example
`-- README.md
```

## Mode G: API Plus Separate Frontend

Use when a backend owns API endpoints and a separate frontend owns rendering. The backend may be Flask, FastAPI, Django, or another explicit framework.

```text
root/
|-- docs/
|-- web/
|   |-- frontend/
|   |   |-- src/
|   |   |   |-- app/
|   |   |   |-- pages/
|   |   |   |-- layouts/
|   |   |   |-- components/
|   |   |   |-- features/
|   |   |   |-- hooks/
|   |   |   |-- lib/
|   |   |   |-- styles/
|   |   |   `-- assets/
|   |   |-- public/
|   |   `-- package.json
|   |-- backend/
|   |   |-- app/
|   |   |   |-- routes/
|   |   |   |-- services/
|   |   |   |-- models/
|   |   |   |-- schemas/
|   |   |   |-- auth/
|   |   |   `-- config.py
|   |   |-- tests/
|   |   `-- pyproject.toml
|   `-- shared/
|       `-- contracts/
|-- scripts/
|-- .env.example
`-- README.md
```

## Mode H: Django Application

Use when Django owns views, templates, apps, ORM, migrations, and admin behavior.

```text
root/
|-- docs/
|-- web/
|   |-- config/
|   |   |-- settings/
|   |   |-- urls.py
|   |   |-- asgi.py
|   |   `-- wsgi.py
|   |-- apps/
|   |   `-- <domain_app>/
|   |       |-- migrations/
|   |       |-- templates/
|   |       |-- static/
|   |       |-- models.py
|   |       |-- views.py
|   |       |-- urls.py
|   |       `-- tests.py
|   |-- static/
|   `-- templates/
|-- scripts/
|-- pyproject.toml
|-- .env.example
`-- README.md
```

## Mode I: Dockerized Web App

Use when local development, staging, or production must run through containers.

```text
root/
|-- docs/
|-- web/
|   |-- frontend/
|   |-- backend/
|   `-- shared/
|       `-- contracts/
|-- infra/
|   |-- docker/
|   |-- nginx/
|   `-- compose.yaml
|-- scripts/
|   |-- dev.sh
|   |-- build.sh
|   `-- test.sh
|-- .env.example
`-- README.md
```

## Ownership Guidelines

- Route/page views belong in framework route files, `pages/`, or backend view modules.
- Reusable visual pieces belong in `components/ui/`.
- Navigation, sidebars, footers, and app chrome belong in `components/layout/` or `layouts/`.
- Domain-specific UI belongs in `components/feature/` or `features/`.
- Hooks belong in `hooks/` unless they are private to a feature.
- Shared frontend helpers belong in `lib/`.
- Server routes belong in backend route modules, Next route handlers, server actions, or framework URL/view files.
- API schemas, OpenAPI specs, and shared types belong in `shared/contracts/` when frontend and backend both depend on them.
- Design tokens and visual decisions must be documented in `docs/design-system.md`.

## Python Test Layout

When a web project uses Python, keep lightweight tests in a top-level `tests/` directory and split them into purpose-based sub-directories as coverage grows.

```text
tests/
|-- app/
|   |-- test_routes.py
|   `-- test_views.py
|-- auth/
|   `-- test_sessions.py
|-- data/
|   `-- test_serializers.py
`-- utils/
    `-- test_helpers.py
```

- Keep shared fixtures close to the test area they support.
- Prefer small, targeted files over a large flat test dump.
- Name folders after behavior or subsystem boundaries, not implementation details.
