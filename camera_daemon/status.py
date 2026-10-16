"""The controller's STATUS reply, shared by every service that reads one.

STATUS answers with every measured value in the chassis at once, so services
read it through one reader rather than each asking the controller separately.
"""

import time
from typing import Any, Dict

# Long enough that reading all of a daemon's keywords costs one command, short
# enough that nothing STATUS reports can move measurably within it
TTL_S = 0.5


class StatusReader:
    """One STATUS snapshot, refreshed on demand."""

    def __init__(self, host: Any) -> None:
        self.host = host
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
        """Return one STATUS field, failing if the controller omits it."""
        value = self.fields().get(key)
        if value is None:
            raise RuntimeError(f"{key} not in the controller status")
        return cast(value)

    def fields(self) -> Dict[str, str]:
        """Return the snapshot, reading a fresh one once it has expired."""
        now = time.monotonic()
        if self._fields and now - self._read_at < TTL_S:
            return self._fields

        if self.camera is None:
            raise RuntimeError("no camera; measured values need a controller")
        reply = self.camera.native("STATUS")
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
