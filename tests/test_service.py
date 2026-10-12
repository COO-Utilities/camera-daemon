"""Cover the service's contract with its host daemon, without libby or hardware."""

# Test names state the case; a docstring would only restate them, and a test
# double is as small as the thing it stands in for
# pylint: disable=missing-function-docstring,too-few-public-methods

import logging

import pytest

from camera_daemon import CameraService, STATE_IDLE, STATE_NOCAMERA

GENERIC_KEYWORDS = {
    "state", "isconnected", "isopen", "ispowered", "initialize",
    "loadfirmware", "readacf",
    "exptime", "nframes", "datacube", "expose", "abort", "lastframe",
}


class FakeRegistry:
    """Record what a keyword registry is asked to register."""

    def __init__(self):
        self.registered = {}

    def _record(self, name, **kwargs):
        self.registered[name] = kwargs

    bool = int = float = string = trigger = _record


class FakeCamera:
    """Stand in for a Camerad, recording the config it was built from."""

    def __init__(self, config_file):
        self.config_file = config_file
        self.closed = False
        self.calls = []

    def open(self):
        """Record that the controller link was opened."""
        self.calls.append("open")

    def load(self, firmware=""):
        """Record a firmware load, which writes the controller."""
        self.calls.append(f"load {firmware}".strip())

    def read_acf(self, firmware=""):
        """Record a host-only ACF read, which writes nothing."""
        self.calls.append(f"read_acf {firmware}".strip())

    def power(self, on=""):
        """Record a power change."""
        self.calls.append(f"power {on}".strip())
        return "ON" if on == "on" else "OFF"

    @classmethod
    def from_config(cls, config_file):
        """Build from a camerad config path."""
        return cls(config_file)

    def close(self):
        """Record that the connection was released."""
        self.closed = True


class FakeInstrument:
    """Instrument object supplying a camera class, as a real one does."""

    camera_class = FakeCamera


class FakeHost:
    """The four attributes the service reads, plus the camera it writes."""

    def __init__(self, config=None, instrument=None):
        self._config = config or {}
        self.instrument = instrument
        self.camera = None
        self.logger = logging.getLogger("fakehost")
        self.keyword_registry = FakeRegistry()

    def get_config(self, key, default=None):
        """Read a config value by dotted key."""
        return self._config.get(key, default)


def test_registers_every_generic_keyword():
    host = FakeHost()
    CameraService(host).register_keywords(host.keyword_registry)
    assert set(host.keyword_registry.registered) == GENERIC_KEYWORDS


def test_connect_uses_the_instrument_camera_class():
    host = FakeHost({"camera.config_file": "/etc/cam.cfg"}, FakeInstrument())
    service = CameraService(host)
    service.connect()
    assert isinstance(host.camera, FakeCamera)
    assert host.camera.config_file == "/etc/cam.cfg"
    assert service.camera is host.camera


def test_missing_config_file_leaves_the_daemon_serving():
    host = FakeHost()
    service = CameraService(host)
    service.register_keywords(host.keyword_registry)
    service.connect()
    assert host.camera is None
    assert host.keyword_registry.registered["state"]["getter"]() == STATE_NOCAMERA


def test_state_is_idle_once_the_camera_is_built():
    host = FakeHost({"camera.config_file": "/etc/cam.cfg"}, FakeInstrument())
    service = CameraService(host)
    service.register_keywords(host.keyword_registry)
    service.connect()
    assert host.keyword_registry.registered["state"]["getter"]() == STATE_IDLE


def test_nframes_comes_from_config_and_rejects_nonsense():
    host = FakeHost({"camera.config_file": "/etc/cam.cfg", "camera.nframes": 7},
                    FakeInstrument())
    service = CameraService(host)
    service.register_keywords(host.keyword_registry)
    service.connect()
    nframes = host.keyword_registry.registered["nframes"]
    assert nframes["getter"]() == 7
    assert nframes["validator"](0) is not None
    assert nframes["validator"]("x") is not None
    assert nframes["validator"](3) is None


def test_keywords_fail_cleanly_with_no_camera():
    host = FakeHost()
    service = CameraService(host)
    service.register_keywords(host.keyword_registry)
    with pytest.raises(RuntimeError, match="no camera"):
        host.keyword_registry.registered["expose"]["action"]()


def test_on_stop_releases_a_built_camera():
    host = FakeHost({"camera.config_file": "/etc/cam.cfg"}, FakeInstrument())
    service = CameraService(host)
    service.connect()
    service.on_stop()
    assert host.camera.closed


def _service_with_camera():
    host = FakeHost({"camera.config_file": "/etc/cam.cfg"}, FakeInstrument())
    service = CameraService(host)
    service.register_keywords(host.keyword_registry)
    service.connect()
    return host, service


def test_opening_does_not_configure_or_power_the_controller():
    host, _ = _service_with_camera()
    host.keyword_registry.registered["isopen"]["setter"](True)
    assert host.camera.calls == ["open"]


def test_closing_releases_the_link():
    host, _ = _service_with_camera()
    host.keyword_registry.registered["isopen"]["setter"](True)
    host.keyword_registry.registered["isopen"]["setter"](False)
    assert host.camera.closed


def test_loadfirmware_writes_the_controller_without_powering_it():
    host, _ = _service_with_camera()
    host.keyword_registry.registered["loadfirmware"]["action"]()
    assert host.camera.calls == ["load"]


def test_readacf_writes_nothing_to_the_controller():
    host, _ = _service_with_camera()
    host.keyword_registry.registered["readacf"]["action"]()
    assert host.camera.calls == ["read_acf"]
