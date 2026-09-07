"""What happened when the desktop was asked to do something."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Outcome:
    """The result of one attempt at a desktop action.

    Carries ``attempts`` as well as the answer because "it did not work" is not actionable and
    "wl-copy is not installed, klipper is not on the bus" is.
    """

    ok: bool
    backend: str = ""
    detail: str = ""
    attempts: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok

    def describe(self) -> str:
        """One line for a log or a notification."""
        if self.ok:
            return f"{self.backend}: {self.detail}" if self.detail else self.backend
        if not self.attempts:
            return self.detail or "nothing was tried"
        return f"{self.detail or 'nothing worked'} (tried {', '.join(self.attempts)})"
