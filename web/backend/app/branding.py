"""The product's name, and every identifier derived from it.

This module exists because the *last* rename was never centralised. "Live Seminar Transcriber" was
typed out in eleven files and "transcriber" was baked into a config filename, a keyring service, an
audio node prefix, a desktop component and three environment variables — so changing the name meant
finding all of them, and the word "transcriber" also appears forty times as an ordinary English noun
that must not be touched. One module makes the next rename a diff of this file.

## The migration rule

Several of the constants below name state that already exists *outside this repository*: a file in
``data/``, a secret in the login keyring, a registration with the desktop's shortcut service, a node
in the PipeWire graph. Renaming those without recognising the old value is indistinguishable from
data loss — a working install becomes a blank configuration and a missing API key.

So every such constant is paired with a ``LEGACY_*`` counterpart, and the rule everywhere is the
same: **recognise the old value, adopt what it points at, and never write it again.** The legacy
names are read-only history. Nothing in this application should ever produce one.

Identifiers with no persistent state behind them — a portal request token that lives for seconds —
get no legacy pair, because there is nothing to migrate.
"""

from __future__ import annotations

from typing import Final

# -- the name ---------------------------------------------------------------------------------

#: The product. Two words, both capitalised.
APP_NAME: Final = "Loud Radish"

#: What it does, for the places a bare name would be unhelpful — a browser tab, a desktop entry, an
#: OpenAPI title. Deliberately not "seminar": window and video capture outgrew that word.
APP_TAGLINE: Final = "Live Audio & Video Transcriber"

#: Name and tagline joined. The long form, used where there is room for it.
APP_TITLE: Final = f"{APP_NAME} — {APP_TAGLINE}"

#: One sentence, for meta descriptions and the desktop entry's comment.
APP_DESCRIPTION: Final = (
    "Record and transcribe a talk as it happens, and ask a language model about what was said."
)

#: The lowercase, hyphenated form. Every machine identifier below is built from this by hand rather
#: than by f-string, so that grepping for a literal identifier finds it.
APP_SLUG: Final = "loud-radish"

#: The prefix on this application's environment variables.
ENV_PREFIX: Final = "LOUD_RADISH"
LEGACY_ENV_PREFIX: Final = "TRANSCRIBER"

# -- identifiers naming state that already exists ------------------------------------------------

#: The user configuration file, written into the data directory. Adopted by rename when the legacy
#: name is found and the new one is not — see ``config.store``.
CONFIG_FILENAME: Final = "loud-radish-config.json"
LEGACY_CONFIG_FILENAME: Final = "transcriber-config.json"

#: The service name credentials are stored under in the OS credential store. A read that misses
#: falls back to the legacy service, re-homes the secret, and deletes the old entry — see
#: ``config.credentials``.
KEYRING_SERVICE: Final = "loud-radish"
LEGACY_KEYRING_SERVICE: Final = "transcriber-prototype"

#: Shared start of every tap sink's name. Only used to *recognise* this application's sinks; the
#: name a sink is created with carries the owning process and a random suffix as well.
TAP_SINK_PREFIX: Final = "loud-radish-tap"

#: Prefixes taps were created with by earlier builds. The leaked-sink sweep still recognises these,
#: because a sink leaked by a build that predates this rename outlives the build that leaked it.
LEGACY_TAP_SINK_PREFIXES: Final[tuple[str, ...]] = ("transcriber-tap",)

#: The component global shortcuts are registered under with the desktop's shortcut service.
#: Registration unregisters the legacy component first, so bindings move rather than duplicate.
SHORTCUT_COMPONENT: Final = "loud-radish"
LEGACY_SHORTCUT_COMPONENT: Final = "transcriber"

#: The user systemd unit installed by ``scripts/install_autostart.py``. Installing removes the
#: legacy unit: two units on one port would start the server twice.
SERVICE_UNIT: Final = "loud-radish.service"
LEGACY_SERVICE_UNIT: Final = "transcriber.service"

#: The desktop entry's filename.
DESKTOP_ENTRY: Final = "loud-radish.desktop"
LEGACY_DESKTOP_ENTRY: Final = "transcriber.desktop"

#: The ``localStorage`` key prefix used by an exported bundle for its own view state. No legacy
#: pair: an export is a self-contained folder that is never upgraded in place, so bundles already
#: written keep their own prefix and keep working. See the plan's Gaps section.
EXPORT_STORAGE_PREFIX: Final = "loud-radish-export"

#: The prefix on a ScreenCast portal request token. No legacy pair — the token is per-request and
#: lives for seconds, so there is nothing to migrate.
PORTAL_TOKEN_PREFIX: Final = "loud_radish"

#: The out-of-browser control script, relative to the repository root. Named here because the
#: companion and the shortcut installer both build command lines that invoke it.
CONTROL_SCRIPT: Final = "utils/loud_radish_ctl.py"

# -- the mark ------------------------------------------------------------------------------------

#: The logo, served by the static mount and copied into every export.
LOGO_PATH: Final = "brand/radish.svg"

#: The logo's palette, mirrored into ``static/css/tokens.css`` as ``--brand-*``. Recorded here so a
#: caller that needs one — the tray, an export — does not sample it out of the SVG.
#:
#: These are **not** the interface's accent. That is a teal, and the same token is aliased as
#: ``--success``; making crimson mean "healthy" would be actively wrong. See D-038.
BRAND_COLORS: Final[dict[str, str]] = {
    "radish": "#C22D4C",
    "leaf": "#598F3B",
    "cream": "#F6F2E8",
    "blush": "#D9868A",
    "ink": "#080808",
}


def env_var(name: str) -> str:
    """Return this application's environment variable for ``name``.

    ``env_var("CONFIG_PATH")`` is ``"LOUD_RADISH_CONFIG_PATH"``.
    """
    return f"{ENV_PREFIX}_{name}"


def legacy_env_var(name: str) -> str:
    """Return the deprecated environment variable for ``name``.

    Read to keep an existing install working, and reported when it is used. Never written.
    """
    return f"{LEGACY_ENV_PREFIX}_{name}"
