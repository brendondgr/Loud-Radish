"""Seam B — the language model layer.

Import from here rather than from the individual modules, so a provider can move without every call
site changing.
"""

from .anthropic import AnthropicBackend
from .contract import (
    GenerationOptions,
    LlmBackend,
    LlmCapabilities,
    LlmChunk,
    LlmMessage,
    LlmModelInfo,
    LlmUsage,
    assistant,
    system,
    user,
)
from .errors import (
    ConnectionTest,
    LlmAuthError,
    LlmConfigError,
    LlmError,
    LlmServerError,
    LlmUnreachableError,
)
from .openai_compatible import OpenAiCompatibleBackend
from .registry import (
    API_PROVIDERS,
    LOCAL_PRESETS,
    build_llm,
    credential_provider,
    describe_providers,
    generation_options,
)
from .tokens import estimate_messages, estimate_tokens, fit_to_budget, trim_to_tokens

__all__ = [
    "API_PROVIDERS",
    "LOCAL_PRESETS",
    "AnthropicBackend",
    "ConnectionTest",
    "GenerationOptions",
    "LlmAuthError",
    "LlmBackend",
    "LlmCapabilities",
    "LlmChunk",
    "LlmConfigError",
    "LlmError",
    "LlmMessage",
    "LlmModelInfo",
    "LlmServerError",
    "LlmUnreachableError",
    "LlmUsage",
    "OpenAiCompatibleBackend",
    "assistant",
    "build_llm",
    "credential_provider",
    "describe_providers",
    "estimate_messages",
    "estimate_tokens",
    "fit_to_budget",
    "generation_options",
    "system",
    "trim_to_tokens",
    "user",
]
