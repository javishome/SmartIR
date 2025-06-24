import asyncio
import aiofiles
import json
import logging
import os.path

import voluptuous as vol

from homeassistant.components.climate import ClimateEntity, PLATFORM_SCHEMA
from homeassistant.components.climate.const import (ClimateEntityFeature, HVACMode, HVAC_MODES, ATTR_HVAC_MODE)
from homeassistant.const import (
    CONF_NAME, STATE_ON, STATE_OFF, STATE_UNKNOWN, STATE_UNAVAILABLE, ATTR_TEMPERATURE,
    PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE)
from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.helpers.event import async_track_state_change, async_track_state_change_event
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity
from . import COMPONENT_ABS_DIR, Helper
from .controller import get_controller
from homeassistant.components.mqtt import async_subscribe

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "SmartIR Climate"
DEFAULT_DELAY = 0.5

CONF_UNIQUE_ID = 'unique_id'
CONF_MODEL = 'model'
CONF_DEVICE_CODE = 'device_code'
CONF_CONTROLLER_DATA = "controller_data"
CONF_DELAY = "delay"
CONF_MQTT = "mqtt"
CONF_TEMPERATURE_SENSOR = 'temperature_sensor'
CONF_HUMIDITY_SENSOR = 'humidity_sensor'
CONF_POWER_SENSOR = 'power_sensor'
CONF_POWER_SENSOR_RESTORE_STATE = 'power_sensor_restore_state'

#them
switch = ["on", "off"]
mode = ["auto", "cool", "dry", "fan_only", "heat"]
temperature = [str(temp) for temp in range(16, 31)]  # Generating temperature values from 16 to 31
speed = ["auto", "low", "mid", "high"]

# them
EASYIOT_CONTROLLER = "Easyiot"

SUPPORT_FLAGS = (
    ClimateEntityFeature.TURN_OFF |
    ClimateEntityFeature.TURN_ON |
    ClimateEntityFeature.TARGET_TEMPERATURE | 
    ClimateEntityFeature.FAN_MODE
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_UNIQUE_ID): cv.string,
    vol.Optional(CONF_MODEL): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_DEVICE_CODE): cv.positive_int,
    vol.Required(CONF_CONTROLLER_DATA): cv.string,
    vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_float,
    vol.Optional(CONF_TEMPERATURE_SENSOR): cv.entity_id,
    vol.Optional(CONF_HUMIDITY_SENSOR): cv.entity_id,
    vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
    vol.Optional(CONF_MQTT, default=False): cv.boolean,
    vol.Optional(CONF_POWER_SENSOR_RESTORE_STATE, default=False): cv.boolean
})

async def set_controller(self):
    """Set controller with given device code."""
    
    def create_command(command_str):
        output = [int(command_str[i:i + 2], 16) for i in range(0, len(command_str), 2)]
        checksum = zigbeeUartFrameCalcXOR(output)
        command_str += f"{checksum:02x}"
        return {"send_command": command_str}

    def zigbeeUartFrameCalcXOR(msg):
        xor_result = 0
        for byte in msg:
            xor_result ^= byte
        return xor_result

    try:
        base_command = "8001" + self._model + "00"
        enable_feedback_command = "8201000000"
        command_list = [create_command(base_command), create_command(enable_feedback_command)]

        for command in command_list:
            service_data = {
                'topic': self._controller_data,
                'payload': json.dumps(command)
            }
            await self.hass.services.async_call('mqtt', 'publish', service_data)
            await asyncio.sleep(self._delay)
    except Exception as e:
        _LOGGER.error(f"Error setting controller: {e}")


async def set_feedback(self, power=None, mode=None, temp=None, fan=None):
    """Set feedback based on power, mode, temperature, and fan speed."""

    def create_command(prefix, value):
        command_str = f"8f01{prefix}{value}00"
        output = [int(command_str[i:i + 2], 16) for i in range(0, len(command_str), 2)]
        checksum = zigbeeUartFrameCalcXOR(output)
        command_str += f"{checksum:02x}"
        return {"send_command": command_str}

    def zigbeeUartFrameCalcXOR(msg):
        xor_result = 0
        for byte in msg:
            xor_result ^= byte
        return xor_result

    try:
        command_list = []
        if power is not None:
            command_list.append(create_command("00", power))
        if mode is not None:
            command_list.append(create_command("01", mode))
        if temp is not None:
            command_list.append(create_command("02", temp))
        if fan is not None:
            command_list.append(create_command("04", fan))

        for command in command_list:
            service_data = {
                'topic': self._controller_data,
                'payload': json.dumps(command)
            }
            await self.hass.services.async_call('mqtt', 'publish', service_data)
            await asyncio.sleep(self._delay)
    except Exception as e:
        _LOGGER.error(f"Error setting feedback: {e}")


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the IR Climate platform."""
    _LOGGER.debug("Setting up the smartir platform")

    device_code = config.get(CONF_DEVICE_CODE)
    device_files_subdir = os.path.join('codes', 'climate')
    device_files_absdir = os.path.join(COMPONENT_ABS_DIR, device_files_subdir)

    if not os.path.isdir(device_files_absdir):
        os.makedirs(device_files_absdir)

    device_json_filename = str(device_code) + '.json'
    device_json_path = os.path.join(device_files_absdir, device_json_filename)

    if not os.path.exists(device_json_path):
        _LOGGER.warning("Couldn't find the device Json file. The component will " \
                        "try to download it from the GitHub repo.")

        try:
            codes_source = ("https://raw.githubusercontent.com/"
                            "smartHomeHub/SmartIR/master/"
                            "codes/climate/{}.json")

            await Helper.downloader(codes_source.format(device_code), device_json_path)
        except Exception:
            _LOGGER.error("There was an error while downloading the device Json file. " \
                          "Please check your internet connection or if the device code " \
                          "exists on GitHub. If the problem still exists please " \
                          "place the file manually in the proper directory.")
            return
        
        try:
            async with aiofiles.open(device_json_path, mode='r') as j:
                _LOGGER.debug(f"loading json file {device_json_path}")
                content = await j.read()
                device_data = json.loads(content)
                _LOGGER.debug(f"{device_json_path} file loaded")
        except Exception:
            _LOGGER.error("The device Json file is invalid")
            return

    async_add_entities([SmartIRClimate(
        hass, config, device_data
    )])


class SmartIRClimate(ClimateEntity, RestoreEntity):
    def __init__(self, hass, config, device_data):
        _LOGGER.debug(
            f"SmartIRClimate init started for device {config.get(CONF_NAME)} supported models {device_data['supportedModels']}")
        self.hass = hass
        self._unique_id = config.get(CONF_UNIQUE_ID)
        self._model = config.get(CONF_MODEL)
        self._name = config.get(CONF_NAME)
        self._device_code = config.get(CONF_DEVICE_CODE)
        self._controller_data = config.get(CONF_CONTROLLER_DATA)
        self._topic = '/'.join(self._controller_data.split('/')[:-1])
        self._delay = config.get(CONF_DELAY)
        self._mqtt = config.get(CONF_MQTT)
        self._temperature_sensor = config.get(CONF_TEMPERATURE_SENSOR)
        self._humidity_sensor = config.get(CONF_HUMIDITY_SENSOR)
        self._power_sensor = config.get(CONF_POWER_SENSOR)
        self._power_sensor_restore_state = config.get(CONF_POWER_SENSOR_RESTORE_STATE)
        self._manufacturer = device_data['manufacturer']
        self._supported_models = device_data['supportedModels']
        if(self._mqtt == True):
            self._supported_controller = "MQTT"
        else:
            self._supported_controller = device_data['supportedController']
        self._commands_encoding = device_data['commandsEncoding']
        self._min_temperature = device_data['minTemperature']
        self._max_temperature = device_data['maxTemperature']
        self._precision = device_data['precision'] 

        valid_hvac_modes = [x for x in device_data['operationModes'] if x in HVAC_MODES]

        self._operation_modes = [HVACMode.OFF] + valid_hvac_modes
        self._fan_modes = device_data['fanModes']
        self._swing_modes = device_data.get('swingModes')
        self._commands = device_data['commands']

        self._target_temperature = self._min_temperature
        self._hvac_mode = HVACMode.OFF
        self._current_fan_mode = self._fan_modes[0]
        self._current_swing_mode = None
        self._last_on_operation = None

        self._current_temperature = None
        self._current_humidity = None

        self._unit = hass.config.units.temperature_unit

        # Supported features
        self._support_flags = SUPPORT_FLAGS
        self._support_swing = False

        if self._swing_modes:
            self._support_flags = self._support_flags | ClimateEntityFeature.SWING_MODE
            self._current_swing_mode = self._swing_modes[0]
            self._support_swing = True

        self._temp_lock = asyncio.Lock()
        self._on_by_remote = False

        self.hass.async_create_task(self.async_initialize_controller())

    async def async_initialize_controller(self):
        # Init the IR/RF controller
        self._controller = get_controller(
            self.hass,
            self._supported_controller,
            self._commands_encoding,
            self._controller_data,
            self._delay)
        # If controller is EAYIOT, then
        if self._supported_controller == EASYIOT_CONTROLLER:
            await set_controller(self)
            await self.async_subscribe_topics()

    async def async_subscribe_topics(self):
        """Subscribe to MQTT topics."""
        async def message_handler(msg):
            await self.async_received_message(msg)

        await async_subscribe(
            self.hass, self._topic, message_handler
        )

    async def async_received_message(self, msg):
        """Handle messages received on zigbee2mqtt/0xf4b3b1fffe45fda8/set topic."""
        # Check if the message topic matches the expected topic
        if msg.topic != self._topic or not msg.payload:
            return
        try:
            payload_dict = json.loads(msg.payload)
            last_received_command = payload_dict.get("last_received_command")
            if not last_received_command:
                return

            separated_list = [last_received_command[i:i + 2] for i in range(0, len(last_received_command), 2)]
            if len(separated_list) >= 5 and separated_list[0] == "08":
                switch_index = int(separated_list[1], 16)
                mode_index = int(separated_list[2], 16)
                speed_index = int(separated_list[4], 16)
                temperature_index = int(separated_list[3], 16)
                power = md = temp = fan = None
                if 0 <= switch_index < len(switch):
                    power = separated_list[1]
                    if switch[switch_index].lower() == 'off':
                        self._hvac_mode = switch[switch_index]
                if 0 <= mode_index < len(mode):
                    if (power is not None):
                        if (switch[switch_index].lower() != 'off'):
                            self._hvac_mode = mode[mode_index]
                            self._last_on_operation = mode[mode_index]
                            md = separated_list[2]
                if 0 <= temperature_index < len(temperature):
                    if (power is not None):
                        if (switch[switch_index].lower() != 'off'):
                            self._target_temperature = round(float(temperature[temperature_index]))
                            temp = separated_list[3]
                if 0 <= speed_index < len(speed):
                    if (power is not None):
                        if (switch[switch_index].lower() != 'off'):
                            self._current_fan_mode = speed[speed_index]
                            fan = separated_list[4]
                # await self.async_update_ha_state()
                self.async_write_ha_state()
                await set_feedback(self, power, md, temp, fan)

        except json.JSONDecodeError:
            # Log the error as a warning or ignore the message
            _LOGGER.warning("Invalid JSON payload received: %s", msg.payload)

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        _LOGGER.debug(f"async_added_to_hass {self} {self.name} {self.supported_features}")

        last_state = await self.async_get_last_state()

        if last_state is not None:
            self._hvac_mode = last_state.state
            self._current_fan_mode = last_state.attributes['fan_mode']
            self._current_swing_mode = last_state.attributes.get('swing_mode')
            self._target_temperature = last_state.attributes['temperature']

            if 'last_on_operation' in last_state.attributes:
                self._last_on_operation = last_state.attributes['last_on_operation']

        if self._temperature_sensor:
            async_track_state_change_event(self.hass, self._temperature_sensor, 
                                           self._async_temp_sensor_changed)

            temp_sensor_state = self.hass.states.get(self._temperature_sensor)
            if temp_sensor_state and temp_sensor_state.state != STATE_UNKNOWN:
                self._async_update_temp(temp_sensor_state)

        if self._humidity_sensor:
            async_track_state_change_event(self.hass, self._humidity_sensor, 
                                           self._async_humidity_sensor_changed)

            humidity_sensor_state = self.hass.states.get(self._humidity_sensor)
            if humidity_sensor_state and humidity_sensor_state.state != STATE_UNKNOWN:
                self._async_update_humidity(humidity_sensor_state)

        if self._power_sensor:
            async_track_state_change_event(self.hass, self._power_sensor, 
                                           self._async_power_sensor_changed)

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

    @property
    def model(self):
        """Return a unique ID."""
        return self._model

    @property
    def name(self):
        """Return the name of the climate device."""
        return self._name

    @property
    def state(self):
        """Return the current state."""
        if self.hvac_mode != HVACMode.OFF:
            return self.hvac_mode
        return HVACMode.OFF

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def min_temp(self):
        """Return the polling state."""
        return self._min_temperature

    @property
    def max_temp(self):
        """Return the polling state."""
        return self._max_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return self._precision

    @property
    def hvac_modes(self):
        """Return the list of available operation modes."""
        return self._operation_modes

    @property
    def hvac_mode(self):
        """Return hvac mode ie. heat, cool."""
        return self._hvac_mode

    @property
    def last_on_operation(self):
        """Return the last non-idle operation ie. heat, cool."""
        return self._last_on_operation

    @property
    def fan_modes(self):
        """Return the list of available fan modes."""
        return self._fan_modes

    @property
    def fan_mode(self):
        """Return the fan setting."""
        return self._current_fan_mode

    @property
    def swing_modes(self):
        """Return the swing modes currently supported for this device."""
        return self._swing_modes

    @property
    def swing_mode(self):
        """Return the current swing mode."""
        return self._current_swing_mode

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temperature

    @property
    def current_humidity(self):
        """Return the current humidity."""
        return self._current_humidity

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._support_flags

    @property
    def extra_state_attributes(self):
        """Platform specific attributes."""
        return {
            'last_on_operation': self._last_on_operation,
            'device_code': self._device_code,
            'manufacturer': self._manufacturer,
            'supported_models': self._supported_models,
            'supported_controller': self._supported_controller,
            'commands_encoding': self._commands_encoding
        }

    async def async_set_temperature(self, **kwargs):
        """Set new target temperatures."""
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        temperature = kwargs.get(ATTR_TEMPERATURE)

        if temperature is None:
            return

        if temperature < self._min_temperature or temperature > self._max_temperature:
            _LOGGER.warning('The temperature value is out of min/max range')
            return

        if self._precision == PRECISION_WHOLE:
            self._target_temperature = round(temperature)
        else:
            self._target_temperature = round(temperature, 1)

        if hvac_mode:
            await self.async_set_hvac_mode(hvac_mode)
            return

        if not self._hvac_mode.lower() == HVACMode.OFF:
            if self._supported_controller == EASYIOT_CONTROLLER:
                target_temperature = '{0:g}'.format(self._target_temperature)
                await self._controller.send(self._commands[target_temperature])
            else:
                await self.send_command()

        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        """Set operation mode."""
        last_state = self._hvac_mode
        self._hvac_mode = hvac_mode

        if not hvac_mode == HVACMode.OFF:
            self._last_on_operation = hvac_mode

        if self._supported_controller == EASYIOT_CONTROLLER:
            if self._hvac_mode.lower() == HVACMode.OFF:
                await self._controller.send(self._commands['off'])
                #thay
                self.async_write_ha_state()
                # await self.async_update_ha_state()
                return
            if last_state == HVACMode.OFF:
                await self._controller.send(self._commands['on'])
            await asyncio.sleep(self._delay*2)
            await self._controller.send(self._commands[self._hvac_mode.lower()])
        else:
            await self.send_command()

        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode):
        """Set fan mode."""
        self._current_fan_mode = fan_mode

        if not self._hvac_mode.lower() == HVACMode.OFF:
            if self._supported_controller == EASYIOT_CONTROLLER:
                if self._current_fan_mode.lower() == 'auto':
                    await self._controller.send(self._commands['auto1'])
                else:
                    await self._controller.send(self._commands[self._current_fan_mode.lower()])
            else:
                await self.send_command()

        self.async_write_ha_state()

    async def async_set_swing_mode(self, swing_mode):
        """Set swing mode."""
        self._current_swing_mode = swing_mode

        if not self._hvac_mode.lower() == HVACMode.OFF:
            if self._supported_controller == EASYIOT_CONTROLLER:
                if self._current_swing_mode.lower() == "auto":
                    await self._controller.send(self._commands['swing_auto'])
                elif self._current_swing_mode.lower() == "top":
                    await self._controller.send(self._commands['swing_top'])
                elif self._current_swing_mode.lower() == "high":
                    await self._controller.send(self._commands['swing_high'])
                elif self._current_swing_mode.lower() == "medium":
                    await self._controller.send(self._commands['swing_mid'])
                elif self._current_swing_mode.lower() == "low":
                    await self._controller.send(self._commands['swing_low'])
                elif self._current_swing_mode.lower() == "on":
                    await self._controller.send(self._commands['swing_on'])
                elif self._current_swing_mode.lower() == "off":
                    await self._controller.send(self._commands['swing_off'])
                else:
                    await self._controller.send(self._commands['swing_bottom'])
            else:
                await self.send_command()

        self.async_write_ha_state()

    async def async_turn_off(self):
        """Turn off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self):
        """Turn on."""
        if self._last_on_operation is not None:
            await self.async_set_hvac_mode(self._last_on_operation)
        else:
            await self.async_set_hvac_mode(self._operation_modes[1])

    async def send_command(self):
        async with self._temp_lock:
            try:
                self._on_by_remote = False
                operation_mode = self._hvac_mode
                fan_mode = self._current_fan_mode
                swing_mode = self._current_swing_mode
                target_temperature = '{0:g}'.format(self._target_temperature)

                if operation_mode.lower() == HVACMode.OFF:
                    await self._controller.send(self._commands['off'])
                    return

                if 'on' in self._commands:
                    await self._controller.send(self._commands['on'])
                    await asyncio.sleep(self._delay)

                if self._support_swing == True:
                    await self._controller.send(
                        self._commands[operation_mode][fan_mode][swing_mode][target_temperature])
                else:
                    await self._controller.send(
                        self._commands[operation_mode][fan_mode][target_temperature])

            except Exception as e:
                _LOGGER.exception(e)

    @callback
    async def _async_temp_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle temperature sensor changes."""
        new_state = event.data["new_state"]   
        if new_state is None:
            return

        self._async_update_temp(new_state)

        self.async_write_ha_state()

    @callback
    async def _async_humidity_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle humidity sensor changes."""
        new_state = event.data["new_state"]
        if new_state is None:
            return

        self._async_update_humidity(new_state)

        self.async_write_ha_state()

    @callback
    async def _async_power_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle power sensor changes."""
        entity_id = event.data["entity_id"]
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]

        if new_state is None:
            return

        if old_state is not None and new_state.state == old_state.state:
            return

        if new_state.state == STATE_ON and self._hvac_mode == HVACMode.OFF:
            self._on_by_remote = True
            if self._power_sensor_restore_state == True and self._last_on_operation is not None:
                self._hvac_mode = self._last_on_operation
            else:
                self._hvac_mode = STATE_ON

            self.async_write_ha_state()

        if new_state.state == STATE_OFF:
            self._on_by_remote = False
            if self._hvac_mode != HVACMode.OFF:
                self._hvac_mode = HVACMode.OFF

            self.async_write_ha_state()

    @callback
    def _async_update_temp(self, state):
        """Update thermostat with latest state from temperature sensor."""
        try:
            if state.state != STATE_UNKNOWN and state.state != STATE_UNAVAILABLE:
                self._current_temperature = float(state.state)
        except ValueError as ex:
            _LOGGER.error("Unable to update from temperature sensor: %s", ex)

    @callback
    def _async_update_humidity(self, state):
        """Update thermostat with latest state from humidity sensor."""
        try:
            if state.state != STATE_UNKNOWN and state.state != STATE_UNAVAILABLE:
                self._current_humidity = float(state.state)
        except ValueError as ex:
            _LOGGER.error("Unable to update from humidity sensor: %s", ex)