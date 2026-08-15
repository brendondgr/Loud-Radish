"""Language model configuration, credentials, and connection testing.

Two rules shape every handler here.

**A credential never travels back.** ``PUT /api/llm/credential`` accepts one; nothing returns one.
Responses carry only whether a credential is present and where it would be read from, so a leak
would have to be written deliberately rather than arrived at by accident.

**A test result is never generic.** ``POST /api/llm/test`` answers with one of four outcomes and a
message naming the specific remedy (``docs/api-contract.md``). "Connection failed" leaves the user
with nothing to do; "no server responded at http://localhost:9090/v1 — check that it is running"
leaves them with one thing to do.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..config.credentials import CredentialStore, CredentialUnavailableError
from ..config.schema import LlmConfig
from ..schemas.api import (
    ConnectionTestResponse,
    LlmConfigResponse,
    LlmCredentialRequest,
)
from ..services.llm import LlmError, build_llm, credential_provider, describe_providers

router = APIRouter(prefix="/api/llm", tags=["llm"])


def _error(code: str, message: str, severity: str = "warning") -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "severity": severity}}


def _credentials(request: Request) -> CredentialStore:
    """The process-wide credential store, created once in the app factory."""
    store = getattr(request.app.state, "credentials", None)
    return store if store is not None else CredentialStore()


def _llm_config(request: Request, overrides: dict[str, Any] | None = None) -> LlmConfig:
    """Resolve the LLM configuration, optionally with unsaved form values applied.

    The overrides exist for the connection test: the user types an address, presses Test, and
    expects *that* address to be probed — not the one currently saved. Testing the saved value
    would make the button useless precisely when it is needed.
    """
    config: LlmConfig = request.app.state.config.resolve().llm
    if not overrides:
        return config

    try:
        return LlmConfig.model_validate(_merge(config.model_dump(), overrides))
    except Exception as exc:  # noqa: BLE001 - pydantic raises several validation types
        raise HTTPException(
            status_code=422,
            detail=_error("invalid-llm-config", f"Those settings are not valid: {exc}"),
        ) from exc


def _merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Merge ``overrides`` into a copy of ``base``, one level deep, dropping unknown keys.

    Unknown keys are dropped rather than rejected because ``GET /api/llm/config`` decorates its
    response with ``presets`` and ``providers`` for the settings form. Requiring the client to strip
    those before echoing the object back would make round-tripping the form a source of 422s.
    """
    merged = dict(base)
    for key, value in overrides.items():
        if key not in base:
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {k: v for k, v in {**merged[key], **value}.items() if k in merged[key]}
        else:
            merged[key] = value
    return merged


# -- configuration ---------------------------------------------------------------------


@router.get("/config", response_model=LlmConfigResponse)
async def get_llm_config(request: Request) -> dict[str, Any]:
    """Both provider configurations, and whether a credential exists. Never a credential value."""
    config = _llm_config(request)
    described = describe_providers(config, _credentials(request))
    return {
        "mode": config.mode,
        "local": {**config.local.model_dump(), "presets": described["local_presets"]},
        "api": {**config.api.model_dump(), "providers": described["api_providers"]},
        "generation": config.generation.model_dump(),
        "credential": described["credential"],
    }


# -- credentials -----------------------------------------------------------------------


@router.put("/credential")
async def put_credential(request: Request, body: LlmCredentialRequest) -> dict[str, Any]:
    """Store a credential in the OS credential store.

    A failure here is reported rather than swallowed: a key the user believes is saved but is not
    produces an authentication failure much later, with nothing connecting it to this moment.
    """
    store = _credentials(request)
    try:
        store.set(body.provider, body.value)
    except CredentialUnavailableError as exc:
        raise HTTPException(
            status_code=409, detail=_error("credential-store-unavailable", str(exc))
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=_error("empty-credential", str(exc))) from exc
    return store.status(body.provider)


@router.delete("/credential/{provider}")
async def delete_credential(request: Request, provider: str) -> dict[str, Any]:
    """Remove a stored credential. Removing one that was never there is not an error."""
    store = _credentials(request)
    store.delete(provider)
    return store.status(provider)


# -- discovery and testing ---------------------------------------------------------------


@router.get("/models")
async def get_llm_models(request: Request) -> dict[str, Any]:
    """Models the configured endpoint reports.

    A failure is a 200 with an empty list and an explanation, not an HTTP error: this is called to
    populate a dropdown while the user is still typing an address, and a red error for "you have
    not finished typing the URL yet" is noise.
    """
    config = _llm_config(request)
    try:
        backend = build_llm(config, _credentials(request))
        models = await backend.list_models()
    except LlmError as exc:
        return {"models": [], "note": exc.message, "endpoint": ""}

    return {
        "models": [model.as_dict() for model in models],
        "note": "",
        "endpoint": backend.endpoint,
    }


@router.post("/test", response_model=ConnectionTestResponse)
async def test_connection(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Probe the provider and report one of four specific outcomes.

    Accepts an optional partial LLM configuration so the settings form can test what is on screen
    rather than what is saved.
    """
    config = _llm_config(request, body or None)
    try:
        backend = build_llm(config, _credentials(request))
    except LlmError as exc:
        return exc.as_test().as_dict()

    return (await backend.test()).as_dict()


@router.get("/status")
async def get_llm_status(request: Request) -> dict[str, Any]:
    """A cheap description of the assistant's readiness, with no network call.

    The header reads this to decide between "Assistant ready" and "Assistant not set up", and it is
    read on every page load — so it must never wait on a model server that may not be running.
    """
    config = _llm_config(request)
    store = _credentials(request)
    provider = credential_provider(config)

    try:
        backend = build_llm(config, store)
    except LlmError as exc:
        return {
            "configured": False,
            "mode": config.mode,
            "provider": provider,
            "model": "",
            "endpoint": "",
            "local": True,
            "note": exc.message,
        }

    # "Configured" means a model has been chosen. Whether the server is actually up is what the
    # Test button answers; claiming readiness here would need a network call on every page load.
    configured = bool(backend.model_id)
    return {
        "configured": configured,
        "mode": config.mode,
        "provider": provider,
        "model": backend.model_id,
        "endpoint": backend.endpoint,
        "local": backend.capabilities.local,
        "note": "" if configured else "No model is selected. Choose one in Settings → Assistant.",
    }
