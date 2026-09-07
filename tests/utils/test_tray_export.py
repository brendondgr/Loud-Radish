"""The StatusNotifierItem and its menu, without a session bus (D-043).

`TrayIcon.answer` is deliberately separable from the dispatch loop, because almost everything that
can be wrong here is a *reply* rather than a connection: a property an SNI host requires and this
does not return produces an icon that never appears, and no test of the renderer would notice.

The one thing asserted about the loop itself is that it never places a real icon in the developer's
panel — `tests/conftest.py` makes `start()` raise for exactly that reason, so anything here that
wants a connection builds a double instead.
"""

from __future__ import annotations

import pytest
from app.companion.animation import Snapshot
from app.companion.tray import (
    ITEM_IFACE,
    ITEM_PATH,
    MENU_IFACE,
    MENU_PATH,
    PROPERTIES_IFACE,
    TrayIcon,
)

from app import branding

#: The real `start`, captured at import time. `tests/conftest.py` replaces it on the class for every
#: test, so that a test cannot plant an icon in the developer's panel; the one test below that means
#: to exercise the failure path needs the original, and this is the only moment it is reachable.
_REAL_START = TrayIcon.start

#: Everything a StatusNotifierItem host reads before it will draw anything.
REQUIRED_PROPERTIES = (
    "Category",
    "Id",
    "Title",
    "Status",
    "IconName",
    "IconPixmap",
    "AttentionIconName",
    "AttentionIconPixmap",
    "OverlayIconName",
    "OverlayIconPixmap",
    "ToolTip",
    "ItemIsMenu",
    "Menu",
)


@pytest.fixture
def tray():
    icon = TrayIcon()
    icon.set_snapshot(Snapshot(reachable=True, mode="live", state="idle"))
    return icon


def properties(tray) -> dict:
    signature, body = tray.answer(PROPERTIES_IFACE, "GetAll", ITEM_PATH)
    assert signature == "a{sv}"
    return body[0]


def layout(tray):
    signature, body = tray.answer(MENU_IFACE, "GetLayout", MENU_PATH)
    assert signature == "u(ia{sv}av)"
    revision, root = body
    return revision, [entry[1] for entry in root[2]]


# -- the item's properties -----------------------------------------------------------------


@pytest.mark.parametrize("name", REQUIRED_PROPERTIES)
def test_every_property_a_host_requires_is_answered(tray, name: str) -> None:
    assert name in properties(tray)


def test_the_icon_is_a_pixmap_of_the_declared_size(tray) -> None:
    """`a(iiay)` carries its own dimensions and a host trusts them. Bytes that disagree with the
    width and height beside them are read off the end of the buffer."""
    signature, value = properties(tray)["IconPixmap"]

    assert signature == "a(iiay)"
    width, height, data = value[0]
    assert (width, height) == (22, 22)
    assert len(data) == width * height * 4


def test_the_icon_name_is_present_and_empty(tray) -> None:
    """A host reads IconName first and falls through to the pixmap when it is empty. One that finds
    neither draws nothing at all, which is indistinguishable from this never having been built."""
    assert properties(tray)["IconName"] == ("s", "")


def test_the_item_is_not_a_menu_button(tray) -> None:
    """ItemIsMenu true makes the whole icon a menu button and loses the left click, which is the
    single most useful click on a tray icon."""
    assert properties(tray)["ItemIsMenu"] == ("b", False)
    assert properties(tray)["Menu"] == ("o", MENU_PATH)


def test_the_name_comes_from_branding_rather_than_a_literal(tray) -> None:
    table = properties(tray)

    assert table["Id"] == ("s", branding.APP_SLUG)
    assert table["Title"] == ("s", branding.APP_TITLE)


def test_the_tooltip_describes_the_current_state(tray) -> None:
    tray.set_snapshot(Snapshot(reachable=True, mode="live", state="recording"))
    _, tooltip = properties(tray)["ToolTip"]

    assert tooltip[3] == "listening · writing"


def test_an_unreachable_server_shows_in_the_tooltip(tray) -> None:
    tray.set_snapshot(Snapshot(reachable=False))
    _, tooltip = properties(tray)["ToolTip"]

    assert "not running" in tooltip[2] or "not running" in tooltip[3]


def test_one_property_can_be_fetched_on_its_own(tray) -> None:
    class Call:
        body = ("org.kde.StatusNotifierItem", "Status")

    signature, body = tray.answer(PROPERTIES_IFACE, "Get", ITEM_PATH, Call())

    assert signature == "v"
    assert body[0] == ("s", "Active")


def test_an_unknown_property_is_refused_rather_than_guessed(tray) -> None:
    class Call:
        body = ("org.kde.StatusNotifierItem", "NoSuchThing")

    assert tray.answer(PROPERTIES_IFACE, "Get", ITEM_PATH, Call()) is None


def test_setting_a_property_is_accepted_and_ignored(tray) -> None:
    """Several hosts set properties speculatively. Erroring makes them log a failure every time,
    and nothing here is writable anyway."""
    assert tray.answer(PROPERTIES_IFACE, "Set", ITEM_PATH) == ("", ())


# -- unknown calls -------------------------------------------------------------------------


def test_an_unknown_member_gets_no_reply_body_so_the_loop_sends_an_error(tray) -> None:
    """The loop turns `None` into an error reply. Silence would leave the host blocked until its
    own timeout, and a tray that takes thirty seconds to appear is hard to attribute."""
    assert tray.answer(ITEM_IFACE, "Teleport", ITEM_PATH) is None
    assert tray.answer("com.example.Nonsense", "Anything", ITEM_PATH) is None


def test_introspection_is_answered_for_both_objects(tray) -> None:
    _, (item,) = tray.answer("org.freedesktop.DBus.Introspectable", "Introspect", ITEM_PATH)
    _, (menu,) = tray.answer("org.freedesktop.DBus.Introspectable", "Introspect", MENU_PATH)

    assert ITEM_IFACE in item and "NewIcon" in item
    assert MENU_IFACE in menu and "GetLayout" in menu


def test_ping_is_answered(tray) -> None:
    assert tray.answer("org.freedesktop.DBus.Peer", "Ping", ITEM_PATH) == ("", ())


# -- the menu ------------------------------------------------------------------------------


def test_the_menu_offers_the_recording_controls_when_idle(tray) -> None:
    _, children = layout(tray)
    labels = [child[1].get("label", ("s", ""))[1] for child in children]

    assert "Start live transcription" in labels
    assert "Record a window…" in labels


def test_the_menu_shrinks_when_the_server_is_gone(tray) -> None:
    """Every other item would fail at the moment it was clicked."""
    tray.set_snapshot(Snapshot(reachable=False))
    _, children = layout(tray)

    assert len(children) == 4


def test_a_recording_session_offers_stop_and_not_start(tray) -> None:
    tray.set_snapshot(Snapshot(reachable=True, mode="live", state="recording"))
    _, children = layout(tray)
    labels = [child[1].get("label", ("s", ""))[1] for child in children]

    assert "Stop recording" in labels
    assert "Start live transcription" not in labels


def test_clicking_an_item_activates_it_by_id() -> None:
    clicked: list[str] = []
    tray = TrayIcon(on_activate=clicked.append)
    tray.set_snapshot(Snapshot(reachable=True, mode="live", state="idle"))
    _, children = layout(tray)
    index = next(
        child[0]
        for child in children
        if child[1].get("label", ("s", ""))[1] == "Open the interface"
    )

    class Call:
        body = (index, "clicked", ("s", ""), 0)

    tray.answer(MENU_IFACE, "Event", MENU_PATH, Call())

    assert clicked == ["open"]


def test_a_hover_is_not_a_click() -> None:
    clicked: list[str] = []
    tray = TrayIcon(on_activate=clicked.append)
    tray.set_snapshot(Snapshot(reachable=True, mode="live", state="idle"))

    class Call:
        body = (3, "hovered", ("s", ""), 0)

    tray.answer(MENU_IFACE, "Event", MENU_PATH, Call())

    assert clicked == []


def test_an_event_for_an_item_that_is_not_there_is_ignored() -> None:
    """The menu is rebuilt on every read, so a click can arrive against a layout that has already
    changed — a session that started between the host drawing the menu and the user releasing."""
    clicked: list[str] = []
    tray = TrayIcon(on_activate=clicked.append)
    tray.set_snapshot(Snapshot(reachable=False))

    class Call:
        body = (99, "clicked", ("s", ""), 0)

    tray.answer(MENU_IFACE, "Event", MENU_PATH, Call())

    assert clicked == []


def test_opening_the_menu_bumps_the_revision(tray) -> None:
    before, _ = layout(tray)
    tray.answer(MENU_IFACE, "AboutToShow", MENU_PATH)
    after, _ = layout(tray)

    assert after > before


def test_the_menu_object_reports_the_protocol_version(tray) -> None:
    signature, body = tray.answer(PROPERTIES_IFACE, "GetAll", MENU_PATH)

    assert body[0]["Version"] == ("u", 3)


# -- left click ------------------------------------------------------------------------------


def test_activating_the_icon_opens_the_interface() -> None:
    clicked: list[str] = []
    tray = TrayIcon(on_activate=clicked.append)

    tray.answer(ITEM_IFACE, "Activate", ITEM_PATH)

    assert clicked == ["open"]


def test_scrolling_is_accepted_and_does_nothing() -> None:
    """Hosts send scroll events unprompted. An error reply on every wheel tick over the tray is
    noise in someone else's log."""
    tray = TrayIcon()

    assert tray.answer(ITEM_IFACE, "Scroll", ITEM_PATH) == ("", ())


# -- failing to reach a bus ------------------------------------------------------------------


def test_a_bus_that_cannot_be_opened_is_not_fatal(monkeypatch) -> None:
    """A desktop with no session bus is an ordinary outcome. The companion still holds its
    shortcuts and still answers the control script; losing the whole process over a missing tray
    would be the wrong trade."""
    from app.companion import tray as tray_module

    def refuse(**_kwargs):
        raise OSError("no bus here")

    monkeypatch.setattr(tray_module, "open_dbus_connection", refuse)
    icon = TrayIcon()

    assert _REAL_START(icon) is False
    assert icon.registered is False
