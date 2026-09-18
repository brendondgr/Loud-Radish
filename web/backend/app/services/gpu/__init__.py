"""The GPU as a shared resource between the speech model and a local language model (D-072).

* ``turns`` — who may be running on it now, and how long the speech model waits for its turn
"""

from .turns import GpuTurns, is_loopback, shared_turns

__all__ = ["GpuTurns", "is_loopback", "shared_turns"]
