"""Connection to vimar web server."""

from __future__ import annotations

import logging
import os
import ssl
import sys
import xml.etree.ElementTree as xmlTree
from collections.abc import Callable
from typing import TypedDict
from xml.etree import ElementTree

import requests
import urllib3
from requests import adapters
from requests.exceptions import HTTPError

# Suppress InsecureRequestWarning when using self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Import device type constants - fallback to local definitions for standalone use
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
    # Standalone mode (e.g., examples/example.py) - define constants locally
    DEVICE_TYPE_LIGHTS = "light"
    DEVICE_TYPE_COVERS = "cover"
    DEVICE_TYPE_SWITCHES = "switch"
    DEVICE_TYPE_CLIMATES = "climate"
    DEVICE_TYPE_MEDIA_PLAYERS = "media_player"
    DEVICE_TYPE_SCENES = "scene"
    DEVICE_TYPE_SENSORS = "sensor"
    DEVICE_TYPE_OTHERS = "other"

_LOGGER = logging.getLogger(__name__)
_LOGGER_isDebug = _LOGGER.isEnabledFor(logging.DEBUG)
MAX_ROWS_PER_REQUEST = 300

# from homeassistant/components/switch/__init__.py
DEVICE_CLASS_OUTLET = "outlet"
DEVICE_CLASS_SWITCH = "switch"
# from homeassistant/components/cover/__init__.py
DEVICE_CLASS_SHUTTER = "shutter"
DEVICE_CLASS_WINDOW = "window"
# from homeassistant/const.py
DEVICE_CLASS_POWER = "power"
DEVICE_CLASS_TEMPERATURE = "temperature"
DEVICE_CLASS_PRESSURE = "pressure"

SSL_IGNORED = False