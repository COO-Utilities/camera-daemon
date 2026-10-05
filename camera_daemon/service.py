"""Generic camera keywords, state machine and exposure worker.

Composed into a project's own libby daemon, which supplies the transport and
loads the instrument object. Anything instrument-specific lives in that object.
"""

import threading
import time
from dataclasses import dataclass
from typing import Any, List, Optional

from pycamerad import Camerad, ModuleNotAvailable

# initialize talks to the controller, so it needs longer than an RPC default
INITIALIZE_TIMEOUT_S = 300.0

# The writer is asynchronous, so the path appears shortly after the run ends
LASTFRAME_WAIT_S = 5.0
LASTFRAME_POLL_S = 0.05

STATE_NOCAMERA = "nocamera"
STATE_IDLE = "idle"
STATE_EXPOSING = "exposing"
STATE_ABORTING = "aborting"
STATE_ERROR = "error"


@dataclass
class _Run:
    """What the current or most recent exposure run is doing."""

    state: str = STATE_NOCAMERA
    worker: Optional[threading.Thread] = None
    lastframe: str = ""
    aborting: bool = False


class CameraService:
    """The camera keywords every camerad camera has, for any libby daemon.

    Reads ``get_config``, ``logger``, ``keyword_registry`` and ``instrument``
    from its host daemon, and assigns the camera it builds to ``host.camera``.
    """

    def __init__(self, host: Any) -> None:
        self.host = host
        self.config_file: Optional[str] = None
        self.nframes = 1
        self._isopen = False
        self._command_lock = threading.Lock()
        self._run = _Run()

    @property
    def camera(self) -> Optional[Camerad]:
        """Return the camera, which is owned by the host daemon."""
        return self.host.camera

    ### lifecycle

    def register_keywords(self, registry: Any) -> None:
        """Add the generic camera keywords to the given registry."""
        registry.string("state",
                        getter=lambda: self._run.state,
                        description="nocamera, idle, exposing, aborting or error.")
        registry.bool("isconnected",
                      getter=lambda: self.camera is not None,
                      description="Camera object exists for this config.")
        registry.bool("isopen",
                      getter=lambda: self._isopen,
                      description="Controller connection has been opened.")
        registry.bool("ispowered",
                      getter=self._is_powered,
                      setter=self._set_powered,
                      description="Detector power is on.")
        registry.trigger("initialize",
                         action=self._initialize,
                         timeout_s=INITIALIZE_TIMEOUT_S,
                         description="Open, load firmware and power on.")
        registry.float("exptime",
                       getter=self._get_exptime,
                       setter=self._set_exptime,
                       units="s",
                       description="Exposure time.")
        registry.int("nframes",
                     getter=lambda: self.nframes,
                     setter=self._set_nframes,
                     validator=self._check_nframes,
                     description="Frame count used by expose.")
        registry.bool("datacube",
                      getter=self._get_datacube,
                      setter=self._set_datacube,
                      description="Write a frame sequence as one FITS cube.")
        registry.trigger("expose",
                         action=self._expose,
                         description="Start nframes exposures; watch state.")
        registry.trigger("abort",
                         action=self._abort,
                         description="Abort the running exposure.")
        registry.string("lastframe",
                        getter=lambda: self._run.lastframe,
                        description="Path of the last file written.")

    def connect(self) -> None:
        """Build the camera from config, logging failure rather than raising."""
        self.config_file = self.host.get_config("camera.config_file")
        self.nframes = int(self.host.get_config("camera.nframes", 1))

        if not self.config_file:
            self.host.logger.error("camera.config_file is not set")
            return

        try:
            camera_class = getattr(self.host.instrument, "camera_class", Camerad)
            self.host.camera = camera_class.from_config(self.config_file)
            self._run.state = STATE_IDLE
            self.host.logger.info("Camera built from %s", self.config_file)
        except (ModuleNotAvailable, RuntimeError, OSError) as exc:
            self.host.logger.error("Camera unavailable: %s", exc)

    def on_stop(self) -> None:
        """Abort anything running, then drop the connection."""
        if self.camera is None:
            return
        try:
            if self._run.state == STATE_EXPOSING:
                self.camera.abort()
            self.camera.close()
        except (RuntimeError, OSError) as exc:
            self.host.logger.error("Error releasing the camera: %s", exc)

    ### keyword implementations

    def _require_camera(self) -> Camerad:
        if self.camera is None:
            raise RuntimeError("no camera; check camera.config_file and the installed module")
        return self.camera

    def _is_powered(self) -> bool:
        return bool(self._require_camera().power())

    def _set_powered(self, value: bool) -> None:
        self._require_camera().power(bool(value))

    def _initialize(self) -> None:
        camera = self._require_camera()
        with self._command_lock:
            camera.initialize()
            self._isopen = True
            self._run.state = STATE_IDLE

    def _get_exptime(self) -> float:
        return float(self._require_camera().exptime())

    def _set_exptime(self, value: float) -> None:
        self._require_camera().exptime(float(value))

    def _check_nframes(self, value: Any) -> Optional[str]:
        try:
            if int(value) < 1:
                return "nframes must be at least 1"
        except (TypeError, ValueError):
            return "nframes must be an integer"
        return None

    def _set_nframes(self, value: int) -> None:
        self.nframes = int(value)

    def _get_datacube(self) -> bool:
        return bool(self._require_camera().datacube())

    def _set_datacube(self, value: bool) -> None:
        self._require_camera().datacube(bool(value))

    def _expose(self) -> None:
        """Start a counted run and return; progress shows in state."""
        self._require_camera()
        with self._command_lock:
            if self._run.worker is not None and self._run.worker.is_alive():
                raise RuntimeError(f"already {self._run.state}; abort first")
            self._run.aborting = False
            self._run.state = STATE_EXPOSING
            self._run.worker = threading.Thread(target=self._run_exposure,
                                                args=(self.nframes,),
                                                name="expose", daemon=True)
            self._run.worker.start()

    def _run_exposure(self, count: int) -> None:
        try:
            self.camera.expose(count)
        except RuntimeError as exc:
            # An aborted run fails too, but on request, so it is not an error
            if self._run.aborting:
                self.host.logger.info("Exposure aborted")
                self._run.state = STATE_IDLE
                return
            self.host.logger.error("Exposure failed: %s", exc)
            self._run.state = STATE_ERROR
            return
        self._run.lastframe = self._await_written(self._run.lastframe)
        self._run.state = STATE_IDLE

    def _await_written(self, previous: str) -> str:
        """Wait briefly for the writer to report a new path."""
        deadline = time.monotonic() + LASTFRAME_WAIT_S
        while time.monotonic() < deadline:
            current = self._written_path()
            if current and current != previous:
                return current
            time.sleep(LASTFRAME_POLL_S)
        return previous

    def _written_path(self) -> str:
        written: List[str] = [output.last_written
                              for output in self.camera.output_status()
                              if output.last_written]
        return written[-1] if written else ""

    def _abort(self) -> None:
        camera = self._require_camera()
        if self._run.state == STATE_EXPOSING:
            self._run.aborting = True
            self._run.state = STATE_ABORTING
        camera.abort()
