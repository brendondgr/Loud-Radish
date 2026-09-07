"""What happens to an install that predates the rename to Loud Radish (D-038).

Every case here covers state that exists *outside* this repository — a file the user's settings live
in, a secret in the login keyring, a variable in their shell, a node left in the audio graph. The
fault these guard against is not a crash. It is a rename that presents itself as a factory reset:
the application starts, finds nothing under the new name, and cheerfully writes fresh defaults over
a working configuration while the real one sits on disk one filename away.

So each test asserts the same shape of promise — the old value is recognised, what it points at is
adopted, and it is not written again.
"""

from __future__ import annotations

import pytest
from app.config import credentials as credentials_module
from app.config.store import (
    CONFIG_PATH_ENV,
    LEGACY_CONFIG_PATH_ENV,
    adopt_legacy_config,
    default_config_path,
)
from app.services.audio import tap as tap_module

from app import branding

# -- the configuration file ------------------------------------------------------------------


def test_a_pre_rename_config_file_is_adopted(tmp_path):
    """The settings survive the rename, which is the entire point of the migration."""
    legacy = tmp_path / branding.LEGACY_CONFIG_FILENAME
    legacy.write_text('{"asr": {"model": "large-v3"}}')
    current = tmp_path / branding.CONFIG_FILENAME

    assert adopt_legacy_config(current) is True
    assert current.read_text() == '{"asr": {"model": "large-v3"}}'
    assert not legacy.exists(), "the move must not leave a second copy to drift out of step"


def test_adopting_is_a_move_that_happens_once(tmp_path):
    """A second run finds the new name present and does nothing — so a file the user has edited
    since the migration is never overwritten by a stale copy."""
    (tmp_path / branding.LEGACY_CONFIG_FILENAME).write_text('{"old": true}')
    current = tmp_path / branding.CONFIG_FILENAME
    assert adopt_legacy_config(current) is True

    (tmp_path / branding.LEGACY_CONFIG_FILENAME).write_text('{"resurrected": true}')
    assert adopt_legacy_config(current) is False
    assert current.read_text() == '{"old": true}'


def test_nothing_happens_on_a_fresh_install(tmp_path):
    assert adopt_legacy_config(tmp_path / branding.CONFIG_FILENAME) is False


def test_a_current_file_is_never_replaced_by_a_legacy_one(tmp_path):
    current = tmp_path / branding.CONFIG_FILENAME
    current.write_text('{"current": true}')
    (tmp_path / branding.LEGACY_CONFIG_FILENAME).write_text('{"legacy": true}')

    assert adopt_legacy_config(current) is False
    assert current.read_text() == '{"current": true}'


# -- the environment variables ---------------------------------------------------------------


def test_the_current_environment_variable_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "chosen.json"))
    monkeypatch.setenv(LEGACY_CONFIG_PATH_ENV, str(tmp_path / "ignored.json"))
    assert default_config_path() == tmp_path / "chosen.json"


def test_the_deprecated_environment_variable_is_still_honoured(tmp_path, monkeypatch, caplog):
    """Honoured, and complained about. Someone who set this did so for a reason; ignoring it would
    silently move their configuration somewhere they are not looking."""
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    monkeypatch.setenv(LEGACY_CONFIG_PATH_ENV, str(tmp_path / "legacy.json"))

    with caplog.at_level("WARNING"):
        assert default_config_path() == tmp_path / "legacy.json"

    assert LEGACY_CONFIG_PATH_ENV in caplog.text
    assert CONFIG_PATH_ENV in caplog.text


# -- the credential store --------------------------------------------------------------------


class FakeKeyring:
    """A keyring double keyed by (service, provider). Records deletions so the test can prove the
    old entry is gone rather than merely copied."""

    def __init__(self, initial: dict[tuple[str, str], str] | None = None) -> None:
        self.store: dict[tuple[str, str], str] = dict(initial or {})
        self.deleted: list[tuple[str, str]] = []

    def get_password(self, service: str, provider: str) -> str | None:
        return self.store.get((service, provider))

    def set_password(self, service: str, provider: str, value: str) -> None:
        self.store[(service, provider)] = value

    def delete_password(self, service: str, provider: str) -> None:
        self.deleted.append((service, provider))
        self.store.pop((service, provider), None)


@pytest.fixture
def fake_keyring(monkeypatch):
    def install(initial=None):
        kr = FakeKeyring(initial)
        monkeypatch.setattr(credentials_module, "_keyring", lambda: kr)
        return kr

    return install


def test_a_pre_rename_credential_is_moved_and_returned(fake_keyring, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    kr = fake_keyring({(branding.LEGACY_KEYRING_SERVICE, "anthropic"): "sk-secret"})

    store = credentials_module.CredentialStore()
    assert store.get("anthropic") == "sk-secret"

    assert kr.store[(branding.KEYRING_SERVICE, "anthropic")] == "sk-secret"
    assert (branding.LEGACY_KEYRING_SERVICE, "anthropic") in kr.deleted


def test_the_move_happens_once(fake_keyring, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    kr = fake_keyring({(branding.LEGACY_KEYRING_SERVICE, "anthropic"): "sk-secret"})
    store = credentials_module.CredentialStore()

    store.get("anthropic")
    kr.deleted.clear()
    assert store.get("anthropic") == "sk-secret"
    assert kr.deleted == [], "the second read must come from the new service, not migrate again"


def test_a_key_entered_since_the_rename_is_never_shadowed(fake_keyring, monkeypatch):
    """The current service is consulted first, so a stale pre-rename entry cannot resurrect an old
    key over one the user has since replaced."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    kr = fake_keyring(
        {
            (branding.KEYRING_SERVICE, "anthropic"): "sk-current",
            (branding.LEGACY_KEYRING_SERVICE, "anthropic"): "sk-stale",
        }
    )
    store = credentials_module.CredentialStore()

    assert store.get("anthropic") == "sk-current"
    assert kr.deleted == []


def test_nothing_to_migrate_is_silent(fake_keyring, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_keyring()
    assert credentials_module.CredentialStore().get("anthropic") is None


# -- the audio graph -------------------------------------------------------------------------


def test_the_sweep_still_recognises_a_pre_rename_tap_sink():
    """A sink leaked by an older build outlives the build that leaked it, so the sweep has to keep
    recognising the name that build used — otherwise it is stranded in the graph forever."""
    for legacy in branding.LEGACY_TAP_SINK_PREFIXES:
        assert legacy in tap_module.LEGACY_SINK_NAMES

    assert tap_module.TAP_SINK_PREFIX in tap_module.LEGACY_SINK_NAMES, (
        "the bare current prefix is also stale: nothing creates a tap without a pid suffix"
    )


def test_a_live_tap_name_is_not_treated_as_stale():
    """The names swept unconditionally are bare prefixes. A real tap carries a pid and a random
    suffix, and must not match — sweeping it would tear down the running session's own capture."""
    live = tap_module.tap_sink_name()
    assert live not in tap_module.LEGACY_SINK_NAMES
    assert live.startswith(branding.TAP_SINK_PREFIX)
