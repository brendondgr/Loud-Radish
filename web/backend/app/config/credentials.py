"""API credential handling (BE §10.6).

Three rules, all enforced here rather than relied upon elsewhere:

* a key is never written to the config file,
* a key is never logged,
* a key is never sent to the frontend — the frontend learns only whether one is *present*.

Storage prefers the OS credential store via ``keyring`` (Windows Credential Manager, macOS Keychain,
Secret Service on Linux). When the optional ``credentials`` dependency group is not installed, an
environment variable is read instead. Environment variables are readable but not writable, so
:meth:`CredentialStore.set` reports honestly that it cannot persist rather than pretending it did.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SERVICE_NAME = "transcriber-prototype"

#: Provider name → environment variable consulted when no OS credential store is available.
#:
#: ``local`` has its own slot rather than sharing one with a hosted provider. Most local servers
#: need no key at all, but relays in front of them often do, and a key entered for a machine on the
#: same desk must never be sent to a hosted API if the user flips the mode switch.
ENV_VARS: dict[str, str] = {
    "local": "LOCAL_LLM_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "together": "TOGETHER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}


def _keyring() -> Any | None:
    """Return the ``keyring`` module, or ``None`` if the optional group is not installed."""
    try:
        import keyring
    except ImportError:
        return None
    return keyring


class CredentialUnavailableError(RuntimeError):
    """Raised when a credential cannot be stored because no writable backend exists."""


class CredentialStore:
    """Reads and writes provider credentials without ever exposing their values."""

    def __init__(self, service: str = SERVICE_NAME) -> None:
        self._service = service

    @property
    def backend_name(self) -> str:
        """A short, safe description of where credentials are read from."""
        return "os-credential-store" if _keyring() is not None else "environment"

    def get(self, provider: str) -> str | None:
        """Return the credential for ``provider``, or ``None``.

        Only the pieces of the backend that actually make a request may call this. The return value
        must never be logged, serialised, or returned over HTTP.
        """
        kr = _keyring()
        if kr is not None:
            try:
                value = kr.get_password(self._service, provider)
            except Exception as exc:  # noqa: BLE001 - backend errors vary by platform
                logger.warning(
                    "Credential store unavailable for %s: %s", provider, type(exc).__name__
                )
                value = None
            if value:
                return value

        env_var = ENV_VARS.get(provider)
        if env_var:
            return os.environ.get(env_var) or None
        return None

    def has(self, provider: str) -> bool:
        """Whether a credential exists. This is the only credential fact the frontend receives."""
        return self.get(provider) is not None

    def set(self, provider: str, value: str) -> None:
        """Store a credential in the OS credential store.

        Raises:
            CredentialUnavailableError: when ``keyring`` is not installed, or the platform backend
                refuses the write. The caller must surface this rather than silently dropping the
                key — a key the user believes is saved but is not produces a confusing auth failure
                much later.
        """
        if not value:
            raise ValueError("Refusing to store an empty credential")
        kr = _keyring()
        if kr is None:
            env_var = ENV_VARS.get(provider, "the provider environment variable")
            raise CredentialUnavailableError(
                "No OS credential store is available. Install the optional 'credentials' "
                f"dependency group, or set {env_var} in the environment instead."
            )
        try:
            kr.set_password(self._service, provider, value)
        except Exception as exc:  # noqa: BLE001 - backend errors vary by platform
            raise CredentialUnavailableError(
                f"The OS credential store rejected the write ({type(exc).__name__})."
            ) from exc

    def delete(self, provider: str) -> bool:
        """Remove a stored credential. Returns whether anything was removed."""
        kr = _keyring()
        if kr is None:
            return False
        try:
            kr.delete_password(self._service, provider)
        except Exception:  # noqa: BLE001 - "not found" is not an error worth surfacing
            return False
        return True

    def status(self, provider: str) -> dict[str, Any]:
        """A frontend-safe description of credential state. Contains no secret material."""
        return {
            "provider": provider,
            "present": self.has(provider),
            "source": self.backend_name,
            "writable": _keyring() is not None,
        }
