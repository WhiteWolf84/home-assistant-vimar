"""Entities name their function, not their device (Home Assistant required).

`VimarEntity` used to define `name` as a property returning `device_name` -
literally the same string it already publishes as the device name in
`device_info`. With `has_entity_name` left at its default of False, Home
Assistant treats that string as the entity's full name AND as the last piece of
the generated entity_id, which it prefixes with area and device. The device
token therefore appeared twice: `cover.bagnetto_tapparella_tapparella`.

The contract now is the Home Assistant one:

  * `has_entity_name` is True everywhere, so HA composes area + device + entity;
  * an entity that IS its device (climate, cover, switch, scene) names nothing;
  * an entity that measures one quantity (sensor) names only the quantity,
    never the room and never the device.

The trap these tests exist to catch: `_attr_name` is only ever read through
`Entity.name`. Re-adding a `name` property to `VimarEntity` or to any subclass
silently shadows `_attr_name` and restores the old behaviour with no error
anywhere - the entity_ids just start duplicating again on the next
"Recreate entity IDs".

`unique_id` is asserted alongside because it must NOT move with the naming: a
changed unique_id makes HA register a new entity and orphan its history.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.climate import VimarClimate  # noqa: E402
from custom_components.vimar.cover import VimarCover  # noqa: E402
from custom_components.vimar.scene import VimarScene  # noqa: E402
from custom_components.vimar.sensor import (  # noqa: E402
    VimarClimateTempSensor,
    VimarSensor,
)
from custom_components.vimar.switch import VimarSwitch  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required


def _device(object_type, status):
    """A device named after its FUNCTION, sitting in a room of its own name.

    This is what device_name_from_object_name() now produces for
    "TAPPARELLA BAGNETTO" in Bagnetto, and it is the shape that makes the
    entity-side contract below testable: if an entity re-published the device
    name, area + device + entity would read "Bagnetto Tapparella Tapparella".
    """
    return {
        "object_id": "721",
        "object_type": object_type,
        "object_name": "TAPPARELLA BAGNETTO",
        "device_friendly_name": "Tapparella",
        "room_friendly_name": "Bagnetto",
        "device_class": None,
        "icon": "",
        "status": status,
    }


def _coordinator(device):
    coordinator = MagicMock()
    coordinator.vimarproject.devices = {"721": device}
    coordinator.entity_unique_id_prefix = "casa"
    return coordinator


def _entity(cls, object_type, status, *args):
    device = _device(object_type, status)
    entity = cls(_coordinator(device), 721, *args)
    entity._device = device
    return entity


ON_OFF = {"on/off": {"status_id": "722", "status_value": "0", "status_range": ""}}
SHUTTER = {"up/down": {"status_id": "722", "status_value": "0", "status_range": ""}}
THERMOSTAT = {
    "temperatura_misurata": {"status_id": "722", "status_value": "21.5", "status_range": ""}
}
METER = {"dynamic_mode": {"status_id": "722", "status_value": "1", "status_range": "min=0|max=1"}}


PRIMARY_ENTITIES = [
    pytest.param(VimarCover, "CH_ShutterWithoutPosition_Automation", SHUTTER, id="cover"),
    pytest.param(VimarSwitch, "CH_Main_Automation", ON_OFF, id="switch"),
    pytest.param(VimarScene, "CH_Scene", ON_OFF, id="scene"),
    pytest.param(VimarClimate, "CH_HVAC_NoZonaNeutra", THERMOSTAT, id="climate"),
]


@pytest.mark.parametrize(("cls", "object_type", "status"), PRIMARY_ENTITIES)
def test_primary_entities_are_their_device(cls, object_type, status):
    """_attr_name None means "the entity IS the device" - HA supplies the name."""
    entity = _entity(cls, object_type, status)

    assert entity.has_entity_name is True
    assert entity.name is None


@pytest.mark.parametrize(("cls", "object_type", "status"), PRIMARY_ENTITIES)
def test_primary_entities_never_repeat_the_device_name(cls, object_type, status):
    """The regression itself: entity name must not be the device name again."""
    entity = _entity(cls, object_type, status)

    assert entity.device_info["name"] == "Tapparella"
    assert entity.name != entity.device_info["name"]


def test_sensor_names_only_the_quantity():
    """Not "Bagnetto Dynamic Mode": the room is HA's job, not the entity's."""
    sensor = _entity(VimarSensor, "CH_Misuratore", METER, "dynamic_mode")

    assert sensor.has_entity_name is True
    assert sensor.name == "Dynamic Mode"
    assert "Bagnetto" not in sensor.name  # the room is the area's job


def test_companion_temperature_sensor_names_only_the_quantity():
    sensor = _entity(
        VimarClimateTempSensor, "CH_HVAC_NoZonaNeutra", THERMOSTAT, "temperatura_misurata"
    )

    assert sensor.has_entity_name is True
    assert sensor.name == "Temperatura"


def test_unique_ids_did_not_move_with_the_naming():
    """A changed unique_id orphans the entity's history. These are the old ones."""
    cover = _entity(VimarCover, "CH_ShutterWithoutPosition_Automation", SHUTTER)
    sensor = _entity(VimarSensor, "CH_Misuratore", METER, "dynamic_mode")
    temp = _entity(
        VimarClimateTempSensor, "CH_HVAC_NoZonaNeutra", THERMOSTAT, "temperatura_misurata"
    )

    assert cover.unique_id == "vimar_casa_cover_721"
    assert sensor.unique_id == "vimar_casa_sensor_721-722"
    assert temp.unique_id == "vimar_casa_sensor_721-temp"


def test_device_name_is_still_available_for_logging():
    """The property the log lines moved to when `name` was removed."""
    cover = _entity(VimarCover, "CH_ShutterWithoutPosition_Automation", SHUTTER)

    assert cover.device_name == "Tapparella"
