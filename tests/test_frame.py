"""Cover the frame-buffer keywords against a stand-in controller, without hardware."""

# Test names state the case; a docstring would only restate them, and a test
# double is as small as the thing it stands in for
# pylint: disable=missing-function-docstring,too-few-public-methods

from fakes import FakeRegistry

from camera_daemon.archon.frame import BUFFERS, FrameService

FRAME_REPLY = ("TIMER=000000A1B2C3D4E5 RBUF=0 WBUF=2 "
               "BUF1SAMPLE=0 BUF1COMPLETE=1 BUF1FRAME=41 BUF1WIDTH=4096 BUF1HEIGHT=4112 "
               "BUF1PIXELS=16842752 BUF1LINES=4112 "
               "BUF2SAMPLE=7 BUF2COMPLETE=0 BUF2FRAME=42 BUF2WIDTH=4096 BUF2HEIGHT=4112 "
               "BUF2PIXELS=1024 BUF2LINES=1 "
               "BUF3SAMPLE=0 BUF3COMPLETE=0 BUF3FRAME=0 BUF3WIDTH=0 BUF3HEIGHT=0 "
               "BUF3PIXELS=0 BUF3LINES=0")


class CountingCamera:
    """Answer FRAME, counting how often it is asked."""

    def __init__(self):
        self.frame_reads = 0

    def native(self, command):
        assert command == "FRAME"
        self.frame_reads += 1
        return FRAME_REPLY


class BareHost:
    """A host with nothing but the camera the service needs."""

    def __init__(self, camera):
        self.camera = camera


def _registry():
    host = BareHost(CountingCamera())
    registry = FakeRegistry()
    FrameService(host).register_keywords(registry)
    return host, registry


def test_active_buffers_and_timer():
    _, registry = _registry()
    assert registry.read("framerbuf") == 0
    assert registry.read("framewbuf") == 2
    assert registry.read("frametimer") == 0xA1B2C3D4E5


def test_each_buffer_reports_its_frame():
    _, registry = _registry()
    assert registry.read("buf1complete") is True
    assert registry.read("buf2complete") is False
    assert registry.read("buf2frame") == 42
    assert registry.read("buf1pixels") == 16842752


def test_every_buffer_gets_the_same_keywords():
    _, registry = _registry()
    for buf in BUFFERS:
        for name in ("complete", "frame", "sample", "width", "height", "pixels", "lines"):
            assert f"buf{buf}{name}" in registry.registered


def test_reading_every_keyword_costs_one_frame_command():
    host, registry = _registry()
    for name in registry.registered:
        registry.read(name)
    assert host.camera.frame_reads == 1
