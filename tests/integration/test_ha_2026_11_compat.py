"""Home Assistant 2026.11 API: native climate temperatures.

2026.11 logs a warning at import for every custom climate class still providing
current_temperature / target_temperature / temperature_unit (removed in 2027.11).
The values shown must not change: the same thermostat reads the same in 2026.10
and in 2026.11.
"""

import importlib.util
import logging
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from homeassistant.components.climate import ClimateEntity
from homeassistant.util.unit_system import METRIC_SYSTEM

from custom_components.vimar import climate
from custom_components.vimar.climate import VimarClimate

pytestmark = pytest.mark.integration  # Home Assistant required

HA_2026_11 = hasattr(ClimateEntity, "native_temperature_unit")
CLIMATE_PY = climate.__file__


def _climate(setpoint="25.00", measured="23.1", unita=None):
    """Build a CH_HVAC_NoZonaNeutra VimarClimate in cool/manual on a metric system."""
    entity = VimarClimate.__new__(VimarClimate)
    status = {
        "funzionamento": {"status_id": "F", "status_value": "1"},
        "regolazione": {"status_id": "R", "status_value": "1"},
        "setpoint": {"status_id": "S", "status_value": setpoint},
        "temperatura_misurata": {"status_id": "T", "status_value": measured},
        "stato_principale_condizionamento on/off": {"status_id": "C", "status_value": "1"},
        "stato_principale_riscaldamento on/off": {"status_id": "H", "status_value": "0"},
    }
    if unita is not None:
        status["unita"] = {"status_id": "U", "status_value": unita}
    entity._device = {"object_type": "CH_HVAC_NoZonaNeutra", "status": status}
    entity._device_id = "462"
    entity.entity_id = "climate.test_termostato"
    entity._logger = MagicMock()
    entity.hass = MagicMock()
    entity.hass.config.units = METRIC_SYSTEM
    return entity


@pytest.mark.skipif(not HA_2026_11, reason="Home Assistant 2026.11+ only")
def test_climate_class_provides_no_deprecated_temperature(caplog):
    """Defining VimarClimate logs no 'provides the deprecated' warning.

    The warning is emitted once, when the class is created, so the module is
    executed again from its file under its own name, without replacing the
    module the other tests use.
    """
    spec = importlib.util.spec_from_file_location("custom_components.vimar.climate", CLIMATE_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with caplog.at_level(logging.WARNING):
        spec.loader.exec_module(module)
    deprecated = [r.getMessage() for r in caplog.records if "deprecated" in r.getMessage()]
    assert deprecated == []


def test_climate_values_unchanged():
    """Same setpoint, temperature, range and step as before the rename."""
    entity = _climate()
    state = entity.state_attributes
    capabilities = entity.capability_attributes
    assert state["temperature"] == 25.0
    assert state["current_temperature"] == 23.1
    assert capabilities["min_temp"] == 7
    assert capabilities["max_temp"] == 35
    assert capabilities["target_temp_step"] == 0.1
    assert entity.state == "cool"


def test_climate_fahrenheit_device_converted_once():
    """A device reporting Fahrenheit (unita=1) is converted once, to Celsius."""
    entity = _climate(setpoint="77.00", measured="68.0", unita="1")
    state = entity.state_attributes
    assert state["temperature"] == 25.0
    assert state["current_temperature"] == 20.0
