"""The device classification table has exactly one definition (NO HA required).

The same lists of VIMAR ``CH_*`` object types used to be written out by hand in
three files that had to agree:

  * ``vimarlink.parse_device_type``    - platform and device class;
  * ``const.ENERGY_METER_OBJECT_TYPES`` - which devices get the periodic
    GETVALUE refresh, carrying the comment "kept in sync with
    vimarlink.parse_device_type";
  * ``sensor.VimarSensor.class_and_units`` - unit and device class per reading.

Nothing enforced the "kept in sync" part. Adding a meter model to one and not
the others gives a device that shows up but never refreshes, or refreshes but
carries no unit. These tests check the lists against the code that consumes
them, so drifting apart is a test failure rather than a silent misbehaviour.
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink import VimarProject  # noqa: E402
from vimarlink.device_types import (  # noqa: E402
    CLIMATE_OBJECT_TYPES,
    COVER_OBJECT_TYPES,
    DEVICE_CLASS_POWER,
    DEVICE_CLASS_SWITCH,
    DEVICE_CLASS_TEMPERATURE,
    DEVICE_CLASS_WIND_SPEED,
    DEVICE_TYPE_CLIMATES,
    DEVICE_TYPE_COVERS,
    DEVICE_TYPE_LIGHTS,
    DEVICE_TYPE_SENSORS,
    DEVICE_TYPE_SWITCHES,
    DIMMER_OBJECT_TYPES,
    ENERGY_METER_OBJECT_TYPES,
    KNX_SWITCH_OBJECT_TYPES,
    KNX_TEMPERATURE_OBJECT_TYPES,
    KNX_WINDSPEED_OBJECT_TYPES,
)

pytestmark = pytest.mark.no_ha  # No HA required

ALL_GROUPS = {
    "KNX_SWITCH": KNX_SWITCH_OBJECT_TYPES,
    "DIMMER": DIMMER_OBJECT_TYPES,
    "COVER": COVER_OBJECT_TYPES,
    "CLIMATE": CLIMATE_OBJECT_TYPES,
    "ENERGY_METER": ENERGY_METER_OBJECT_TYPES,
    "KNX_TEMPERATURE": KNX_TEMPERATURE_OBJECT_TYPES,
    "KNX_WINDSPEED": KNX_WINDSPEED_OBJECT_TYPES,
}


def _classify(object_type, object_name="Dispositivo"):
    """Run the real classifier over one object type."""
    project = VimarProject.__new__(VimarProject)
    project._device_customizer_action = None
    project._platforms_exists = {}
    device = {"object_type": object_type, "object_name": object_name}
    project.parse_device_type(device)
    return device


# ---------------------------------------------------------------------------
# The table and the classifier agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("object_type", sorted(ENERGY_METER_OBJECT_TYPES))
def test_every_energy_meter_is_classified_as_a_power_sensor(object_type):
    """THE cross-check: the refresh list and the classifier share one list.

    A meter listed for the periodic GETVALUE but not recognised as a sensor
    would be refreshed and never displayed, or displayed and never refreshed.
    """
    device = _classify(object_type)

    assert device["device_type"] == DEVICE_TYPE_SENSORS
    assert device["device_class"] == DEVICE_CLASS_POWER


@pytest.mark.parametrize(
    ("group", "expected_type", "expected_class"),
    [
        (KNX_SWITCH_OBJECT_TYPES, DEVICE_TYPE_SWITCHES, DEVICE_CLASS_SWITCH),
        (DIMMER_OBJECT_TYPES, DEVICE_TYPE_LIGHTS, None),
        (COVER_OBJECT_TYPES, DEVICE_TYPE_COVERS, "shutter"),
        (CLIMATE_OBJECT_TYPES, DEVICE_TYPE_CLIMATES, None),
        (KNX_TEMPERATURE_OBJECT_TYPES, DEVICE_TYPE_SENSORS, DEVICE_CLASS_TEMPERATURE),
        (KNX_WINDSPEED_OBJECT_TYPES, DEVICE_TYPE_SENSORS, DEVICE_CLASS_WIND_SPEED),
    ],
)
def test_each_group_lands_on_the_platform_it_names(group, expected_type, expected_class):
    for object_type in sorted(group):
        device = _classify(object_type)
        assert device["device_type"] == expected_type, object_type
        assert device["device_class"] == expected_class, object_type


def test_an_anemometer_is_not_a_pressure_sensor():
    """Regression: parse_device_type gave wind speed the pressure class."""
    device = _classify("CH_KNX_GENERIC_WINDSPEED")

    assert device["device_class"] == DEVICE_CLASS_WIND_SPEED
    assert device["device_class"] != "pressure"


# ---------------------------------------------------------------------------
# The table is internally sound
# ---------------------------------------------------------------------------


def test_no_object_type_belongs_to_two_groups():
    """Overlap would make the classification depend on if/elif order."""
    seen: dict[str, str] = {}
    for name, group in ALL_GROUPS.items():
        for object_type in group:
            assert object_type not in seen, (
                f"{object_type} is in both {seen.get(object_type)} and {name}"
            )
            seen[object_type] = name


def test_the_groups_are_immutable():
    """A mutable module-level list is a shared object anyone can edit."""
    for name, group in ALL_GROUPS.items():
        assert isinstance(group, frozenset), name


def test_an_unknown_object_type_falls_through_to_other():
    device = _classify("CH_QualcosaDiNuovo")

    assert device["device_type"] == "other"


def test_device_types_imports_nothing():
    """It sits below both sides of the tree; any import risks a cycle.

    const.py imports it and vimarlink.py imports it, so an import back into
    either (or into homeassistant, which the standalone library must not need)
    would close a loop. Checked on the syntax tree, not on the text, so the
    word appearing in a comment does not count.
    """
    import ast

    import vimarlink.device_types as module

    with open(module.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    imported = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and not (isinstance(node, ast.ImportFrom) and node.module == "__future__")
    ]

    assert imported == [], [ast.unparse(node) for node in imported]
