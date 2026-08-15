"""The xdg-desktop-portal ScreenCast session — consent, and a PipeWire node (D-022).

**This is the step with no alternative implementation.** Everything else in window capture has a
fallback; this does not. Under Wayland an application cannot enumerate windows, cannot read another
window's pixels, and cannot grab the screen. The compositor shows *its own* picker, the user
consents, and only then do we receive a node we may read.

Two consequences run through the whole feature. There is **a dialog in the middle of it that can be
declined**, which is a normal outcome and not an error. And the application **never learns what
windows exist** — so nothing in the interface may imply a list, a grid, or a pre-selection.

## The pattern that is easy to get wrong

Portal methods do not return their answer. They return an *object path*, and the real answer arrives
later as a `Response` signal on that path. Miss the signal and the call appears to hang forever.

Worse, the signal can arrive *before* the method reply does — so the match rule has to be installed
first, and the reply read second. Doing it the natural way round is a race that passes on a fast
machine and hangs on a loaded one.

The path is predictable rather than discovered: `/org/freedesktop/portal/desktop/request/<our bus
name, dotted parts joined by underscores>/<our token>`. We supply the token, which is what lets the
rule be installed before the call.
"""

from __future__ import annotations

import logging
import secrets
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

PORTAL_BUS: Final = "org.freedesktop.portal.Desktop"
PORTAL_PATH: Final = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE: Final = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE: Final = "org.freedesktop.portal.Request"

#: Portal response codes. 0 succeeded, 1 the user cancelled, 2 ended some other way.
RESPONSE_OK: Final = 0
RESPONSE_CANCELLED: Final = 1

#: Cursor handling. `hidden` keeps the pointer out of the recording; `embedded` draws it in.
CURSOR_HIDDEN: Final = 1
CURSOR_EMBEDDED: Final = 2

#: Ask the portal to remember consent for as long as the application lives *and* across restarts.
PERSIST_EXPLICIT: Final = 2

#: How long to wait for the user to answer the picker. Generous on purpose — they may be finding
#: the window, or the dialog may be behind something.
CONSENT_TIMEOUT_S: Final = 120.0

#: How long to wait for the non-interactive calls, which involve no human.
CALL_TIMEOUT_S: Final = 10.0


class PortalError(RuntimeError):
    """The screen-cast portal could not give us a stream."""


class PortalDeclined(PortalError):
    """The user closed the picker or refused. **Not a failure** — an answer."""


class PortalUnavailable(PortalError):
    """There is no portal, or no session bus, to talk to."""


@dataclass(frozen=True)
class WindowStream:
    """A window the user chose, and the PipeWire node carrying it."""

    node_id: int
    #: An open file descriptor for the PipeWire remote. The recorder owns closing it.
    fd: int
    #: Width and height the portal reported, when it reported any.
    width: int = 0
    height: int = 0
    #: Opaque token that lets the next recording skip the picker. Stored in the credential store,
    #: never in the config file: it is a granted capability, and D-017 governs where those live.
    restore_token: str = ""
    #: The portal's session handle, kept so the session can be closed explicitly.
    session_handle: str = ""

    def describe(self) -> str:
        size = f"{self.width}×{self.height}" if self.width and self.height else "unknown size"
        return f"PipeWire node {self.node_id} ({size})"


def _token() -> str:
    """A token for one request. Random, because two concurrent requests must not collide."""
    return f"transcriber_{secrets.token_hex(8)}"


def _request_path(unique_name: str, token: str) -> str:
    """The object path the portal will emit this request's ``Response`` on."""
    sender = unique_name.lstrip(":").replace(".", "_")
    return f"{PORTAL_PATH}/request/{sender}/{token}"


class PortalSession:
    """One screen-cast negotiation, start to finish.

    Deliberately synchronous. It is called from a worker thread while a human looks at a dialog,
    and making it async would put a two-minute await in the event loop for no benefit.
    """

    def __init__(self, *, cursor_mode: str = "hidden", restore_token: str = "") -> None:
        self.cursor_mode = CURSOR_EMBEDDED if cursor_mode == "embedded" else CURSOR_HIDDEN
        self.restore_token = restore_token
        self._connection: Any = None
        self._session_handle = ""

    # -- lifecycle -------------------------------------------------------------------

    def open(self) -> WindowStream:
        """Negotiate a window stream. Raises :class:`PortalDeclined` if the user says no."""
        self._connect()
        try:
            self._session_handle = self._create_session()
            self._select_sources(self._session_handle)
            streams, restore_token = self._start(self._session_handle)
            fd = self._open_pipewire_remote(self._session_handle)
        except PortalError:
            self.close()
            raise
        except Exception as exc:  # noqa: BLE001 - any transport failure reads as unavailable
            self.close()
            raise PortalError(f"The screen-sharing portal failed: {exc}") from exc

        if not streams:
            self.close()
            raise PortalError("The portal granted access but returned no stream to read.")

        node_id, properties = streams[0]
        size = properties.get("size", (0, 0)) if isinstance(properties, dict) else (0, 0)
        return WindowStream(
            node_id=int(node_id),
            fd=fd,
            width=int(size[0]) if size else 0,
            height=int(size[1]) if size else 0,
            restore_token=restore_token,
            session_handle=self._session_handle,
        )

    def close(self) -> None:
        """Close the portal session and the bus connection. Safe at any point."""
        connection, self._connection = self._connection, None
        if connection is None:
            return
        if self._session_handle:
            with suppress(Exception):
                from jeepney import DBusAddress, new_method_call

                address = DBusAddress(
                    object_path=self._session_handle,
                    bus_name=PORTAL_BUS,
                    interface="org.freedesktop.portal.Session",
                )
                connection.send(new_method_call(address, "Close"))
        with suppress(Exception):
            connection.close()
        self._session_handle = ""

    # -- the four calls --------------------------------------------------------------

    def _connect(self) -> None:
        try:
            from jeepney.io.blocking import open_dbus_connection
        except ImportError as exc:
            raise PortalUnavailable(
                "The D-Bus client is missing. Reinstall the dependencies with: uv sync"
            ) from exc

        try:
            # `enable_fds` is not optional: `OpenPipeWireRemote` answers with a file descriptor,
            # and a connection without descriptor passing silently receives nothing usable.
            self._connection = open_dbus_connection(bus="SESSION", enable_fds=True)
        except Exception as exc:  # noqa: BLE001
            raise PortalUnavailable(
                "Could not reach the desktop session bus, so screen sharing is unavailable here."
            ) from exc

    def _call_with_response(
        self,
        method: str,
        body: tuple,
        signature: str,
        *,
        timeout: float,
    ) -> dict[str, Any]:
        """Make a portal call and wait for its ``Response`` signal.

        The match rule is installed **before** the call, because the signal can arrive first.
        """
        from jeepney import DBusAddress, MatchRule, message_bus, new_method_call
        from jeepney.io.blocking import Proxy

        connection = self._connection
        token = _token()
        path = _request_path(connection.unique_name, token)

        rule = MatchRule(type="signal", interface=REQUEST_IFACE, member="Response", path=path)
        Proxy(message_bus, connection).AddMatch(rule)

        address = DBusAddress(
            object_path=PORTAL_PATH, bus_name=PORTAL_BUS, interface=SCREENCAST_IFACE
        )
        options = body[-1]
        options["handle_token"] = ("s", token)
        message = new_method_call(address, method, signature, body)

        with connection.filter(rule) as queue:
            connection.send_and_get_reply(message)
            try:
                signal = connection.recv_until_filtered(queue, timeout=timeout)
            except TimeoutError as exc:
                raise PortalError(
                    f"The screen-sharing dialog did not answer within {timeout:.0f} seconds."
                ) from exc

        code, results = signal.body
        if code == RESPONSE_CANCELLED:
            raise PortalDeclined("Screen sharing was cancelled.")
        if code != RESPONSE_OK:
            raise PortalError(f"The screen-sharing portal refused ({method}, code {code}).")
        return {key: value[1] for key, value in results.items()}

    def _create_session(self) -> str:
        results = self._call_with_response(
            "CreateSession",
            ({"session_handle_token": ("s", _token())},),
            "a{sv}",
            timeout=CALL_TIMEOUT_S,
        )
        handle = results.get("session_handle", "")
        if not handle:
            raise PortalError("The portal created a session but did not say which.")
        return handle

    def _select_sources(self, session_handle: str) -> None:
        from .probe import SOURCE_WINDOW

        options: dict[str, Any] = {
            "types": ("u", SOURCE_WINDOW),
            "multiple": ("b", False),
            "cursor_mode": ("u", self.cursor_mode),
            "persist_mode": ("u", PERSIST_EXPLICIT),
        }
        if self.restore_token:
            options["restore_token"] = ("s", self.restore_token)

        self._call_with_response(
            "SelectSources", (session_handle, options), "oa{sv}", timeout=CALL_TIMEOUT_S
        )

    def _start(self, session_handle: str) -> tuple[list, str]:
        # The empty parent-window identifier means the picker is not parented to a window of ours.
        # We have no toplevel — the interface is a browser tab — so there is nothing to parent to.
        results = self._call_with_response(
            "Start",
            (session_handle, "", {}),
            "osa{sv}",
            timeout=CONSENT_TIMEOUT_S,
        )
        return list(results.get("streams", [])), str(results.get("restore_token", ""))

    def _open_pipewire_remote(self, session_handle: str) -> int:
        from jeepney import DBusAddress, new_method_call

        address = DBusAddress(
            object_path=PORTAL_PATH, bus_name=PORTAL_BUS, interface=SCREENCAST_IFACE
        )
        message = new_method_call(address, "OpenPipeWireRemote", "oa{sv}", (session_handle, {}))
        reply = self._connection.send_and_get_reply(message)

        descriptor = reply.body[0]
        # jeepney hands back a wrapper that owns the descriptor; `to_raw_fd` transfers ownership to
        # us, which is what lets it survive being passed to a subprocess.
        if hasattr(descriptor, "to_raw_fd"):
            return int(descriptor.to_raw_fd())
        return int(descriptor)


def open_window_stream(*, cursor_mode: str = "hidden", restore_token: str = "") -> WindowStream:
    """Ask the desktop for a window. Convenience wrapper for the ordinary case."""
    return PortalSession(cursor_mode=cursor_mode, restore_token=restore_token).open()
