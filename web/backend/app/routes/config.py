"""Configuration reading and writing.

The backend owns configuration (FE §8.1). The frontend reads it, presents it, writes changes back,
and re-reads — it never keeps a parallel notion of what the settings are. Every write therefore
returns the resolved configuration, so the client cannot drift.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..config import CLASS_CONSEQUENCE, HotSwapClass
from ..config.presets import PRESET_DESCRIPTIONS, preset_names
from ..schemas.api import ConfigPatchRequest, ConfigPatchResponse, ConfigResponse, PresetRequest

router = APIRouter(prefix="/api/config", tags=["config"])


def _presets() -> list[dict[str, str]]:
    return [{"name": name, "description": PRESET_DESCRIPTIONS[name]} for name in preset_names()]


@router.get("", response_model=ConfigResponse)
async def get_config(request: Request) -> dict[str, Any]:
    """The full resolved configuration."""
    return {"config": request.app.state.config.as_dict(), "presets": _presets()}


@router.patch("", response_model=ConfigPatchResponse)
async def patch_config(request: Request, body: ConfigPatchRequest) -> dict[str, Any]:
    """Apply dotted-path changes and report what applying them costs.

    The hot-swap class comes back with the response so the frontend can warn *before* the user
    commits to something that will interrupt transcription (BE §13.3).
    """
    config = request.app.state.config
    try:
        hot_swap: HotSwapClass = config.update(body.changes, layer=body.layer)
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
    }


@router.post("/save")
async def save_config(request: Request) -> dict[str, Any]:
    """Persist runtime changes to the user config file. Credentials are never written."""
    config = request.app.state.config
    config.save()
    return {"saved": True, "path": str(config.path)}
