"""Cover the heaterX keywords against a stand-in controller, without hardware."""

# Test names state the case; a docstring would only restate them, and a test
# double is as small as the thing it stands in for
# pylint: disable=missing-function-docstring,too-few-public-methods

import logging

import pytest

from camera_daemon.heater import HeaterService

# A bare heater read answers with the enable state and the target together,
# and the controller pads its replies
HEATER_REPLY = "ON -110.0 "

STATUS_REPLY = ("VALID=1 POWER=2 MOD11/TEMP=27.687 MOD11/TEMPA=-202.5 "
                "MOD11/HEATERAOUTPUT=0.900 MOD11/HEATERAP=0 MOD11/HEATERAI=0 "
                "MOD11/HEATERAD=0")


class FakeRegistry:
    """Record what a keyword registry is asked to register."""

    def __init__(self):
        self.registered = {}

    def _record(self, name, **kwargs):
        self.registered[name] = kwargs

    bool = int = float = string = trigger = _record


class FakeCamera:
    """Answer heater, sensor and status reads, recording every write."""

    def __init__(self, heater_reply=HEATER_REPLY):
        self.heater_reply = heater_reply
        self.writes = []
        self.status_reads = 0

    def heater(self, args):
        """Read a heater, or record a write."""
        if len(args.split()) > 2:
            self.writes.append(args)
        return "A  " if args.endswith("INPUT") else self.heater_reply

    def sensor(self, args):
        """Read a sensor's excitation current, or record a write."""
        if len(args.split()) > 2:
            self.writes.append(args)
        return "10000.0"

    def native(self, command):
        """Answer STATUS, counting how often it is asked."""
        assert command == "STATUS"
        self.status_reads += 1
        return STATUS_REPLY


class FakeHost:
    """The three attributes the service reads, plus the camera."""

    def __init__(self, config, camera=None):
        self._config = config
        self.camera = camera
        self.logger = logging.getLogger("fakehost")

    def get_config(self, key, default=None):
        """Read a config value by name."""
        return self._config.get(key, default)


HEATER = {"module": 11, "channel": "A", "keyword": "detheater"}
SENSOR = {"module": 11, "channel": "A", "keyword": "dettemp"}


def _service(camera=None):
    host = FakeHost({"heaters": [HEATER], "sensors": [SENSOR]},
                    camera or FakeCamera())
    registry = FakeRegistry()
    HeaterService(host).register_keywords(registry)
    return host, registry


def test_registers_a_keyword_set_per_heater_and_sensor():
    _, registry = _service()
    assert set(registry.registered) == {
        "detheater", "detheaterenable", "detheaterinput", "detheaterilim",
        "detheateroutput", "detheaterp", "detheateri", "detheaterd",
        "dettemp", "dettempcurrent", "dettempfilter",
    }


def test_a_bare_read_gives_both_the_enable_state_and_the_target():
    _, registry = _service()
    assert registry.registered["detheater"]["getter"]() == -110.0
    assert registry.registered["detheaterenable"]["getter"]() is True


def test_a_disabled_heater_reads_as_off():
    _, registry = _service(FakeCamera("OFF 30.0 "))
    assert registry.registered["detheaterenable"]["getter"]() is False


def test_the_input_sensor_is_not_padded():
    _, registry = _service()
    assert registry.registered["detheaterinput"]["getter"]() == "A"


def test_live_values_come_from_status():
    _, registry = _service()
    assert registry.registered["detheateroutput"]["getter"]() == 0.9
    assert registry.registered["dettemp"]["getter"]() == -202.5


def test_polling_several_status_keywords_costs_one_command():
    host, registry = _service()
    for name in ("detheateroutput", "detheaterp", "detheateri", "dettemp"):
        registry.registered[name]["getter"]()
    assert host.camera.status_reads == 1


def test_a_status_key_the_controller_does_not_report_fails_clearly():
    camera = FakeCamera()
    camera.native = lambda command: "VALID=1 POWER=2"
    host = FakeHost({"heaters": [HEATER], "sensors": []}, camera)
    registry = FakeRegistry()
    HeaterService(host).register_keywords(registry)
    with pytest.raises(RuntimeError, match="MOD11/HEATERAOUTPUT"):
        registry.registered["detheateroutput"]["getter"]()


def test_writing_a_setpoint_and_an_enable_reaches_the_controller():
    host, registry = _service()
    registry.registered["detheater"]["setter"](-105.0)
    registry.registered["detheaterenable"]["setter"](False)
    assert host.camera.writes == ["11 A -105.0", "11 A off"]


def test_a_sensor_current_round_trips():
    host, registry = _service()
    assert registry.registered["dettempcurrent"]["getter"]() == 10000.0
    registry.registered["dettempcurrent"]["setter"](5000)
    assert host.camera.writes == ["11 A 5000"]


def test_a_write_drops_the_snapshot_so_the_next_read_is_not_stale():
    host, registry = _service()
    registry.registered["detheateroutput"]["getter"]()
    registry.registered["detheater"]["setter"](-105.0)
    registry.registered["detheateroutput"]["getter"]()
    assert host.camera.status_reads == 2


def test_a_missing_camera_is_reported_rather_than_crashing():
    host = FakeHost({"heaters": [HEATER], "sensors": []}, None)
    registry = FakeRegistry()
    HeaterService(host).register_keywords(registry)
    with pytest.raises(RuntimeError, match="no camera"):
        registry.registered["detheater"]["getter"]()
