"""Cover the chassis keywords against a stand-in controller, without hardware."""

# Test names state the case; a docstring would only restate them, and a test
# double is as small as the thing it stands in for
# pylint: disable=missing-function-docstring,too-few-public-methods

import pytest

from fakes import FakeRegistry

from camera_daemon.archon.chassis import RAILS, ChassisService

# Values as the LRIS2 Red Archon reported them
STATUS_REPLY = ("VALID=1 COUNT=4285568 LOG=0 POWER=2 POWERGOOD=1 OVERHEAT=0 "
                "EXTCLKPRESENT=0 BACKPLANE_TEMP=34.250 FANTACH=3241 "
                + " ".join(f"{rail}_V=1.000 {rail}_I=0.250" for rail in RAILS)
                + " P5V_V=5.090 P5V_I=3.380 MOD11/TEMP=27.687 MOD11/DINPUTS=10101000")


class FakeCamera:
    """Answer STATUS."""

    def native(self, command):
        assert command == "STATUS"
        return STATUS_REPLY


class FakeHost:
    """The config and camera the service reads."""

    def __init__(self, config, camera=None):
        self._config = config
        self.camera = camera

    def get_config(self, key, default=None):
        return self._config.get(key, default)


def _registry(config=None):
    if config is None:
        config = {"chassis.modules": [11], "chassis.dinputs": [11]}
    host = FakeHost(config,
                    FakeCamera())
    registry = FakeRegistry()
    ChassisService(host).register_keywords(registry)
    return registry


def test_health_flags_read_as_bools():
    registry = _registry()
    assert registry.read("valid") is True
    assert registry.read("powergood") is True
    assert registry.read("overheat") is False


def test_counters_and_backplane_read_as_numbers():
    registry = _registry()
    assert registry.read("powerstate") == 2
    assert registry.read("statuscount") == 4285568
    assert registry.read("backplanetemp") == pytest.approx(34.25)
    assert registry.read("fantach") == pytest.approx(3241.0)


def test_every_rail_gets_a_voltage_and_current_keyword():
    registry = _registry()
    for rail in RAILS:
        assert f"rail{rail.lower()}v" in registry.registered
        assert f"rail{rail.lower()}i" in registry.registered
    assert registry.read("railp5vv") == pytest.approx(5.09)
    assert registry.registered["railp5vi"]["units"] == "A"


def test_only_declared_modules_get_keywords():
    registry = _registry()
    assert registry.read("mod11temp") == pytest.approx(27.687)
    assert "mod9temp" not in registry.registered


def test_dinput_bits_count_from_the_right():
    registry = _registry()
    assert [registry.read(f"mod11dinput{bit}") for bit in range(8)] == \
        [False, False, False, True, False, True, False, True]


def test_no_declarations_means_no_module_keywords():
    registry = _registry({})
    assert not [name for name in registry.registered if name.startswith("mod")]
    assert "valid" in registry.registered


def test_a_declared_module_the_controller_omits_names_itself():
    registry = _registry({"chassis.modules": [7]})
    with pytest.raises(RuntimeError, match="MOD7/TEMP"):
        registry.read("mod7temp")
