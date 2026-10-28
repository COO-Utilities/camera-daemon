"""Archon frame-buffer keywords from FRAME: which buffer is active, and each buffer's frame."""

from typing import Any

from .status import frame_reader_for

# The Archon has three frame buffers, BUF1 to BUF3
BUFFERS = (1, 2, 3)

# FRAME suffix, keyword suffix, description
BUFFER_FIELDS = (("FRAME", "frame", "Frame number stored"),
                 ("SAMPLE", "sample", "Samples taken so far"),
                 ("WIDTH", "width", "Frame width in pixels"),
                 ("HEIGHT", "height", "Frame height in pixels"),
                 ("PIXELS", "pixels", "Pixels written so far"),
                 ("LINES", "lines", "Lines written so far"))


class FrameService:  # pylint: disable=too-few-public-methods
    """Frame-buffer keywords for the controller of a host daemon.

    Reads ``camera`` from its host. Everything comes from one FRAME reply,
    shared through the host's frame reader.
    """

    def __init__(self, host: Any) -> None:
        self.host = host
        self.frame = frame_reader_for(host)

    def register_keywords(self, registry: Any) -> None:
        """Add the buffer-selection, timer and per-buffer keywords."""
        registry.int("framerbuf", getter=lambda: self.frame.value("RBUF", int),
                     description="Buffer being read out, 1 to 3, or 0 when idle.")
        registry.int("framewbuf", getter=lambda: self.frame.value("WBUF", int),
                     description="Buffer being written, 1 to 3, or 0 when idle.")
        registry.int("frametimer", getter=lambda: self.frame.value("TIMER", _hex),
                     description="Controller timer in 10 ns ticks; flat means it has stopped.")
        for buf in BUFFERS:
            registry.bool(f"buf{buf}complete",
                          getter=lambda b=buf: self.frame.value(f"BUF{b}COMPLETE", int) == 1,
                          description=f"Buffer {buf} holds a complete frame.")
            for suffix, name, description in BUFFER_FIELDS:
                registry.int(f"buf{buf}{name}",
                             getter=lambda b=buf, s=suffix: self.frame.value(f"BUF{b}{s}", int),
                             description=f"{description}, buffer {buf}.")


def _hex(value: str) -> int:
    return int(value, 16)
