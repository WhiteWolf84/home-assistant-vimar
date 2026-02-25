"""Main Vimar Link module - refactored to use modular components."""

from __future__ import annotations

import logging
from collections.abc import Callable
from xml.etree import ElementTree

try:
    from ..const import (
        DEVICE_TYPE_CLIMATES,
        DEVICE_TYPE_COVERS,
        DEVICE_TYPE_LIGHTS,
        DEVICE_TYPE_MEDIA_PLAYERS,
        DEVICE_TYPE_OTHERS,
        DEVICE_TYPE_SCENES,
        DEVICE_TYPE_SENSORS,
        DEVICE_TYPE_SWITCHES,
    )
except ImportError:
    DEVICE_TYPE_LIGHTS = "light"
    DEVICE_TYPE_COVERS = "cover"
    DEVICE_TYPE_SWITCHES = "switch"
    DEVICE_TYPE_CLIMATES = "climate"
    DEVICE_TYPE_MEDIA_PLAYERS = "media_player"
    DEVICE_TYPE_SCENES = "scene"
    DEVICE_TYPE_SENSORS = "sensor"
    DEVICE_TYPE_OTHERS = "other"

from .connection import VimarConnection
from .device_queries import (
    VimarDevice,
    get_device_status_query,
    get_remote_devices_query,
    get_room_devices_query,
    get_room_ids_query,
    get_status_only_query,
)
from .exceptions import VimarApiError, VimarConnectionError
from .sql_parser import parse_sql_payload

_LOGGER = logging.getLogger(__name__)
# FIX #11: removed module-level _LOGGER_isDebug constant.
# A module-level bool is evaluated once at import time, before HA configures
# log levels. If debug logging is enabled after import, the cached False value
# would cause all debug branches to be silently skipped for the entire session.
# All callers now use _LOGGER.isEnabledFor(logging.DEBUG) evaluated at runtime.
MAX_ROWS_PER_REQUEST = 300

# Device class constants
DEVICE_CLASS_OUTLET = "outlet"
DEVICE_CLASS_SWITCH = "switch"
DEVICE_CLASS_SHUTTER = "shutter"
DEVICE_CLASS_WINDOW = "window"
DEVICE_CLASS_POWER = "power"
DEVICE_CLASS_TEMPERATURE = "temperature"
DEVICE_CLASS_PRESSURE = "pressure"


class VimarLink:
    """Link to communicate with the Vimar webserver."""

    def __init__(
        self,
        schema=None,
        host=None,
        port=None,
        username=None,
        password=None,
        certificate=None,
        timeout=None,
    ):
        """Initialize Vimar link with connection parameters."""
        _LOGGER.info("Vimar link initialized")

        self._connection = VimarConnection(
            schema=schema or "https",
            host=host or "",
            port=port or 443,
            username=username or "",
            password=password or "",
            certificate=certificate,
            timeout=timeout or 6,
        )

        self._room_ids = None
        self._rooms = None

    @property
    def _session_id(self):
        """Get session ID from connection."""
        return self._connection.session_id

    @property
    def request_last_exception(self):
        """Get last request exception."""
        return self._connection.request_last_exception

    def install_certificate(self):
        """Download CA certificate from web server."""
        return self._connection.install_certificate()

    def login(self):
        """Authenticate and get session ID."""
        return self._connection.login()

    def is_logged(self):
        """Check if session is available."""
        return self._connection.is_logged()

    def check_login(self):
        """Ensure valid session exists."""
        return self._connection.check_login()

    def check_session(self):
        """Check if session is valid."""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Expect": "",
        }
        post = f"sessionid={self._session_id}&op=getjScriptEnvironment&context=runtime"
        return self._request_vimar(post, "vimarbyweb/modules/system/dpadaction.php", headers)

    def set_device_status(self, object_id, status, optionals="NO-OPTIONALS"):
        """Set status for one device."""
        post = (
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            '<soapenv:Body><service-runonelement xmlns="urn:xmethods-dpadws">'
            f"<payload>{status}</payload>"
            "<hashcode>NO-HASHCODE</hashcode>"
            f"<optionals>{optionals}</optionals>"
            "<callsource>WEB-DOMUSPAD_SOAP</callsource>"
            f"<sessionid>{self._session_id}</sessionid><waittime>10</waittime>"
            f"<idobject>{object_id}</idobject>"
            "<operation>SETVALUE</operation>"
            "</service-runonelement></soapenv:Body></soapenv:Envelope>"
        )

        response = self._request_vimar_soap(post)
        if response is not None and response is not False:
            payload = response.find(".//payload")
            if payload is not None:
                _LOGGER.warning(
                    "set_device_status returned payload: %s from request: %s",
                    payload.text or "unknown error",
                    post,
                )
                return parse_sql_payload(payload.text)
        return None

    def get_optionals_param(self, state):
        """Return SYNCDB for climate states."""
        if state in [
            "setpoint",
            "stagione",
            "unita",
            "temporizzazione",
            "channel",
            "source",
            "global_channel",
            "centralizzato",
        ]:
            return "SYNCDB"
        return "NO-OPTIONALS"

    def get_device_status(self, object_id):
        """Get attribute status for a single device."""
        status_list = {}
        select = get_device_status_query(object_id)
        payload = self._request_vimar_sql(select)

        if payload is not None:
            for device in payload:
                if device["status_name"] != "":
                    status_list[device["status_name"]] = {
                        "status_id": device["status_id"],
                        "status_value": device["status_value"],
                    }
        return status_list

    def get_status_only(self, status_ids: list[int]) -> list[dict] | None:
        """Slim poll: fetch only CURRENT_VALUE for known status IDs.

        PATCH #1: Optimized polling method that avoids expensive JOINs.
        """
        if not status_ids:
            return []
        select = get_status_only_query(status_ids)
        return self._request_vimar_sql(select)

    def get_paged_results(
        self,
        method: Callable[
            [dict[str, VimarDevice], int | None, int | None],
            tuple[dict[str, VimarDevice], int] | None,
        ],
        objectlist: dict[str, VimarDevice] | None = None,
        start: int = 0,
    ):
        """Page results from a method automatically.

        FIX #7: converted from recursive to iterative implementation.
        The recursive version would call itself once per batch of MAX_ROWS_PER_REQUEST
        rows; on large installations (many devices/statuses) this could exhaust
        Python's default recursion limit (1000 frames) and raise RecursionError.
        The while-loop below is functionally identical but has O(1) stack depth.
        """
        if objectlist is None:
            objectlist = {}
        limit = MAX_ROWS_PER_REQUEST

        if not callable(method):
            raise VimarApiError(f"Invalid method for paged results: {method}")

        total_count = 0
        while True:
            result = method(objectlist, start, limit)
            if result is None:
                raise VimarApiError(f"Invalid method results: {method}")

            objectlist, state_count = result
            total_count += state_count

            # If we got fewer rows than the page size, we've reached the last page
            if state_count < limit:
                break

            start += state_count

        return objectlist, total_count

    def get_room_devices(
        self,
        devices: dict[str, VimarDevice] | None = None,
        start: int | None = None,
        limit: int | None = None,
    ):
        """Load all devices that belong to a room."""
        if devices is None:
            devices = {}
        if self._room_ids is None:
            return None

        start, limit = self._sanitize_limits(start, limit)
        _LOGGER.debug("get_room_devices started - from %d to %d", start, start + limit)

        select = get_room_devices_query(self._room_ids, start, limit)
        return self._generate_device_list(select, devices, only_update=True)

    def get_remote_devices(
        self,
        devices: dict[str, VimarDevice] | None = None,
        start: int | None = None,
        limit: int | None = None,
    ):
        """Get all devices that can be triggered remotely (includes scenes)."""
        if devices is None:
            devices = {}
        if len(devices) == 0:
            _LOGGER.debug(
                "get_remote_devices started - from %d to %d",
                start,
                (start or 0) + (limit or 0),
            )

        start, limit = self._sanitize_limits(start, limit)
        select = get_remote_devices_query(start, limit)
        return self._generate_device_list(select, devices)

    def _sanitize_limits(self, start: int | None, limit: int | None):
        """Check for sane values in start and limit."""
        if limit is None or limit > MAX_ROWS_PER_REQUEST or limit <= 0:
            limit = MAX_ROWS_PER_REQUEST
        if start is None or start < 0:
            start = 0
        return start, limit

    def _generate_device_list(
        self,
        select: str,
        devices: dict[str, VimarDevice] | None = None,
        only_update: bool = False
    ):
        """Generate device list from SQL query."""
        if devices is None:
            devices = {}

        payload = self._request_vimar_sql(select)
        if payload is None:
            return None

        for device in payload:
            object_id = device["object_id"]

            if object_id not in devices:
                if only_update:
                    continue

                deviceItem: VimarDevice = {
                    "room_ids": [],
                    "room_names": [],
                    "room_name": "",
                    "object_id": object_id,
                    "object_name": device["object_name"],
                    "object_type": device["object_type"],
                    "status": {},
                    "device_type": "",
                    "device_class": "",
                    "device_friendly_name": "",
                    "icon": "",
                }
                devices[object_id] = deviceItem
            else:
                deviceItem = devices[object_id]

            # Update status
            if device["status_name"] != "":
                deviceItem["status"][device["status_name"]] = {
                    "status_id": device["status_id"],
                    "status_value": device["status_value"],
                }
                if "status_range" in device:
                    deviceItem["status"][device["status_name"]]["status_range"] = device["status_range"]

            # Update room info
            if device["room_ids"] is not None and device["room_ids"] != "":
                room_ids = []
                room_names = []
                for roomId in device["room_ids"].split(","):
                    if roomId and roomId in self._rooms:
                        room = self._rooms[roomId]
                        room_ids.append(roomId)
                        room_names.append(room["name"])

                deviceItem["room_ids"] = room_ids
                deviceItem["room_names"] = room_names
                deviceItem["room_name"] = room_names[0] if room_names else ""

        return devices, len(payload)

    def get_room_ids(self):
        """Load main rooms - later used in get_room_devices."""
        if self._room_ids is not None:
            return self._room_ids

        _LOGGER.debug("get_main_groups start")
        select = get_room_ids_query()
        payload = self._request_vimar_sql(select)

        if payload is None:
            return None

        _LOGGER.debug("get_room_ids ends - payload: %s", str(payload))
        roomIds = []
        rooms = {}

        for group in payload:
            room_id = str(group["id"])
            roomIds.append(room_id)
            rooms[room_id] = {
                "id": room_id,
                "name": str(group["name"]),
            }

        self._rooms = rooms
        self._room_ids = ",".join(roomIds)
        _LOGGER.info("get_room_ids ends - found %d rooms", len(roomIds))

        return self._room_ids

    def _request_vimar_sql(self, select: str):
        """Build and execute SQL request.

        PATCH #2: Changed error handling from ERROR+relogin to WARNING+return None.
        """
        select = (
            select.replace("\r\n", " ")
            .replace("\n", " ")
            .replace('"', "&apos;")
            .replace("'", "&apos;")
        )

        post = (
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            '<soapenv:Body><service-databasesocketoperation xmlns="urn:xmethods-dpadws">'
            "<payload>NO-PAYLOAD</payload>"
            "<hashcode>NO-HASCHODE</hashcode>"
            "<optionals>NO-OPTIONAL</optionals>"
            "<callsource>WEB-DOMUSPAD_SOAP</callsource>"
            f"<sessionid>{self._session_id}</sessionid>"
            "<waittime>5</waittime>"
            "<function>DML-SQL</function><type>SELECT</type>"
            f"<statement>{select}</statement><statement-len>{len(select)}</statement-len>"
            "</service-databasesocketoperation></soapenv:Body></soapenv:Envelope>"
        )

        response = self._request_vimar_soap(post)
        if response is None:
            _LOGGER.warning("Unparseable response from SQL")
            _LOGGER.info("Erroneous SQL: %s", select)
            return None

        if response is False:
            return None

        payload = response.find(".//payload")
        if payload is None:
            _LOGGER.warning("Empty payload from SQL")
            return None

        parsed_data = parse_sql_payload(payload.text)
        if parsed_data is None:
            _LOGGER.warning(
                "Received invalid data from SQL: %s from post: %s",
                ElementTree.tostring(response, encoding="unicode"),
                post,
            )
        return parsed_data

    def _request_vimar_soap(self, post: str):
        """Execute SOAP request."""
        headers = {
            "SOAPAction": "dbSoapRequest",
            "SOAPServer": "",
            "Content-Type": 'text/xml; charset="UTF-8"',
            "Expect": "",
        }
        return self._request_vimar(post, "cgi-bin/dpadws", headers)

    def _request_vimar(self, post: str, path: str, headers: dict):
        """Prepare call to Vimar webserver."""
        url = f"{self._connection._schema}://{self._connection._host}:{self._connection._port}/{path}"
        response = self._connection._request(url, post, headers)

        if response is None or response is False:
            return response

        return self._connection._parse_xml(response)


class VimarProject:
    """Container that holds all Vimar devices and states."""

    def __init__(self, link: VimarLink, device_customizer_action=None):
        """Create new container to hold all states."""
        self._link = link
        self._device_customizer_action = device_customizer_action
        self._devices: dict[str, VimarDevice] = {}
        self._platforms_exists = {}
        self.global_channel_id = None

    @property
    def devices(self):
        """Return all devices in current project."""
        return self._devices

    def update(self, forced=False):
        """Get all devices from Vimar webserver, update states only."""
        if self._devices is None:
            self._devices = {}

        devices_count = len(self._devices)

        self._devices, state_count = self._link.get_paged_results(
            self._link.get_remote_devices, self._devices
        )

        # Parse device types on first run or forced update
        if devices_count != len(self._devices) or forced:
            self._link.get_room_ids()
            self._link.get_paged_results(self._link.get_room_devices, self._devices)
            self.check_devices()

        return self._devices

    def check_devices(self):
        """Parse device types and names to determine correct platform."""
        if not self._devices:
            return False

        for device_id, device in self._devices.items():
            self.parse_device_type(device)

        # FIX #11: use isEnabledFor() at runtime instead of module-level cached bool
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("check_devices end. Devices: %s", str(self._devices))
        return True

    def get_by_device_type(self, platform):
        """Filter devices by platform type."""
        return {k: v for (k, v) in self._devices.items() if v["device_type"] == platform}

    def platform_exists(self, platform):
        """Check if there are devices for a given platform."""
        return self._platforms_exists.get(platform, False)

    def parse_device_type(self, device):
        """Classify devices into supported groups based on their types and names."""
        device_type = DEVICE_TYPE_OTHERS
        device_class = None
        icon = "mdi:home-assistant"
        obj_type = device["object_type"]
        obj_name = device["object_name"].upper()

        # Main automation devices
        if obj_type == "CH_Main_Automation":
            if any(x in obj_name for x in ["VENTILATOR", "FANCOIL"]):
                device_type = DEVICE_TYPE_SWITCHES
                device_class = DEVICE_CLASS_SWITCH
                icon = ["mdi:fan", "mdi:fan-off"]
            elif "LAMPE" in obj_name:
                device_type = DEVICE_TYPE_LIGHTS
                device_class = DEVICE_CLASS_SWITCH
                icon = ["mdi:lightbulb-on", "mdi:lightbulb-off"]
            elif any(x in obj_name for x in ["STECKDOSE", "PULSANTE"]):
                device_type = DEVICE_TYPE_SWITCHES
                device_class = DEVICE_CLASS_OUTLET
                icon = ["mdi:power-plug", "mdi:power-plug-off"]
            elif any(x in obj_name for x in ["HEIZUNG", "HEIZKÖRPER"]):
                device_type = DEVICE_TYPE_SWITCHES
                device_class = DEVICE_CLASS_SWITCH
                icon = ["mdi:radiator", "mdi:radiator-off"]
            elif " IR " in obj_name:
                device_type = DEVICE_TYPE_SWITCHES
                device_class = DEVICE_CLASS_SWITCH
                icon = ["mdi:motion-sensor", "mdi:motion-sensor-off"]
                _LOGGER.debug("IR Sensor: %s / %s", obj_type, device["object_name"])
            else:
                device_type = DEVICE_TYPE_LIGHTS
                icon = "mdi:ceiling-light"

        # KNX generic devices
        elif obj_type in [
            "CH_KNX_GENERIC_ONOFF",
            "CH_KNX_GENERIC_TIME_S",
            "CH_KNX_RELE",
            "CH_KNX_GENERIC_ENABLE",
            "CH_KNX_GENERIC_RESET",
        ]:
            device_type = DEVICE_TYPE_SWITCHES
            device_class = DEVICE_CLASS_SWITCH
            icon = ["mdi:toggle-switch", "mdi:toggle-switch-off"]

        # Dimmers
        elif obj_type in [
            "CH_Dimmer_Automation",
            "CH_Dimmer_RGB",
            "CH_Dimmer_White",
            "CH_Dimmer_Hue",
        ]:
            device_type = DEVICE_TYPE_LIGHTS
            icon = ["mdi:speedometer", "mdi:speedometer-slow"]

        # Shutters/Covers
        elif obj_type in [
            "CH_ShutterWithoutPosition_Automation",
            "CH_ShutterBlindWithoutPosition_Automation",
            "CH_Shutter_Automation",
            "CH_Shutter_Slat_Automation",
            "CH_ShutterBlind_Automation",
        ]:
            if "FERNBEDIENUNG" in obj_name:
                device_type = DEVICE_TYPE_COVERS
                device_class = DEVICE_CLASS_WINDOW
                icon = ["mdi:window-closed-variant", "mdi:window-open-variant"]
            else:
                device_type = DEVICE_TYPE_COVERS
                device_class = DEVICE_CLASS_SHUTTER
                icon = ["mdi:window-shutter", "mdi:window-shutter-open"]

        # Climate devices
        elif obj_type in [
            "CH_Clima",
            "CH_HVAC_NoZonaNeutra",
            "CH_HVAC_RiscaldamentoNoZonaNeutra",
            "CH_Fancoil",
            "CH_HVAC",
        ]:
            device_type = DEVICE_TYPE_CLIMATES
            icon = "mdi:thermometer-lines"
            _LOGGER.debug("Climate: %s / %s", obj_type, device["object_name"])

        # Scenes
        elif obj_type == "CH_Scene":
            device_type = DEVICE_TYPE_SCENES
            icon = "hass:palette"
            _LOGGER.debug("Scene: %s / %s", obj_type, device["object_name"])

        # Power sensors
        elif obj_type in [
            "CH_Misuratore",
            "CH_Carichi_Custom",
            "CH_Carichi",
            "CH_Carichi_3F",
            "CH_KNX_GENERIC_POWER_KW",
        ]:
            device_type = DEVICE_TYPE_SENSORS
            device_class = DEVICE_CLASS_POWER
            icon = "mdi:chart-bell-curve-cumulative"

        # Counter sensors
        elif "CH_Contatore_" in obj_type.upper():
            device_type = DEVICE_TYPE_SENSORS
            icon = "mdi:pulse"

        # Temperature sensors
        elif obj_type == "CH_KNX_GENERIC_TEMPERATURE_C":
            device_type = DEVICE_TYPE_SENSORS
            device_class = DEVICE_CLASS_TEMPERATURE
            icon = "mdi:thermometer"

        # Wind sensors
        elif obj_type == "CH_KNX_GENERIC_WINDSPEED":
            device_type = DEVICE_TYPE_SENSORS
            device_class = DEVICE_CLASS_PRESSURE
            icon = "mdi:windsock"

        # Weather stations
        elif obj_type == "CH_WEATHERSTATION":
            device_type = DEVICE_TYPE_SENSORS
            icon = "mdi:weather-partly-snowy-rainy"

        # Audio/Media players
        elif obj_type == "CH_Audio":
            device_type = DEVICE_TYPE_MEDIA_PLAYERS
            icon = ["mdi:radio", "mdi:radio-off"]
            _LOGGER.debug("Audio: %s / %s", obj_type, device["object_name"])

        # Unsupported types
        elif obj_type in ["CH_SAI", "CH_Event", "CH_KNX_GENERIC_TIMEPERIODMIN"]:
            _LOGGER.debug("Unsupported: %s / %s", obj_type, device["object_name"])
        else:
            _LOGGER.warning("Unknown: %s / %s", obj_type, device["object_name"])

        # Set device properties
        friendly_name = self.format_name(device["object_name"])
        device["device_type"] = device_type
        device["device_class"] = device_class
        device["device_friendly_name"] = friendly_name
        device["icon"] = icon

        # Apply customizer
        if self._device_customizer_action:
            self._device_customizer_action(device)

        # Track platform count
        device_type = device["device_type"]
        self._platforms_exists[device_type] = self._platforms_exists.get(device_type, 0) + 1

    def format_name(self, name):
        """Format device name to remove unused terms."""
        parts = name.split(" ")

        if len(parts) >= 4:
            device_type, entity_number, room_name, *level_parts = parts
            level_name = " ".join(level_parts)
        elif len(parts) >= 2:
            device_type = parts[0]
            entity_number = ""
            room_name = ""
            level_name = " ".join(parts[1:])
        else:
            device_type = parts[0] if parts else ""
            entity_number = ""
            room_name = ""
            level_name = ""

        # Clean up device type
        replacements = {
            "LUCE": "",
            "TAPPARELLA": "",
            "ROLLLADEN": "",
            "F-FERNBEDIENUNG": "FENSTER",
            "VENTILATORE": "",
            "VENTILATOR": "",
            "STECKDOSE": "",
            "THERMOSTAT": "",
        }

        for old, new in replacements.items():
            if old == "LUCE" and device_type == "LICHT":
                continue
            device_type = device_type.replace(old, new)

        # Build final name
        parts = [level_name, room_name, device_type, entity_number]
        name = " ".join(p for p in parts if p)
        return name.title().strip()
