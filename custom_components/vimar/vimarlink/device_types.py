"""Vimar device classification tables (NO Home Assistant required).

Single source of truth for "which VIMAR ``CH_*`` object type is what". These
lists used to be written out by hand in three places that had to agree:

* ``vimarlink.parse_device_type`` - decides the platform and device class;
* ``const.ENERGY_METER_OBJECT_TYPES`` - decides which devices get the periodic
  GETVALUE refresh, carrying the comment "kept in sync with
  vimarlink.parse_device_type" (a comment where a shared constant belongs);
* ``sensor.VimarSensor.class_and_units`` - decides the unit and device class of
  each individual measurement.

Adding a new energy meter model meant editing all three, and forgetting one
produced a device that appears in Home Assistant but never refreshes, or
refreshes but shows no unit. Same list, one definition.

This module deliberately imports nothing. It sits at the bottom of the
dependency graph so that both the standalone ``vimarlink`` library and the
Home Assistant side (``const.py``) can import it without a cycle.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Platform names
# ---------------------------------------------------------------------------
# These match the Home Assistant platform strings, but they are plain str on
# purpose: the standalone library must not import homeassistant.

DEVICE_TYPE_LIGHTS = "light"
DEVICE_TYPE_COVERS = "cover"
DEVICE_TYPE_SWITCHES = "switch"
DEVICE_TYPE_CLIMATES = "climate"
DEVICE_TYPE_MEDIA_PLAYERS = "media_player"
DEVICE_TYPE_SCENES = "scene"
DEVICE_TYPE_FANS = "fan"
DEVICE_TYPE_SENSORS = "sensor"
DEVICE_TYPE_OTHERS = "other"
DEVICE_TYPE_ALARM = "alarm_control_panel"
DEVICE_TYPE_BINARY_SENSOR = "binary_sensor"

# ---------------------------------------------------------------------------
# Device classes
# ---------------------------------------------------------------------------
# Values of the Home Assistant device class enums, again as plain str.

DEVICE_CLASS_OUTLET = "outlet"
DEVICE_CLASS_SWITCH = "switch"
DEVICE_CLASS_SHUTTER = "shutter"
DEVICE_CLASS_WINDOW = "window"
DEVICE_CLASS_POWER = "power"
DEVICE_CLASS_TEMPERATURE = "temperature"
DEVICE_CLASS_WIND_SPEED = "wind_speed"

# ---------------------------------------------------------------------------
# Object type groups
# ---------------------------------------------------------------------------

KNX_SWITCH_OBJECT_TYPES = frozenset(
    {
        "CH_KNX_GENERIC_ONOFF",
        "CH_KNX_GENERIC_TIME_S",
        "CH_KNX_RELE",
        "CH_KNX_GENERIC_ENABLE",
        "CH_KNX_GENERIC_RESET",
    }
)

DIMMER_OBJECT_TYPES = frozenset(
    {
        "CH_Dimmer_Automation",
        "CH_Dimmer_RGB",
        "CH_Dimmer_White",
        "CH_Dimmer_Hue",
    }
)

COVER_OBJECT_TYPES = frozenset(
    {
        "CH_ShutterWithoutPosition_Automation",
        "CH_ShutterBlindWithoutPosition_Automation",
        "CH_Shutter_Automation",
        "CH_Shutter_Slat_Automation",
        "CH_ShutterBlind_Automation",
    }
)

CLIMATE_OBJECT_TYPES = frozenset(
    {
        "CH_Clima",
        "CH_HVAC_NoZonaNeutra",
        "CH_HVAC_RiscaldamentoNoZonaNeutra",
        "CH_Fancoil",
        "CH_HVAC",
        "CH_HVAC_FanCoil",
        "CH_HVAC_FanCoilWithNeutralZone",
    }
)

#: Energy meters. Also the devices that need the periodic GETVALUE refresh:
#: the firmware only updates their stored value when explicitly asked (see
#: ``const.CONF_ENERGY_REFRESH_INTERVAL``).
ENERGY_METER_OBJECT_TYPES = frozenset(
    {
        "CH_Misuratore",
        "CH_Carichi",
        "CH_Carichi_Custom",
        "CH_Carichi_3F",
        "CH_KNX_GENERIC_POWER_KW",
    }
)

KNX_TEMPERATURE_OBJECT_TYPES = frozenset({"CH_KNX_GENERIC_TEMPERATURE_C"})

KNX_WINDSPEED_OBJECT_TYPES = frozenset({"CH_KNX_GENERIC_WINDSPEED"})
