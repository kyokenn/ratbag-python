import collections
import enum
import libevdev
import logging
import struct
import time
import traceback

from typing import Any, Dict, List, Tuple

from gi.repository import GObject

import ratbag
import ratbag.hid
import ratbag.driver
import ratbag.util
from ratbag.parser import Spec, Parser

from ratbag.util import as_hex

logger = logging.getLogger(__name__)


def range_to_tuple(s: str) -> tuple:
    min_max, step = s.split('@')
    step = int(step)
    min_, max_ = tuple(map(int, min_max.split(':')))
    return tuple(range(min_, max_ + step, step))


PACKET_SIZE = 64

CMD_GET_PROFILE_DATA = (0x12, 0x00)
CMD_GET_LED_DATA = (0x12, 0x03)
CMD_GET_RESOLUTION_DATA = (0x12, 0x04)
CMD_GET_BUTTON_DATA = (0x12, 0x05)
CMD_GET_DISTANCE = (0x12, 0x06)
CMD_GET_BATTERY_DATA = (0x12, 0x07)

CMD_SET_PROFILE = (0x50, 0x02)
CMD_SAVE_PROFILE = (0x50, 0x03)

CMD_SET_BUTTON_ACTION = (0x51, 0x21)
CMD_SET_LED = (0x51, 0x28)
CMD_SET_RESOLUTION_DATA = (0x51, 0x31)
CMD_SET_DISTANCE = (0x51, 0x35)
CMD_SET_BATTERY_DATA = (0x51, 0x37)

QUIRK_DOUBLE_DPI = 'DOUBLE_DPI'
QUIRK_BATTERY_V2 = 'BATTERY_V2'
QUIRK_STRIX_PROFILE = 'STRIX_PROFILE'

# param for CMD_SET_RESOLUTION_DATA command
OFFSET_RATE = 0
OFFSET_RESPONSE = 1
OFFSET_SNAPPING = 2

ACTION_TYPE_KEYBOARD = 0
ACTION_TYPE_MOUSE = 1

BUTTON_TYPE = (
    ratbag.Action.Type.NONE,
    ratbag.Action.Type.BUTTON,
    ratbag.Action.Type.SPECIAL,
    ratbag.Action.Type.KEY,
    ratbag.Action.Type.MACRO,
)

# polling rate choice in Hz
POLLING_RATE = (
    125,
    250,
    500,
    1000,
)

# sleep timeout choice in minutes
SLEEP_TIMEOUT = (
    1,
    2,
    3,
    5,
    10,
    0,
)

# button response time (debounce) range in ms
RESPONSE = '4:32@4'

PROFILE = (
    'Profile 1 (DPI + Backward)',
    'Profile 2 (DPI + Forward)',
    'Profile 3 (DPI + Scroll)',
    'Profile 4',
    'Profile 5',
    'Profile 6',
)

LED_MODE = (
    ratbag.Led.Mode.ON,
    ratbag.Led.Mode.BREATHING,
    ratbag.Led.Mode.CYCLE,
    ratbag.Led.Mode.WAVE,
    ratbag.Led.Mode.REACTIVE,
    ratbag.Led.Mode.FLASHER,
    ratbag.Led.Mode.BATTERY,
)

# available actions, order is important!
ACTION = (
    (0xf0, (ratbag.ActionButton, 1)),  # LMB
    (0xf1, (ratbag.ActionButton, 2)),  # RMB
    (0xf2, (ratbag.ActionButton, 3)),  # MMB
    (0xe4, (ratbag.ActionButton, 4)),  # backward
    (0xe5, (ratbag.ActionButton, 5)),  # forward
    (0xe6, (ratbag.ActionSpecial, ratbag.ActionSpecial.Special.RESOLUTION_CYCLE_UP)),
    (0xe8, (ratbag.ActionSpecial, ratbag.ActionSpecial.Special.WHEEL_UP)),
    (0xe9, (ratbag.ActionSpecial, ratbag.ActionSpecial.Special.WHEEL_DOWN)),
    (0xff, (ratbag.ActionNone,)),
    (0xe1, (ratbag.ActionButton, 4)),  # backward on right side
    (0xe2, (ratbag.ActionButton, 5)),  # forward on right side
    (0xf0, (ratbag.ActionKey, 'KEY_PLAYPAUSE')),
    (0xf1, (ratbag.ActionKey, 'KEY_STOP')),
    (0xf2, (ratbag.ActionKey, 'KEY_PREVIOUSSONG')),
    (0xf3, (ratbag.ActionKey, 'KEY_NEXTSONG')),
    (0xf4, (ratbag.ActionKey, 'KEY_MUTE')),
    (0xf5, (ratbag.ActionKey, 'KEY_VOLUMEDOWN')),
    (0xf6, (ratbag.ActionKey, 'KEY_VOLUMEUP')),
    (4,    (ratbag.ActionKey, 'KEY_A')),
    (5,    (ratbag.ActionKey, 'KEY_B')),
    (6,    (ratbag.ActionKey, 'KEY_C')),
    (7,    (ratbag.ActionKey, 'KEY_D')),
    (8,    (ratbag.ActionKey, 'KEY_E')),
    (9,    (ratbag.ActionKey, 'KEY_F')),
    (10,   (ratbag.ActionKey, 'KEY_G')),
    (11,   (ratbag.ActionKey, 'KEY_H')),
    (12,   (ratbag.ActionKey, 'KEY_I')),
    (13,   (ratbag.ActionKey, 'KEY_J')),
    (14,   (ratbag.ActionKey, 'KEY_K')),
    (15,   (ratbag.ActionKey, 'KEY_L')),
    (16,   (ratbag.ActionKey, 'KEY_M')),
    (17,   (ratbag.ActionKey, 'KEY_N')),
    (18,   (ratbag.ActionKey, 'KEY_O')),
    (19,   (ratbag.ActionKey, 'KEY_P')),
    (20,   (ratbag.ActionKey, 'KEY_Q')),
    (21,   (ratbag.ActionKey, 'KEY_R')),
    (22,   (ratbag.ActionKey, 'KEY_S')),
    (23,   (ratbag.ActionKey, 'KEY_T')),
    (24,   (ratbag.ActionKey, 'KEY_U')),
    (25,   (ratbag.ActionKey, 'KEY_V')),
    (26,   (ratbag.ActionKey, 'KEY_W')),
    (27,   (ratbag.ActionKey, 'KEY_X')),
    (28,   (ratbag.ActionKey, 'KEY_Y')),
    (29,   (ratbag.ActionKey, 'KEY_Z')),
    (30,   (ratbag.ActionKey, 'KEY_1')),
    (31,   (ratbag.ActionKey, 'KEY_2')),
    (32,   (ratbag.ActionKey, 'KEY_3')),
    (33,   (ratbag.ActionKey, 'KEY_4')),
    (34,   (ratbag.ActionKey, 'KEY_5')),
    (35,   (ratbag.ActionKey, 'KEY_6')),
    (36,   (ratbag.ActionKey, 'KEY_7')),
    (37,   (ratbag.ActionKey, 'KEY_8')),
    (38,   (ratbag.ActionKey, 'KEY_9')),
    (39,   (ratbag.ActionKey, 'KEY_0')),
    (40,   (ratbag.ActionKey, 'KEY_ENTER')),
    (41,   (ratbag.ActionKey, 'KEY_ESC')),
    (42,   (ratbag.ActionKey, 'KEY_BACKSPACE')),
    (43,   (ratbag.ActionKey, 'KEY_TAB')),
    (44,   (ratbag.ActionKey, 'KEY_SPACE')),
    (45,   (ratbag.ActionKey, 'KEY_MINUS')),
    (46,   (ratbag.ActionKey, 'KEY_KPPLUS')),
    (53,   (ratbag.ActionKey, 'KEY_GRAVE')),
    (54,   (ratbag.ActionKey, 'KEY_EQUAL')),
    (56,   (ratbag.ActionKey, 'KEY_SLASH')),
    (58,   (ratbag.ActionKey, 'KEY_F1')),
    (59,   (ratbag.ActionKey, 'KEY_F2')),
    (60,   (ratbag.ActionKey, 'KEY_F3')),
    (61,   (ratbag.ActionKey, 'KEY_F4')),
    (62,   (ratbag.ActionKey, 'KEY_F5')),
    (63,   (ratbag.ActionKey, 'KEY_F6')),
    (64,   (ratbag.ActionKey, 'KEY_F7')),
    (65,   (ratbag.ActionKey, 'KEY_F8')),
    (66,   (ratbag.ActionKey, 'KEY_F9')),
    (67,   (ratbag.ActionKey, 'KEY_F10')),
    (68,   (ratbag.ActionKey, 'KEY_F11')),
    (69,   (ratbag.ActionKey, 'KEY_F12')),
    (74,   (ratbag.ActionKey, 'KEY_HOME')),
    (75,   (ratbag.ActionKey, 'KEY_PAGEUP')),
    (76,   (ratbag.ActionKey, 'KEY_DELETE')),
    (78,   (ratbag.ActionKey, 'KEY_PAGEDOWN')),
    (79,   (ratbag.ActionKey, 'KEY_RIGHT')),
    (80,   (ratbag.ActionKey, 'KEY_LEFT')),
    (81,   (ratbag.ActionKey, 'KEY_DOWN')),
    (82,   (ratbag.ActionKey, 'KEY_UP')),
    (89,   (ratbag.ActionKey, 'KEY_KP1')),
    (90,   (ratbag.ActionKey, 'KEY_KP2')),
    (91,   (ratbag.ActionKey, 'KEY_KP3')),
    (92,   (ratbag.ActionKey, 'KEY_KP4')),
    (93,   (ratbag.ActionKey, 'KEY_KP5')),
    (94,   (ratbag.ActionKey, 'KEY_KP6')),
    (95,   (ratbag.ActionKey, 'KEY_KP7')),
    (96,   (ratbag.ActionKey, 'KEY_KP8')),
    (97,   (ratbag.ActionKey, 'KEY_KP9')),
)


class DeviceError(Exception):
    pass


class NotReadyError(Exception):
    pass


class ASUSDevice(GObject.Object):
    def __init__(self, driver, rodent, config):
        GObject.Object.__init__(self)
        self.driver = driver
        self.hidraw_device = rodent
        self.config = config
        self.ratbag_device = ratbag.Device(
            self.driver, self.path, self.name, rodent.model)
        self.ratbag_device.connect('commit', self.on_commit)

    @property
    def name(self):
        return self.hidraw_device.name

    @property
    def path(self):
        return self.hidraw_device.path

    def _has_quirk(self, quirk: str):
        quirks = (self.config.quirks or '').split(';')
        return quirk in quirks

    def _is_wireless(self):
        if hasattr(self.config, 'wireless'):
            return int(self.config.wireless) == 1
        return False

    def _get_num_profiles(self):
        if hasattr(self.config, 'profiles'):
            return int(self.config.profiles)
        return 1

    def _get_num_buttons(self):
        if hasattr(self.config, 'buttons'):
            return int(self.config.buttons)
        return 8

    def _get_num_dpis(self):
        if hasattr(self.config, 'dpis'):
            return int(self.config.dpis)
        return 2

    def _get_num_leds(self):
        if hasattr(self.config, 'leds'):
            return int(self.config.leds)
        return 0

    def _get_button_offset(self, button_idx: int):
        """
        Get button offset in the bindings table.
        """
        button_mapping = '0;1;2;6;7;5;3;4'  # defaults
        if hasattr(self.config, 'button_mappping'):
            button_mapping = self.config.button_mappping

        mapping = tuple(map(int, button_mapping.split(';')))

        # failed to get mapping, using raw index without conversion
        if button_idx >= len(mapping):
            return button_idx

        return mapping[button_idx]

    def _query(self, request: list):
        self.hidraw_device.send(bytes(request))
        response = self.hidraw_device.recv()

        if response[0] == 0xff and response[1] == 0xaa:
            raise DeviceError('device offline or in invalid state')

        return response

    def get_profile_data(self) -> dict:
        """
        Get profile data including current profile index and firmware versions.
        """
        logger.debug('get_profile_data()')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_GET_PROFILE_DATA
        response = self._query(request)

        if self._has_quirk(QUIRK_STRIX_PROFILE):
            logger.debug(f'Using {STRIX_PROFILE} quirk')
            profile = response[9]
        else:
            profile = response[10]

        versions = (
            list(reversed(response[13:15+1])),
            list(reversed(response[4:6+1])),
        )

        logger.debug(f'Current profile is {profile}')
        v = ', '.join(['{:02x}.{:02x}.{:02x}'.format(*x) for x in versions])
        logger.debug(f'Firmware versions: {v}')

        return {
            'profile': profile,
            'versions': versions,
        }

    def get_led_data(self) -> list:
        """
        Get LED data.

        :returns: list of LED data.
        :rtype: list
        """
        logger.debug('get_led_data()')

        if not self._get_num_leds():
            return []

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_GET_LED_DATA
        response = self._query(request)

        leds = []
        i = 4
        for idx in range(self._get_num_leds()):
            mode = LED_MODE[response[i]]
            i += 1
            brightness = response[i]
            i += 1
            r = response[i]
            i += 1
            g = response[i]
            i += 1
            b = response[i]
            i += 1
            color = (r, g, b)
            logger.debug(f'LED.{idx}: mode={mode} color={color} brightness={brightness}')
            leds.append({
                'mode': mode,
                'color': color,
                'brightness': min(brightness * 64, 255),
            })

        return leds

    def get_resolution_data(self):
        """
        Get DPI data including polling rate,
        button response time and angle snapping.

        :returns: resolution data
        :rtype: dict
        """
        logger.debug('get_resolution_data()')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_GET_RESOLUTION_DATA
        response = self._query(request)

        i = 4
        dpis = []
        for _ in range(self._get_num_dpis()):
            dpi = response[i] * 50 + 50
            if self._has_quirk(QUIRK_DOUBLE_DPI):
                dpi *= 2
            dpis.append(dpi)
            i += 2

        # polling rate in Hz
        if response[i] in POLLING_RATE:
            rate = POLLING_RATE[response[i]]
        else:
            rate = 1000
        i += 2

        # button response time (debounce) in ms
        bresponse = (response[i] + 1) * 4
        i += 2

        # angle snapping
        snapping = bool(response[i])
        i += 2

        logger.debug(f'DPIs={dpis} rate={rate}Hz response={bresponse}ms snapping={snapping}')
        return {
            'dpis': dpis,
            'rate': rate,
            'response': bresponse,
            'snapping': snapping,
        }

    def get_button_actions(self) -> list:
        """
        Get button actions.

        :returns: list of button actions.
        :rtype: list
        """
        logger.debug('get_button_actions()')

        if not self._get_num_buttons():
            return []

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_GET_BUTTON_DATA
        response = self._query(request)

        buttons = []
        for idx in range(self._get_num_buttons()):
            # get offset for bindings array lookup
            button_off = self._get_button_offset(idx)
            # each binding is 2-byte length with 4-byte packet header
            i = button_off * 2 + 4
            code_cur = response[i]
            type_cur = response[i + 1]
            for code, (rba_class, *rba_args) in ACTION:
                if type_cur == ACTION_TYPE_KEYBOARD:
                    if rba_class != ratbag.ActionKey:
                        continue
                elif type_cur == ACTION_TYPE_MOUSE:
                    if rba_class == ratbag.ActionKey:
                        continue

                if code_cur == code:
                    logger.debug(f'Button.{idx}: {rba_class}{tuple(rba_args)}')
                    buttons.append([rba_class] + rba_args)
                    break
            else:
                logger.warning(f'Button with code {code_cur} and type {type_cur} is undefined')
                buttons.append((ratbag.ActionNone,))

        return buttons

    def get_distance(self):
        """
        Get lift-off distance.

        :returns: 0 - low, 1 - hight
        :rtype: int
        """
        logger.debug('get_distance()')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_GET_DISTANCE
        response = self._query(request)

        distance = response[4]
        logger.debug(f'Lift-off distance is {distance}')
        return distance

    def get_battery_data(self):
        """
        Get sleep timeout, battery alert and charge levels.

        :returns: battety data
        :rtype: dict
        """
        logger.debug('get_battery_data()')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_GET_BATTERY_DATA
        response = self._query(request)

        sleep_off = 4
        alert_off = 5
        charge_off = 6
        if self._has_quirk(QUIRK_BATTERY_V2):
            charge_off = 4
            sleep_off = 5
            alert_off = 6

        sleep = 0  # disabled
        if response[sleep_off] < len(SLEEP_TIMEOUT):
            sleep = SLEEP_TIMEOUT[response[sleep_off]]
        alert = response[alert_off] * 25
        charge = response[charge_off] * 25

        logger.debug(f'Battery data: sleep={sleep}min alert={alert}% charge={charge}%')
        return {
            'sleep': sleep,
            'alert': alert,
            'charge': charge,
        }

    def set_profile(self, profile_idx: int):
        """
        Switch device profile.

        :param profile: profile index, starting from zero
        :type profile: int
        """
        logger.debug(f'set_profile({profile_idx})')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_SET_PROFILE
        request[2] = profile_idx
        response = self._query(request)

    def save_profile(self):
        """
        Save current profile settings.
        """
        logger.debug('save_profile()')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_SAVE_PROFILE
        response = self._query(request)

    def set_button_action(self, button_idx: int, rba_new: ratbag.Action):
        logger.debug(f'set_button_action({button_idx}, <{rba_new}>)')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_SET_BUTTON_ACTION

        # get offset for code array lookup
        button_off = self._get_button_offset(button_idx)
        code = ACTION[button_off][0]
        request[4] = code  # button to bind
        request[5] = ACTION_TYPE_MOUSE  # button type

        # action
        for code, (rba_class, *rba_args) in ACTION:
            if isinstance(rba_new, ratbag.ActionMacro):  # ActionMacro compatibility
                rba_class_new = ratbag.ActionKey  # look for ActionKey instead
            else:
                rba_class_new = rba_new.__class__

            if rba_class_new != rba_class:
                continue

            rba_args_new = []
            if isinstance(rba_new, ratbag.ActionButton):
                rba_args_new = [rba_new.button]
            elif isinstance(rba_new, ratbag.ActionSpecial):
                rba_args_new = [rba_new.special]
            elif isinstance(rba_new, ratbag.ActionKey):
                ekey = libevdev.EV_KEY.codes[rba_new.key]
                rba_args_new = [ekey.name]
            elif isinstance(rba_new, ratbag.ActionMacro):
                event, key = rba_new.events[0]  # limit macro to first key only
                ekey = libevdev.EV_KEY.codes[key]
                rba_args_new = [ekey.name]

            if rba_args_new != rba_args:
                continue

            logger.debug(
                f'Found button action {rba_class.__name__}{tuple(rba_args)} '
                f'with code {code}')
            request[6] = code  # action
            if rba_class == ratbag.ActionKey:
                request[7] = ACTION_TYPE_KEYBOARD
            else:
                request[7] = ACTION_TYPE_MOUSE
            break
        else:
            logger.warning('Button action not found')
            request[6] = 0xff  # disable
            request[7] = ACTION_TYPE_MOUSE

        response = self._query(request)

    def set_led(self, led_idx: int, color: tuple, brightness: int, mode: int):
        """
        Set LED color, brightness and mode.

        :param color: color as RGB tuple: (0-255, 0-255, 0-255)
        :type color: tuple

        :param brightness: brightness as int: 0-255
        :type brightness: int

        :param mode: mode
        :type mode: int
        """
        logger.debug(f'set_led({led_idx}, {color}, {brightness}, {mode})')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_SET_LED
        request[2] = led_idx

        request[4] = 0
        if mode in LED_MODE:
            request[4] = LED_MODE.index(mode)

        request[5] = int(round(brightness / 64))
        request[6] = color[0]  # r
        request[7] = color[1]  # g
        request[8] = color[2]  # b
        self._query(request)

    def set_dpi(self, preset: int, dpi: int):
        """
        Set DPI for specified preset.

        :param preset: DPI preset starting from zero
        :type preset: int

        :param dpi: DPI valule
        :type dpi: int
        """
        logger.debug(f'set_dpi({preset}, {dpi})')

        if self._has_quirk(QUIRK_DOUBLE_DPI):
            dpi //= 2

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_SET_RESOLUTION_DATA
        request[2] = preset
        request[4] = (dpi - 50) // 50
        response = self._query(request)

    def set_rate(self, hz: int):
        """
        Set polling rate
        """
        logger.debug(f'set_rate({hz})')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_SET_RESOLUTION_DATA
        request[2] = self._get_num_dpis() + OFFSET_RATE
        if hz in POLLING_RATE:
            request[4] = POLLING_RATE.index(hz)
        response = self._query(request)

    def set_response(self, ms: int):
        """
        Set buttons response time (debounce) in ms.
        """
        logger.debug(f'set_response({ms})')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_SET_RESOLUTION_DATA
        request[2] = self._get_num_dpis() + OFFSET_RESPONSE
        request[4] = max(0, ms // 4 - 1)
        response = self._query(request)

    def set_snapping(self, enabled: bool):
        """
        Set angle snapping.
        """
        logger.debug(f'set_snapping({enabled})')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_SET_RESOLUTION_DATA
        request[2] = self._get_num_dpis() + OFFSET_SNAPPING
        request[4] = 1 if enabled else 0
        response = self._query(request)

    def set_distance(self, distance: int):
        """
        Set lift-off distance.

        :param distance: 0 - low, 1 - high
        :type distance: int
        """
        logger.debug(f'set_distance({distance})')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_SET_DISTANCE
        request[4] = distance
        response = self._query(request)

    def set_battery_data(self, sleep: int = 0, alert: int = 0):
        """
        Set battery data.

        :param sleep: sleep timeout in minutes, (0 to disable sleep)
        :type sleep: int

        :param alert: battery alert level - 0% (disabled), 25%, 50%
        :type alert: int
        """
        logger.debug(f'set_battery_data({sleep}, {alert})')

        request = [0] * PACKET_SIZE
        request[0:2] = CMD_SET_BATTERY_DATA
        if sleep and sleep in SLEEP_TIMEOUT:
            request[4] = SLEEP_TIMEOUT.index(sleep)
        else:
            request[4] = 0xff  # sleep disabled
        request[6] = alert // 25
        response = self._query(request)

    def read_profile(self, rb_profile: ratbag.Profile):
        """
        Read profile from device into ratbag.
        """
        leds = self.get_led_data()
        res_data = self.get_resolution_data()
        buttons = self.get_button_actions()
        distance = self.get_distance()
        bat_data = self.get_battery_data()

        rb_profile.set_report_rate(res_data['rate'])
        rb_profile.set_response(res_data['response'])
        rb_profile.set_snapping(res_data['snapping'])
        rb_profile.set_distance(distance)
        rb_profile.set_sleep_timeout(bat_data['sleep'])
        rb_profile.set_battery_alert(bat_data['alert'])
        rb_profile.set_enabled(True)

        for rb_resolution in rb_profile.resolutions:
            dpi = res_data['dpis'][rb_resolution.index]
            rb_resolution.set_dpi([dpi, dpi])

        for rb_led in rb_profile.leds:
            led_data = leds[rb_led.index]
            rb_led.set_mode(led_data['mode'])
            rb_led.set_color(led_data['color'])

        for rb_button in rb_profile.buttons:
            action_class, *action_args = buttons[rb_button.index]
            # ActionKey is not supported in Piper, convert to ActionMacro
            if action_class == ratbag.ActionKey:
                key = action_args[0]
                if hasattr(libevdev.EV_KEY, key):
                    ekey = getattr(libevdev.EV_KEY, key)
                    events = [
                        (ratbag.ActionMacro.Event.KEY_PRESS, ekey.value),
                        (ratbag.ActionMacro.Event.KEY_RELEASE, ekey.value),
                    ]
                    action = ratbag.ActionMacro(rb_profile, events=events)
                else:
                    action = ratbag.ActionNone(rb_profile)
            else:
                action = action_class(rb_profile, *action_args)
            rb_button.set_action(action)

    def on_profile_active(self, rb_profile: ratbag.Profile, value):
        """
        Profile switch callback.
        """
        if rb_profile.active:
            self.set_profile(rb_profile.index)
            # profile_enabled = rb_profile.enabled
            # self.read_profile(rb_profile)
            # if rb_profile.enabled and rb_profile.enabled != profile_enabled:
            #     self.ratbag_device.emit('resync', 1000)

    def start(self):
        logger.debug(f'Intializing device {self.name}')

        # fetch current profile index
        try:
            profile_data = self.get_profile_data()
            current_profile_idx = profile_data['profile']
        except DeviceError as e:
            logger.warning('Failed to fetch current profile')
            current_profile_idx = 0

        # prepare constant data
        profile_caps = [
            ratbag.Profile.Capability.DISABLE,
            ratbag.Profile.Capability.INDIVIDUAL_REPORT_RATE,
            ratbag.Profile.Capability.PERSISTENT,
        ]
        if self._is_wireless():
            profile_caps.append(ratbag.Profile.Capability.WIRELESS)

        if self._get_num_dpis() == 4:
            dpi_default = 2  # with blue LED
        else:
            dpi_default = 0  # with DPI LED off
        dpi_list = range_to_tuple(self.config.dpi_range)
        dpi_min = dpi_list[0]

        # prepare defaults
        for idx in range(self._get_num_profiles()):
            rb_profile = ratbag.Profile(
                self.ratbag_device, idx,
                name=PROFILE[idx],
                active=idx == current_profile_idx,
                capabilities=profile_caps,
                report_rates=POLLING_RATE,
                responses=range_to_tuple(RESPONSE),
                sleep_timeouts=SLEEP_TIMEOUT,
                report_rate=1000)
            rb_profile._enabled = False

            for dpi_idx in range(self._get_num_dpis()):
                rb_resolution = ratbag.Resolution(
                    rb_profile, dpi_idx,
                    [dpi_min, dpi_min], dpi_list=dpi_list)
                rb_resolution._default = dpi_idx == dpi_default
                rb_resolution._active = rb_resolution._default

            for led_idx in range(self._get_num_leds()):
                ratbag.Led(rb_profile, led_idx, modes=LED_MODE)

            for button_idx in range(self._get_num_buttons()):
                action = ratbag.ActionNone(rb_profile)
                ratbag.Button(
                    rb_profile, button_idx,
                    types=BUTTON_TYPE, action=action)

        # read actial profiles from device
        for rb_profile in self.ratbag_device.profiles:
            try:
                self.set_profile(rb_profile.index)
                self.read_profile(rb_profile)
            except DeviceError as e:
                logger.warning(f'Failed to fetch profile {rb_profile.index}')
            finally:
                rb_profile.connect('notify::active', self.on_profile_active)

        try:
            self.set_profile(current_profile_idx)
        except DeviceError as e:
            logger.warning('Failed to switch profile')

        return self.ratbag_device

    def on_commit(self, ratbag_device: ratbag.Device, transaction: ratbag.CommitTransaction):
        """
        Commit callback.
        """
        def is_dirty(feature):
            return feature.dirty

        success = True

        try:
            assert self.ratbag_device == ratbag_device
            logger.debug(f'Commiting to device {self.name}')

            # fetch current profile index
            profile_data = self.get_profile_data()
            current_profile_idx = profile_data['profile']
            active_profile_idx = current_profile_idx

            # write all modified profiles to the device
            for rb_profile in filter(is_dirty, ratbag_device.profiles):
                if not rb_profile.enabled:
                    raise NotReadyError('profile is not loaded yet')
                    # self.read_profile(rb_profile)

                logger.debug(f'Profile {rb_profile.index} has changes')
                self.set_profile(rb_profile.index)

                if rb_profile.active:
                    active_profile_idx = rb_profile.index
                    logger.debug(f'Profile {rb_profile.index} is active')

                self.set_rate(rb_profile.report_rate)
                self.set_response(rb_profile.response)
                self.set_snapping(rb_profile.snapping)
                self.set_distance(rb_profile.distance)
                self.set_battery_data(
                    sleep=rb_profile.sleep_timeout,
                    alert=rb_profile.battery_alert)

                for rb_resolution in filter(is_dirty, rb_profile.resolutions):
                    logger.debug(
                        f'Resolution {rb_profile.index}.{rb_resolution.index} '
                        f'has changed to {rb_resolution.dpi}')
                    self.set_dpi(rb_resolution.index, rb_resolution.dpi[0])

                for rb_button in filter(is_dirty, rb_profile.buttons):
                    logger.debug(f'Button {rb_profile.index}.{rb_button.index} has changed')
                    self.set_button_action(rb_button.index, rb_button.action)

                for rb_led in filter(is_dirty, rb_profile.leds):
                    logger.debug(f'LED {rb_profile.index}.{rb_led.index} has changed')
                    self.set_led(rb_led.index, rb_led.color, rb_led.brightness, rb_led.mode)

                self.save_profile()

            self.set_profile(active_profile_idx)

        except Exception as e:
            logger.critical(f'::::::: ERROR: Exception during commit: {e}')
            traceback.print_exc()
            success = False

        transaction.complete(success=success)


@ratbag.driver.ratbag_driver('asus')
class ASUSDriver(ratbag.driver.HidrawDriver):
    def probe(self, rodent: ratbag.driver.Rodent, config: ratbag.driver.DeviceConfig):
        # HID device descriptor must have input and output
        if not rodent.report_ids['input'] or not rodent.report_ids['output']:
            return

        # This is the device that will handle everything for us
        asus_device = ASUSDevice(self, rodent, config)

        # Calling start() will make the device talk to the physical device
        asus_device = asus_device.start()
        self.emit('device-added', asus_device)
