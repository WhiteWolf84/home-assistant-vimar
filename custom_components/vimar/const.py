"""Constant for Vimar component."""

import logging

_LOGGER = logging.getLogger(__package__)
PACKAGE_NAME = __package__

# Home-Assistant specific consts
DOMAIN = "vimar"
DOMAIN_CONFIG_YAML = "vimar_default_config"

CONF_TITLE = "title"
CONF_SCHEMA = "schema"
CONF_SECURE = "secure"
CONF_CERTIFICATE = "certificate"
CONF_GLOBAL_CHANNEL_ID = "global_channel_id"
CONF_IGNORE_PLATFORM = "ignore"

DEFAULT_USERNAME = "admin"
DEFAULT_SCHEMA = "https"
DEFAULT_SECURE = True
DEFAULT_PORT = 443
DEFAULT_CERTIFICATE = "rootCA.VIMAR.crt"
DEFAULT_VERIFY_SSL = True
DEFAULT_TIMEOUT = 6
DEFAULT_SCAN_INTERVAL = 8


# Device overrides
CONF_OVERRIDE = "device_override"

CONF_ENTITY_PREFIX = "entity_prefix"

CONF_USE_VIMAR_NAMING = "use_vimar_naming"
CONF_FRIENDLY_NAME_ROOM_NAME_AT_BEGIN = "friendly_name_room_name_at_begin"
CONF_DEVICES_LIGHTS_RE = "devices_as_lights_re"
CONF_DEVICES_BINARY_SENSOR_RE = "devices_as_binary_sensor_re"
CONF_DELETE_AND_RELOAD_ALL_ENTITIES = "delete_and_reload_all_entities"

# vimar integration specific const

DEVICE_TYPE_LIGHTS = "light"
DEVICE_TYPE_COVERS = "cover"
DEVICE_TYPE_SWITCHES = "switch"
DEVICE_TYPE_CLIMATES = "climate"
DEVICE_TYPE_MEDIA_PLAYERS = "media_player"
DEVICE_TYPE_SCENES = "scene"
DEVICE_TYPE_FANS = "fan"
DEVICE_TYPE_SENSORS = "sensor"
DEVICE_TYPE_OTHERS = "other"


VIMAR_CLIMATE_OFF = "VIMAR_CLIMATE_OFF"
VIMAR_CLIMATE_AUTO = "VIMAR_CLIMATE_AUTO"
VIMAR_CLIMATE_MANUAL = "VIMAR_CLIMATE_MANUAL"
VIMAR_CLIMATE_HEAT = "VIMAR_CLIMATE_HEAT"
VIMAR_CLIMATE_COOL = "VIMAR_CLIMATE_COOL"


VIMAR_CLIMATE_OFF_I = "0"
VIMAR_CLIMATE_AUTO_I = "8"
VIMAR_CLIMATE_MANUAL_I = "6"

VIMAR_CLIMATE_HEAT_I = "0"
VIMAR_CLIMATE_COOL_I = "1"

VIMAR_CLIMATE_OFF_II = "6"
VIMAR_CLIMATE_AUTO_II = "0"
VIMAR_CLIMATE_MANUAL_II = "1"

VIMAR_CLIMATE_NEUTRAL_II = "0"
VIMAR_CLIMATE_HEAT_II = "2"
VIMAR_CLIMATE_COOL_II = "1"

AVAILABLE_PLATFORMS = {
    DEVICE_TYPE_LIGHTS: "light",
    DEVICE_TYPE_COVERS: "cover",
    DEVICE_TYPE_SWITCHES: "switch",
    DEVICE_TYPE_CLIMATES: "climate",
    DEVICE_TYPE_MEDIA_PLAYERS: "media_player",
    DEVICE_TYPE_SCENES: "scene",
    # DEVICE_TYPE_FANS: 'fan',
    DEVICE_TYPE_SENSORS: "sensor",
    # DEVICE_TYPE_OTHERS: ''
}
DEVICE_TYPE_BINARY_SENSOR = "binary_sensor"
PLATFORMS = [
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_LIGHTS,
    DEVICE_TYPE_COVERS,
    DEVICE_TYPE_SWITCHES,
    DEVICE_TYPE_CLIMATES,
    DEVICE_TYPE_MEDIA_PLAYERS,
    DEVICE_TYPE_SCENES,
    DEVICE_TYPE_SENSORS,
]


# VIMAR_UNIQUE_ID = "vimar_unique_id"
