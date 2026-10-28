"""Cover the bias keywords against a stand-in controller, without hardware."""

# Test names state the case; a docstring would only restate them, and a test
# double is as small as the thing it stands in for
# pylint: disable=missing-function-docstring,too-few-public-methods

import logging

import pytest

from camera_daemon.archon.bias import BiasService
from camera_daemon.archon.heater import HeaterService

# What a bank with every attribute answers, and one with only a voltage
HVHC_REPLY = 'MOD4 chan 25 LVHC V=5.0 ORDER=0 ENABLE=1 IL=40.0 LABEL="CCD +5V"'
HVLC_REPLY = 'MOD9 chan 4 HVLC V=24.0 ORDER=1 LABEL="Output Drain a"'

# Channel 25 is the first of the LVHC bank, so it measures as index 1
FULL_BANK_STATUS = "VALID=1 MOD4/LVHC_V1=4.998 MOD4/LVHC_I1=-15.588"


class FakeRegistry:
    """Record what a keyword registry is asked to register."""

    def __init__(self):
        self.registered = {}

    def _record(self, name, **kwargs):
        self.registered[name] = kwargs

    bool = int = float = string = trigger = _record


class FakeCamera:
    """Answer bias and status reads from canned replies, recording writes."""

    def __init__(self, reply, status=""):
        self.reply = reply
        self.status = status
        self.writes = []
        self.status_reads = 0

    def bias(self, args):
        """Read a channel, or record a write and return the reply."""
        if len(args.split()) > 2:
            self.writes.append(args)
        return self.reply

    def native(self, command):
        """Answer STATUS, counting how often it is asked."""
        assert command == "STATUS"
        self.status_reads += 1
        return self.status


class FakeHost:
    """The three attributes the service reads, plus the camera."""

    def __init__(self, config, camera=None):
        self._config = config
        self.camera = camera
        self.logger = logging.getLogger("fakehost")

    def get_config(self, key, default=None):
        """Read a config value by name."""
        return self._config.get(key, default)


def _service(entry, reply, status=""):
    host = FakeHost({"biases": [entry]}, FakeCamera(reply, status))
    registry = FakeRegistry()
    BiasService(host).register_keywords(registry)
    return host, registry


FULL_BANK = {"module": 4, "channel": 25, "keyword": "ccd5v"}
BARE_BANK = {"module": 9, "channel": 4, "keyword": "draina"}


def test_registers_one_keyword_set_per_channel():
    _, registry = _service(FULL_BANK, HVHC_REPLY)
    assert set(registry.registered) == {
        "ccd5v", "ccd5vorder", "ccd5vlabel", "ccd5venable", "ccd5viset",
        "ccd5vvmeas", "ccd5vimeas",
    }


def test_reads_every_field_from_one_reply():
    _, registry = _service(FULL_BANK, HVHC_REPLY)
    assert registry.registered["ccd5v"]["getter"]() == 5.0
    assert registry.registered["ccd5vorder"]["getter"]() == 0
    assert registry.registered["ccd5vlabel"]["getter"]() == "CCD +5V"
    assert registry.registered["ccd5venable"]["getter"]() is True
    assert registry.registered["ccd5viset"]["getter"]() == 40.0


def test_a_bank_without_enable_or_iset_reads_null_rather_than_failing():
    _, registry = _service(BARE_BANK, HVLC_REPLY)
    assert registry.registered["draina"]["getter"]() == 24.0
    assert registry.registered["drainaenable"]["getter"] () is None
    assert registry.registered["drainaiset"]["getter"]() is None


def test_writing_sends_the_field_and_applies_that_module_only():
    host, registry = _service(FULL_BANK, HVHC_REPLY)
    registry.registered["ccd5v"]["setter"](4.5)
    registry.registered["ccd5venable"]["setter"](False)
    registry.registered["ccd5viset"]["setter"](35.0)
    registry.registered["ccd5vorder"]["setter"](2)
    assert host.camera.writes == [
        "4 25 V 4.5", "4 25 ENABLE 0", "4 25 IL 35.0", "4 25 ORDER 2",
    ]


def test_measured_values_come_from_status_not_from_the_commanded_reply():
    _, registry = _service(FULL_BANK, HVHC_REPLY, FULL_BANK_STATUS)
    assert registry.registered["ccd5v"]["getter"]() == 5.0
    assert registry.registered["ccd5vvmeas"]["getter"]() == 4.998
    assert registry.registered["ccd5vimeas"]["getter"]() == -15.588


# The index counts within the bank, so it parts company with the channel
# number on every bank that does not start at channel 1
@pytest.mark.parametrize("reply,channel,key", [
    ('MOD9 chan 26 HVHC V=29.0 ORDER=1 ENABLE=1 IL=27.0 LABEL="Drain"',
     26, "MOD9/HVHC_V2"),
    ('MOD9 chan 13 HVLC V=-1.0 ORDER=4 LABEL="LastGateA"', 13, "MOD9/HVLC_V13"),
    ('MOD9 chan 5 XVN V=-50.0 ORDER=0 ENABLE=0 LABEL="Backside"', 5, "MOD9/XVN_V1"),
])
def test_a_measurement_is_indexed_within_its_bank(reply, channel, key):
    entry = {"module": 9, "channel": channel, "keyword": "rail"}
    _, registry = _service(entry, reply, f"VALID=1 {key}=1.5")
    assert registry.registered["railvmeas"]["getter"]() == 1.5


def test_reading_both_measurements_costs_one_status_command():
    host, registry = _service(FULL_BANK, HVHC_REPLY, FULL_BANK_STATUS)
    registry.registered["ccd5vvmeas"]["getter"]()
    registry.registered["ccd5vimeas"]["getter"]()
    assert host.camera.status_reads == 1


def test_a_write_drops_the_snapshot_so_the_next_read_is_not_stale():
    host, registry = _service(FULL_BANK, HVHC_REPLY, FULL_BANK_STATUS)
    registry.registered["ccd5vvmeas"]["getter"]()
    registry.registered["ccd5v"]["setter"](4.5)
    registry.registered["ccd5vvmeas"]["getter"]()
    assert host.camera.status_reads == 2


def test_a_channel_whose_bank_status_omits_fails_clearly():
    _, registry = _service(FULL_BANK, HVHC_REPLY, "VALID=1")
    with pytest.raises(RuntimeError, match="MOD4/LVHC_V1"):
        registry.registered["ccd5vvmeas"]["getter"]()


def test_the_heater_service_on_the_same_host_shares_one_status_reply():
    camera = FakeCamera(HVHC_REPLY, FULL_BANK_STATUS + " MOD11/TEMPA=-202.5")
    host = FakeHost({"biases": [FULL_BANK],
                     "sensors": [{"module": 11, "channel": "A",
                                  "keyword": "dettemp"}]}, camera)
    registry = FakeRegistry()
    BiasService(host).register_keywords(registry)
    HeaterService(host).register_keywords(registry)

    for name in ("ccd5vvmeas", "ccd5vimeas", "dettemp"):
        registry.registered[name]["getter"]()
    assert camera.status_reads == 1


def test_a_voltage_limit_refuses_a_write_past_it():
    entry = dict(FULL_BANK, maxvolts=5.0)
    _, registry = _service(entry, HVHC_REPLY)
    validate = registry.registered["ccd5v"]["validator"]
    assert validate(4.9) is None
    assert "at most 5.0" in validate(5.5)


def test_a_lower_limit_refuses_a_write_below_it():
    entry = dict(FULL_BANK, keyword="ccdminus5v", minvolts=-5.0)
    _, registry = _service(entry, HVHC_REPLY)
    validate = registry.registered["ccdminus5v"]["validator"]
    assert validate(-4.9) is None
    assert "at least -5.0" in validate(-5.5)


def test_a_channel_without_limits_accepts_anything_numeric():
    _, registry = _service(BARE_BANK, HVLC_REPLY)
    assert registry.registered["draina"]["validator"](999.0) is None


def test_a_limit_is_named_in_the_description():
    entry = dict(FULL_BANK, maxvolts=5.0)
    _, registry = _service(entry, HVHC_REPLY)
    assert "at most 5.0 V" in registry.registered["ccd5v"]["description"]
