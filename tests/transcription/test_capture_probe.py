"""Window-capture capability detection (D-022).

Five things can be missing, and each has a different fix. These tests exist because "window capture
unavailable" for all five is the failure that generates support requests — so every branch is
checked to name its own remedy, and the *order* is checked too: reporting a missing encoder to
someone who has no portal at all sends them to install the wrong thing.

The probe must also never raise. It runs inside `GET /api/health`, which is the one endpoint that
has to answer when everything else is unhappy.
"""

from __future__ import annotations

import pytest
from app.services.capture import probe


@pytest.fixture(autouse=True)
def clear_element_cache():
    """The element list is cached across calls; each test needs its own.

    Tolerant of the name having been replaced, because most tests here patch
    ``probe.gstreamer_elements`` with a plain function that has no cache to clear. Whether
    ``monkeypatch`` has already undone that by teardown time depends on fixture ordering, which is
    not this fixture's business to know — and when a suite-wide autouse fixture was added in
    ``tests/conftest.py``, the ordering changed and eleven tests started erroring in teardown while
    still passing. A cache that is not there needs no clearing.
    """

    def clear() -> None:
        cache_clear = getattr(probe.gstreamer_elements, "cache_clear", None)
        if cache_clear is not None:
            cache_clear()

    clear()
    yield
    clear()


def working_machine(monkeypatch, **overrides) -> None:
    """Patch every check to pass, so a test can knock out exactly one."""
    monkeypatch.setattr(probe, "session_type", lambda: overrides.get("session", "wayland"))
    monkeypatch.setattr(probe, "dbus_client_available", lambda: overrides.get("dbus", True))
    monkeypatch.setattr(
        probe,
        "portal_capabilities",
        lambda: overrides.get("portal", (5, probe.SOURCE_MONITOR | probe.SOURCE_WINDOW)),
    )
    monkeypatch.setattr(
        probe.shutil, "which", lambda name: overrides.get("binary", "/usr/bin/gst-launch-1.0")
    )
    monkeypatch.setattr(
        probe,
        "gstreamer_elements",
        lambda: frozenset(
            overrides.get(
                "elements",
                {"pipewiresrc", "videoconvert", "videorate", "vp8enc", "webmmux", "jpegenc"},
            )
        ),
    )


# -- the happy path -----------------------------------------------------------------------


def test_a_capable_machine_is_available(monkeypatch) -> None:
    working_machine(monkeypatch)
    verdict = probe.detect()

    assert verdict.available is True
    assert verdict.missing == ""
    assert verdict.reason == ""
    assert (verdict.encoder, verdict.muxer, verdict.extension) == ("vp8enc", "webmmux", "webm")
    assert verdict.preview is True


def test_a_missing_preview_encoder_does_not_make_it_unavailable(monkeypatch) -> None:
    """A missing preview costs the monitor its picture and costs the recording nothing (D-020)."""
    working_machine(
        monkeypatch,
        elements={"pipewiresrc", "videoconvert", "videorate", "vp8enc", "webmmux"},
    )
    verdict = probe.detect()

    assert verdict.available is True
    assert verdict.preview is False


# -- each of the five ways to be unavailable ------------------------------------------------


def test_no_graphical_session(monkeypatch) -> None:
    working_machine(monkeypatch, session="")
    verdict = probe.detect()

    assert verdict.available is False
    assert verdict.missing == "no-display"
    assert "no windows to capture" in verdict.reason


def test_no_dbus_client_names_the_extra(monkeypatch) -> None:
    working_machine(monkeypatch, dbus=False)
    verdict = probe.detect()

    assert verdict.missing == "dbus-client"
    assert "uv sync" in verdict.reason


def test_no_portal_names_the_packages_that_provide_one(monkeypatch) -> None:
    working_machine(monkeypatch, portal=None)
    verdict = probe.detect()

    assert verdict.missing == "portal"
    assert "xdg-desktop-portal" in verdict.reason


def test_a_portal_without_window_sources_says_nothing_can_be_done(monkeypatch) -> None:
    """Some compositors share a monitor and nothing smaller. That is not something to install."""
    working_machine(monkeypatch, portal=(4, probe.SOURCE_MONITOR))
    verdict = probe.detect()

    assert verdict.missing == "no-window-source"
    assert "limitation of the compositor" in verdict.reason
    assert verdict.portal_version == 4


def test_no_gstreamer_names_the_system_package(monkeypatch) -> None:
    working_machine(monkeypatch, binary=None)
    verdict = probe.detect()

    assert verdict.missing == "gstreamer"
    assert "dnf install" in verdict.reason


def test_missing_elements_are_named_individually(monkeypatch) -> None:
    working_machine(monkeypatch, elements={"videoconvert", "vp8enc", "webmmux"})
    verdict = probe.detect()

    assert verdict.missing == "gst-elements"
    assert "pipewiresrc" in verdict.reason
    assert "pipewiresrc" in verdict.missing_elements
    assert "videorate" in verdict.missing_elements


def test_no_usable_encoder_is_its_own_verdict(monkeypatch) -> None:
    working_machine(monkeypatch, elements={"pipewiresrc", "videoconvert", "videorate"})
    verdict = probe.detect()

    assert verdict.missing == "encoder"
    assert "no video encoder" in verdict.reason


# -- the order of the checks ---------------------------------------------------------------


def test_the_first_missing_piece_is_the_one_reported(monkeypatch) -> None:
    """Telling someone with no portal to install an encoder sends them to the wrong shop."""
    working_machine(monkeypatch, portal=None, binary=None, elements=set())
    assert probe.detect().missing == "portal"


def test_the_display_check_comes_before_everything(monkeypatch) -> None:
    working_machine(monkeypatch, session="", dbus=False, portal=None)
    assert probe.detect().missing == "no-display"


# -- encoder selection ----------------------------------------------------------------------


def test_vp8_is_preferred_over_x264(monkeypatch) -> None:
    """Deliberate: x264 is absent from stock Fedora, so preferring it makes the common case fail."""
    chosen = probe.choose_encoder(frozenset({"vp8enc", "webmmux", "x264enc", "mp4mux"}))
    assert chosen == ("vp8enc", "webmmux", "webm", "")


def test_hardware_is_preferred_over_software() -> None:
    """The whole point, and it is worth more than it looks.

    This application transcribes and records simultaneously, so every core a software encoder takes
    is a core the speech model does not get. Measured through the real pipeline on 150 frames at
    720p: 1.25 s of user CPU for `vp8enc` against 0.30 s for `vaav1enc`.
    """
    chosen = probe.choose_encoder(
        frozenset({"vp8enc", "webmmux", "vaav1enc", "matroskamux", "av1parse"})
    )

    assert chosen == ("vaav1enc", "matroskamux", "mkv", "av1parse")


def test_av1_is_preferred_over_hardware_h264() -> None:
    """Not a quality judgement — a packaging one.

    Fedora strips the H.264 and HEVC VA-API entry points from its Mesa build for patent reasons, so
    `vah264enc` does not register on a stock install even though the hardware supports it. AV1 is
    royalty-free and is present. Preferring H.264 would mean the common case falls back to software.
    """
    elements = frozenset({"vaav1enc", "av1parse", "vah264enc", "h264parse", "matroskamux"})

    assert probe.choose_encoder(elements)[0] == "vaav1enc"


def test_a_hardware_encoder_without_its_parser_is_skipped() -> None:
    """An elementary stream that cannot be parsed cannot be muxed.

    Finding that out at record time is finding it out too late, so the entry is disqualified during
    the probe and the software path is taken instead.
    """
    chosen = probe.choose_encoder(frozenset({"vaav1enc", "matroskamux", "vp8enc", "webmmux"}))

    assert chosen == ("vp8enc", "webmmux", "webm", "")


def test_an_encoder_without_its_muxer_is_not_chosen(monkeypatch) -> None:
    """Half a pipeline is not a pipeline. This machine has openh264enc and needed matroskamux."""
    assert probe.choose_encoder(frozenset({"vp8enc"})) is None
    assert probe.choose_encoder(frozenset({"openh264enc", "matroskamux"})) == (
        "openh264enc",
        "matroskamux",
        "mkv",
        "",
    )


def test_no_encoder_at_all(monkeypatch) -> None:
    assert probe.choose_encoder(frozenset({"videoconvert"})) is None


# -- it must never raise ---------------------------------------------------------------------


def test_a_probe_that_explodes_degrades_rather_than_propagating(monkeypatch) -> None:
    """It runs inside the endpoint that must answer when everything else is broken."""

    def boom() -> str:
        raise RuntimeError("the session bus caught fire")

    monkeypatch.setattr(probe, "session_type", boom)
    verdict = probe.detect()

    assert verdict.available is False
    assert verdict.missing == "probe-failed"


def test_element_listing_survives_a_missing_binary(monkeypatch) -> None:
    monkeypatch.setattr(probe.shutil, "which", lambda name: None)
    assert probe.gstreamer_elements() == frozenset()


def test_portal_capabilities_survive_no_session_bus(monkeypatch) -> None:
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.setattr(probe, "dbus_client_available", lambda: False)
    assert probe.portal_capabilities() is None


# -- session type ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"XDG_SESSION_TYPE": "wayland"}, "wayland"),
        ({"XDG_SESSION_TYPE": "x11"}, "x11"),
        ({"WAYLAND_DISPLAY": "wayland-0"}, "wayland"),
        ({"DISPLAY": ":0"}, "x11"),
        ({}, ""),
    ],
)
def test_session_type_is_read_from_the_environment(monkeypatch, env: dict, expected: str) -> None:
    for name in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert probe.session_type() == expected


def test_an_unrecognised_session_type_falls_back_to_the_display_variables(monkeypatch) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "tty")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert probe.session_type() == "wayland"
