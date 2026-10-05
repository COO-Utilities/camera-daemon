# camera-daemon

The camera keywords every [camerad](https://github.com/CaltechOpticalObservatories/camera-interface)
camera has, as a service object any [libby](https://github.com/CaltechOpticalObservatories/libby)
daemon can compose.

One process owns one camera, holding its controller connection in process
through the `camera_interface` extension by way of
[pycamerad](https://github.com/COO-Utilities/pycamerad). There is no `camerad`
process in the loop.

## Why a service and not a daemon class

Each project has its own libby base that fixes the transport and broker, such
as `HispecDaemon` or `Lris2Daemon`, so a shared daemon class would have to pick
one. `CameraService` has no transport opinion and is composed instead.

## Use

```python
from camera_daemon import CameraService

class CameraDaemon(MyProjectDaemon):
    """Daemon for one camerad camera."""

    def __init__(self):
        super().__init__()
        self.camera = None
        self.instrument = None
        self.service = CameraService(self)

    def on_start(self, _libby):
        self.instrument = self._load_instrument()
        self.service.register_keywords(self.keyword_registry)
        if self.instrument is not None:
            self.instrument.register_keywords(self.keyword_registry)
        self.service.connect()

    def on_stop(self, _libby=None):
        self.service.on_stop()
```

The instrument is loaded by the project, because it resolves a dotted path into
the project's own namespace. It must be in place before `connect()`, which
reads `camera_class` from it.

## What the host supplies

The service reads four attributes from the daemon it is given and writes one:

| Attribute | Direction | Purpose |
|---|---|---|
| `get_config` | read | `camera.config_file` and `camera.nframes` |
| `logger` | read | hardware failures are logged, not raised, so the daemon still serves |
| `keyword_registry` | read | where keywords are registered |
| `instrument` | read | supplies `camera_class`; may be `None` |
| `camera` | write | the built camera, so instrument objects can reach it |

`camera` is assigned on the host rather than kept private so an instrument
object reads `self.daemon.camera`, needing no knowledge of the service.

## Keywords

| Keyword | Type | Access |
|---|---|---|
| `state` | string | R |
| `isconnected` | bool | R |
| `isopen` | bool | R |
| `ispowered` | bool | R/W |
| `initialize` | trigger | W |
| `exptime` | float, s | R/W |
| `nframes` | int | R/W |
| `datacube` | bool | R/W |
| `expose` | trigger | W |
| `abort` | trigger | W |
| `lastframe` | string | R |

`state` is one of `nocamera`, `idle`, `exposing`, `aborting` or `error`.
`expose` returns at once and runs on a worker thread, so a long run does not
block the daemon; watch `state` for progress.

Instrument-specific keywords, such as readout modes and subframe geometry, are
registered by the instrument object, not here.

## Behaviour worth knowing

A camera that is absent, unreachable or uninitialised does not stop the daemon:
`connect()` logs the failure and `state` stays `nocamera`, so keywords are still
served. Hardware initialisation happens only through the `initialize` trigger.

`initialize` gets a 300 second timeout, since it opens the controller, loads
firmware and powers the detector.
