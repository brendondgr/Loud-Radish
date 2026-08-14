# Shared Contracts

Schemas and types that **both** the backend and the frontend depend on. This directory is the seam
that keeps the two sides from drifting apart.

Currently empty — no API exists yet.

## What belongs here

- The OpenAPI specification, generated from the backend.
- Types generated from that specification for frontend consumption.

## What does not

- Logic of any kind. Contracts describe shapes, nothing else.
- Anything only one side uses.

## Rules

- The generated OpenAPI spec is authoritative.
  [../../../docs/api-contract.md](../../../docs/api-contract.md) is its human-readable companion and
  must agree with it.
- Frontend types are **generated**, never hand-copied from the backend.
- A contract change updates this directory, `docs/api-contract.md`,
  `web/backend/app/schemas/`, the frontend API client, and `docs/routes.md` — in the same change.
