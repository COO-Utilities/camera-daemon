"""HeaterX keywords, one set per heater and sensor the host declares.

A chassis can carry more than one heater module, so bare ``heaterA`` cannot
address them and ``heater11a`` tells an operator nothing. The host declares
each heater and sensor it wants, and keywords are registered under that name.

Setpoints are in degrees C while sensor readings are in K, as the firmware
reports them.
"""

from dataclasses import dataclass
from typing import Any, List

from .status import reader_for

ON_VALUES = ("on", "1", "true")


@dataclass(frozen=True)
class Channel:
    """One declared heater output or sensor input, and the name it answers to."""

    module: int
    channel: str
    keyword: str

    @property
    def status_prefix(self) -> str:
        """Return the STATUS key prefix for this channel's module."""
        return f"MOD{self.module}"

    @property
    def command_args(self) -> str:
        """Return the leading ``<module> <channel>`` both commands take."""
        return f"{self.module} {self.channel}"


class HeaterService:
    """HeaterX keywords for the heaters and sensors a host daemon declares.

    Reads ``get_config``, ``logger`` and ``camera`` from its host, the same way
    the camera service does.
    """

    def __init__(self, host: Any) -> None:
        self.host = host
        self.heaters: List[Channel] = []
        self.sensors: List[Channel] = []
        self.status = reader_for(host)

    @property
    def camera(self) -> Any:
        """Return the camera, which is owned by the host daemon."""
        return self.host.camera

    def load_declarations(self) -> None:
        """Read the declared heaters and sensors from config."""
        self.heaters = _channels(self.host.get_config("heaters", []))
        self.sensors = _channels(self.host.get_config("sensors", []))

    def register_keywords(self, registry: Any) -> None:
        """Add one keyword set per declared heater, and one per sensor."""
        self.load_declarations()
        for heater in self.heaters:
            self._register_heater(registry, heater)
        for sensor in self.sensors:
            self._register_sensor(registry, sensor)

    def verify(self) -> None:
        """Warn about any declared channel the controller does not report."""
        if self.camera is None:
            return
        for heater in self.heaters:
            self._check(heater, f"{heater.status_prefix}/HEATER{heater.channel}OUTPUT")
        for sensor in self.sensors:
            self._check(sensor, f"{sensor.status_prefix}/TEMP{sensor.channel}")

    def _check(self, channel: Channel, key: str) -> None:
        try:
            self.status.value(key, float)
        except (RuntimeError, OSError, ValueError) as exc:
            self.host.logger.error("Declared channel %s unreadable: %s",
                                   channel.keyword, exc)

    def _register_heater(self, registry: Any, heater: Channel) -> None:
        name = heater.keyword
        where = f"module {heater.module}{heater.channel}"
        registry.float(name,
                       getter=lambda h=heater: self._target(h),
                       setter=lambda value, h=heater: self._set_heater(h, str(float(value))),
                       units="C",
                       description=f"Heater setpoint, {where}.")
        registry.bool(f"{name}enable",
                      getter=lambda h=heater: self._enabled(h),
                      setter=lambda value, h=heater: self._set_enabled(h, value),
                      description=f"Closed loop is running, {where}.")
        registry.string(f"{name}input",
                        getter=lambda h=heater: self._heater(h, "INPUT"),
                        setter=lambda value, h=heater: self._set_heater(h, f"INPUT {value}"),
                        description="Sensor this heater controls on, A, B or C.")
        registry.float(f"{name}ilim",
                       getter=lambda h=heater: float(self._heater(h, "ILIM")),
                       setter=lambda value, h=heater: self._set_heater(h, f"ILIM {float(value)}"),
                       units="mA",
                       description="Heater current limit.")
        registry.float(f"{name}output",
                       getter=lambda h=heater: self._heater_status(h, "OUTPUT", float),
                       units="V",
                       description=f"Heater drive level, {where}.")
        for term in ("P", "I", "D"):
            registry.float(f"{name}{term.lower()}",
                           getter=lambda t=term, h=heater: self._heater_status(h, t, float),
                           description=f"Live {term} error term of the loop, {where}.")

    def _register_sensor(self, registry: Any, sensor: Channel) -> None:
        name = sensor.keyword
        registry.float(name,
                       getter=lambda s=sensor: self.status.value(
                           f"{s.status_prefix}/TEMP{s.channel}", float),
                       units="K",
                       description=f"Temperature, module {sensor.module}{sensor.channel}.")
        registry.float(f"{name}current",
                       getter=lambda s=sensor: float(self._sensor(s)),
                       setter=lambda value, s=sensor: self._set_sensor(s, str(int(value))),
                       units="nA",
                       description="RTD excitation current.")
        registry.int(f"{name}filter",
                     getter=lambda s=sensor: int(self._sensor(s, "AVG")),
                     setter=lambda value, s=sensor: self._set_sensor(s, f"AVG {int(value)}"),
                     description="Digital averaging count, a power of two up to 256.")

    def _require_camera(self) -> Any:
        if self.camera is None:
            raise RuntimeError("no camera; heater keywords need a controller")
        return self.camera

    def _heater(self, heater: Channel, args: str = "") -> str:
        # The controller pads its reply, which would reach a string keyword
        reply = self._require_camera().heater(f"{heater.command_args} {args}".strip())
        return reply.strip()

    def _sensor(self, sensor: Channel, args: str = "") -> str:
        return self._require_camera().sensor(f"{sensor.command_args} {args}".strip())

    def _target(self, heater: Channel) -> float:
        # A bare read answers with the enable state and the target together
        return float(self._heater(heater).split()[-1])

    def _enabled(self, heater: Channel) -> bool:
        return self._heater(heater).split()[0].lower() in ON_VALUES

    def _set_heater(self, heater: Channel, args: str) -> None:
        """Write a heater field, dropping the readings the change invalidates."""
        self._heater(heater, args)
        self.status.invalidate()

    def _set_sensor(self, sensor: Channel, args: str) -> None:
        """Write a sensor field, dropping the readings the change invalidates."""
        self._sensor(sensor, args)
        self.status.invalidate()

    def _set_enabled(self, heater: Channel, value: bool) -> None:
        self._set_heater(heater, "on" if value else "off")

    def _heater_status(self, heater: Channel, field: str, cast: Any) -> Any:
        return self.status.value(
            f"{heater.status_prefix}/HEATER{heater.channel}{field}", cast)


def _channels(declared: Any) -> List[Channel]:
    return [Channel(module=int(entry["module"]),
                    channel=str(entry["channel"]).upper(),
                    keyword=str(entry["keyword"]))
            for entry in declared or []]
