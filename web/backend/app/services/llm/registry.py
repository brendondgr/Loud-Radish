"""Turning configuration into a language model backend.

The mirror of ``services/asr/registry.py``, and it exists for the same reason: chat orchestration
receives an :class:`~.contract.LlmBackend` and never learns which one it got.

**Credentials are read here and nowhere else.** :func:`build_llm` is the single place a key crosses
from the credential store into a client, which is what makes "never logged, never serialised, never
returned over HTTP" checkable by reading one function rather than auditing the whole package.
"""

from __future__ import annotations

from typing import Any

from ...config.credentials import CredentialStore
from ...config.schema import LlmConfig
from .anthropic import DEFAULT_BASE_URL, AnthropicBackend
from .contract import GenerationOptions, LlmBackend
from .errors import LlmConfigError
from .openai_compatible import OpenAiCompatibleBackend

#: Hosted providers reachable through their OpenAI-compatible surface, and where that surface lives.
#: Anthropic is deliberately absent — it gets a native client (see :mod:`.anthropic`).
OPENAI_COMPATIBLE_PROVIDERS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "mistral": "https://api.mistral.ai/v1",
}

#: Every provider the API tab can offer, in presentation order.
API_PROVIDERS: list[dict[str, str]] = [
    {
        "id": "anthropic",
        "name": "Anthropic",
        "base_url": DEFAULT_BASE_URL,
        "env_var": "ANTHROPIC_API_KEY",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": OPENAI_COMPATIBLE_PROVIDERS["openai"],
        "env_var": "OPENAI_API_KEY",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "base_url": OPENAI_COMPATIBLE_PROVIDERS["openrouter"],
        "env_var": "OPENROUTER_API_KEY",
    },
    {
        "id": "groq",
        "name": "Groq",
        "base_url": OPENAI_COMPATIBLE_PROVIDERS["groq"],
        "env_var": "GROQ_API_KEY",
    },
    {
        "id": "together",
        "name": "Together",
        "base_url": OPENAI_COMPATIBLE_PROVIDERS["together"],
        "env_var": "TOGETHER_API_KEY",
    },
    {
        "id": "mistral",
        "name": "Mistral",
        "base_url": OPENAI_COMPATIBLE_PROVIDERS["mistral"],
        "env_var": "MISTRAL_API_KEY",
    },
]

#: Local servers worth suggesting, with the address each one listens on out of the box. Offered as
#: shortcuts in the settings interface — the endpoint remains a free-text field, because the whole
#: point of an OpenAI-compatible client is that it works with servers nobody here has heard of.
LOCAL_PRESETS: list[dict[str, str]] = [
    {"name": "Ollama", "endpoint": "http://localhost:11434/v1"},
    {"name": "LM Studio", "endpoint": "http://localhost:1234/v1"},
    {"name": "llama.cpp server", "endpoint": "http://localhost:8080/v1"},
    {"name": "vLLM", "endpoint": "http://localhost:8000/v1"},
]


def build_llm(config: LlmConfig, credentials: CredentialStore | None = None) -> LlmBackend:
    """Construct the configured backend.

    Raises:
        LlmConfigError: when the configuration cannot produce a working client — an empty endpoint
            in local mode, or an unknown provider in API mode. The message names the field.
    """
    store = credentials or CredentialStore()

    if config.mode == "local":
        endpoint = config.local.endpoint.strip()
        if not endpoint:
            raise LlmConfigError(
                "No local server address is set. Enter one in Settings → Assistant, "
                "for example http://localhost:11434/v1."
            )
        return OpenAiCompatibleBackend(
            endpoint=endpoint,
            model=config.local.model,
            # A local server usually needs no key. One is passed when present anyway, because
            # relays and proxies in front of local models frequently do.
            api_key=store.get("local"),
            context_window=config.local.context_window,
            provider_id="local",
        )

    provider = config.api.provider.strip().lower()
    if provider == "anthropic":
        return AnthropicBackend(
            api_key=store.get("anthropic"),
            model=config.api.model,
            base_url=config.api.base_url or DEFAULT_BASE_URL,
            context_window=config.api.context_window,
        )

    base_url = config.api.base_url or OPENAI_COMPATIBLE_PROVIDERS.get(provider)
    if not base_url:
        known = ", ".join(sorted({*OPENAI_COMPATIBLE_PROVIDERS, "anthropic"}))
        raise LlmConfigError(
            f"Unknown provider {provider!r}. Choose one of: {known}, "
            f"or set a base URL for it in Settings → Assistant."
        )

    return OpenAiCompatibleBackend(
        endpoint=base_url,
        model=config.api.model,
        api_key=store.get(provider),
        context_window=config.api.context_window,
        provider_id=provider,
    )


def generation_options(config: LlmConfig) -> GenerationOptions:
    """Read the shared generation parameters out of configuration."""
    return GenerationOptions(
        temperature=config.generation.temperature,
        max_output_tokens=config.generation.max_output_tokens,
    )


def credential_provider(config: LlmConfig) -> str:
    """Which credential slot the current mode uses.

    Local mode has its own slot rather than sharing one with a hosted provider: a key entered for a
    local relay must not be sent to Anthropic if the user flips the mode switch.
    """
    return "local" if config.mode == "local" else config.api.provider.strip().lower()


def describe_providers(
    config: LlmConfig, credentials: CredentialStore | None = None
) -> dict[str, Any]:
    """Everything the settings interface needs to render the Assistant tab.

    Contains no credential values — only whether one is present, and where it would be read from.
    """
    store = credentials or CredentialStore()
    provider = credential_provider(config)
    return {
        "api_providers": API_PROVIDERS,
        "local_presets": LOCAL_PRESETS,
        "credential": store.status(provider),
    }
