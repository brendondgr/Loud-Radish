"""Talking to the desktop the user is actually sitting in front of.

Three small things the application needs once a keystroke can start a dictation: put text on the
clipboard, put it into the window that has focus, and say when that did not work. None of them
existed anywhere in this repository before — `navigator.clipboard.writeText` in `selection.js` is
the only clipboard code and it lives inside a browser tab.

**Every one is a list of backends, tried in order, each probed before it is used.** Not one
hard-coded tool: which tool works differs per desktop, and a transcription tool that only pastes on
the developer's machine is not a feature. Nothing here raises — each call returns an
:class:`Outcome` saying what was tried and what happened, in the same spirit as
`companion/shortcuts.py::describe`, because a dictation that silently fails to arrive is
indistinguishable from one that never recorded.
"""

from __future__ import annotations

from .clipboard import copy, read_back
from .keystroke import paste
from .notification import notify
from .outcome import Outcome

__all__ = ["Outcome", "copy", "notify", "paste", "read_back"]
