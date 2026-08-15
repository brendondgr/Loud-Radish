"""The portal ScreenCast negotiation (D-022).

Driven against a scripted D-Bus double, because the real thing puts a dialog on screen and waits
for a human. What is being protected is the request/response pattern, which is the specific thing
that is easy to get subtly wrong and hard to debug afterwards:

* the match rule must be installed **before** the call, since the signal can arrive first;
* a user closing the picker is an *answer*, not a failure, and must be distinguishable;
* every exit path must close the session, or the compositor keeps showing a sharing indicator for
  a recording that is not happening.
"""

from __future__ import annotations

import types
from typing import Any

import pytest
from app.services.capture import portal


class FakeSignal:
    def __init__(self, body: tuple) -> None:
        self.body = body


class FakeQueue:
    pass


class FakeConnection:
    """Records the order of operations, which is the whole point of these tests."""

    def __init__(self, responses: list[tuple[int, dict]], *, fd: int = 42) -> None:
        self.unique_name = ":1.99"
        self.responses = list(responses)
        self.fd = fd
        self.events: list[str] = []
        self.sent: list[Any] = []
        self.closed = False
        self.filters_open = 0

    # -- the bits jeepney exposes ---------------------------------------------------

    def send_and_get_reply(self, message):  # noqa: ANN001
        self.sent.append(message)
        name = getattr(message, "member", "")
        self.events.append(f"call:{name}")
        if name == "OpenPipeWireRemote":
            return types.SimpleNamespace(body=(_FakeFd(self.fd),))
        return types.SimpleNamespace(body=("/request/path",))

    def send(self, message):  # noqa: ANN001
        self.events.append(f"send:{getattr(message, 'member', '')}")

    def filter(self, rule):  # noqa: ANN001
        connection = self

        class _Filter:
            def __enter__(self):
                connection.filters_open += 1
                connection.events.append("filter:open")
                return FakeQueue()

            def __exit__(self, *exc):
                connection.filters_open -= 1
                connection.events.append("filter:close")
                return False

        return _Filter()

    def recv_until_filtered(self, queue, timeout=None):  # noqa: ANN001, ARG002
        self.events.append("await-response")
        if not self.responses:
            raise TimeoutError("no scripted response left")
        return FakeSignal(self.responses.pop(0))

    def close(self) -> None:
        self.closed = True
        self.events.append("close")


class _FakeFd:
    def __init__(self, raw: int) -> None:
        self._raw = raw

    def to_raw_fd(self) -> int:
        return self._raw


def variant(value: Any) -> tuple[str, Any]:
    """A D-Bus variant as jeepney presents it: `(signature, value)`."""
    return ("v", value)


def scripted(*, streams=None, restore_token="", cancel_at=None) -> list[tuple[int, dict]]:
    """Responses for CreateSession, SelectSources, and Start, in that order."""
    streams = streams if streams is not None else [(77, {"size": (1920, 1080)})]
    sequence = [
        (portal.RESPONSE_OK, {"session_handle": variant("/session/1")}),
        (portal.RESPONSE_OK, {}),
        (
            portal.RESPONSE_OK,
            {"streams": variant(streams), "restore_token": variant(restore_token)},
        ),
    ]
    if cancel_at is not None:
        sequence[cancel_at] = (portal.RESPONSE_CANCELLED, {})
    return sequence


@pytest.fixture
def connect(monkeypatch):
    """Replace the bus connection with a scripted double."""
    made: list[FakeConnection] = []

    def factory(responses, **kwargs):
        connection = FakeConnection(responses, **kwargs)
        made.append(connection)

        def _connect(self) -> None:
            self._connection = connection

        monkeypatch.setattr(portal.PortalSession, "_connect", _connect)
        return connection

    # `AddMatch` goes through a Proxy we do not want to build, so it is neutralised.
    monkeypatch.setattr(portal, "_token", lambda: "tok")
    return factory, made


@pytest.fixture(autouse=True)
def stub_jeepney(monkeypatch):
    """Stand in for the jeepney imports the module makes lazily."""
    fake = types.ModuleType("jeepney")

    class DBusAddress:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class MatchRule:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def new_method_call(address, member, signature=None, body=None):  # noqa: ANN001
        return types.SimpleNamespace(member=member, signature=signature, body=body)

    fake.DBusAddress = DBusAddress
    fake.MatchRule = MatchRule
    fake.new_method_call = new_method_call
    fake.message_bus = object()

    blocking = types.ModuleType("jeepney.io.blocking")

    class Proxy:
        def __init__(self, *args, **kwargs):
            pass

        def AddMatch(self, rule):  # noqa: N802, ANN001
            return None

    blocking.Proxy = Proxy
    blocking.open_dbus_connection = lambda **kwargs: None

    monkeypatch.setitem(__import__("sys").modules, "jeepney", fake)
    monkeypatch.setitem(__import__("sys").modules, "jeepney.io.blocking", blocking)
    return fake


# -- the happy path -----------------------------------------------------------------------


def test_a_granted_stream_carries_its_node_and_descriptor(connect) -> None:
    factory, _ = connect
    factory(scripted())

    stream = portal.PortalSession().open()

    assert stream.node_id == 77
    assert stream.fd == 42
    assert (stream.width, stream.height) == (1920, 1080)
    assert stream.session_handle == "/session/1"


def test_a_restore_token_comes_back_when_the_portal_offers_one(connect) -> None:
    factory, _ = connect
    factory(scripted(restore_token="remember-me"))

    assert portal.PortalSession().open().restore_token == "remember-me"


def test_a_stream_without_a_size_is_still_usable(connect) -> None:
    """Not every compositor reports one, and the recorder does not need it."""
    factory, _ = connect
    factory(scripted(streams=[(5, {})]))

    stream = portal.PortalSession().open()
    assert stream.node_id == 5
    assert (stream.width, stream.height) == (0, 0)


# -- the pattern that is easy to get wrong ---------------------------------------------------


def test_the_match_rule_is_installed_before_the_call(connect) -> None:
    """The signal can arrive before the method reply. Doing this the natural way round is a race
    that passes on a fast machine and hangs on a loaded one."""
    factory, made = connect
    factory(scripted())
    portal.PortalSession().open()

    events = made[0].events
    first_filter = events.index("filter:open")
    first_call = events.index("call:CreateSession")
    assert first_filter < first_call, f"call was made before the filter was open: {events}"


def test_every_interactive_call_waits_for_its_response(connect) -> None:
    factory, made = connect
    factory(scripted())
    portal.PortalSession().open()

    assert made[0].events.count("await-response") == 3


def test_the_filter_is_always_closed(connect) -> None:
    factory, made = connect
    factory(scripted())
    portal.PortalSession().open()
    assert made[0].filters_open == 0


# -- declining is an answer, not a failure ----------------------------------------------------


@pytest.mark.parametrize("stage", [0, 1, 2])
def test_cancelling_at_any_stage_raises_declined(connect, stage: int) -> None:
    factory, _ = connect
    factory(scripted(cancel_at=stage))

    with pytest.raises(portal.PortalDeclined):
        portal.PortalSession().open()


def test_declined_is_distinguishable_from_broken(connect) -> None:
    """The interface says "cancelled" for one and "something is wrong" for the other."""
    assert issubclass(portal.PortalDeclined, portal.PortalError)
    assert not issubclass(portal.PortalError, portal.PortalDeclined)


def test_a_refusal_that_is_not_a_cancellation_is_an_error(connect) -> None:
    factory, _ = connect
    factory([(2, {})])

    with pytest.raises(portal.PortalError, match="refused"):
        portal.PortalSession().open()


def test_a_dialog_nobody_answers_times_out_with_a_readable_message(connect) -> None:
    factory, _ = connect
    factory([])  # no scripted responses: recv raises TimeoutError

    with pytest.raises(portal.PortalError, match="did not answer"):
        portal.PortalSession().open()


def test_granting_access_but_returning_no_stream_is_an_error(connect) -> None:
    factory, _ = connect
    factory(scripted(streams=[]))

    with pytest.raises(portal.PortalError, match="no stream"):
        portal.PortalSession().open()


# -- cleanup ------------------------------------------------------------------------------------


def test_a_failure_closes_the_connection(connect) -> None:
    """Otherwise the compositor keeps showing a sharing indicator for a recording that is not
    happening, which is both alarming and impossible to dismiss."""
    factory, made = connect
    factory(scripted(cancel_at=2))

    with pytest.raises(portal.PortalDeclined):
        portal.PortalSession().open()
    assert made[0].closed is True


def test_close_is_safe_before_anything_opened() -> None:
    portal.PortalSession().close()  # must not raise


def test_close_is_idempotent(connect) -> None:
    factory, made = connect
    factory(scripted())

    session = portal.PortalSession()
    session.open()
    session.close()
    session.close()
    assert made[0].closed is True


def test_closing_ends_the_portal_session_too(connect) -> None:
    factory, made = connect
    factory(scripted())

    session = portal.PortalSession()
    session.open()
    session.close()
    assert "send:Close" in made[0].events


# -- options ------------------------------------------------------------------------------------


def test_the_cursor_can_be_kept_out_of_the_recording() -> None:
    assert portal.PortalSession(cursor_mode="hidden").cursor_mode == portal.CURSOR_HIDDEN
    assert portal.PortalSession(cursor_mode="embedded").cursor_mode == portal.CURSOR_EMBEDDED
    # Anything unrecognised keeps the pointer out, which is the more private default.
    assert portal.PortalSession(cursor_mode="nonsense").cursor_mode == portal.CURSOR_HIDDEN


def test_a_stored_token_is_offered_back_to_the_portal(connect) -> None:
    """This is what lets a second recording of the same window skip the picker."""
    factory, made = connect
    factory(scripted())

    portal.PortalSession(restore_token="from-last-time").open()
    select = next(m for m in made[0].sent if m.member == "SelectSources")
    assert select.body[1]["restore_token"] == ("s", "from-last-time")


def test_no_stored_token_means_the_option_is_absent(connect) -> None:
    factory, made = connect
    factory(scripted())

    portal.PortalSession().open()
    select = next(m for m in made[0].sent if m.member == "SelectSources")
    assert "restore_token" not in select.body[1]


def test_only_window_sources_are_requested(connect) -> None:
    """The mode is window capture. Asking for monitors too would let a user share their screen
    while believing they had shared one window."""
    from app.services.capture.probe import SOURCE_WINDOW

    factory, made = connect
    factory(scripted())

    portal.PortalSession().open()
    select = next(m for m in made[0].sent if m.member == "SelectSources")
    assert select.body[1]["types"] == ("u", SOURCE_WINDOW)


def test_consent_persistence_is_requested(connect) -> None:
    factory, made = connect
    factory(scripted())

    portal.PortalSession().open()
    select = next(m for m in made[0].sent if m.member == "SelectSources")
    assert select.body[1]["persist_mode"] == ("u", portal.PERSIST_EXPLICIT)


# -- request paths ------------------------------------------------------------------------------


def test_the_request_path_is_derived_from_the_bus_name() -> None:
    """Predicting it is what lets the rule be installed before the call."""
    assert (
        portal._request_path(":1.42", "tok") == "/org/freedesktop/portal/desktop/request/1_42/tok"
    )


def test_tokens_are_unique() -> None:
    assert portal._token() != portal._token()
