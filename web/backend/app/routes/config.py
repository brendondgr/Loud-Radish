"""Configuration reading and writing.

The backend owns configuration (FE §8.1). The frontend reads it, presents it, writes changes back,
and re-reads — it never keeps a parallel notion of what the settings are. Every write therefore
returns the resolved configuration, so the client cannot drift.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..config import CLASS_CONSEQUENCE, HotSwapClass
from ..config.hotswap import classify_many
from ..config.presets import PRESET_DESCRIPTIONS, preset_names
from ..config.store import persists
from ..schemas.api import ConfigPatchRequest, ConfigPatchResponse, ConfigResponse, PresetRequest
from ..services.dictation import prompts as dictation_prompts
from ..services.polish import prompts as polish_prompts

router = APIRouter(prefix="/api/config", tags=["config"])


def _presets() -> list[dict[str, str]]:
    return [{"name": name, "description": PRESET_DESCRIPTIONS[name]} for name in preset_names()]


def _prompt_defaults() -> dict[str, str]:
    """The shipped instruction list behind each settable one (D-068).

    Sent with every configuration response rather than from an endpoint of its own. The settings
    panel needs it on open, on Reset, and after any write that re-renders the field, and a client
    that has to remember to fetch it separately is a client that will one day render an empty box
    where the instructions should be.
    """
    return {
        "polish.instructions": polish_prompts.DEFAULT_POLISH_PROMPT,
        "dictation.instructions": dictation_prompts.DEFAULT_DICTATION_PROMPT,
    }


@router.get("", response_model=ConfigResponse)
async def get_config(request: Request) -> dict[str, Any]:
    """The full resolved configuration."""
    return {
        "config": request.app.state.config.as_dict(),
        "presets": _presets(),
        "prompt_defaults": _prompt_defaults(),
    }


@router.patch("", response_model=ConfigPatchResponse)
async def patch_config(request: Request, body: ConfigPatchRequest) -> dict[str, Any]:
    """Apply dotted-path changes and report what applying them costs.

    The hot-swap class comes back with the response so the frontend can warn *before* the user
    commits to something that will interrupt transcription (BE §13.3).
    """
    config = request.app.state.config
    # Which input to listen to is written straight through to the config file, whatever layer was
    # asked for. Everything else goes to the requested layer and waits for Save. See
    # `PERSISTENT_PATHS` for why the microphone is not like a threshold — and note that this lives
    # here, on the shared route, so the tray's device picker and the native settings window inherit
    # it rather than each having to remember.
    lasting = {path: value for path, value in body.changes.items() if persists(path)}
    passing = {path: value for path, value in body.changes.items() if not persists(path)}
    try:
        config.update(passing, layer=body.layer)
        config.persist(lasting)
        # Classified over every path the caller sent, not per group: the user is warned about the
        # worst consequence of the whole write, which is what `update` reports for a single layer.
        hot_swap: HotSwapClass = classify_many(list(body.changes))
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "invalid-config",
                    "message": f"That setting could not be applied: {exc}",
                    "severity": "warning",
                }
            },
        ) from exc

    manager = getattr(request.app.state, "session_manager", None)
    if manager is not None and hot_swap is HotSwapClass.LIVE:
        manager.apply_live_config()

    return {
        "applied": True,
        "hot_swap": str(hot_swap),
        "consequence": CLASS_CONSEQUENCE[hot_swap],
        "config": config.as_dict(),
        "prompt_defaults": _prompt_defaults(),
    }


@router.post("/preset", response_model=ConfigPatchResponse)
async def apply_preset(request: Request, body: PresetRequest) -> dict[str, Any]:
    """Apply a named profile — Accuracy, Balanced, or Low resource."""
    config = request.app.state.config
    try:
        hot_swap = config.apply_preset(body.name)
    except KeyError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "unknown-preset", "message": str(exc).strip("'")}},
        ) from exc

    return {
        "applied": True,
        "hot_swap": str(hot_swap),
        "consequence": CLASS_CONSEQUENCE[hot_swap],
        "config": config.as_dict(),
        "prompt_defaults": _prompt_defaults(),
    }


@router.post("/save")
async def save_config(request: Request) -> dict[str, Any]:
    """Persist runtime changes to the user config file. Credentials are never written."""
    config = request.app.state.config
    config.save()
    return {"saved": True, "path": str(config.path)}
