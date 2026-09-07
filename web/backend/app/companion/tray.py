"""The tray icon: one `StatusNotifierItem` on the session bus, and the menu behind it (D-043).

Everything else in this package was built and tested long before this file existed — the renderer,
the frame clock, the (mode, run state) map, the menu model — and none of it put a picture on screen,
because the last hop was missing. This is that hop.

**`jeepney` has no server side, only primitives**, and that is the whole shape of this module. There
is no "export this object" helper: what there is, is `new_method_return`, `new_error`, `new_signal`
and a connection you can `receive()` from. So this is a dispatch loop on its own thread that reads
messages, matches (interface, member), and replies. The checklist carried "whether `jeepney` can
export SNI pixmaps at all" as an open question for weeks; it was answered by measurement before this
was written — a 22 × 22 ARGB32 buffer round-trips through an `a(iiay)` reply byte-identical — so the
`PySide6` fallback that question named is not needed.

**Anything unrecognised gets an error reply, never silence.** A host that asks for a property and
receives nothing blocks until its own timeout, and a tray that takes thirty seconds to draw looks
broken in a way that is hard to attribute.

Two protocols are served from one loop:

* `org.kde.StatusNotifierItem` on `/StatusNotifierItem` — the icon, its status, and activation.
* `com.canonical.dbusmenu` on `/MenuBar` — the right-click menu, built from `menu.py`'s model.

Both are read-mostly: the host asks, this answers from whatever the frame clock last produced. The
only thing pushed the other way is `NewIcon`, and that is throttled — the clock runs at ten frames a
second and no tray needs telling ten times a second.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Final

from jeepney import (
    DBusAddress,
    HeaderFields,
    MessageType,
    message_bus,
    new_error,
    new_method_call,
    new_method_return,
    new_signal,
)
from jeepney.io.blocking import open_dbus_connection

from .. import branding
from .animation import Snapshot
from .menu import MenuItem
from .menu import build as build_menu
from .raster import render_pixmap
from .visual_states import VISUALS

logger = logging.getLogger(__name__)

WATCHER_BUS: Final = "org.kde.StatusNotifierWatcher"
WATCHER_PATH: Final = "/StatusNotifierWatcher"
WATCHER_IFACE: Final = "org.kde.StatusNotifierWatcher"

ITEM_PATH: Final = "/StatusNotifierItem"
ITEM_IFACE: Final = "org.kde.StatusNotifierItem"
MENU_PATH: Final = "/MenuBar"
MENU_IFACE: Final = "com.canonical.dbusmenu"

PROPERTIES_IFACE: Final = "org.freedesktop.DBus.Properties"
INTROSPECTABLE_IFACE: Final = "org.freedesktop.DBus.Introspectable"
PEER_IFACE: Final = "org.freedesktop.DBus.Peer"

#: The shortest gap between two `NewIcon` signals. The frame clock runs at 10 fps and a tray
#: redrawing ten times a second buys nothing a person can see, while costing a round trip each time.
ICON_SIGNAL_INTERVAL_S: Final = 0.25

#: How long the dispatch loop blocks before checking whether it has been asked to stop.
RECEIVE_TIMEOUT_S: Final = 0.5

#: `RequestName` flags: do not queue behind an existing owner, and allow replacement.
NAME_FLAGS: Final = 0x1 | 0x4


def _introspection(interfaces: str) -> str:
    return (
        '<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN" '
        '"http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">\n'
        f"<node>{interfaces}</node>"
    )


class TrayIcon:
    """One StatusNotifierItem, served from a background thread.

    The caller drives it: :meth:`update` is handed each frame the clock produces, and the loop
    answers whatever the host asks with the most recent one. Nothing here polls the server or knows
    what a session is — that stays in `Companion`, which is what keeps this file about D-Bus.
    """

    def __init__(
        self,
        *,
        on_activate: Any = None,
        listening: Any = None,
        size: int = 22,
    ) -> None:
        self._on_activate = on_activate or (lambda item_id: "")
        self._listening = listening or (lambda: True)
        self._size = size

        self._lock = threading.Lock()
        self._snapshot = Snapshot()
        self._pixmap = render_pixmap(VISUALS["server-down"], 0.0, size=size)
        self._items: list[MenuItem] = []
        self._menu_revision = 1

        self._connection: Any = None
        self._bus_name = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_signal = 0.0
        self._last_visual = ""
        self.registered = False

    # -- lifecycle -----------------------------------------------------------------------

    def start(self) -> bool:
        """Claim a bus name, register with the watcher, and begin serving. Never raises.

        Returns whether the icon reached the tray. **A desktop with no StatusNotifierWatcher is an
        ordinary outcome, not an error** — the companion still holds its shortcuts and still answers
        the control script, and taking the whole process down over a missing tray would be the
        wrong trade.
        """
        try:
            self._connection = open_dbus_connection(bus="SESSION")
        except Exception:
            logger.info("No session bus; the tray icon is unavailable", exc_info=True)
            return False

        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        try:
            self._connection.send_and_get_reply(
                new_method_call(message_bus, "RequestName", "su", (self._bus_name, NAME_FLAGS))
            )
        except Exception:
            logger.info("Could not claim %s", self._bus_name, exc_info=True)
            self._close()
            return False

        self._thread = threading.Thread(target=self._serve, name="tray-dbus", daemon=True)
        self._thread.start()

        self.registered = self._register_with_watcher()
        if not self.registered:
            logger.info("No %s on this desktop; running without a tray icon", WATCHER_BUS)
        return self.registered

    def _register_with_watcher(self) -> bool:
        watcher = DBusAddress(WATCHER_PATH, bus_name=WATCHER_BUS, interface=WATCHER_IFACE)
        try:
            reply = self._connection.send_and_get_reply(
                new_method_call(watcher, "RegisterStatusNotifierItem", "s", (self._bus_name,))
            )
        except Exception:
            logger.debug("The status notifier watcher refused registration", exc_info=True)
            return False
        return reply.header.message_type is not MessageType.error

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._close()

    def _close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.debug("The tray connection did not close cleanly", exc_info=True)

    # -- what the frame clock hands us ---------------------------------------------------

    def update(self, frame: Any, visual: Any, snapshot: Snapshot | None = None) -> None:
        """Take one frame. Signals `NewIcon` only when the picture has actually moved on.

        ``frame`` is `aperture.Frame`; its SVG is not used here, but taking it keeps this callable
        directly from the frame clock rather than needing an adapter.
        """
        # The frame's own amplitudes, not a second sample of the same animation: the two renderers
        # then draw the identical instant rather than two instants of the same clock.
        pixmap = render_pixmap(
            visual, time.monotonic(), size=self._size, amplitudes=getattr(frame, "amplitudes", None)
        )
        with self._lock:
            self._pixmap = pixmap
            if snapshot is not None:
                self._snapshot = snapshot
            changed = visual.name != self._last_visual
            self._last_visual = visual.name

        now = time.monotonic()
        if now - self._last_signal < ICON_SIGNAL_INTERVAL_S and not changed:
            return
        self._last_signal = now
        self._emit(ITEM_PATH, ITEM_IFACE, "NewIcon")
        if changed:
            self._emit(ITEM_PATH, ITEM_IFACE, "NewToolTip")

    def set_snapshot(self, snapshot: Snapshot) -> None:
        """The state the menu is built from. Separate from the frame, which arrives ten times as
        often and says nothing the menu needs."""
        with self._lock:
            if snapshot == self._snapshot:
                return
            self._snapshot = snapshot
            self._menu_revision += 1
        self._emit(MENU_PATH, MENU_IFACE, "LayoutUpdated", "ui", (self._menu_revision, 0))

    def _emit(self, path: str, interface: str, signal: str, signature=None, body=()) -> None:
        connection = self._connection
        if connection is None:
            return
        emitter = DBusAddress(path, bus_name=self._bus_name, interface=interface)
        try:
            connection.send(new_signal(emitter, signal, signature, body))
        except Exception:
            logger.debug("Could not emit %s", signal, exc_info=True)

    # -- the dispatch loop ---------------------------------------------------------------

    def _serve(self) -> None:
        while not self._stop.is_set():
            connection = self._connection
            if connection is None:
                return
            try:
                message = connection.receive(timeout=RECEIVE_TIMEOUT_S)
            except TimeoutError:
                continue
            except Exception:
                if not self._stop.is_set():
                    logger.debug("The tray connection ended", exc_info=True)
                return
            if message.header.message_type is not MessageType.method_call:
                continue
            try:
                self._dispatch(connection, message)
            except Exception:
                logger.exception("A tray request could not be answered")

    def _dispatch(self, connection: Any, message: Any) -> None:
        fields = message.header.fields
        interface = fields.get(HeaderFields.interface, "")
        member = fields.get(HeaderFields.member, "")
        path = fields.get(HeaderFields.path, "")

        reply = self.answer(interface, member, path, message)
        if reply is None:
            connection.send(
                new_error(
                    message,
                    "org.freedesktop.DBus.Error.UnknownMethod",
                    "s",
                    (f"{interface}.{member} is not implemented",),
                )
            )
            return
        signature, body = reply
        connection.send(new_method_return(message, signature, body))

    def answer(self, interface: str, member: str, path: str, message: Any = None):
        """Work out the reply to one call, as ``(signature, body)`` — or ``None`` for unknown.

        Split out of the loop so the whole protocol can be tested without a bus, which is most of
        what can be wrong here: a property a host requires and this does not answer produces an
        icon that never appears, and no unit test of the renderer would notice.
        """
        body = tuple(message.body) if message is not None else ()

        if interface == PEER_IFACE and member == "Ping":
            return ("", ())
        if interface == INTROSPECTABLE_IFACE and member == "Introspect":
            return ("s", (self._introspect(path),))

        if interface == PROPERTIES_IFACE:
            table = self._menu_properties() if path == MENU_PATH else self._item_properties()
            if member == "GetAll":
                return ("a{sv}", (table,))
            if member == "Get":
                name = body[1] if len(body) > 1 else ""
                if name not in table:
                    return None
                return ("v", (table[name],))
            if member == "Set":
                # Nothing here is writable. Answering rather than erroring keeps a host that sets
                # a property speculatively — several do — from logging a failure every time.
                return ("", ())
            return None

        if interface == ITEM_IFACE:
            return self._item_method(member, body)
        if interface == MENU_IFACE:
            return self._menu_method(member, body)
        return None

    # -- the item ------------------------------------------------------------------------

    def _item_properties(self) -> dict[str, tuple[str, Any]]:
        with self._lock:
            pixmap = self._pixmap
            snapshot = self._snapshot
        description = snapshot.visual().description
        icon = [pixmap.as_sni_pixmap()]
        return {
            "Category": ("s", "ApplicationStatus"),
            "Id": ("s", branding.APP_SLUG),
            "Title": ("s", branding.APP_TITLE),
            "Status": ("s", "Active"),
            "WindowId": ("i", 0),
            # Empty rather than absent: a host reads IconName first and falls through to the pixmap
            # when it is empty, and one that finds neither draws nothing at all.
            "IconName": ("s", ""),
            "IconPixmap": ("a(iiay)", icon),
            "OverlayIconName": ("s", ""),
            "OverlayIconPixmap": ("a(iiay)", []),
            "AttentionIconName": ("s", ""),
            "AttentionIconPixmap": ("a(iiay)", []),
            "AttentionMovieName": ("s", ""),
            "ToolTip": ("(sa(iiay)ss)", ("", icon, branding.APP_TITLE, description)),
            # False, so a left click reaches `Activate` and opens the interface. True would make
            # the whole icon a menu button and lose the single most useful click on it.
            "ItemIsMenu": ("b", False),
            "Menu": ("o", MENU_PATH),
        }

    def _item_method(self, member: str, body: tuple):
        if member in ("Activate", "SecondaryActivate"):
            self._on_activate("open")
            return ("", ())
        if member == "ContextMenu":
            return ("", ())
        if member == "Scroll":
            return ("", ())
        return None

    # -- the menu ------------------------------------------------------------------------

    def _menu_properties(self) -> dict[str, tuple[str, Any]]:
        return {
            "Version": ("u", 3),
            "TextDirection": ("s", "ltr"),
            "Status": ("s", "normal"),
            "IconThemePath": ("as", []),
        }

    def _current_items(self) -> list[MenuItem]:
        with self._lock:
            snapshot = self._snapshot
        return build_menu(snapshot, listening=bool(self._listening()))

    def _menu_method(self, member: str, body: tuple):
        if member == "GetLayout":
            return self._layout()
        if member == "AboutToShow":
            # Rebuilt on every open, so an item's enabled state is never one poll behind what the
            # user is looking at. True asks the host to re-read the layout before drawing it.
            with self._lock:
                self._menu_revision += 1
            return ("b", (True,))
        if member == "AboutToShowGroup":
            return ("aiai", ([], []))
        if member == "GetGroupProperties":
            return self._group_properties(body[0] if body else [])
        if member == "GetProperty":
            item_id = int(body[0]) if body else 0
            name = str(body[1]) if len(body) > 1 else ""
            value = self._properties_for(item_id).get(name)
            return None if value is None else ("v", (value,))
        if member == "Event":
            self._handle_event(body)
            return ("", ())
        if member == "EventGroup":
            for entry in body[0] if body else []:
                self._handle_event(entry)
            return ("ai", ([],))
        return None

    def _handle_event(self, body: tuple) -> None:
        if not body:
            return
        item_id = int(body[0])
        event = str(body[1]) if len(body) > 1 else "clicked"
        if event != "clicked":
            return
        items = self._current_items()
        if 1 <= item_id <= len(items):
            result = self._on_activate(items[item_id - 1].id)
            logger.info("Tray: %s", result)

    def _layout(self):
        """The whole menu in one reply. `(revision, (id, properties, children))`.

        Flat by construction: `menu.py` builds one level, and a submenu would be a second place
        where an item's enabled state is decided.
        """
        items = self._current_items()
        children = [
            (index, self._dbus_properties(item), []) for index, item in enumerate(items, start=1)
        ]
        with self._lock:
            revision = self._menu_revision
        root = (
            0,
            {"children-display": ("s", "submenu")},
            [(("(ia{sv}av)"), child) for child in children],
        )
        return ("u(ia{sv}av)", (revision, root))

    def _group_properties(self, ids):
        items = self._current_items()
        wanted = [int(i) for i in ids] if ids else list(range(1, len(items) + 1))
        return (
            "a(ia{sv})",
            ([(i, self._properties_for(i)) for i in wanted if 1 <= i <= len(items)],),
        )

    def _properties_for(self, item_id: int) -> dict[str, tuple[str, Any]]:
        items = self._current_items()
        if not 1 <= item_id <= len(items):
            return {}
        return self._dbus_properties(items[item_id - 1])

    @staticmethod
    def _dbus_properties(item: MenuItem) -> dict[str, tuple[str, Any]]:
        """`menu.py`'s property map, with each value wrapped in its variant signature."""
        typed: dict[str, tuple[str, Any]] = {}
        for key, value in item.as_dbus().items():
            if isinstance(value, bool):
                typed[key] = ("b", value)
            elif isinstance(value, int):
                typed[key] = ("i", value)
            else:
                typed[key] = ("s", str(value))
        return typed

    def _introspect(self, path: str) -> str:
        if path == MENU_PATH:
            return _introspection(
                f'<interface name="{MENU_IFACE}">'
                '<method name="GetLayout">'
                '<arg type="i" name="parentId" direction="in"/>'
                '<arg type="i" name="recursionDepth" direction="in"/>'
                '<arg type="as" name="propertyNames" direction="in"/>'
                '<arg type="u" name="revision" direction="out"/>'
                '<arg type="(ia{sv}av)" name="layout" direction="out"/>'
                "</method>"
                '<method name="Event">'
                '<arg type="i" name="id" direction="in"/>'
                '<arg type="s" name="eventId" direction="in"/>'
                '<arg type="v" name="data" direction="in"/>'
                '<arg type="u" name="timestamp" direction="in"/>'
                "</method>"
                '<method name="AboutToShow">'
                '<arg type="i" name="id" direction="in"/>'
                '<arg type="b" name="needUpdate" direction="out"/>'
                "</method>"
                '<signal name="LayoutUpdated">'
                '<arg type="u" name="revision"/><arg type="i" name="parent"/>'
                "</signal>"
                "</interface>"
            )
        return _introspection(
            f'<interface name="{ITEM_IFACE}">'
            '<method name="Activate">'
            '<arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/>'
            "</method>"
            '<method name="SecondaryActivate">'
            '<arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/>'
            "</method>"
            '<method name="ContextMenu">'
            '<arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/>'
            "</method>"
            '<signal name="NewIcon"/><signal name="NewToolTip"/><signal name="NewStatus">'
            '<arg type="s" name="status"/></signal>'
            "</interface>"
        )
