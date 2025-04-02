from abc import ABC, abstractmethod
from base64 import b64encode, b64decode
import binascii
import requests
import logging
import json
from homeassistant.const import ATTR_ENTITY_ID
from . import Helper
from .broadlink2tuya import *
import asyncio
_LOGGER = logging.getLogger(__name__)

BROADLINK_CONTROLLER = 'Broadlink'
XIAOMI_CONTROLLER = 'Xiaomi'
MQTT_CONTROLLER = 'MQTT'
LOOKIN_CONTROLLER = 'LOOKin'
ESPHOME_CONTROLLER = 'ESPHome'
EASYIOT_CONTROLLER = 'Easyiot'
ENC_BASE64 = 'Base64'
ENC_HEX = 'Hex'
ENC_PRONTO = 'Pronto'
ENC_RAW = 'Raw'

BROADLINK_COMMANDS_ENCODING = [ENC_BASE64, ENC_HEX, ENC_PRONTO]
XIAOMI_COMMANDS_ENCODING = [ENC_PRONTO, ENC_RAW]
MQTT_COMMANDS_ENCODING = [ENC_BASE64, ENC_PRONTO] #ENC_RAW
LOOKIN_COMMANDS_ENCODING = [ENC_PRONTO, ENC_RAW]
ESPHOME_COMMANDS_ENCODING = [ENC_RAW]
EASYIOT_COMMANDS_ENCODING = [ENC_HEX]

def get_controller(hass, controller, encoding, controller_data, delay):
    """Return a controller compatible with the specification provided."""
    controllers = {
        BROADLINK_CONTROLLER: BroadlinkController,
        XIAOMI_CONTROLLER: XiaomiController,
        MQTT_CONTROLLER: MQTTController,
        LOOKIN_CONTROLLER: LookinController,
        ESPHOME_CONTROLLER: ESPHomeController,
        EASYIOT_CONTROLLER: EASYIOTController
    }
    try:
        return controllers[controller](hass, controller, encoding, controller_data, delay)
    except KeyError:
        raise Exception("The controller is not supported.")


class AbstractController(ABC):
    """Representation of a controller."""
    def __init__(self, hass, controller, encoding, controller_data, delay):
        self.check_encoding(encoding)
        self.hass = hass
        self._controller = controller
        self._encoding = encoding
        self._controller_data = controller_data
        self._delay = delay

    @abstractmethod
    def check_encoding(self, encoding):
        """Check if the encoding is supported by the controller."""
        pass

    @abstractmethod
    async def send(self, command):
        """Send a command."""
        pass

class EASYIOTController(AbstractController):
    def check_encoding(self, encoding):
        """Check if the encoding is supported by the controller."""
        if encoding not in EASYIOT_COMMANDS_ENCODING:
            raise Exception("The encoding is not supported "
                            "by the easyiot controller.")
    async def send(self, command):
        """Send a command."""
        try:
            # set a command
            command = "{\"send_command\": \"" + command + "\"}"
            service_data = {
                'topic': self._controller_data,
                'payload': command
            }
            await self.hass.services.async_call(
                'mqtt', 'publish', service_data)
            await asyncio.sleep(self._delay)
        except:
            raise Exception("Error format!")

class BroadlinkController(AbstractController):
    """Controls a Broadlink device."""

    def check_encoding(self, encoding):
        """Check if the encoding is supported by the controller."""
        if encoding not in BROADLINK_COMMANDS_ENCODING:
            raise Exception("The encoding is not supported "
                            "by the Broadlink controller.")

    async def send(self, command):
        """Send a command."""
        commands = []

        if not isinstance(command, list):
            command = [command]

        for _command in command:
            if self._encoding == ENC_BASE64:
                try:
                    raw_ir_code = encode_ir(_command)
                    _command = "{\"ir_code_to_send\": \"" + raw_ir_code + "\"}"
                except:
                    raise Exception("Error while converting "
                                    "Base64 encoding to Raw")

            if self._encoding == ENC_HEX:
                try:
                    _command = binascii.unhexlify(_command)
                    _command = b64encode(_command).decode('utf-8')
                    _command = "{\"ir_code_to_send\": \"" + _command + "\"}"
                except:
                    raise Exception("Error while converting "
                                    "Hex to Raw")

            if self._encoding == ENC_PRONTO:
                try:
                    _command = _command.replace(' ', '')
                    _command = bytearray.fromhex(_command)
                    _command = Helper.pronto2lirc(_command)
                    _command = Helper.lirc2broadlink(_command)
                    _command = b64encode(_command).decode('utf-8')
                    _command = "{\"ir_code_to_send\": \"" + _command + "\"}"
                except:
                    raise Exception("Error while converting "
                                    "Pronto to Raw")

        service_data = {
            'topic': self._controller_data,
            'payload': _command
        }

        await self.hass.services.async_call('mqtt', 'publish', service_data)


class XiaomiController(AbstractController):
    """Controls a Xiaomi device."""

    def check_encoding(self, encoding):
        """Check if the encoding is supported by the controller."""
        if encoding not in XIAOMI_COMMANDS_ENCODING:
            raise Exception("The encoding is not supported "
                            "by the Xiaomi controller.")

    async def send(self, command):
        """Send a command."""
        service_data = {
            ATTR_ENTITY_ID: self._controller_data,
            'command':  self._encoding.lower() + ':' + command
        }

        await self.hass.services.async_call(
            'remote', 'send_command', service_data)


class MQTTController(AbstractController):
    """Controls a MQTT device."""

    def check_encoding(self, encoding):
        """Check if the encoding is supported by the controller."""
        if encoding not in MQTT_COMMANDS_ENCODING:
            raise Exception("The encoding is not supported "
                            "by the mqtt controller.")
    async def send(self, command):
        """Send a command."""        
        def process2(a):
            if int(a[0:2],16) == 0:
                return 1
            elif int(a[0:2],16) > 10:
                return 2
            else:
                return 3

        def process3(a):
            if(len(a)<4 and process2(a) == 1):
                return None  
            elif (process2(a) == 3):
                return a[:4], a[4:]
            elif (process2(a) == 1):
                return a[2:6], a[6:]
            else:
                return a[:2], a[2:]

        def process(a):
            a = b64decode(str(a)).hex()
            start = a[:4]
            num_byte = a[6:8]+a[4:6]
            cl = a[8:].lower().split('0d05')[0]
            data = "{"
            a = 0
            while(len(cl)>=4 ):
                if process3(cl) is not None:
                    t1, cl = process3(cl)
                if a != 0:
                    data = data + ","
                a = a + 1
                data= data + str(int(int(t1, 16) / 269 * 8192))
            data = data + "}"
            return data
        if self._encoding == ENC_BASE64:
            try:
                command = process(command)
                service_data = {
                    'topic': self._controller_data,
                    'payload': command
                }
                await self.hass.services.async_call(
                'mqtt', 'publish', service_data)
            except:
                raise Exception("Error while converting "
                                        "Base64 to raw encoding")
        else:
            service_data = {
                'topic': self._controller_data,
                'payload': command
            }
            await self.hass.services.async_call(
            'mqtt', 'publish', service_data)



class LookinController(AbstractController):
    """Controls a Lookin device."""

    def check_encoding(self, encoding):
        """Check if the encoding is supported by the controller."""
        if encoding not in LOOKIN_COMMANDS_ENCODING:
            raise Exception("The encoding is not supported "
                            "by the LOOKin controller.")

    async def send(self, command):
        """Send a command."""
        encoding = self._encoding.lower().replace('pronto', 'prontohex')
        url = f"http://{self._controller_data}/commands/ir/" \
                f"{encoding}/{command}"
        await self.hass.async_add_executor_job(requests.get, url)


class ESPHomeController(AbstractController):
    """Controls a ESPHome device."""

    def check_encoding(self, encoding):
        """Check if the encoding is supported by the controller."""
        if encoding not in ESPHOME_COMMANDS_ENCODING:
            raise Exception("The encoding is not supported "
                            "by the ESPHome controller.")
    
    async def send(self, command):
        """Send a command."""
        service_data = {'command':  json.loads(command)}

        await self.hass.services.async_call(
            'esphome', self._controller_data, service_data)