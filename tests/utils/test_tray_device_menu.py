"""The microphone submenu, and the numbering that nesting breaks (D-050).

The menu was one level deep and a click was resolved as `items[id - 1]`. Nesting breaks that
**silently**: the arithmetic still produces an item, just the wrong one — so an off-by-one here
would switch to a microphone the user did not pick rather than raising. That is why the numbering
and the resolution go through the same function, and why most of this file is about ids.
"""

from __future__ import annotations

import pytest
from app.companion.animation import Snapshot
from app.companion.menu import build
from app.companion.tray import TrayIcon

DEVICES = [
    {
        "id": "samson-gomic-usb-audio-hw-3-0",
        "name": "Samson GoMic: USB Audio (hw:3,0)",
        "kind": "microphone",
    },
    {
        "id": "hd-audio-generic-alc294-analog-hw-2-0",
        "name": "HD-Audio Generic: ALC294",
        "kind": "microphone",
    },
    {"id": "spotify-spotify", "name": "Spotify/spotify", "kind": "loopback"},
    {"id": "a-file", "name": "A wav file", "kind": "file"},
]


@pytest.fixture
def idle() -> Snapshot:
    return Snapshot(reachable=True, mode="live", state="idle")


@pytest.fixture
def tray(idle):
    icon = TrayIcon(on_activate=lambda item_id: item_id, listening=lambda: True)
    icon.set_snapshot(idle)
    icon.set_devices(DEVICES, "samson-gomic-usb-audio-hw-3-0")
    return icon


def _find(items, item_id):
    for item in items:
        if item.id == item_id:
            return item
        found = _find(item.children, item_id)
        if found is not None:
            return found
    return None


# -- what the submenu holds ------------------------------------------------------------------


def test_the_inputs_appear_as_children(idle) -> None:
    devices = _find(build(idle, listening=True, devices=DEVICES, device_id="a"), "devices")

    assert [child.id for child in devices.children] == [
        "device:samson-gomic-usb-audio-hw-3-0",
        "device:hd-audio-generic-alc294-analog-hw-2-0",
        "device:spotify-spotify",
    ]


def test_a_file_source_is_not_offered_as_a_microphone(idle) -> None:
    """It is a source, not a device, and choosing it from a tray menu is not what anyone means."""
    devices = _find(build(idle, listening=True, devices=DEVICES, device_id="a"), "devices")

    assert not any(child.id.endswith("a-file") for child in devices.children)


def test_the_chosen_input_is_ticked_and_named_in_the_parent(idle) -> None:
    items = build(idle, listening=True, devices=DEVICES, device_id="spotify-spotify")
    devices = _find(items, "devices")

    assert "Spotify" in devices.label
    ticked = [child.id for child in devices.children if child.checked]
    assert ticked == ["device:spotify-spotify"]


def test_a_list_not_yet_read_is_not_the_same_as_an_empty_one(idle) -> None:
    """An unasked list must not claim there are no microphones."""
    unasked = _find(build(idle, listening=True, devices=None), "devices")
    empty = _find(build(idle, listening=True, devices=[]), "devices")

    assert unasked.label == "Microphone"
    assert empty.label == "No microphone found"
    assert not unasked.enabled and not empty.enabled


def test_the_picker_is_disabled_while_recording(idle) -> None:
    """Swapping the input mid-recording restarts the capture stage. A menu that silently
    interrupts a talk you are recording is not a convenience."""
    recording = Snapshot(reachable=True, mode="live", state="recording")

    devices = _find(build(recording, listening=True, devices=DEVICES, device_id="a"), "devices")

    assert not devices.enabled


def test_a_long_device_name_is_trimmed(idle) -> None:
    long = [{"id": "x", "name": "A" * 80, "kind": "microphone"}]

    devices = _find(build(idle, listening=True, devices=long, device_id="x"), "devices")

    assert len(devices.children[0].label) <= 44


# -- the numbering ----------------------------------------------------------------------------


def test_every_item_including_children_gets_a_distinct_id(tray) -> None:
    numbered = tray._numbered()

    ids = [number for number, _ in numbered]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert any(item.id.startswith("device:") for _n, item in numbered)


def test_a_child_click_resolves_to_that_child(tray) -> None:
    """**The bug this file exists for.** With the old positional arithmetic a child's id addressed
    a top-level row instead, so clicking the second microphone would have started a recording."""
    numbered = dict(tray._numbered())
    wanted = next(
        number
        for number, item in numbered.items()
        if item.id == "device:hd-audio-generic-alc294-analog-hw-2-0"
    )

    activated: list[str] = []
    tray._on_activate = activated.append
    tray._handle_event((wanted, "clicked", ("s", ""), 0))

    assert activated == ["device:hd-audio-generic-alc294-analog-hw-2-0"]


def test_a_top_level_click_still_resolves(tray) -> None:
    numbered = dict(tray._numbered())
    wanted = next(number for number, item in numbered.items() if item.id == "start-live")

    activated: list[str] = []
    tray._on_activate = activated.append
    tray._handle_event((wanted, "clicked", ("s", ""), 0))

    assert activated == ["start-live"]


def test_an_id_that_is_not_in_the_menu_does_nothing(tray) -> None:
    """Rather than resolving to whatever the arithmetic lands on."""
    activated: list[str] = []
    tray._on_activate = activated.append

    tray._handle_event((9999, "clicked", ("s", ""), 0))

    assert activated == []


# -- the D-Bus layout --------------------------------------------------------------------------


def test_the_layout_nests_the_devices_under_their_parent(tray) -> None:
    _signature, (_revision, root) = tray._layout()
    # Each row is ("(ia{sv}av)", (id, properties, children)) — the signature, then the triple.
    rows = [row[1] for row in root[2]]

    with_children = [row for row in rows if row[2]]

    assert len(with_children) == 1, "only the microphone row should have children"
    assert with_children[0][1]["children-display"] == ("s", "submenu")
    assert len(with_children[0][2]) == 3


def test_the_layout_ids_match_the_ones_events_are_resolved_by(tray) -> None:
    """Numbering and resolution must come from the same traversal, or a click lands elsewhere."""
    _signature, (_revision, root) = tray._layout()

    seen: list[int] = []

    def walk(rows) -> None:
        for row in rows:
            number, _props, kids = row[1]
            seen.append(number)
            walk(kids)

    walk(root[2])
    assert seen == [number for number, _item in tray._numbered()]


def test_choosing_a_device_signals_a_relayout_only_when_it_changed(tray) -> None:
    signals: list[str] = []
    tray._emit = lambda _path, _iface, signal, *_a: signals.append(signal)

    tray.set_devices(DEVICES, "samson-gomic-usb-audio-hw-3-0")
    assert signals == [], "an unchanged list must not make the host re-read the menu"

    tray.set_devices(DEVICES, "spotify-spotify")
    assert signals == ["LayoutUpdated"]
