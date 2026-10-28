"""Archon chassis keywords from STATUS: health flags, backplane rails, module boards.

The twelve backplane rails are the same on every Archon, so they are fixed
here. Which slots hold a module, and which modules carry digital inputs, differ
per chassis, so the host declares those.
"""

from typing import Any, List

from .status import reader_for

# Backplane rails every Archon reports, as <RAIL>_V and <RAIL>_I
RAILS = ("P2V5", "P5V", "P6V", "N6V", "P17V", "N17V",
         "P35V", "N35V", "P100V", "N100V", "USER", "HEATER")

# DINPUTS is one bit per input, rightmost first
DINPUT_BITS = 8

# STATUS key, keyword, description
FLAGS = (("VALID", "valid", "The controller answered with a valid status."),
         ("POWERGOOD", "powergood", "Every backplane supply is within range."),
         ("OVERHEAT", "overheat", "The chassis is over temperature."),
         ("EXTCLKPRESENT", "extclkpresent", "An external clock is present."))
COUNTS = (("POWER", "powerstate",
           "Detector power state: 0 unknown, 1 not configured, 2 off, "
           "3 intermediate, 4 on, 5 standby."),
          ("COUNT", "statuscount",
           "Free-running status counter; flat means the controller has stopped."),
          ("LOG", "logcount", "Log entries waiting to be read."))


class ChassisService:
    """Chassis keywords for the controller of a host daemon.

    Reads ``get_config`` and ``camera`` from its host. Config, all optional:
    ``chassis.modules`` lists the slots that report a board temperature and
    ``chassis.dinputs`` the slots that report digital inputs.
    """

    def __init__(self, host: Any) -> None:
        self.host = host
        self.status = reader_for(host)
        self.modules: List[int] = []
        self.dinput_modules: List[int] = []

    def load_declarations(self) -> None:
        """Read the declared module slots from config."""
        self.modules = [int(slot) for slot in
                        self.host.get_config("chassis.modules", []) or []]
        self.dinput_modules = [int(slot) for slot in
                               self.host.get_config("chassis.dinputs", []) or []]

    def register_keywords(self, registry: Any) -> None:
        """Add the health, rail and module keywords."""
        self.load_declarations()
        for key, name, description in FLAGS:
            registry.bool(name, getter=lambda k=key: self._flag(k), description=description)
        for key, name, description in COUNTS:
            registry.int(name, getter=lambda k=key: self.status.value(k, int),
                         description=description)
        registry.float("backplanetemp",
                       getter=lambda: self.status.value("BACKPLANE_TEMP", float),
                       units="C", description="Backplane temperature.")
        registry.float("fantach", getter=lambda: self.status.value("FANTACH", float),
                       units="rpm", description="Chassis fan speed.")
        for rail in RAILS:
            name = f"rail{rail.lower()}"
            registry.float(f"{name}v", getter=lambda r=rail: self.status.value(f"{r}_V", float),
                           units="V", description=f"{rail} backplane rail voltage.")
            registry.float(f"{name}i", getter=lambda r=rail: self.status.value(f"{r}_I", float),
                           units="A", description=f"{rail} backplane rail current.")
        for slot in self.modules:
            registry.float(f"mod{slot}temp",
                           getter=lambda s=slot: self.status.value(f"MOD{s}/TEMP", float),
                           units="C", description=f"Board temperature, module {slot}.")
        for slot in self.dinput_modules:
            for bit in range(DINPUT_BITS):
                registry.bool(f"mod{slot}dinput{bit}",
                              getter=lambda s=slot, b=bit: self._dinput(s, b),
                              description=f"Digital input {bit}, module {slot}.")

    def _flag(self, key: str) -> bool:
        return self.status.value(key, int) == 1

    def _dinput(self, slot: int, bit: int) -> bool:
        bits = self.status.value(f"MOD{slot}/DINPUTS", str)
        if len(bits) <= bit:
            raise RuntimeError(f"MOD{slot}/DINPUTS has no bit {bit}: {bits}")
        return bits[-1 - bit] == "1"
