"""An OpenAI-compatible chat client (BE §10.3).

One client covers Ollama, LM Studio, llama.cpp's server, vLLM, LiteLLM, OpenRouter, OpenAI itself,
and every relay in front of them, because they all speak the same two endpoints: ``GET /models`` and
``POST /chat/completions``. The differences that matter are in the *failures*, and those are
normalised here into the four-way taxonomy in :mod:`.errors`.

**Reasoning models.** Several servers now stream the model's working in ``delta.reasoning`` (or
``delta.reasoning_content``) alongside ``delta.content``. Two consequences the rest of the
application must not have to know about:

* the two fields are kept apart — merging them shows the user a monologue instead of an answer;
* an answer can legitimately arrive with **no content at all** when the token budget was spent on
  reasoning, which surfaces as ``finish_reason == "length"`` and an empty ``text``.

**Not thinking at all.** Built with ``disable_reasoning``, every request carries
:data:`NO_REASONING_EXTRAS`. There is no portable field for this, so several are sent, and an
endpoint that answers them with 400 or 422 is asked once more without them and never sent them
again in this process (D-073).

**Timeouts.** Connection and read timeouts are separate. A local model may take a minute to answer,
which is normal, but should refuse to connect instantly if nothing is listening — one timeout value
for both would either declare a working server dead or hang for a minute on a wrong port.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..gpu.turns import GpuTurns
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
    LlmError,
    auth_rejected,
    bad_response,
    short_message,
    timed_out,
    unreachable,
)

logger = logging.getLogger(__name__)

#: Seconds to wait for the TCP connection. Short: nothing listening should fail immediately.
CONNECT_TIMEOUT_S = 4.0
#: Seconds to wait between streamed chunks. Generous: the first token from a cold local model can
#: take a while, and cutting it off looks identical to a broken server.
READ_TIMEOUT_S = 180.0
#: Seconds allowed for a whole non-streaming call, such as listing models.
PROBE_TIMEOUT_S = 10.0

#: Extra request-body fields that ask a server not to think before answering.
#:
#: Each is understood by some servers and ignored by the rest. ``chat_template_kwargs`` reaches the
#: chat template on llama.cpp and vLLM, and it is the one that works for gemma-4 behind llama.cpp:
#: measured on 2026-09-18, a one-sentence request cost 242 tokens without it and 12 with it.
#: ``reasoning_effort`` and llama.cpp's own ``reasoning_budget`` were both ignored there.
#: ``reasoning_effort`` is what OpenAI and vLLM read, and ``think`` is what Ollama reads.
NO_REASONING_EXTRAS: dict[str, Any] = {
    "chat_template_kwargs": {"enable_thinking": False},
    "reasoning_effort": "none",
    "think": False,
}

#: The statuses a server uses when it refuses a field it does not know. Anything else — a 500, a
#: 503 while a model loads — says nothing about the fields and is reported as it is.
REJECTED_FIELD_STATUSES = frozenset({400, 422})

#: Endpoints that have refused :data:`NO_REASONING_EXTRAS`. Kept for the life of the process,
#: because a backend is built per request and would otherwise pay for the refusal every time.
_REFUSES_REASONING_FIELDS: set[str] = set()


class _FieldsRefused(Exception):
    """Raised before anything is yielded, so the request can be made again without the fields."""


#: Fields carrying the model's working rather than its answer. Servers disagree on the name.
REASONING_KEYS = ("reasoning", "reasoning_content")


def normalise_base_url(endpoint: str) -> str:
    """Return ``endpoint`` without a trailing slash, so path joining never doubles one."""
    return endpoint.rstrip("/")


def _hosts_locally(endpoint: str) -> bool:
    """Whether this endpoint keeps data on this machine.

    Drives the privacy indicator, so it errs toward *not* local: an address this function cannot
    parse is treated as remote rather than quietly claiming privacy the user does not have.
    """
    try:
        host = (httpx.URL(endpoint).host or "").lower()
    except Exception:  # noqa: BLE001 - a malformed address is simply not known to be local
        return False
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or host.endswith(".local")


class OpenAiCompatibleBackend(LlmBackend):
    """Chat completions over any OpenAI-compatible HTTP API."""

    def __init__(
        self,
        endpoint: str,
        model: str = "",
        api_key: str | None = None,
        context_window: int = 8192,
        provider_id: str = "openai-compatible",
        client: httpx.AsyncClient | None = None,
        disable_reasoning: bool = False,
        gpu_turns: GpuTurns | None = None,
    ) -> None:
        self._endpoint = normalise_base_url(endpoint)
        self._model = model
        self._api_key = api_key
        self._context_window = context_window
        self._provider_id = provider_id
        #: Injected by tests. When absent a client is created per request, which costs a negligible
        #: amount over loopback and avoids owning a connection pool with no lifecycle to close it.
        self._client = client
        #: Set once a stream has been seen to carry reasoning, so the UI can offer to show it.
        self._saw_reasoning = False
        self._disable_reasoning = disable_reasoning
        #: Set when this server computes on the speech model's GPU: every stream waits out a
        #: speech pass before it starts, and holds the GPU until it ends (D-072).
        self._gpu_turns = gpu_turns

    # -- identity ------------------------------------------------------------------

    @property
    def provider_id(self) -> str:
        """Stable identifier used in configuration."""
        return self._provider_id

    @property
    def model_id(self) -> str:
        """The configured model name, exactly as the server must receive it."""
        return self._model

    @property
    def endpoint(self) -> str:
        """The base URL, without a trailing slash."""
        return self._endpoint

    @property
    def capabilities(self) -> LlmCapabilities:
        """What this provider can do, as far as is known without contacting it."""
        return LlmCapabilities(
            streaming=True,
            reasoning=self._saw_reasoning,
            context_window=self._context_window,
            local=_hosts_locally(self._endpoint),
        )

    # -- plumbing ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _timeout(self, read: float) -> httpx.Timeout:
        return httpx.Timeout(read, connect=CONNECT_TIMEOUT_S)

    def _open(self, read_timeout: float) -> tuple[httpx.AsyncClient, bool]:
        """Return a client and whether the caller owns it."""
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(timeout=self._timeout(read_timeout)), True

    def _raise_for_status(self, response: httpx.Response, body: str = "") -> None:
        """Translate an HTTP status into the taxonomy, keeping the server's own words."""
        status = response.status_code
        if status < 400:
            return

        detail = short_message(body or _safe_text(response))
        if status in (401, 403):
            raise auth_rejected(self._endpoint, bool(self._api_key))
        if status == 404:
            raise bad_response(self._endpoint, f"HTTP 404 at {response.request.url.path}")
        if status == 429:
            raise LlmError(f"{self._endpoint} is rate limiting this application. Wait and retry.")
        raise bad_response(self._endpoint, f"HTTP {status}{f': {detail}' if detail else ''}")

    # -- model discovery -----------------------------------------------------------

    async def list_models(self) -> list[LlmModelInfo]:
        """List the models the endpoint reports, via ``GET {endpoint}/models``."""
        client, owned = self._open(PROBE_TIMEOUT_S)
        url = f"{self._endpoint}/models"
        try:
            response = await client.get(
                url, headers=self._headers(), timeout=self._timeout(PROBE_TIMEOUT_S)
            )
            self._raise_for_status(response)
            payload = response.json()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise unreachable(self._endpoint, type(exc).__name__) from exc
        except httpx.TimeoutException as exc:
            raise timed_out(self._endpoint, PROBE_TIMEOUT_S) from exc
        except json.JSONDecodeError as exc:
            raise bad_response(self._endpoint, "the model list was not JSON") from exc
        except httpx.HTTPError as exc:
            raise unreachable(self._endpoint, type(exc).__name__) from exc
        finally:
            if owned:
                await client.aclose()

        return _parse_models(payload, self._endpoint)

    # -- generation ----------------------------------------------------------------

    async def stream(
        self, messages: list[LlmMessage], options: GenerationOptions | None = None
    ) -> AsyncIterator[LlmChunk]:
        """Stream an answer as server-sent events."""
        opts = options or GenerationOptions()
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [message.as_dict() for message in messages],
            "temperature": opts.temperature,
            "max_tokens": opts.max_output_tokens,
            "stream": True,
        }
        asks_not_to_think = (
            self._disable_reasoning and self._endpoint not in _REFUSES_REASONING_FIELDS
        )
        # The caller's own fields win: a request that needs something specific says so.
        first = {**body, **NO_REASONING_EXTRAS, **opts.extra} if asks_not_to_think else None

        if self._gpu_turns is None:
            async for chunk in self._stream_with_fallback(body, first, opts):
                yield chunk
            return
        async with self._gpu_turns.language_model():
            async for chunk in self._stream_with_fallback(body, first, opts):
                yield chunk

    async def _stream_with_fallback(
        self, body: dict[str, Any], first: dict[str, Any] | None, opts: GenerationOptions
    ) -> AsyncIterator[LlmChunk]:
        """Ask with the no-reasoning fields; ask once more without them if they are refused."""
        try:
            async for chunk in self._stream_once(first or {**body, **opts.extra}, bool(first)):
                yield chunk
        except _FieldsRefused:
            _REFUSES_REASONING_FIELDS.add(self._endpoint)
            logger.info(
                "%s refused the no-reasoning fields; sending requests without them",
                self._endpoint,
            )
            async for chunk in self._stream_once({**body, **opts.extra}, False):
                yield chunk

    async def _stream_once(
        self, body: dict[str, Any], refusal_is_retryable: bool
    ) -> AsyncIterator[LlmChunk]:
        """Make one request. Raises :class:`_FieldsRefused` before yielding, or not at all."""
        client, owned = self._open(READ_TIMEOUT_S)
        url = f"{self._endpoint}/chat/completions"
        usage: LlmUsage | None = None
        finish_reason = ""

        try:
            async with client.stream(
                "POST",
                url,
                json=body,
                headers=self._headers(),
                timeout=self._timeout(READ_TIMEOUT_S),
            ) as response:
                if response.status_code >= 400:
                    # The body must be read before it can be quoted; on a streaming response that
                    # is an explicit step rather than an attribute.
                    await response.aread()
                    if refusal_is_retryable and response.status_code in REJECTED_FIELD_STATUSES:
                        raise _FieldsRefused
                    self._raise_for_status(response, _safe_text(response))

                async for line in response.aiter_lines():
                    event = _parse_sse_line(line)
                    if event is None:
                        continue
                    if event is _DONE:
                        break

                    chunk, chunk_usage, reason = _parse_delta(event)
                    if chunk_usage is not None:
                        usage = chunk_usage
                    if reason:
                        finish_reason = reason
                    if chunk.reasoning:
                        self._saw_reasoning = True
                    if not chunk.is_empty:
                        yield chunk

        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise unreachable(self._endpoint, type(exc).__name__) from exc
        except httpx.TimeoutException as exc:
            raise timed_out(self._endpoint, READ_TIMEOUT_S) from exc
        except httpx.HTTPError as exc:
            raise unreachable(self._endpoint, type(exc).__name__) from exc
        finally:
            if owned:
                await client.aclose()

        yield LlmChunk(done=True, usage=usage, finish_reason=finish_reason or "stop")


# -- parsing -------------------------------------------------------------------------

#: Sentinel for the ``[DONE]`` line that terminates an OpenAI-style stream.
_DONE = object()


def _parse_sse_line(line: str) -> Any:
    """Return the decoded payload of one SSE line, ``_DONE``, or ``None`` to skip it.

    Non-``data:`` lines (comments, ``event:``, blank keep-alives) are skipped rather than treated
    as errors: they are ordinary in SSE and several servers emit them.
    """
    if not line or not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload:
        return None
    if payload == "[DONE]":
        return _DONE
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        # One unreadable frame is not worth aborting a working stream over.
        logger.debug("Skipping unparseable SSE frame: %.120s", payload)
        return None


def _parse_delta(event: Any) -> tuple[LlmChunk, LlmUsage | None, str]:
    """Pull text, reasoning, usage, and finish reason out of one streamed event."""
    usage = _parse_usage(event.get("usage")) if isinstance(event, dict) else None
    if not isinstance(event, dict):
        return LlmChunk(), usage, ""

    choices = event.get("choices") or []
    if not choices:
        return LlmChunk(), usage, ""

    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta") or {}
    if not isinstance(delta, dict):
        delta = {}

    text = delta.get("content") or ""
    reasoning = ""
    for key in REASONING_KEYS:
        value = delta.get(key)
        if value:
            reasoning = str(value)
            break

    return (
        LlmChunk(text=str(text), reasoning=reasoning),
        usage,
        str(choice.get("finish_reason") or ""),
    )


def _parse_usage(raw: Any) -> LlmUsage | None:
    """Read token counts, tolerating providers that omit them."""
    if not isinstance(raw, dict):
        return None
    return LlmUsage(
        prompt_tokens=int(raw.get("prompt_tokens") or 0),
        completion_tokens=int(raw.get("completion_tokens") or 0),
    )


def _parse_models(payload: Any, endpoint: str) -> list[LlmModelInfo]:
    """Read a ``GET /models`` body, which is ``{"data": [...]}`` on every compliant server."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise bad_response(endpoint, "the model list had no 'data' array")

    models: list[LlmModelInfo] = []
    for entry in data:
        if isinstance(entry, str):
            models.append(LlmModelInfo(id=entry))
        elif isinstance(entry, dict) and entry.get("id"):
            models.append(
                LlmModelInfo(id=str(entry["id"]), owned_by=str(entry.get("owned_by") or ""))
            )
    return models


def _safe_text(response: httpx.Response) -> str:
    """Read a response body without letting a decoding failure mask the real error."""
    try:
        return response.text
    except Exception:  # noqa: BLE001 - the status code is the useful part regardless
        return ""
