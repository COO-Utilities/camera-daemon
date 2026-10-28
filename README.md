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
| `isopen` | bool | R/W |
| `ispowered` | bool | R/W |
| `initialize` | trigger | W |
| `loadfirmware` | trigger | W |
| `readacf` | trigger | W |
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

`initialize` and `ispowered` get a 300 second timeout, since they load firmware
or power the detector. Bias, heater and sensor writes get 30 seconds, since each
ends in an `APPLYMOD`.

The three steps are separable, which matters for a cryostat. Writing `isopen`
opens the link alone, enough to read module-specific keywords such as
temperatures with the detector unpowered. `loadfirmware` writes the ACF to the
controller without powering it, which a keyword write needs, since a write
patches a single configuration line and the controller has to hold the
configuration camerad parsed. `readacf` parses the ACF into the host without
writing the controller, for recovering after a restart when the controller is
already configured.

## Archon services

`camera_daemon.archon` holds the services that speak Archon commands, each
composed the same way as `CameraService`:

```python
from camera_daemon.archon import BiasService, ChassisService, FrameService, HeaterService
```

### Bias and heaterX

`BiasService` and `HeaterService` declare every channel in the host's config,
so no module number or label is assumed:

```yaml
biases:
  - {module: 9, channel: 4, keyword: hvxbiasoutputdraina}
  - {module: 4, channel: 28, keyword: lvxbiasccdplus5v, maxvolts: 5.0}
heaters:
  - {module: 11, channel: A, keyword: heaterxdetheater}
sensors:
  - {module: 11, channel: A, keyword: heaterxdettemp}
```

Each bias channel gets seven keywords, named after the columns of the archongui
bias table:

| keyword | GUI column | |
|---|---|---|
| `<name>` | Vset | commanded volts, writable |
| `<name>vmeas` | Vmeas | measured volts |
| `<name>iset` | Iset | current limit, writable |
| `<name>imeas` | Imeas | measured current |
| `<name>order` `<name>enable` `<name>label` | | configuration |

Which attributes a channel actually has depends on its bank, so `enable` and
`iset` are nullable and read null where the bank carries none. `minvolts` and
`maxvolts` are optional; a write past either is refused before it reaches the
controller.

The commanded keywords report what the board was told, which is not what it is
doing: a bias write applies that module alone and never the whole system, so it
does not disturb the detector's power, but the controller refuses the change
while the detector is unpowered and the commanded value moves anyway. The
measured keywords are what to trust, and what to plot.

Measured values come from one `STATUS` reply, shared by both services through
`StatusReader` and held for half a second, so reading every keyword of a daemon
costs the controller one command. Any write drops the snapshot, so a read after
a write never answers from before it.

### Chassis and frame buffers

`ChassisService` serves the chassis health from `STATUS`: `valid`,
`powergood`, `overheat`, `extclkpresent`, `powerstate`, `statuscount`,
`logcount`, `backplanetemp`, `fantach`, and `rail<name>v` / `rail<name>i` for
each of the twelve backplane rails. Module boards are declared, since which
slots are filled differs per chassis:

```yaml
chassis:
  modules: [1, 2, 3, 5, 6, 9, 10, 11, 12]   # mod<n>temp
  dinputs: [11, 12]                         # mod<n>dinput0 .. mod<n>dinput7
```

`FrameService` serves the frame buffers from `FRAME`: `framerbuf`,
`framewbuf`, `frametimer`, and for each of the three buffers
`buf<n>complete`, `buf<n>frame`, `buf<n>sample`, `buf<n>width`,
`buf<n>height`, `buf<n>pixels` and `buf<n>lines`. It has its own reader, held
for half a second like the `STATUS` one.

Neither needs the ACF loaded or the detector powered, only the link open.
