#!/usr/bin/env python3
#
# SPDX-License-Identifier: MIT
#
# This file is formatted with Python Black

from dasbus.connection import SystemMessageBus
from dasbus.server.interface import dbus_signal, dbus_interface
from dasbus.server.property import emits_properties_changed
from dasbus.server.template import InterfaceTemplate
from dasbus.typing import Bool, Int32, UInt32, List, ObjPath, Str, Variant, Tuple

from pathlib import Path
from gi.repository import GLib

import attr
import argparse
import datetime
import logging
import sys
import os

import ratbag
from ratbag.hid import Key

logger = logging.getLogger("ratbagd")

PATH_PREFIX = "/org/freedesktop/ratbag1"
NAME_PREFIX = "org.freedesktop.ratbag1"

# Replacements in here: {console_log_level}, {log_level}, {log_file}
log_config = """
version: 1
formatters:
  simple:
    format: '%(levelname).1s|%(name)s: %(message)s'
handlers:
  console:
    class: logging.StreamHandler
    level: {console_log_level}
    formatter: simple
    stream: ext://sys.stdout
  file:
    class: logging.handlers.RotatingFileHandler
    formatter: simple
    level: {log_level}
    filename: {log_file}
    maxBytes: 4194304
    backupCount: 5
root:
    level: DEBUG
    handlers: [console, file]
"""


@attr.s
class LogLevels(object):
    console: int = attr.ib()
    file: int = attr.ib()

    @classmethod
    def from_args(cls, console: str, file: str):
        map = {
            "disabled": logging.NOTSET,
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }
        return cls(map[console], map[file])


def init_logger(levels: LogLevels, logdir: Path) -> None:
    import yaml
    import logging.config

    logfile = logdir / "ratbagd.log"

    yml = yaml.safe_load(
        log_config.format(
            console_log_level=levels.console, log_level=levels.file, log_file=logfile
        )
    )
    logging.config.dictConfig(yml)


def make_name(name: str) -> str:
    """
    Creates the interface name based on the suffix given
    """
    assert name in (
        "Manager",
        "Device",
        "Profile",
        "Resolution",
        "Led",
        "Button",
        "ValueError",
    )
    return f"{NAME_PREFIX}.{name}"


def make_path(*args) -> str:
    """
    Creates an object path based on the args given
    """
    items = args
    return f"{PATH_PREFIX}/{'/'.join([str(i) for i in items])}"


@dbus_interface('org.freedesktop.ratbag1.Resolution')
class RatbagResolution(InterfaceTemplate):
    def __init__(self, bus, ratbag_resolution):
        super().__init__(self)
        self._resolution = ratbag_resolution
        self._bus = bus
        self.objpath = make_path(
            "device",
            ratbag_resolution.profile.device.path.name,
            "p",
            ratbag_resolution.profile.index,
            "r",
            ratbag_resolution.index,
        )
        bus.publish_object(self.objpath, self)

    @property
    def Index(self) -> UInt32:
        return self._resolution.index

    @property
    def IsActive(self) -> Bool:
        return self._resolution.active

    @property
    def IsDefault(self) -> Bool:
        return self._resolution.default

    @property
    def Resolutions(self) -> List[UInt32]:
        return list(self._resolution.dpi_list)

    @property
    def Resolution(self) -> Variant:
        if ratbag.Resolution.Capability.SEPARATE_XY_RESOLUTION in self._resolution.capabilities:
            return Variant("(uu)", list(self._resolution.dpi))
        else:
            return Variant("u", self._resolution.dpi[0])

    @Resolution.setter
    @emits_properties_changed
    def Resolution(self, res: Variant):
        # TODO: better way to ask for a type or signature?
        size = 1
        try:
            size = len(res)
        except Exception:
            pass

        if size == 1:  # u
            x = res.get_uint32()
            y = x
        elif size == 2:  # (uu)
            x, y = res
        else:
            raise Exception(make_name("ValueError"), "Resolution must be (uu)")
        self._resolution.set_dpi((x, y))
        self.report_changed_property('Resolution')

    @emits_properties_changed
    def SetActive(self) -> UInt32:
        self._resolution.set_active()
        self.report_changed_property('IsActive')
        return 0

    @emits_properties_changed
    def SetDefault(self) -> UInt32:
        self._resolution.set_default()
        self.report_changed_property('IsDefault')
        return 0


@dbus_interface('org.freedesktop.ratbag1.Led')
class RatbagLed(InterfaceTemplate):
    def __init__(self, bus, ratbag_led):
        super().__init__(self)
        self._led = ratbag_led
        self._bus = bus
        self.objpath = make_path(
            "device",
            ratbag_led._profile.device.path.name,
            "p",
            ratbag_led._profile.index,
            "l",
            ratbag_led.index,
        )
        bus.publish_object(self.objpath, self)

    @property
    def Index(self) -> UInt32:
        return self._led.index

    @property
    def Mode(self) -> UInt32:
        return self._led.mode

    @Mode.setter
    @emits_properties_changed
    def Mode(self, mode: UInt32):
        modes = {
            0: ratbag.Led.Mode.OFF,
            1: ratbag.Led.Mode.ON,
            2: ratbag.Led.Mode.CYCLE,
            3: ratbag.Led.Mode.BREATHING,
        }
        self._led.set_mode(modes[mode])
        self.report_changed_property('Mode')

    @property
    def Modes(self) -> List[UInt32]:
        return list(self._led.modes)  # FIXME

    @property
    def Color(self) -> Tuple[UInt32, UInt32, UInt32]:
        return list(self._led.color)  # FIXME

    @Color.setter
    @emits_properties_changed
    def Color(self, color: Tuple[UInt32, UInt32, UInt32]):
        r, g, b = color
        self._led.set_color((r, g, b))
        self.report_changed_property('Color')

    @property
    def ColorDepth(self) -> UInt32:
        return self._led.colordepth  # FIXME

    @property
    def EffectDuration(self) -> UInt32:
        return self._led.effect_duration

    @EffectDuration.setter
    @emits_properties_changed
    def EffectDuration(self, duration: UInt32):
        self._led.set_effect_duration(duration)
        self.report_changed_property('EffectDuration')

    @property
    def Brightness(self) -> UInt32:
        return self._led.brightness

    @Brightness.setter
    @emits_properties_changed
    def Brightness(self, brightness: UInt32):
        self._led.set_brightness(brightness)
        self.report_changed_property('Brightness')


@dbus_interface('org.freedesktop.ratbag1.Button')
class RatbagButton(InterfaceTemplate):
    def __init__(self, bus, ratbag_button):
        super().__init__(self)
        self._button = ratbag_button
        self._bus = bus
        self.objpath = make_path(
            "device",
            ratbag_button._profile.device.path.name,
            "p",
            ratbag_button._profile.index,
            "b",
            ratbag_button.index,
        )
        bus.publish_object(self.objpath, self)

    @property
    def Index(self) -> UInt32:
        return self._button.index

    @property
    def Mapping(self) -> Tuple[UInt32, Variant]:
        action = self._button.action
        value = None

        if action.type == ratbag.Action.Type.BUTTON:
            value = Variant("u", int(action.button))
        elif action.type == ratbag.Action.Type.SPECIAL:
            value = Variant("u", int(action.special))
        elif action.type == ratbag.Action.Type.KEY:
            value = Variant("u", int(action.key.evdev))
        elif action.type == ratbag.Action.Type.MACRO:
            value = Variant(
                "a(uu)", [[e[0].value, e[1]] for e in action.events]
            )
        else:
            value = Variant("u", int(ratbag.Action.Type.UNKNOWN))

        assert value is not None
        return [action.type.value, value]

    @Mapping.setter
    def Mapping(self, mapping: Tuple[UInt32, Variant]):
        return
        type = mapping[0]
        variant = mapping[1]

        if type == int(ratbag.Action.Type.BUTTON):
            action = ratbag.ActionButton(self._button, variant.value)
        elif action.type == ratbag.Action.Type.SPECIAL:
            action = ratbag.ActionSpecial(
                self._button, ratbag.ActionSpecial.Special(variant.value)
            )
        if action.type == ratbag.Action.Type.KEY:
            action = ratbag.ActionKey(self._button, Key.from_evdev(variant.value))
        if action.type == ratbag.Action.Type.MACRO:
            events = [(ratbag.ActionMacro.Event(t), v) for t, v in variant.value]
            action = ratbag.ActionMacro(self._button, events=events)
        self._button.set_action(action)

    @property
    def ActionTypes(self) -> List[UInt32]:
        return [t.value for t in self._button.types]

    def Disable(self) -> UInt32:
        # FIXME
        return 0


@dbus_interface('org.freedesktop.ratbag1.Profile')
class RatbagProfile(InterfaceTemplate):
    def __init__(self, bus, ratbag_profile):
        super().__init__(self)
        self._profile = ratbag_profile
        self._bus = bus
        self.objpath = make_path(
            "device", ratbag_profile.device.path.name, "p", ratbag_profile.index
        )
        self._resolutions = [
            RatbagResolution(bus, r) for r in ratbag_profile.resolutions
        ]
        self._buttons = [RatbagButton(bus, r) for r in ratbag_profile.buttons]
        self._leds = [RatbagLed(bus, r) for r in ratbag_profile.leds]
        self._profile.connect('notify::active', self.cb_active)
        bus.publish_object(self.objpath, self)

    @property
    def Index(self) -> UInt32:
        return self._profile.index

    @property
    def Name(self) -> Str:
        return self._profile.name or f"Profile {self._profile.index}"

    @property
    def Capabilities(self) -> List[UInt32]:
        mapping = {
            ratbag.Profile.Capability.SET_DEFAULT: 101,
            ratbag.Profile.Capability.DISABLE: 102,
            ratbag.Profile.Capability.WRITE_ONLY: 103,
            ratbag.Profile.Capability.INDIVIDUAL_REPORT_RATE: 103,
        }
        return [mapping[c] for c in self._profile.capabilities]

    @property
    def Enabled(self) -> Bool:
        return self._profile.enabled

    @Enabled.setter
    def Enabled(self, enabled: Bool):
        self._profile.set_enabled(enabled)

    @property
    def ReportRates(self) -> List[UInt32]:
        return list(self._profile.report_rates)

    @property
    def ReportRate(self) -> UInt32:
        return self._profile.report_rate

    @ReportRate.setter
    @emits_properties_changed
    def ReportRate(self, rate: UInt32):
        self._profile.set_report_rate(rate)
        self.report_changed_property('ReportRate')

    @property
    def IsActive(self) -> Bool:
        return self._profile.active

    @property
    def IsDefault(self) -> Bool:
        return self._profile.default

    @property
    def Resolutions(self) -> List[ObjPath]:
        return [r.objpath for r in self._resolutions]

    @property
    def Buttons(self) -> List[ObjPath]:
        return [b.objpath for b in self._buttons]

    @property
    def Leds(self) -> List[ObjPath]:
        return [led.objpath for led in self._leds]

    def SetActive(self) -> UInt32:
        self._profile.set_active()
        return 0

    def SetDefault(self) -> UInt32:
        self._profile.set_default()
        return 0

    @emits_properties_changed
    def cb_active(self, *args):
        self.report_changed_property('IsActive')


@dbus_interface('org.freedesktop.ratbag1.Device')
class RatbagdDevice(InterfaceTemplate):
    def __init__(self, bus, ratbag_device):
        super().__init__(self)
        self._device = ratbag_device
        self._bus = bus
        self.objpath = make_path("device", ratbag_device.path.name)
        self._profiles = list(RatbagProfile(bus, p) for p in ratbag_device.profiles)
        bus.publish_object(self.objpath, self)

    @property
    def Name(self) -> Str:
        return self._device.name

    @property
    def Model(self) -> Str:
        return self._device.model

    @property
    def DeviceType(self) -> UInt32:
        return self._device.devicetype

    @property
    def Profiles(self) -> List[ObjPath]:
        return [p.objpath for p in self._profiles]

    def Commit(self) -> UInt32:
        logger.debug(f"Committing state to {self._device.name}")
        self._device.emit('commit', None)
        return 0

    @dbus_signal
    def Resync(self) -> None:
        logger.debug(f"Signal resync for {self._device.name}")


@dbus_interface('org.freedesktop.ratbag1.Manager')
class RatbagdManager(InterfaceTemplate):
    def __init__(self, bus, rb):
        super().__init__(self)
        self._devices: List[RatbagdDevice] = []
        self._ratbag = rb
        self._bus = bus
        self._ratbag.connect("device-added", self.cb_device_added)
        bus.publish_object(PATH_PREFIX, self)

    @property
    def APIVersion(self) -> Int32:
        return 2

    @property
    def Devices(self) -> List[ObjPath]:
        return [d.objpath for d in self._devices]

    @emits_properties_changed
    def cb_device_added(self, ratbagd, device):
        logger.info(f"exporting device {device.name}")
        self._devices.append(RatbagdDevice(self._bus, device))
        self.report_changed_property('Devices')


class Ratbagd(object):
    def __init__(self, rb: ratbag.Ratbag):
        self.ratbag = rb
        self.busname = None

    def init_dbus(self, busname=NAME_PREFIX):
        self.busname = busname
        self.bus = SystemMessageBus()
        logger.debug(f"Requesting bus name '{self.busname}'")
        self.bus.get_proxy(self.busname, PATH_PREFIX)
        self.manager = RatbagdManager(self.bus, self.ratbag)

    def start(self):
        self.ratbag.start()
        self.bus.register_service(NAME_PREFIX)


def init_logdir(path):
    xdg = path or os.getenv("XDG_STATE_HOME")
    if not xdg:
        if os.getuid() != 0:
            xdg = Path.home() / ".local" / "state"
        else:
            xdg = Path("/") / "var" / "log"
    basedir = Path(xdg) / "ratbagd"
    logdir = basedir / datetime.datetime.now().strftime("%y-%m-%d-%H%M%S")
    logdir.mkdir(exist_ok=True, parents=True)

    latest = basedir / "latest"
    if latest.is_symlink() or not latest.exists():
        latest.unlink(missing_ok=True)
        latest.symlink_to(logdir)

    return logdir


desc = """
This daemon needs sufficient privileges to access the devices and own the DBus
name. This usually means it needs to be run as root.

Log files and recordings of devices are stored in $XDG_STATE_HOME/ratbagd by
default (or /var/log/ratbagd if run as root). The recordings contain all
interactions of ratbagd with the device - this does not usually include
sensitive data.
"""


def main():
    parser = argparse.ArgumentParser(
        description="A ratbag DBus daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=desc,
    )
    parser.add_argument(
        "--disable-recordings",
        default=False,
        action="store_true",
        help="Disable device recordings",
    )
    parser.add_argument(
        "--logdir",
        type=Path,
        default=None,
        help="Directory to store log files and recordings in",
    )
    parser.add_argument(
        "--console-log-level",
        default="info",
        choices=["disabled", "debug", "info", "warning", "error", "critical"],
        help="Log level for stdout logging",
    )
    parser.add_argument(
        "--log-level",
        default="debug",
        choices=["disabled", "debug", "info", "warning", "error", "critical"],
        help="Log level for log file logging",
    )

    ns = parser.parse_args()
    logdir = init_logdir(ns.logdir)
    levels = LogLevels.from_args(ns.console_log_level, ns.log_level)
    init_logger(levels, logdir)
    kwargs = {}
    if not ns.disable_recordings:
        blackbox = ratbag.Blackbox.create(directory=logdir)
        kwargs["blackbox"] = blackbox
    rb = ratbag.Ratbag.create(**kwargs)
    ratbagd = Ratbagd(rb)
    try:
        ratbagd.init_dbus()
    except IOError as e:
        print(
            f"Failed to own bus name {ratbagd.busname}: {e}. Another ratbagd may be running. Exiting."
        )
        sys.exit(1)

    ratbagd.start()

    try:
        mainloop = GLib.MainLoop()
        mainloop.run()
    except KeyboardInterrupt:
        pass
