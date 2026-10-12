"""Bias voltage keywords, one set per channel the host declares.

Channel numbers carry no meaning for an operator while the ACF labels do, so
the host names each channel it wants and this registers keywords under that
name. Undeclared channels get none, which keeps the namespace to what matters.
"""

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# "MOD3 chan 2 HVLC V=24.0 ORDER=1 ENABLE=1 IL=20.0 LABEL="Output Drain""
_FIELD = re.compile(r'(\w+)=("[^"]*"|\S+)')

# One reply carries every field of a channel, so hold it long enough that
# polling the keywords of one channel costs one command rather than five
CACHE_TTL_S = 0.5


@dataclass(frozen=True)
class BiasChannel:
    """One declared bias channel and the keyword prefix it answers to."""

    module: int
    channel: int
    keyword: str

    # A rail that damages hardware beyond a value is held here rather than left
    # to whoever writes the keyword
    minvolts: Optional[float] = None
    maxvolts: Optional[float] = None


class BiasService:
    """Bias keywords for the channels a host daemon declares.

    Reads ``get_config``, ``logger`` and ``camera`` from its host, the same way
    the camera service does.
    """

    def __init__(self, host: Any) -> None:
        self.host = host
        self.channels: List[BiasChannel] = []
        self._cache: Dict[Tuple[int, int], Tuple[float, Dict[str, str]]] = {}

    @property
    def camera(self) -> Any:
        """Return the camera, which is owned by the host daemon."""
        return self.host.camera

    def load_channels(self) -> None:
        """Read the declared channels from config."""
        declared = self.host.get_config("biases", []) or []
        self.channels = [BiasChannel(module=int(entry["module"]),
                                     channel=int(entry["channel"]),
                                     keyword=str(entry["keyword"]),
                                     minvolts=_optional_float(entry.get("minvolts")),
                                     maxvolts=_optional_float(entry.get("maxvolts")))
                         for entry in declared]

    def register_keywords(self, registry: Any) -> None:
        """Add one keyword set per declared channel."""
        self.load_channels()
        for channel in self.channels:
            self._register_channel(registry, channel)

    def _register_channel(self, registry: Any, channel: BiasChannel) -> None:
        name = channel.keyword
        registry.float(name,
                       getter=lambda c=channel: self._field(c, "V", float),
                       setter=lambda value, c=channel: self._write(c, "V", float(value)),
                       validator=lambda value, c=channel: _check_volts(c, value),
                       units="V",
                       description=f"Bias voltage, module {c_label(channel)}."
                                   + _limit_text(channel))
        registry.int(f"{name}order",
                     getter=lambda c=channel: self._field(c, "ORDER", int),
                     setter=lambda value, c=channel: self._write(c, "ORDER", int(value)),
                     description=f"Power-on order, module {c_label(channel)}.")
        registry.string(f"{name}label",
                        getter=lambda c=channel: self._field(c, "LABEL", str),
                        description="Label this channel carries in the ACF.")
        # Nullable because which of these a channel has depends on its bank,
        # which is not known until the controller is read
        registry.bool(f"{name}enable",
                      getter=lambda c=channel: self._optional(c, "ENABLE", lambda v: bool(int(v))),
                      setter=lambda value, c=channel: self._write(c, "ENABLE", int(bool(value))),
                      nullable=True,
                      description="Channel is enabled, null where its bank has no switch.")
        registry.float(f"{name}ilim",
                       getter=lambda c=channel: self._optional(c, "IL", float),
                       setter=lambda value, c=channel: self._write(c, "IL", float(value)),
                       units="mA",
                       nullable=True,
                       description="Current limit, null where its bank has none.")

    def verify(self) -> None:
        """Warn about any declared channel the controller does not report."""
        if self.camera is None:
            return
        for channel in self.channels:
            try:
                self._read(channel)
            except (RuntimeError, OSError) as exc:
                self.host.logger.error("Bias %s (module %s) unreadable: %s",
                                       channel.keyword, c_label(channel), exc)

    def _read(self, channel: BiasChannel) -> Dict[str, str]:
        if self.camera is None:
            raise RuntimeError("no camera; bias keywords need a controller")

        key = (channel.module, channel.channel)
        cached = self._cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < CACHE_TTL_S:
            return cached[1]

        reply = self.camera.bias(f"{channel.module} {channel.channel}")
        fields = {name: value.strip('"') for name, value in _FIELD.findall(reply)}
        self._cache[key] = (time.monotonic(), fields)
        return fields

    def _field(self, channel: BiasChannel, field: str, cast: Any) -> Any:
        """Return one field of a channel, reading every field in one command."""
        value: Optional[str] = self._read(channel).get(field)
        if value is None:
            raise RuntimeError(f"{channel.keyword} reported no {field}")
        return cast(value)

    def _write(self, channel: BiasChannel, field: str, value: Any) -> None:
        """Set one field of a channel and apply it to that module alone.

        The controller applies per module, so a bias change never disturbs
        another board or the detector's power.
        """
        if self.camera is None:
            raise RuntimeError("no camera; bias keywords need a controller")
        self.camera.bias(f"{channel.module} {channel.channel} {field} {value}")
        self._cache.pop((channel.module, channel.channel), None)

    def _optional(self, channel: BiasChannel, field: str, cast: Any) -> Any:
        """Return a field this channel's bank may not carry, else None."""
        value: Optional[str] = self._read(channel).get(field)
        return None if value is None else cast(value)


def _optional_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _limit_text(channel: BiasChannel) -> str:
    """Render a channel's limits for its description, empty when it has none."""
    bounds = []
    if channel.minvolts is not None:
        bounds.append(f"at least {channel.minvolts} V")
    if channel.maxvolts is not None:
        bounds.append(f"at most {channel.maxvolts} V")
    return f" Must be {' and '.join(bounds)}." if bounds else ""


def _check_volts(channel: BiasChannel, value: Any) -> Optional[str]:
    """Return why a voltage is out of bounds, or None when it is allowed."""
    try:
        volts = float(value)
    except (TypeError, ValueError):
        return f"{channel.keyword} must be a number"
    if channel.minvolts is not None and volts < channel.minvolts:
        return f"{channel.keyword} must be at least {channel.minvolts} V, got {volts}"
    if channel.maxvolts is not None and volts > channel.maxvolts:
        return f"{channel.keyword} must be at most {channel.maxvolts} V, got {volts}"
    return None


def c_label(channel: BiasChannel) -> str:
    """Render a channel as "9 chan 2" for a message."""
    return f"{channel.module} chan {channel.channel}"
