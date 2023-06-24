import logging
import struct

from gi.repository import GObject

import ratbag
import ratbag.hid
import ratbag.driver
import ratbag.util
from ratbag.hid import Key

logger = logging.getLogger(__name__)


# asus.h

ASUS_QUIRK_DOUBLE_DPI = 1 << 0
ASUS_QUIRK_STRIX_PROFILE = 1 << 1
ASUS_QUIRK_BATTERY_V2 = 1 << 2

ASUS_PACKET_SIZE = 64
ASUS_BUTTON_ACTION_TYPE_KEY = 0  # keyboard key
ASUS_BUTTON_ACTION_TYPE_BUTTON = 1  # mouse button
ASUS_BUTTON_CODE_DISABLED = 0xff  # disabled mouse button
ASUS_STATUS_ERROR = 0xffaa  # invalid state/request, disconnected or sleeping

# maximum number of buttons across all ASUS devices
ASUS_MAX_NUM_BUTTON = 17

# maximum number of DPI presets across all ASUS devices
# for 4 DPI devices: 0 - red, 1 - purple, 2 - blue (default), 3 - green
# for 2 DPI devices: 0 - main (default), 1 - alternative
ASUS_MAX_NUM_DPI = 4

# maximum number of LEDs across all ASUS devices
ASUS_MAX_NUM_LED = 3

ASUS_BUTTON_MAPPING = (
    (0xf0, ratbag.Action.Type.BUTTON, 1, 0),  # left
    (0xf1, ratbag.Action.Type.BUTTON, 2, 0),  # right (button 3 in xev)
    (0xf2, ratbag.Action.Type.BUTTON, 3, 0),  # middle (button 2 in xev)
    (0xe8, ratbag.Action.Type.SPECIAL, 0, ratbag.ActionSpecial.Special.WHEEL_UP),
    (0xe9, ratbag.Action.Type.SPECIAL, 0, ratbag.ActionSpecial.Special.WHEEL_DOWN),
    (0xe6, ratbag.Action.Type.SPECIAL, 0, ratbag.ActionSpecial.Special.RESOLUTION_CYCLE_UP),
    (0xe4, ratbag.Action.Type.BUTTON, 4, 0),  # backward, left side
    (0xe5, ratbag.Action.Type.BUTTON, 5, 0),  # forward, left side
    (0xe1, ratbag.Action.Type.BUTTON, 4, 0),  # backward, right side
    (0xe2, ratbag.Action.Type.BUTTON, 5, 0),  # forward, right side
    (0xe7, ratbag.Action.Type.SPECIAL, 0, ratbag.ActionSpecial.Special.RESOLUTION_ALTERNATE),  # DPI target
    (0xea, ratbag.Action.Type.NONE, 0, 0),  # side button A
    (0xeb, ratbag.Action.Type.NONE, 0, 0),  # side button B
    (0xec, ratbag.Action.Type.NONE, 0, 0),  # side button C
    (0xed, ratbag.Action.Type.NONE, 0, 0),  # side button D
    (0xee, ratbag.Action.Type.NONE, 0, 0),  # side button E
    (0xef, ratbag.Action.Type.NONE, 0, 0),  # side button F
)

# asus.c

# ASUS commands
ASUS_CMD_GET_LED_DATA = 0x0312  # get all LEDs
ASUS_CMD_GET_SETTINGS = 0x0412  # dpi, rate, button response, angle snapping
ASUS_CMD_GET_BUTTON_DATA = 0x0512  # get all buttons
ASUS_CMD_GET_PROFILE_DATA = 0x0012  # get current profile info
ASUS_CMD_SET_LED = 0x2851  # set single led
ASUS_CMD_SET_SETTING = 0x3151  # dpi / rate / button response / angle snapping
ASUS_CMD_SET_BUTTON = 0x2151  # set single button
ASUS_CMD_SET_PROFILE = 0x0250  # switch profile
ASUS_CMD_SAVE = 0x0350  # save settings

# fields order in _asus_dpiX_data, used for setting with ASUS_CMD_SET_SETTING
ASUS_FIELD_RATE = 0
ASUS_FIELD_RESPONSE = 1
ASUS_FIELD_SNAPPING = 2

ASUS_POLLING_RATES = (125, 250, 500, 1000)
ASUS_DEBOUNCE_TIMES = (4, 8, 12, 16, 20, 24, 28, 32)


def asus_find_button_by_action(action):
    """search for ASUS button by ratbag types"""
    for i in range(len(ASUS_BUTTON_MAPPING)):
        if ((action.type == ratbag.Action.Type.BUTTON and ASUS_BUTTON_MAPPING[i][2] == action.button) or
                (action.type == ratbag.Action.Type.SPECIAL and ASUS_BUTTON_MAPPING[i][3] == action.special)):
            return ASUS_BUTTON_MAPPING[i]


def asus_find_button_by_code(asus_code):
    """search for ASUS button by ASUS button code"""
    for i in range(len(ASUS_BUTTON_MAPPING)):
        if ASUS_BUTTON_MAPPING[i][0] == asus_code:
            return ASUS_BUTTON_MAPPING[i]


def asus_get_linux_key_code(asus_code):
    """convert ASUS key code to Linux key code"""
    for name in dir(Key):
        item = getattr(Key, name)
        if hasattr(item, 'value') and item.value == asus_code:
            return item


# driver-asus.c

# ButtonMapping configuration property defaults
ASUS_CONFIG_BUTTON_MAPPING = (
    0xf0,  # left
    0xf1,  # right (button 3 in xev)
    0xf2,  # middle (button 2 in xev)
    0xe4,  # backward
    0xe5,  # forward
    0xe6,  # DPI
    0xe8,  # wheel up
    0xe9,  # wheel down
    -1,  # placeholder
    -1,  # placeholder
    -1,  # placeholder
    -1,  # placeholder
    -1,  # placeholder
    -1,  # placeholder
    -1,  # placeholder
    -1,  # placeholder
    -1,  # placeholder
)

ASUS_LED_MODE = (
    ratbag.Led.Mode.ON,
    ratbag.Led.Mode.BREATHING,
    ratbag.Led.Mode.CYCLE,
    ratbag.Led.Mode.ON,  # rainbow wave
    ratbag.Led.Mode.ON,  # reactive - react to clicks
    ratbag.Led.Mode.ON,  # custom - depends on mouse type
    ratbag.Led.Mode.ON,  # battery - battery indicator
)


class AsusStatusError(Exception):
    pass


class AsusDevice(GObject.Object):
    def __init__(self, driver, rodent, config):
        GObject.Object.__init__(self)
        self.driver = driver
        self.hidraw_device = rodent
        self.config = config
        self.ratbag_device = ratbag.Device.create(
            self.driver, rodent.path, rodent.name, model=rodent.model)
        self.ratbag_device.connect('commit', self.commit)

    # libratbag-data.c

    def _get_profile_count(self,):
        if hasattr(self.config, 'profiles'):
            return int(self.config.profiles)
        return -1

    def _get_button_count(self):
        if hasattr(self.config, 'buttons'):
            return int(self.config.buttons)
        return -1

    def _get_button_mapping(self):
        if hasattr(self.config, 'button_mapping'):
            bm = list(map(lambda x: int(x, 16), self.config.button_mapping.split(';')))
            while len(bm) < ASUS_MAX_NUM_BUTTON:
                bm.append(-1)
            return bm
        return [-1] * ASUS_MAX_NUM_BUTTON

    def _get_led_count(self):
        if hasattr(self.config, 'leds'):
            return int(self.config.leds)
        return -1

    def _get_dpi_count(self):
        if hasattr(self.config, 'dpis'):
            return int(self.config.dpis)
        return -1

    def _get_dpi_list_from_range(self):
        if hasattr(self.config, 'dpi_range'):
            min_max, step = self.config.dpi_range.split('@')
            step = int(step)
            min_, max_ = tuple(map(int, min_max.split(':')))
            return tuple(range(min_, max_ + step, step))
        return []

    def _is_wireless(self):
        if hasattr(self.config, 'wireless'):
            return int(self.config.dpis)
        return -1

    def _get_quirks(self):
        if not hasattr(self, 'quirks'):
            self.quirks = 0
            if hasattr(self.config, 'quirks'):
                for quirk in self.config.quirks.split(';'):
                    if quirk == 'DOUBLE_DPI':
                        self.quirks |= ASUS_QUIRK_DOUBLE_DPI
                    elif quirk == 'STRIX_PROFILE':
                        self.quirks |= ASUS_QUIRK_STRIX_PROFILE
                    else:
                        logger.debug('%s is invalid quirk. Ignoring...' % quirk)
        return self.quirks

    # asus.c

    # generic i/o

    def _query(self, request):
        self.hidraw_device.send(bytes(request))
        response = self.hidraw_device.recv()
        code = struct.unpack('<h', response[0:2])

        # invalid state, disconnected or sleeping
        if code == ASUS_STATUS_ERROR:
            raise AsusStatusError()

        return response

    # commit

    def _save_profile(self):
        request = [0] * ASUS_PACKET_SIZE
        request[0:2] = struct.pack('<h', ASUS_CMD_SAVE)
        self._query(request)

    # profiles

    def _get_profile_data(self):
        data = {}

        request = [0] * ASUS_PACKET_SIZE
        request[0:2] = struct.pack('<h', ASUS_CMD_GET_PROFILE_DATA)
        response = self._query(request)
        results = response[2:]

        if self._get_quirks() & ASUS_QUIRK_STRIX_PROFILE:
            data['profile_id'] = results[7]
        else:
            data['profile_id'] = results[8]

        data.update({
            'version_primary_major': results[13],
            'version_primary_minor': results[12],
            'version_primary_build': results[11],
            'version_secondary_major': results[4],
            'version_secondary_minor': results[3],
            'version_secondary_build': results[2],
        })
        return data

    def _set_profile(self, index):
        request = [0] * ASUS_PACKET_SIZE
        request[0:2] = struct.pack('<h', ASUS_CMD_SET_PROFILE)
        request[2] = index
        self._query(request)

    # button bindings

    def _get_binding_data(self):
        """read button bindings"""
        data = []

        request = [0] * ASUS_PACKET_SIZE
        request[0:2] = struct.pack('<h', ASUS_CMD_GET_BUTTON_DATA)
        response = self._query(request)
        results = response[2:]

        offset = 2
        for i in range(ASUS_MAX_NUM_BUTTON):
            data.append(results[offset:offset + 2])
            offset += 2

        return data

    def _set_button_action(self, asus_code_src, asus_code_dst, asus_type):
        """set button binding using ASUS code of the button"""
        request = [0] * ASUS_PACKET_SIZE
        request[0:2] = struct.pack('<h', ASUS_CMD_SET_BUTTON)

        # source (physical mouse button)
        request[4] = asus_code_src
        request[5] = ASUS_BUTTON_ACTION_TYPE_BUTTON

        # destination (mouse button or keyboard key action)
        request[6] = asus_code_dst
        request[7] = asus_type

        self._query(request)

    # resolution settings

    def _get_resolution_data(self):
        dpi_count = self._get_dpi_count()
        data = {
            'dpi': [0] * dpi_count,
        }

        request = [0] * ASUS_PACKET_SIZE
        request[0:2] = struct.pack('<h', ASUS_CMD_GET_SETTINGS)
        response = self._query(request)
        results = response[2:]

        offset = 2
        for i in range(dpi_count):
            value = struct.unpack('<h', results[offset:offset + 2])[0] * 50 + 50
            if self._get_quirks() & ASUS_QUIRK_DOUBLE_DPI:
                value *= 2
            data['dpi'][i] = value
            offset += 2

        rate_id = struct.unpack('<h', results[offset:offset + 2])[0]
        data['rate'] = ASUS_POLLING_RATES[rate_id]
        offset += 2

        debounce_id = struct.unpack('<h', results[offset:offset + 2])[0]
        data['response'] = ASUS_DEBOUNCE_TIMES[debounce_id]
        offset += 2

        data['snapping'] = struct.unpack('<h', results[offset:offset + 2])[0]
        offset += 2

        return data

    def _set_dpi(self, index, dpi):
        """set DPI for the specified preset"""
        idpi = dpi
        if self._get_quirks() & ASUS_QUIRK_DOUBLE_DPI:
            idpi /= 2

        request = [0] * ASUS_PACKET_SIZE
        request[0:2] = struct.pack('<h', ASUS_CMD_SET_SETTING)
        request[2] = index
        request[4] = int((idpi - 50) / 50)

        self._query(request)

    def _set_polling_rate(self, hz):
        """set polling rate in Hz"""
        request = [0] * ASUS_PACKET_SIZE
        request[0:2] = struct.pack('<h', ASUS_CMD_SET_SETTING)
        request[2] = self._get_dpi_count() + ASUS_FIELD_RATE  # field index to set
        request[4] = ASUS_POLLING_RATES.index(hz)

        self._query(request)

    def _set_button_response(self, ms):
        """set button response/debounce in ms (from 4 to 32 with step of 4)"""
        request = [0] * ASUS_PACKET_SIZE
        request[0:2] = struct.pack('<h', ASUS_CMD_SET_SETTING)
        request[2] = self._get_dpi_count() + ASUS_FIELD_RESPONSE  # field index to set
        request[4] = ASUS_DEBOUNCE_TIMES.index(ms)

        self._query(request)

    def _set_angle_snapping(self, is_enabled):
        request = [0] * ASUS_PACKET_SIZE
        request[0:2] = struct.pack('<h', ASUS_CMD_SET_SETTING)
        request[2] = self._get_dpi_count() + ASUS_FIELD_SNAPPING  # field index to set
        request[4] = 1 if is_enabled else 0

        self._query(request)

    # LED settings

    def _get_led_data(self):
        data = []

        request = [0] * ASUS_PACKET_SIZE
        request[0:2] = struct.pack('<h', ASUS_CMD_GET_LED_DATA)
        response = self._query(request)
        results = response[2:]

        offset = 2
        for i in range(self._get_led_count()):
            led = {}
            led['mode'] = results[offset]
            offset += 1
            led['brightness'] = results[offset]
            offset += 1
            led['r'] = results[offset]
            offset += 1
            led['g'] = results[offset]
            offset += 1
            led['b'] = results[offset]
            offset += 1
            data.append(led)

        return data

    def _set_led(self, index, mode, brightness, color):
        """set LED mode, brightness (0-4) and color"""
        request = [0] * ASUS_PACKET_SIZE
        request[0:2] = struct.pack('<h', ASUS_CMD_SET_LED)
        request[2] = index
        request[4] = mode
        request[5] = brightness
        request[6:9] = color

        self._query(request)

    # driver-asus.c

    def load_profile(self, profile):
        # get buttons

        logger.debug('Loading buttons data')
        binding_data = self._get_binding_data()

        for button in profile.buttons:
            asus_index = self.button_indices[button.index]
            if asus_index == -1:
                logger.debug('No mapping for button %d', button.index)
                continue

            action, type = binding_data[asus_index]

            # disabled
            if action == ASUS_BUTTON_CODE_DISABLED:
                button._action = ratbag.ActionNone.create()
                continue

            # got action
            if type == ASUS_BUTTON_ACTION_TYPE_KEY:
                key = asus_get_linux_key_code(action)
                button._action = ratbag.ActionKey.create(key)
            elif type == ASUS_BUTTON_ACTION_TYPE_BUTTON:
                asus_button = asus_find_button_by_code(action)
                if asus_button:  # found button to bind to
                    asus_code, action_type, button_code, special_code = asus_button
                    if action_type == ratbag.Action.Type.BUTTON:
                        button._action = ratbag.ActionButton.create(button_code)
                    elif action_type == ratbag.Action.Type.SPECIAL:
                        button._action = ratbag.ActionSpecial.create(special_code)
                else:
                    logger.debug('Unknown action code %02x' % action)

        # get DPIs

        logger.debug('Loading resolutions data')
        resolution_data = self._get_resolution_data()

        profile._report_rate = resolution_data['rate']
        profile._angle_snapping = resolution_data['snapping']
        profile._debounce = resolution_data['response']
        for resolution in profile.resolutions:
            resolution._dpi = (
                resolution_data['dpi'][resolution.index],
                resolution_data['dpi'][resolution.index])

        # get LEDs

        logger.debug('Loading LEDs data')
        led_data = self._get_led_data()

        for led in profile.leds:
            led._mode = ASUS_LED_MODE[led_data[led.index]['mode']]
            # convert brightness from 0-4 to 0-255
            led._brightness = min(led_data[led.index]['brightness'] * 64, 255)
            led._color = (
                led_data[led.index]['r'],
                led_data[led.index]['g'],
                led_data[led.index]['b'])

    def save_profile(self, profile):
        # set buttons
        for button in profile.buttons:
            if not button.dirty:
                continue

            asus_index = self.button_indices[button.index]
            if asus_index == -1:
                logger.debug('No mapping for button %d' % button.index)
                continue

            asus_code_src = self.button_mapping[asus_index]
            if asus_code_src == -1:
                logger.debug('No mapping for button %d' % button.index)
                continue

            logger.debug('Button %d (%02x) changed' % (
                  button.index, asus_code_src))

            if button.action.type == ratbag.Action.Type.NONE:
                self._set_button_action(
                    asus_code_src, ASUS_BUTTON_CODE_DISABLED,
                    ASUS_BUTTON_ACTION_TYPE_BUTTON)

            elif button.action.type == ratbag.Action.Type.KEY:
                self._set_button_action(
                    asus_code_src, button.action.key.value,
                    ASUS_BUTTON_ACTION_TYPE_KEY)

            elif button.action.type in (
                    ratbag.Action.Type.BUTTON,
                    ratbag.Action.Type.SPECIAL):
                # ratbag action to ASUS code
                asus_button = asus_find_button_by_action(button.action)
                if asus_button:  # found button to bind to
                    asus_code, action_type, button_code, special_code = asus_button
                    self._set_button_action(
                        asus_code_src, asus_code,
                        ASUS_BUTTON_ACTION_TYPE_BUTTON)

        # set extra settings
        if profile.dirty:  # TODO: separate dirty flag for each option
            logger.debug('Polling rate changed to %d Hz' % profile.report_rate)
            self._set_polling_rate(profile.report_rate)
            logger.debug('Angle snapping changed to %d' % profile.angle_snapping)
            self._set_angle_snapping(profile.angle_snapping)
            logger.debug('Debounce time changed to %d' % profile.debounce)
            self._set_button_response(profile.debounce)

        # set DPIs
        for resolution in profile.resolutions:
            if not resolution.dirty:
                continue

            logger.debug('Resolution %d changed to %d' % (
                resolution.index, resolution.dpi[0]))

            self._set_dpi(resolution.index, resolution.dpi[0])

        # set LEDs
        for led in profile.leds:
            if not led.dirty:
                continue

            logger.debug('LED %d changed' % led.index)
            led_mode = ASUS_LED_MODE.index(led.mode)

            # convert brightness from 0-256 to 0-4
            led_brightness = round(led.brightness / 64.0)
            self._set_led(led.index, led_mode, led_brightness, led.color)

    def load_profiles(self):
        current_profile_id = 0

        # get current profile id
        profile_data = self._get_profile_data()

        if len(self.ratbag_device.profiles) > 1:
            current_profile_id = profile_data['profile_id']
            logger.debug('Initial profile is %d' % current_profile_id)

        logger.debug('Primary version %02X.%02X.%02X' % (
            profile_data['version_primary_major'],
            profile_data['version_primary_minor'],
            profile_data['version_primary_build']))
        logger.debug('Secondary version %02X.%02X.%02X' % (
            profile_data['version_secondary_major'],
            profile_data['version_secondary_minor'],
            profile_data['version_secondary_build']))

        # read ratbag profiles
        for profile in self.ratbag_device.profiles:
            profile._active = profile.index == current_profile_id

            if len(self.ratbag_device.profiles):
                logger.debug('Switching to profile %d' % profile.index)
                self._set_profile(profile.index)

            self.load_profile(profile)

        # back to initial profile
        if len(self.ratbag_device.profiles) > 1:
            logger.debug('Switching back to initial profile %d' % current_profile_id)
            self._set_profile(current_profile_id)

    def save_profiles(self):
        current_profile_id = 0

        # get current profile id
        if len(self.ratbag_device.profiles) > 1:
            profile_data = self._get_profile_data()

            current_profile_id = profile_data['profile_id']
            logger.debug('Initial profile is %d' % current_profile_id)

        for profile in self.ratbag_device.profiles:
            if not profile.dirty:
                continue

            logger.debug('Profile %d changed' % profile.index)

            # switch profile
            if len(self.ratbag_device.profiles):
                logger.debug('Switching to profile %d' % profile.index)
                self._set_profile(profile.index)

            self.save_profile(profile)

            # save profile
            logger.debug('Saving profile')
            self._save_profile()

        # back to initial profile
        if len(self.ratbag_device.profiles) > 1:
            logger.debug('Switching back to initial profile %d' % current_profile_id)
            self._set_profile(current_profile_id)

    # "probe" in driver-asus.c
    def start(self):
        # create device state data
        self.button_mapping = [0] * ASUS_MAX_NUM_BUTTON
        self.button_indices = [0] * ASUS_MAX_NUM_BUTTON
        self.is_ready = True

        # get device properties from configuration file
        profile_count = self._get_profile_count()
        dpi_count = self._get_dpi_count()
        dpi_list = self._get_dpi_list_from_range()
        button_count = self._get_button_count()
        led_count = self._get_led_count()
        bm = self._get_button_mapping()

        # merge ButtonMapping configuration property with defaults
        for i in range(ASUS_MAX_NUM_BUTTON):
            self.button_mapping[i] = bm[i] if bm[i] != -1 else ASUS_CONFIG_BUTTON_MAPPING[i]
            self.button_indices[i] = -1

        # setup a lookup table for all defined buttons
        button_index = 0
        for asus_code, type, button, special in ASUS_BUTTON_MAPPING:
            # search for this button in the ButtonMapping by it's ASUS code
            for i in range(ASUS_MAX_NUM_BUTTON):
                if self.button_mapping[i] == asus_code:
                    # add button to indices array
                    self.button_indices[button_index] = i
                    logger.debug('Button %d is mapped to 0x%02x' % (
                        button_index, self.button_mapping[i]))
                    button_index += 1
                    break

        # init & setup profiles
        for profile_index in range(max(profile_count, 1)):
            profile = ratbag.Profile(
                self.ratbag_device,
                index=profile_index,
                name=f'Profile {profile_index}',
                report_rates=ASUS_POLLING_RATES,
                debounces=ASUS_DEBOUNCE_TIMES,
                capabilities=(
                    ratbag.Profile.Capability.INDIVIDUAL_REPORT_RATE,))
            profile.connect('notify::active', self.set_active_profile)

            for button_index in range(max(button_count, 8)):
                button = ratbag.Button(
                    self.ratbag_device,
                    profile=profile,
                    index=button_index,
                    action=ratbag.ActionUnknown.create(),
                    types=(
                        ratbag.Action.Type.BUTTON,
                        ratbag.Action.Type.SPECIAL,
                        ratbag.Action.Type.KEY))

            for resolution_index in range(max(dpi_count, 2)):
                resolution = ratbag.Resolution(
                    self.ratbag_device,
                    profile=profile,
                    index=resolution_index,
                    dpi=(dpi_list[0], dpi_list[0]),
                    dpi_list=dpi_list)

            for led_index in range(max(led_count, 0)):
                led = ratbag.Led(
                    self.ratbag_device,
                    profile=profile,
                    index=led_index,
                    colordepth=ratbag.Led.Colordepth.RGB_888,
                    mode=ratbag.Led.Mode.ON,
                    modes=(
                        ratbag.Led.Mode.ON,
                        ratbag.Led.Mode.CYCLE,
                        ratbag.Led.Mode.BREATHING))

        # load profiles
        try:
            self.load_profiles()
        except AsusStatusError:  # mouse in invalid state
            self.is_ready = False
        except Exception as e:
            logger.error("Can't talk to the mouse: %s" % e)
            return

        return self.ratbag_device

    def commit(self, device, transaction):
        # check last device state
        if not self.is_ready:
            logger.error('Device was not ready, trying to reload')
            try:
                self.load_profiles()
            except Exception as e:
                logger.error('Device reloading failed: %s' % e)
            else:
                self.is_ready = True
                logger.error('Device was successfully reloaded')
            return  # fail in any case because we tried to rollback instead of commit

        self.save_profiles()

    def set_active_profile(self, profile, param):
        if profile.active:
            logger.debug('Activated profile %d' % profile.index)
            self._set_profile(profile.index)


@ratbag.driver.ratbag_driver('asus')
class AsusDriver(ratbag.driver.HidrawDriver):
    def probe(self, rodent: ratbag.driver.Rodent, config: ratbag.driver.DeviceConfig) -> None:
        # HID device descriptor must have input and output
        if not rodent.report_ids['input'] or not rodent.report_ids['output']:
            return

        # This is the device that will handle everything for us
        asus_device = AsusDevice(self, rodent, config)

        # Calling start() will make the device talk to the physical device
        ratbag_device = asus_device.start()
        if not ratbag_device:
            return

        self.emit('device-added', ratbag_device)
