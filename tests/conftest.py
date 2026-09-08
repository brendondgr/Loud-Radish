"""Suite-wide guards.

## No test may reach the desktop's screen-sharing portal

`tests/api/test_session_modes.py` starts a `window` session through the real HTTP route, and on a
KDE Wayland desktop that did exactly what the application is supposed to do: it opened
`org.freedesktop.portal.ScreenCast` and put a **"choose a window to share" dialog on the developer's
screen**. Five such tests, five dialogs, each blocking the suite for the full 120-second portal
timeout — which is why a 70-second suite became an eleven-minute one the moment nobody was sitting
there dismissing them.

The failure hid well, and it is worth naming how. `PortalSession._connect` turns *any* transport
failure into `PortalUnavailable`, by design — a machine without a session bus must not crash the
application. So an earlier attempt to find the culprit, by making `open_dbus_connection` raise, was
swallowed by that same guard and reported no offenders. What finally showed it was `--durations`:
five tests at **exactly 120.2 seconds**, which is not a slow test, it is a timeout.

The double below therefore refuses rather than grants. Refusing exercises the designed degradation —
a portal failure costs the video and keeps the talk (D-022) — so the routes under test still take
the path they are there to check, while nothing reaches the compositor. Tests that need a *granted*
portal, like `tests/transcription/test_window_session.py`, patch the same name with a granting
double of their own; a later `monkeypatch.setattr` in a test wins over this one.

## No test may create a capture sink in the developer's audio graph

The same lesson, learned again a day later and more expensively. `_open_application_tap` was never
stubbed, so any test that opened a `window` session's audio ran `pactl load-module module-null-sink`
against the **live PipeWire daemon**, linked whatever the developer happened to be playing into it,
and never closed it — the session's teardown, which is what closes a tap, does not run in those
tests. Measured: five tests in `test_window_audio.py` leaked **three sinks per run**.

That was not merely untidy. Every one was called `transcriber-tap`, PipeWire does not uniquify node
names, and once several exist `pw-link` and `pw-record` can resolve that name to *different nodes* —
so the recorder captures a sink nothing is linked into. Every window recording made that afternoon
came back as bit-exact digital silence, and the cause was the test suite that reported them green.

A unique name per tap now makes the silence impossible; this makes the leak impossible, which is
the fault underneath it. The double below records what was asked of it and touches nothing.

## No test may *read* the developer's audio graph either

The same lesson a fourth time, and the guard above is what makes it visible. Creating a sink was
stopped; querying the graph was not. `_open_application_tap` asks `playback_streams()` what is
currently playing before it builds anything, so four tests in `test_window_audio.py` passed on a
machine with a video open and failed on a quiet one — with `MonitorUnavailable: Nothing is playing
any audio`, raised from a code path they were not written to reach at all.

**They were not written to reach it because they predate D-030.** Window mode's "system output"
choice used to open a `MonitorSource` directly; D-030 measured that `pw-record` against a sink's
monitor silently records the *microphone* when the target does not resolve, and routed that choice
through the tap as well. The four tests kept patching `MonitorSource`, which is still where the
path ends — so what they assert is still true — but the graph query in front of it was suddenly
real, and whether they passed came to depend on what the developer happened to be listening to.

So the doubles below answer the three questions that reach PipeWire, with the ordinary healthy
answer: something is playing, the tap carries it, and there is an output to widen to. A test that
needs a different answer — a dead tap, a silent machine, a stream that matches a window — patches
the same name itself, and a later `monkeypatch.setattr` wins over an autouse one. Several in
`test_window_audio.py` already did, which is what made the hole obvious once it was looked for.

## No test may write into the developer's data directory

The same lesson a third time, and this one had been running for months. A test that constructs
``ConfigStore()`` with no path resolves ``./data/loud-radish-config.json`` and therefore the real
``./data/sessions`` — so every route test that started a session left a transcript database behind.
Measured on the machine this was found on: **949 session files, 690 of them empty**, all of them
written by the suite.

That was not merely untidy either. The past-sessions page lists what is in that directory, newest
first, and the newest were hundreds of empty test sessions — which is why the page opened on a wall
of "0 words, 0 segments" and the genuine recordings could not be found. A test suite that fills the
product's own storage with garbage is a test suite reporting a bug it caused.

## No test may run a real model unless somebody asked for it

The same shape of mistake as the three above, pointing outward instead of at the audio graph.
`tests/assistant/test_llm_live.py` skipped itself when nothing answered at ``LLM_TEST_ENDPOINT`` —
which reads as "opt-in by environment" and is not. The relay on this machine *does* answer, so those
tests ran for real on every `uv run pytest`, draining a reasoning model's stream with no deadline
under them. The suite did not fail; it simply never finished, and every full run for a fortnight was
made with ``--ignore``.

Reachability is not consent. The marker below makes the opt-in explicit, and `pytest-timeout` gives
the whole suite the deadline that would have made the original symptom legible in seconds rather
than in a fortnight of `--ignore`.

## No test may install a `.desktop` file in the developer's menu

The fifth, caught within a minute of the code that could cause it existing. `shortcuts.register_all`
writes one `.desktop` entry per action into `~/.local/share/applications` so the desktop has
something to bind a key to — and the first test run that reached it left
`loud-radish-toggle-live.desktop` in the developer's real applications directory.

That is the same shape as the sinks and the session files: a test exercising a feature whose whole
job is to write outside the repository. `XDG_DATA_HOME` is redirected below, which covers the
subprocesses too, because `update-desktop-database` reads the same variable.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_desktop_entries_in_the_users_menu(monkeypatch, tmp_path):
    """Point `XDG_DATA_HOME` at a temporary directory for every test.

    `desktop_entry.applications_dir` honours it, and so does `update-desktop-database`, so this
    covers the subprocess as well as the write. See the fifth lesson in this module's docstring.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live-llm",
        action="store_true",
        default=False,
        help="Run the tests that talk to a real OpenAI-compatible server.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect the live-model tests unless they were asked for.

    Skipped rather than filtered out, so a run says plainly that they exist and were not run —
    a silently shorter suite is how the opposite mistake gets made.
    """
    if config.getoption("--run-live-llm"):
        return
    skip = pytest.mark.skip(reason="needs --run-live-llm (talks to a real model)")
    for item in items:
        if "live_llm" in item.keywords:
            item.add_marker(skip)


class NoPortalInTests:
    """Stands in for the compositor's picker, and always declines to be reached.

    Raising :class:`PortalUnavailable` rather than ``PortalDeclined`` is deliberate: *declined*
    means the user said no and ends the whole session, while *unavailable* means this machine
    cannot share a window, which costs the video and keeps the recording. The second is what a
    test environment actually is.
    """

    def __init__(self, *, cursor_mode: str = "hidden", restore_token: str = "") -> None:
        self.cursor_mode = cursor_mode
        self.restore_token = restore_token

    def open(self):  # noqa: ANN201 - the real signature returns a WindowStream it never reaches
        from app.services.capture import PortalUnavailable

        raise PortalUnavailable(
            "The screen-sharing portal is not reachable from the test suite. A test that needs a "
            "granted window must patch app.services.session.window_capture.PortalSession itself."
        )

    def close(self) -> None:
        """Nothing was opened, so there is nothing to close."""


class NoTapInTests:
    """Stands in for the application audio tap, and never reaches PipeWire.

    Behaves like a tap that opened and linked successfully, because that is the path the session
    tests are there to exercise. It creates no sink, so there is nothing to leak when the teardown
    that would have closed it never runs.
    """

    def __init__(self, sink_name: str = "") -> None:
        self.sink_name = sink_name or "loud-radish-tap-in-tests"
        self._open = False

    @property
    def monitor(self) -> str:
        return f"{self.sink_name}.monitor"

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def live_links(self) -> int:
        """As many as were asked for. The real one counts links in the PipeWire graph."""
        return 2 if self._open else 0

    def open(self) -> None:
        self._open = True

    def link(self, _stream) -> int:
        return 2

    def link_all(self, streams) -> int:
        return 2 * len(list(streams))

    def close(self) -> None:
        self._open = False


@pytest.fixture(autouse=True)
def no_real_portal(monkeypatch):
    """Keep every test away from the desktop's screen-sharing dialog.

    Autouse and suite-wide on purpose. "Remember to stub the portal" is a habit, and this was
    already forgotten once in a file whose author had no reason to think it captured anything.
    """
    from app.services.session import window_capture

    monkeypatch.setattr(window_capture, "PortalSession", NoPortalInTests)


@pytest.fixture(autouse=True)
def no_real_audio_tap(monkeypatch):
    """Keep every test out of the developer's PipeWire graph.

    Patched on the *manager* rather than in `app.services.audio.tap`, so the tests in
    `test_audio_tap.py` that deliberately exercise the real graph — and clean up after themselves —
    still do. The double reports its own `live_links`, so the session's verification runs for real
    against it rather than being stubbed out separately.
    """
    from app.services.session import sources

    monkeypatch.setattr(sources, "ApplicationTap", NoTapInTests)


#: The one stream the suite's audio graph is playing. A `PlaybackStream` rather than a bare object,
#: so the `rank`/`score` heuristic `_tap_candidates` applies to it runs for real.
def _one_playing_stream():  # noqa: ANN202 - returns list[PlaybackStream]
    from app.services.audio.tap import PlaybackStream

    return [
        PlaybackStream(
            node_id=1,
            serial=1,
            node_name="a-test",
            application="a-test",
            binary="a-test",
            media_name="something playing",
            state="running",
        )
    ]


@pytest.fixture(autouse=True)
def no_real_audio_graph(monkeypatch):
    """Keep every test from *reading* the developer's PipeWire graph.

    The companion to `no_real_audio_tap`, which stopped tests writing to it. Three names on the
    session manager query the live daemon — what is playing, whether a node is carrying audio, and
    which sink is the default — and any one of them makes a test's result depend on what the
    developer happens to be listening to.

    The answers are the ordinary healthy ones: something is playing, and it is audible. That is the
    path most tests mean to be on, and the ones that mean to be on another patch these names
    themselves.
    """
    from app.services.session import sources

    monkeypatch.setattr(sources, "tap_streams", _one_playing_stream)
    # Non-zero: the tap is delivering, so the "widen to the whole output" path (D-030) stays shut
    # unless a test opens it deliberately. Zero would mean silence and -1.0 would mean unmeasurable,
    # and both are states a test should have to ask for.
    monkeypatch.setattr(sources, "probe_peak", lambda *_a, **_k: 0.5)
    monkeypatch.setattr(sources, "default_sink", lambda: "a-test-sink")


@pytest.fixture(autouse=True)
def no_real_tray_icon(monkeypatch):
    """Keep every test out of the developer's system tray.

    The companion to `no_real_portal`. Nothing calls `Companion.run()` today, but the tray is the
    one part of this package with a visible side effect on the desktop, and "remember not to start
    the tray" is a habit — which is exactly the argument the portal guard was written on. A test
    that means to exercise the D-Bus side calls `TrayIcon.answer` directly, which needs no bus.
    """
    from app.companion import tray as tray_module

    def refuse(self):  # noqa: ANN001, ANN202
        raise AssertionError("a test tried to place a real icon in the system tray")

    monkeypatch.setattr(tray_module.TrayIcon, "start", refuse)


@pytest.fixture(autouse=True)
def isolated_data_dirs(tmp_path, monkeypatch):
    """Point every default-constructed ``ConfigStore`` at a per-test data directory.

    Autouse and suite-wide, for the same reason as the two above: "remember to pass a config path"
    is a habit, and a test that forgets writes into the directory the user's own recordings live in.

    Two overrides rather than one, because they close different holes. The environment variable
    moves a default-constructed store's *config file* out of the way. Patching the bottom
    configuration layer moves the session and recording directories for **every** store, including
    the many tests that pass their own ``config_path`` and then override only the one directory
    their subject writes to — those were leaking the other one, which is how a suite with careful
    per-test fixtures still filled `data/sessions`.
    """
    import json

    from app.config import defaults
    from app.config import store as store_module

    data = tmp_path / "data"
    sessions = data / "sessions"
    recordings = data / "recordings"
    sessions.mkdir(parents=True)
    recordings.mkdir(parents=True)

    config_path = data / "loud-radish-config.json"
    config_path.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv("LOUD_RADISH_CONFIG_PATH", str(config_path))

    real_default_layer = defaults.default_layer

    def isolated_default_layer():
        layer = real_default_layer()
        layer["storage"]["session_dir"] = str(sessions)
        layer["recording"]["recording_dir"] = str(recordings)
        return layer

    monkeypatch.setattr(defaults, "default_layer", isolated_default_layer)
    # `store.py` imported the name directly, so patching the module it came from is not enough.
    monkeypatch.setattr(store_module, "default_layer", isolated_default_layer)


@pytest.fixture(autouse=True)
def no_wheel_repair(monkeypatch):
    """Keep every test from reinstalling CTranslate2.

    Every script under `scripts/` calls `_bootstrap.prepare()` at import, and the companion's
    `main` makes the same call — both of which run `repair_kept_wheel`, which on an AMD machine
    with a kept wheel and the wrong build installed runs `uv pip install`. A test that loads a
    script by file path must not do that to the developer's environment. The switch is the same
    environment variable a user would set; the tests of the repair itself clear it again.
    """
    from app.services.asr import acceleration

    monkeypatch.setenv(acceleration.REPAIR_OFF, "1")
