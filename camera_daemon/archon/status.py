"""The controller's STATUS and FRAME replies, shared by every service that reads one.

Each answers with all of its values at once, so services read it through one
reader rather than each asking the controller separately.
"""

import time
from typing import Any, Dict

# Long enough that reading all of a daemon's keywords costs one command, short
# enough that nothing STATUS reports can move measurably within it
TTL_S = 0.5

# A module write ends in an APPLYMOD, up to 3 s on LRIS2, past the RPC default
APPLY_TIMEOUT_S = 30.0


class StatusReader:
    """One snapshot of a key=value reply, STATUS by default, refreshed on demand."""

    def __init__(self, host: Any, command: str = "STATUS") -> None:
        self.host = host
        self.command = command
        self._fields: Dict[str, str] = {}
        self._read_at = 0.0

    @property
    def camera(self) -> Any:
        """Return the camera, which is owned by the host daemon."""
        return self.host.camera

    def invalidate(self) -> None:
        """Drop the snapshot so the next read reaches the controller.

        A write is the one case where the snapshot could answer with a value
        from before the change, so every setter calls this.
        """
        self._fields = {}

    def value(self, key: str, cast: Any) -> Any:
        """Return one field, failing if the controller omits it."""
        value = self.fields().get(key)
        if value is None:
            raise RuntimeError(f"{key} not in the controller's {self.command} reply")
        return cast(value)

    def fields(self) -> Dict[str, str]:
        """Return the snapshot, reading a fresh one once it has expired."""
        now = time.monotonic()
        if self._fields and now - self._read_at < TTL_S:
            return self._fields

        if self.camera is None:
            raise RuntimeError("no camera; measured values need a controller")
        reply = self.camera.native(self.command)
        self._fields = dict(field.split("=", 1)
                            for field in reply.split() if "=" in field)
        self._read_at = now
        return self._fields


def reader_for(host: Any) -> StatusReader:
    """Return the host's reader, creating it on first use.

    Attached to the host rather than passed in, so that services sharing one
    snapshot is a property of the code instead of something each instrument's
    daemon has to wire up correctly.
    """
    reader = getattr(host, "status_reader", None)
    if reader is None:
        reader = StatusReader(host)
        host.status_reader = reader
    return reader


def frame_reader_for(host: Any) -> StatusReader:
    """Return the host's FRAME reader, creating it on first use."""
    reader = getattr(host, "frame_reader", None)
    if reader is None:
        reader = StatusReader(host, "FRAME")
        host.frame_reader = reader
    return reader
