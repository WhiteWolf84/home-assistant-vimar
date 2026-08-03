"""Sensors follow the Home Assistant sensor contract (Home Assistant required).

`VimarSensor` overrode `state` and `unit_of_measurement`, both declared @final
by `SensorEntity`. Those two properties are where HA does unit conversion, the
user's per-entity unit override, display precision and the string -> number
conversion; overriding them replaced that whole pipeline with a raw string
straight from the webserver.

Underneath, three unit/device-class pairs were ones HA rejects, which is not
cosmetic: an invalid pair logs a warning on every start and disables the unit
handling for that entity.

  * illuminance in "lm" - HA only accepts lux;
  * wind speed with the *device's* class, which parse_device_type set to
    "pressure" - an anemometer reported as a pressure sensor;
  * reactive power reported as POWER in kW - so it looked like real power and
    could be added to the energy dashboard as such;
  * date/time readings as TIMESTAMP, which HA only accepts as a tz-aware
    datetime object, while we hand it a string (this one raised).

These tests pin the contract, not the individual values: the first two check
EVERY pair the classifier can produce against HA's own tables, so a future
mapping cannot reintroduce the same class of bug.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor.const import (
    DEVICE_CLASS_STATE_CLASSES,
    DEVICE_CLASS_UNITS,
    NON_NUMERIC_DEVICE_CLASSES,
    SensorDeviceClass,
    SensorStateClass,
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.sensor import (  # noqa: E402
    VimarSensor,
    state_class_for,
)

pytestmark = pytest.mark.integration  # Home Assistant required

METER = "CH_Misuratore"


def _sensor(measurement, object_type=METER, value="12.50", device_class=None):
    """Build a VimarSensor around one measurement, bypassing HA's platform."""
    device = {
        "object_id": "768",
        "object_type": object_type,
        "object_name": "Contatore",
        "device_class": device_class,
        "status": {measurement: {"status_id": "769", "status_value": value}},
    }

    coordinator = MagicMock()
    coordinator.vimarproject.devices = {"768": device}
    coordinator.entity_unique_id_prefix = "casa"

    sensor = VimarSensor(coordinator, 768, measurement)
    sensor._device = device
    return sensor


#: Every measurement name the classifier branches on, so the contract tests
#: below cover each branch rather than a lucky sample.
ALL_MEASUREMENTS = [
    ("energia_assoluta", METER),
    ("energia_parziale", METER),
    ("potenza_attiva", METER),
    ("potenza_reattiva", METER),
    ("corrente_fase_1", METER),
    ("reset_date", METER),
    ("temperature", "CH_WEATHERSTATION"),
    ("temperature_min", "CH_KNX_GENERIC_TEMPERATURE_C"),
    ("wind_speed", "CH_WEATHERSTATION"),
    ("wind_speed_max", "CH_KNX_GENERIC_WINDSPEED"),
    ("brightness", "CH_WEATHERSTATION"),
    ("its_raining", "CH_WEATHERSTATION"),
    ("contatore_assoluto", "CH_CONTATORE_IMPULSI"),
]


# ---------------------------------------------------------------------------
# The contract, checked against Home Assistant's own tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("measurement", "object_type"), ALL_MEASUREMENTS)
def test_every_unit_is_valid_for_its_device_class(measurement, object_type):
    """HA validates unit against device class and warns when they disagree."""
    sensor = _sensor(measurement, object_type)
    unit = sensor.native_unit_of_measurement
    device_class = sensor.device_class

    if device_class is None:
        return
    allowed = DEVICE_CLASS_UNITS.get(device_class)
    if allowed is None:  # device class with no unit restriction
        return
    assert unit in allowed, f"{measurement}: {unit!r} invalid for {device_class}"


@pytest.mark.parametrize(("measurement", "object_type"), ALL_MEASUREMENTS)
def test_every_state_class_is_possible_for_its_device_class(measurement, object_type):
    """An impossible pair makes HA log a warning and skip statistics."""
    sensor = _sensor(measurement, object_type)
    device_class = sensor.device_class
    state_class = sensor.state_class

    if device_class is None or state_class is None:
        return
    allowed = DEVICE_CLASS_STATE_CLASSES.get(device_class)
    if allowed is None:
        return
    assert state_class in allowed, f"{measurement}: {state_class} impossible for {device_class}"


@pytest.mark.parametrize(("measurement", "object_type"), ALL_MEASUREMENTS)
def test_non_numeric_device_classes_never_carry_a_unit(measurement, object_type):
    """HA raises ValueError for a unit on a non-numeric device class."""
    sensor = _sensor(measurement, object_type)

    if sensor.device_class in NON_NUMERIC_DEVICE_CLASSES:
        assert not sensor.native_unit_of_measurement


@pytest.mark.parametrize(("measurement", "object_type"), ALL_MEASUREMENTS)
def test_a_numeric_sensor_reports_a_number(measurement, object_type):
    """Whatever HA is told to expect, native_value must match it."""
    sensor = _sensor(measurement, object_type, value="12.50")

    if sensor.device_class is not None or sensor.native_unit_of_measurement is not None:
        assert isinstance(sensor.native_value, float)


# ---------------------------------------------------------------------------
# The @final overrides
# ---------------------------------------------------------------------------


def test_state_is_home_assistants_own():
    """THE regression: overriding @final state bypassed the whole pipeline."""
    assert VimarSensor.state is SensorEntity.state


def test_unit_of_measurement_is_home_assistants_own():
    """Overriding it discarded the user's per-entity unit choice."""
    assert VimarSensor.unit_of_measurement is SensorEntity.unit_of_measurement


def test_the_native_hooks_are_still_ours():
    """The supported way to feed the pipeline; these must NOT be inherited."""
    assert VimarSensor.native_value is not SensorEntity.native_value
    assert VimarSensor.native_unit_of_measurement is not SensorEntity.native_unit_of_measurement


# ---------------------------------------------------------------------------
# The individual mappings that were wrong
# ---------------------------------------------------------------------------


def test_illuminance_is_lux_not_lumen():
    sensor = _sensor("brightness", "CH_WEATHERSTATION")

    assert sensor.device_class is SensorDeviceClass.ILLUMINANCE
    assert sensor.native_unit_of_measurement == "lx"


def test_wind_speed_is_not_a_pressure_sensor():
    """device_class came from the device dict, where it was "pressure"."""
    sensor = _sensor("wind_speed", "CH_KNX_GENERIC_WINDSPEED", device_class="pressure")

    assert sensor.device_class is SensorDeviceClass.WIND_SPEED
    assert sensor.native_unit_of_measurement == "m/s"


def test_reactive_power_is_not_reported_as_real_power():
    sensor = _sensor("potenza_reattiva")

    assert sensor.device_class is SensorDeviceClass.REACTIVE_POWER
    assert sensor.native_unit_of_measurement == "kvar"


def test_active_power_stays_real_power():
    """The fix above must not have caught the active power branch too."""
    sensor = _sensor("potenza_attiva")

    assert sensor.device_class is SensorDeviceClass.POWER
    assert sensor.native_unit_of_measurement == "kW"


def test_a_date_reading_is_not_declared_a_timestamp():
    """TIMESTAMP requires a tz-aware datetime; we only ever have a string."""
    sensor = _sensor("reset_date", value="2026-08-03")

    assert sensor.device_class is not SensorDeviceClass.TIMESTAMP
    assert sensor.native_value == "2026-08-03"  # passed through untouched


def test_energy_is_a_total_increasing_counter():
    """What makes the energy dashboard work."""
    sensor = _sensor("energia_assoluta", value="1234.5")

    assert sensor.device_class is SensorDeviceClass.ENERGY
    assert sensor.native_unit_of_measurement == "kWh"
    assert sensor.state_class is SensorStateClass.TOTAL_INCREASING
    assert sensor.native_value == 1234.5


def test_temperature_now_records_statistics():
    """No state class meant no long-term history past the recorder purge."""
    sensor = _sensor("temperature", "CH_KNX_GENERIC_TEMPERATURE_C", value="21.30")

    assert sensor.device_class is SensorDeviceClass.TEMPERATURE
    assert sensor.state_class is SensorStateClass.MEASUREMENT


def test_an_unknown_device_class_string_is_dropped():
    """A bogus class from the device dict would make HA reject the entity."""
    sensor = _sensor("qualcosa", "CH_ALTRO", device_class="not-a-real-device-class")

    assert sensor.device_class is None


# ---------------------------------------------------------------------------
# native_value conversion
# ---------------------------------------------------------------------------


def test_a_non_numeric_reading_becomes_unknown_not_an_exception():
    """HA raises ValueError on a non-numeric state for a numeric sensor.

    One bad poll must show as unknown for that reading, not take the entity
    down with an exception in the state machine.
    """
    sensor = _sensor("potenza_attiva", value="n/a")

    assert sensor.native_value is None


def test_a_missing_reading_is_none():
    sensor = _sensor("potenza_attiva")
    sensor._device["status"] = {}

    assert sensor.native_value is None


def test_a_classless_reading_is_passed_through_verbatim():
    """Diagnostic values are not numbers and must not be forced into one."""
    sensor = _sensor("its_raining", "CH_WEATHERSTATION", value="0")

    assert sensor.device_class is None
    assert sensor.native_value == "0"


# ---------------------------------------------------------------------------
# state_class_for
# ---------------------------------------------------------------------------


def test_state_class_for_never_returns_an_impossible_pair():
    """Derived from HA's table, so it cannot go stale as HA evolves."""
    for device_class in SensorDeviceClass:
        state_class = state_class_for(device_class)
        if state_class is None:
            continue
        assert state_class in DEVICE_CLASS_STATE_CLASSES[device_class]


def test_state_class_for_has_no_opinion_without_a_device_class():
    assert state_class_for(None) is None


# ---------------------------------------------------------------------------
# One definition of the meter list, not three copies
# ---------------------------------------------------------------------------


def test_const_re_exports_the_library_table_rather_than_copying_it():
    """`is`, not `==`: a copy that happens to match today can drift tomorrow."""
    from custom_components.vimar import const
    from custom_components.vimar.vimarlink import device_types

    assert const.ENERGY_METER_OBJECT_TYPES is device_types.ENERGY_METER_OBJECT_TYPES


def test_the_sensor_classifier_uses_the_same_meter_list():
    """It used to repeat the five CH_* names inline."""
    from custom_components.vimar.vimarlink.device_types import ENERGY_METER_OBJECT_TYPES

    for object_type in ENERGY_METER_OBJECT_TYPES:
        sensor = _sensor("energia_assoluta", object_type)
        assert sensor.device_class is SensorDeviceClass.ENERGY, object_type
