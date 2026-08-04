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
    not_a_reading,
    state_class_for,
)

pytestmark = pytest.mark.integration  # Home Assistant required

METER = "CH_Misuratore"


def _sensor(measurement, object_type=METER, value="12.50", device_class=None, status_range=""):
    """Build a VimarSensor around one measurement, bypassing HA's platform."""
    device = {
        "object_id": "768",
        "object_type": object_type,
        "object_name": "Contatore",
        "device_class": device_class,
        "status": {
            measurement: {
                "status_id": "769",
                "status_value": value,
                "status_range": status_range,
            }
        },
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
    ("energia_totale_consumo", "CH_Carichi_Custom"),
    ("potenza_attiva", METER),
    ("potenza_reattiva", METER),
    ("corrente_fase_1", "CH_Carichi_3F"),
    ("fase", "CH_Carichi_Custom"),
    ("custom_datetime", "CH_Carichi_Custom"),
    ("campo_mai_visto", "CH_Carichi_Custom"),
    ("temperature", "CH_WEATHERSTATION"),
    ("temperature_min", "CH_KNX_GENERIC_TEMPERATURE_C"),
    ("temperature_max", "CH_WEATHERSTATION"),
    ("temperature_alarm", "CH_WEATHERSTATION"),
    ("temperature_reset", "CH_WEATHERSTATION"),
    ("temperature_request_minmax", "CH_WEATHERSTATION"),
    ("wind_speed", "CH_WEATHERSTATION"),
    ("wind_speed_max", "CH_KNX_GENERIC_WINDSPEED"),
    ("wind_speed_alarm", "CH_WEATHERSTATION"),
    ("wind_speed_reset", "CH_WEATHERSTATION"),
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
# Fields that are not readings at all
#
# The rules match on parts of the field name, because the name is all the
# webserver gives us. The webserver also names its flags after the quantity
# they relate to, so "what does this measure?" asked first claims them:
# `temperature_alarm` (a 0/1 flag) was published as **0 °C**, which is
# indistinguishable from a real freezing reading, and `fase` (the phase type,
# "monofase"/"trifase") was published in ampere. Found on real hardware.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "measurement",
    [
        "temperature_alarm",
        "temperature_reset",
        "temperature_request_minmax",
        "wind_speed_alarm",
        "wind_speed_reset",
        "wind_speed_request_minmax",
        "reset_history",
    ],
)
def test_a_flag_is_not_dressed_up_as_a_measurement(measurement):
    """THE regression: a 0/1 flag published as 0 °C or 0 m/s."""
    sensor = _sensor(measurement, "CH_WEATHERSTATION", value="0")

    assert sensor.device_class is None
    assert sensor.native_unit_of_measurement is None
    assert sensor.state_class is None
    assert sensor.native_value == "0"  # shown as-is, not converted


@pytest.mark.parametrize("value", ["monofase", "trifase"])
def test_the_phase_type_is_text_not_a_current(value):
    """`fase` is the installation's phase type, and matched the current rule."""
    sensor = _sensor("fase", "CH_Carichi_Custom", value=value)

    assert sensor.device_class is None
    assert sensor.native_unit_of_measurement is None
    assert sensor.native_value == value


@pytest.mark.parametrize(
    ("measurement", "object_type", "expected_class"),
    [
        ("temperature", "CH_WEATHERSTATION", SensorDeviceClass.TEMPERATURE),
        ("temperature_min", "CH_WEATHERSTATION", SensorDeviceClass.TEMPERATURE),
        ("temperature_max", "CH_WEATHERSTATION", SensorDeviceClass.TEMPERATURE),
        ("wind_speed", "CH_WEATHERSTATION", SensorDeviceClass.WIND_SPEED),
        ("wind_speed_max", "CH_WEATHERSTATION", SensorDeviceClass.WIND_SPEED),
        ("corrente_fase_1", "CH_Carichi_3F", SensorDeviceClass.CURRENT),
        ("energia_totale_consumo", "CH_Carichi_Custom", SensorDeviceClass.ENERGY),
    ],
)
def test_the_guard_does_not_declass_real_readings(measurement, object_type, expected_class):
    """The risk of a guard like this is catching too much. `_min`/`_max` are
    genuine readings, and `corrente_fase_1` is a genuine per-phase current -
    the rule `fase` was written for."""
    assert _sensor(measurement, object_type).device_class is expected_class


def test_an_unknown_meter_field_is_not_guessed_to_be_power():
    """Anything unrecognised on a meter used to fall through to power in kW.

    A wrong unit that looks plausible cannot be spotted; a missing one can.
    """
    sensor = _sensor("campo_mai_visto", "CH_Carichi_Custom", value="123")

    assert sensor.device_class is None
    assert sensor.native_unit_of_measurement is None


def test_not_a_reading_matches_whole_parts_only():
    """Substring matching is the bug this guard exists to contain; it must not
    reintroduce it by matching inside a word."""
    assert not_a_reading("temperature_alarm")
    assert not_a_reading("fase")
    # "alarm"/"reset" as a fragment of a longer word must NOT match
    assert not not_a_reading("resettabile")
    assert not not_a_reading("alarming_power")
    assert not not_a_reading("temperature")
    assert not not_a_reading("corrente_fase_1")


# ---------------------------------------------------------------------------
# Every field of a real installation
#
# Read from live hardware (01945, firmware 2.x) rather than invented, after a
# first attempt at the guard above stripped the unit from six genuine power
# readings. Each entry is what the field was measured to be, not what its name
# suggests:
#
#   * the six `*_totale` flows are instantaneous power in kW - each has a
#     cumulative `energia_totale_*` counterpart in kWh, and they add up
#     (consumo = autoconsumo + prelievo), which only holds for power;
#   * `produzione_presente` names a flow but never moved off 1 in a week,
#     while `produzione_totale` took 528 distinct values from -3.94 to 5.05;
#   * `dynamic_mode` and `reset_history` declare `min=0|max=1` themselves.
# ---------------------------------------------------------------------------

REAL_FIELDS = [
    # (field, object_type, status_range, unit, device class, state class)
    (
        "energia_assoluta",
        "CH_Misuratore",
        "",
        "kWh",
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL_INCREASING,
    ),
    (
        "energia_parziale",
        "CH_Misuratore",
        "",
        "kWh",
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL_INCREASING,
    ),
    (
        "potenza_attiva",
        "CH_Misuratore",
        "min=-2147483648|max=2147483648",
        "kW",
        SensorDeviceClass.POWER,
        SensorStateClass.MEASUREMENT,
    ),
    (
        "potenza_reattiva",
        "CH_Misuratore",
        "min=-2147483648|max=2147483648",
        "kvar",
        SensorDeviceClass.REACTIVE_POWER,
        SensorStateClass.MEASUREMENT,
    ),
    ("dynamic_mode", "CH_Misuratore", "min=0|max=1", None, None, None),
    ("reset_history", "CH_Misuratore", "min=0|max=1", None, None, None),
    ("reset_partial", "CH_Misuratore", "", None, None, None),
    (
        "energia_totale_autoconsumo",
        "CH_Carichi_Custom",
        "",
        "kWh",
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL,
    ),
    (
        "energia_totale_consumo",
        "CH_Carichi_Custom",
        "",
        "kWh",
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL,
    ),
    (
        "energia_totale_immissione",
        "CH_Carichi_Custom",
        "",
        "kWh",
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL,
    ),
    (
        "energia_totale_prelievo",
        "CH_Carichi_Custom",
        "",
        "kWh",
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL,
    ),
    (
        "energia_totale_produzione",
        "CH_Carichi_Custom",
        "",
        "kWh",
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL,
    ),
    (
        "energia_totale_scambio",
        "CH_Carichi_Custom",
        "",
        "kWh",
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL,
    ),
    (
        "autoconsumo_totale",
        "CH_Carichi_Custom",
        "",
        "kW",
        SensorDeviceClass.POWER,
        SensorStateClass.MEASUREMENT,
    ),
    (
        "consumo_totale",
        "CH_Carichi_Custom",
        "",
        "kW",
        SensorDeviceClass.POWER,
        SensorStateClass.MEASUREMENT,
    ),
    (
        "immissione_totale",
        "CH_Carichi_Custom",
        "",
        "kW",
        SensorDeviceClass.POWER,
        SensorStateClass.MEASUREMENT,
    ),
    (
        "prelievo_totale",
        "CH_Carichi_Custom",
        "",
        "kW",
        SensorDeviceClass.POWER,
        SensorStateClass.MEASUREMENT,
    ),
    (
        "produzione_totale",
        "CH_Carichi_Custom",
        "",
        "kW",
        SensorDeviceClass.POWER,
        SensorStateClass.MEASUREMENT,
    ),
    (
        "scambio_totale",
        "CH_Carichi_Custom",
        "",
        "kW",
        SensorDeviceClass.POWER,
        SensorStateClass.MEASUREMENT,
    ),
    ("produzione_presente", "CH_Carichi_Custom", "", None, None, None),
    ("fase", "CH_Carichi_Custom", "", None, None, None),
    ("custom_date", "CH_Carichi_Custom", "", None, None, None),
    ("custom_time", "CH_Carichi_Custom", "", None, None, None),
    ("custom_datetime", "CH_Carichi_Custom", "", None, None, None),
    ("forzatura", "CH_Carichi_3F", "", None, None, None),
    ("funzionamento", "CH_Carichi_3F", "", None, None, None),
]


@pytest.mark.parametrize(
    ("field", "object_type", "status_range", "unit", "device_class", "state_class"),
    REAL_FIELDS,
    ids=[f"{row[1].removeprefix('CH_')}.{row[0]}" for row in REAL_FIELDS],
)
def test_a_real_installation_classifies_exactly_as_measured(
    field, object_type, status_range, unit, device_class, state_class
):
    sensor = _sensor(field, object_type, value="1.0", status_range=status_range)

    assert sensor.native_unit_of_measurement == unit
    assert sensor.device_class is device_class
    assert sensor.state_class is state_class


# ---------------------------------------------------------------------------
# Counted vs recomputed energy
#
# The load-control device recomputes its `energia_totale_*` aggregates once an
# hour instead of counting them, and the recomputed figure is sometimes lower
# than the previous one: measured on real hardware, `energia_totale_produzione`
# stepped back from 38922.32 to 38919.84 kWh, always at :59, eight times over
# 34 hours. The true meter registers on the same installation took 1128 samples
# in that period without a single step back.
#
# TOTAL_INCREASING is a promise that data does not keep. It never corrupted the
# statistics - HA only reads a drop as a counter reset below 90% of the previous
# value, and these are 0.006% - but it did log a warning after every restart
# telling the user to file a bug for correct behaviour.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flow", ["autoconsumo", "consumo", "immissione", "prelievo", "produzione", "scambio"]
)
def test_a_recomputed_total_is_not_declared_strictly_increasing(flow):
    sensor = _sensor(f"energia_totale_{flow}", "CH_Carichi_Custom", value="38919.84")

    assert sensor.device_class is SensorDeviceClass.ENERGY
    assert sensor.state_class is SensorStateClass.TOTAL


@pytest.mark.parametrize("register", ["energia_assoluta", "energia_parziale"])
def test_a_counted_register_stays_strictly_increasing(register):
    """The distinction is the point: a real counter keeps the stronger claim."""
    sensor = _sensor(register, METER, value="25108.51")

    assert sensor.state_class is SensorStateClass.TOTAL_INCREASING


def test_both_energy_state_classes_still_produce_the_same_statistics():
    """Why the switch costs no history: identical metadata, so the stored
    series carries on rather than being invalidated."""
    from homeassistant.components.sensor.recorder import DEFAULT_STATISTICS

    total = DEFAULT_STATISTICS[SensorStateClass.TOTAL]
    increasing = DEFAULT_STATISTICS[SensorStateClass.TOTAL_INCREASING]

    assert total.types == increasing.types == {"sum"}
    assert total.mean_type == increasing.mean_type


def test_the_power_flows_are_not_confused_with_their_energy_counterparts():
    """`consumo_totale` (kW now) and `energia_totale_consumo` (kWh total) are
    two different sensors on the same device; a rule matching "consumo" as a
    substring would collapse them."""
    power = _sensor("consumo_totale", "CH_Carichi_Custom")
    energy = _sensor("energia_totale_consumo", "CH_Carichi_Custom")

    assert power.device_class is SensorDeviceClass.POWER
    assert power.state_class is SensorStateClass.MEASUREMENT
    assert energy.device_class is SensorDeviceClass.ENERGY
    # TOTAL, not TOTAL_INCREASING: this one is recomputed hourly and does step
    # back. See the "counted vs recomputed" section below.
    assert energy.state_class is SensorStateClass.TOTAL


def test_a_field_the_device_limits_to_zero_or_one_is_never_a_measurement():
    """The webserver states the range itself; that beats guessing from a name."""
    sensor = _sensor("qualcosa_di_ignoto", METER, value="1", status_range="min=0|max=1")

    assert sensor.device_class is None
    assert sensor.native_unit_of_measurement is None


def test_a_wide_range_does_not_trigger_the_boolean_guard():
    assert not not_a_reading("potenza_attiva", "min=-2147483648|max=2147483648")
    assert not not_a_reading("consumo_totale", "")
    assert not not_a_reading("consumo_totale", None)


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
