"""Cover the shared STATUS reader directly, without a service around it."""

# Test names state the case; a docstring would only restate them, and a test
# double is as small as the thing it stands in for
# pylint: disable=missing-function-docstring,too-few-public-methods

import pytest

from camera_daemon.archon.status import StatusReader, frame_reader_for, reader_for

STATUS_REPLY = "VALID=1 MOD4/LVHC_V1=4.998 MOD4/LVHC_I1=-15.588"


class CountingCamera:
    """Answer STATUS, counting how often it is asked."""

    def __init__(self):
        self.status_reads = 0

    def native(self, command):
        """Answer STATUS, counting how often it is asked."""
        assert command == "STATUS"
        self.status_reads += 1
        return STATUS_REPLY


class BareHost:
    """A host with nothing but the camera the reader needs."""

    def __init__(self, camera):
        self.camera = camera


def test_repeated_reads_within_the_ttl_cost_one_command():
    host = BareHost(CountingCamera())
    reader = reader_for(host)
    reader.value("MOD4/LVHC_V1", float)
    reader.value("MOD4/LVHC_I1", float)
    assert host.camera.status_reads == 1


def test_invalidating_sends_the_next_read_to_the_controller():
    host = BareHost(CountingCamera())
    reader = reader_for(host)
    reader.value("VALID", int)
    reader.invalidate()
    reader.value("VALID", int)
    assert host.camera.status_reads == 2


def test_one_reader_is_shared_by_everything_on_a_host():
    host = BareHost(CountingCamera())
    assert reader_for(host) is reader_for(host)


def test_a_key_the_controller_omits_names_itself_in_the_error():
    reader = StatusReader(BareHost(CountingCamera()))
    with pytest.raises(RuntimeError, match="MOD9/HVLC_V2"):
        reader.value("MOD9/HVLC_V2", float)


def test_a_missing_camera_is_reported_rather_than_crashing():
    reader = StatusReader(BareHost(None))
    with pytest.raises(RuntimeError, match="no camera"):
        reader.value("VALID", int)


def test_the_frame_reader_is_separate_from_the_status_reader():
    host = BareHost(CountingCamera())
    assert frame_reader_for(host) is frame_reader_for(host)
    assert frame_reader_for(host) is not reader_for(host)
    assert frame_reader_for(host).command == "FRAME"
