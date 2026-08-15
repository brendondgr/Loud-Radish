"""A native Anthropic Messages client (BE §10.4).

Anthropic is reachable through an OpenAI-compatible shim, but the native API is used here because
three things differ in ways that matter: the system prompt is a **top-level field** rather than a
message, authentication uses ``x-api-key`` with a version header rather than a bearer token, and the
stream is a typed event sequence rather than a single chunk shape. Emulating all three through a
translation layer costs more than implementing it once.

Everything user-visible — the four-way failure taxonomy, the separation of reasoning from answer —
is identical to the OpenAI-compatible path, because that is the interface's whole purpose.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .contract import (
    GenerationOptions,
    LlmBackend,
    LlmCapabilities,
    LlmChunk,
    LlmMessage,
    LlmModelInfo,
    LlmUsage,
)
from .errors import (
    ConnectionTest,
    LlmError,
    auth_rejected,
    bad_response,
    model_missing,
    short_message,
    timed_out,
    unreachable,
)
from .openai_compatible import CONNECT_TIMEOUT_S, PROBE_TIMEOUT_S, READ_TIMEOUT_S

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.anthropic.com"
#: Pinned deliberately. An unpinned version header means a provider-side change can alter responses
#: without anything in this repository changing.
API_VERSION = "2023-06-01"


class AnthropicBackend(LlmBackend):
    """Chat over the Anthropic Messages API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "",
        base_url: str | None = None,
        context_window: int = 200_000,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._context_window = context_window
        self._client = client

    # -- identity ------------------------------------------------------------------

    @property
    def provider_id(self) -> str:
        """Stable identifier used in configuration."""
        return "anthropic"

    @property
    def model_id(self) -> str:
        """The configured model name."""
        return self._model

    @property
    def endpoint(self) -> str:
        """The base URL requests go to."""
        return self._base_url

    @property
    def capabilities(self) -> LlmCapabilities:
        """What this provider can do. Never local — requests leave the machine."""
        return LlmCapabilities(
            streaming=True,
            reasoning=False,
            context_window=self._context_window,
            local=False,
        )

    # -- plumbing ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "anthropic-version": API_VERSION}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def _open(self, read_timeout: float) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        timeout = httpx.Timeout(read_timeout, connect=CONNECT_TIMEOUT_S)
        return httpx.AsyncClient(timeout=timeout), True

    def _raise_for_status(self, response: httpx.Response, body: str = "") -> None:
        status = response.status_code
        if status < 400:
            return
        if status in (401, 403):
            raise auth_rejected("Anthropic", bool(self._api_key))
        if status == 404:
            raise bad_response(self._base_url, "HTTP 404 — check the model name and base URL")
        if status == 429:
            raise LlmError("Anthropic is rate limiting this application. Wait and retry.")
        raise bad_response(
            self._base_url, f"HTTP {status}: {short_message(body)}" if body else f"HTTP {status}"
        )

    # -- model discovery -----------------------------------------------------------

    async def list_models(self) -> list[LlmModelInfo]:
        """List available models via ``GET /v1/models``."""
        if not self._api_key:
            raise auth_rejected("Anthropic", has_credential=False)

        client, owned = self._open(PROBE_TIMEOUT_S)
        try:
            response = await client.get(
                f"{self._base_url}/v1/models?limit=100", headers=self._headers()
            )
            self._raise_for_status(response, _safe_text(response))
            payload = response.json()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise unreachable(self._base_url, type(exc).__name__) from exc
        except httpx.TimeoutException as exc:
            raise timed_out(self._base_url, PROBE_TIMEOUT_S) from exc
        except json.JSONDecodeError as exc:
            raise bad_response(self._base_url, "the model list was not JSON") from exc
        except httpx.HTTPError as exc:
            raise unreachable(self._base_url, type(exc).__name__) from exc
        finally:
            if owned:
                await client.aclose()

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise bad_response(self._base_url, "the model list had no 'data' array")
        return [
            LlmModelInfo(id=str(entry["id"]), owned_by="anthropic")
            for entry in data
            if isinstance(entry, dict) and entry.get("id")
        ]

    async def test(self) -> ConnectionTest:
        """Probe Anthropic, distinguishing a missing key from a rejected one."""
        if not self._api_key:
            return auth_rejected("Anthropic", has_credential=False).as_test()
        try:
            models = await self.list_models()
        except LlmError as exc:
            return exc.as_test()

        names = [model.id for model in models]
        if self._model and names and self._model not in names:
            return model_missing(self._model, "Anthropic", names).as_test()
        return ConnectionTest(
            result="connected",
            message=f"Connected to Anthropic. Using {self._model or '(none selected)'}.",
            models=names,
        )

    # -- generation ----------------------------------------------------------------

    async def stream(
        self, messages: list[LlmMessage], options: GenerationOptions | None = None
    ) -> AsyncIterator[LlmChunk]:
        """Stream an answer from the Messages API."""
        if not self._api_key:
            raise auth_rejected("Anthropic", has_credential=False)

        opts = options or GenerationOptions()
        # The system prompt is a top-level field here, not a message. Several system messages are
        # joined rather than dropped, so a caller composing context in parts is not silently
        # truncated to whichever one happened to come first.
        system_parts = [m.content for m in messages if m.role == "system"]
        turns = [m.as_dict() for m in messages if m.role != "system"]

        body: dict[str, Any] = {
            "model": self._model,
            "messages": turns,
            "max_tokens": opts.max_output_tokens,
            "temperature": opts.temperature,
            "stream": True,
            **opts.extra,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)

        client, owned = self._open(READ_TIMEOUT_S)
        usage_in = 0
        usage_out = 0
        finish_reason = ""

        try:
            async with client.stream(
                "POST", f"{self._base_url}/v1/messages", json=body, headers=self._headers()
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    self._raise_for_status(response, _safe_text(response))

                async for line in response.aiter_lines():
                    event = _parse_event(line)
                    if event is None:
                        continue

                    kind = event.get("type")
                    if kind == "content_block_delta":
                        delta = event.get("delta") or {}
                        text = delta.get("text") or ""
                        thinking = delta.get("thinking") or ""
                        if text or thinking:
                            yield LlmChunk(text=str(text), reasoning=str(thinking))
                    elif kind == "message_start":
                        usage_in = _usage_field(event.get("message"), "input_tokens")
                    elif kind == "message_delta":
                        usage_out = _usage_field(event, "output_tokens") or usage_out
                        finish_reason = str((event.get("delta") or {}).get("stop_reason") or "")
                    elif kind == "error":
                        detail = (event.get("error") or {}).get("message", "unknown error")
                        raise bad_response(self._base_url, short_message(str(detail)))

        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise unreachable(self._base_url, type(exc).__name__) from exc
        except httpx.TimeoutException as exc:
            raise timed_out(self._base_url, READ_TIMEOUT_S) from exc
        except httpx.HTTPError as exc:
            raise unreachable(self._base_url, type(exc).__name__) from exc
        finally:
            if owned:
                await client.aclose()

        yield LlmChunk(
            done=True,
            usage=LlmUsage(prompt_tokens=usage_in, completion_tokens=usage_out),
            finish_reason=finish_reason or "stop",
        )


def _parse_event(line: str) -> dict[str, Any] | None:
    """Decode one SSE ``data:`` line, skipping ``event:`` lines and keep-alives."""
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload:
        return None
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        logger.debug("Skipping unparseable Anthropic frame: %.120s", payload)
        return None
    return decoded if isinstance(decoded, dict) else None


def _usage_field(container: Any, field: str) -> int:
    """Read one token count out of a usage object, tolerating its absence."""
    if not isinstance(container, dict):
        return 0
    usage = container.get("usage")
    if not isinstance(usage, dict):
        return 0
    try:
        return int(usage.get(field) or 0)
    except (TypeError, ValueError):
        return 0


def _safe_text(response: httpx.Response) -> str:
    try:
        return response.text
    except Exception:  # noqa: BLE001 - the status code carries the useful information
        return ""
