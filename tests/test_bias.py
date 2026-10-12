"""Cover the bias keywords against a stand-in controller, without hardware."""

# Test names state the case; a docstring would only restate them, and a test
# double is as small as the thing it stands in for
# pylint: disable=missing-function-docstring,too-few-public-methods

import logging

from camera_daemon.bias import BiasService

# What a bank with every attribute answers, and one with only a voltage
HVHC_REPLY = 'MOD4 chan 25 LVHC V=5.0 ORDER=0 ENABLE=1 IL=40.0 LABEL="CCD +5V"'
HVLC_REPLY = 'MOD9 chan 4 HVLC V=24.0 ORDER=1 LABEL="Output Drain a"'


class FakeRegistry:
    """Record what a keyword registry is asked to register."""

    def __init__(self):
        self.registered = {}

    def _record(self, name, **kwargs):
        self.registered[name] = kwargs

    bool = int = float = string = trigger = _record


class FakeCamera:
    """Answer bias reads from a canned reply and record every write."""

    def __init__(self, reply):
        self.reply = reply
        self.writes = []

    def bias(self, args):
        """Read a channel, or record a write and return the reply."""
        if len(args.split()) > 2:
            self.writes.append(args)
        return self.reply


class FakeHost:
    """The three attributes the service reads, plus the camera."""

    def __init__(self, config, camera=None):
        self._config = config
        self.camera = camera
        self.logger = logging.getLogger("fakehost")

    def get_config(self, key, default=None):
        """Read a config value by name."""
        return self._config.get(key, default)


def _service(entry, reply):
    host = FakeHost({"biases": [entry]}, FakeCamera(reply))
    registry = FakeRegistry()
    BiasService(host).register_keywords(registry)
    return host, registry


FULL_BANK = {"module": 4, "channel": 25, "keyword": "ccd5v"}
BARE_BANK = {"module": 9, "channel": 4, "keyword": "draina"}


def test_registers_one_keyword_set_per_channel():
    _, registry = _service(FULL_BANK, HVHC_REPLY)
    assert set(registry.registered) == {
        "ccd5v", "ccd5vorder", "ccd5vlabel", "ccd5venable", "ccd5vilim",
    }


def test_reads_every_field_from_one_reply():
    _, registry = _service(FULL_BANK, HVHC_REPLY)
    assert registry.registered["ccd5v"]["getter"]() == 5.0
    assert registry.registered["ccd5vorder"]["getter"]() == 0
    assert registry.registered["ccd5vlabel"]["getter"]() == "CCD +5V"
    assert registry.registered["ccd5venable"]["getter"]() is True
    assert registry.registered["ccd5vilim"]["getter"]() == 40.0


def test_a_bank_without_enable_or_ilim_reads_null_rather_than_failing():
    _, registry = _service(BARE_BANK, HVLC_REPLY)
    assert registry.registered["draina"]["getter"]() == 24.0
    assert registry.registered["drainaenable"]["getter"] () is None
    assert registry.registered["drainailim"]["getter"]() is None


def test_writing_sends_the_field_and_applies_that_module_only():
    host, registry = _service(FULL_BANK, HVHC_REPLY)
    registry.registered["ccd5v"]["setter"](4.5)
    registry.registered["ccd5venable"]["setter"](False)
    registry.registered["ccd5vilim"]["setter"](35.0)
    registry.registered["ccd5vorder"]["setter"](2)
    assert host.camera.writes == [
        "4 25 V 4.5", "4 25 ENABLE 0", "4 25 IL 35.0", "4 25 ORDER 2",
    ]


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
