"""SAI2 unique_ids survive a reprogramming of the control unit (HA required).

On 2026-10-05 the control unit was reprogrammed and the web server recreated
every SAI2 object under new IDs: "Reparto Giorno" went from 7560 to 11919.
Keyed on that ID, the area came back as a new entity with a "_2" entity_id,
and the old one - with its name, area, labels and device_class override - was
deleted. These tests pin the stable IDs (area/zone number on the control unit),
the registry migration to them, and the recovery of what that day lost.
"""

import os
import sys
from types import SimpleNamespace

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.alarm_control_panel import _sai2_area_indexes  # noqa: E402
from custom_components.vimar.binary_sensor import _guess_device_class  # noqa: E402
from custom_components.vimar.const import DOMAIN  # noqa: E402
from custom_components.vimar.sai2_ids import (  # noqa: E402
    async_migrate_sai2_unique_ids,
    sai2_area_unique_ids,
    sai2_zone_unique_ids,
)
from custom_components.vimar.vimarlink.device_queries import (  # noqa: E402
    get_sai2_groups_query,
    get_sai2_zones_query,
)
from custom_components.vimar.vimarlink.vimarlink import parse_sai2_index  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

PREFIX = "casa"
ALARM = "alarm_control_panel"
ZONE = "binary_sensor"


def _project(groups, zones=None, zone_to_group=None):
    return SimpleNamespace(
        sai2_groups=groups, sai2_zones=zones or {}, sai2_zone_to_group=zone_to_group or {}
    )


# After the reprogramming: only Giorno got a new ID.
GROUPS_NOW = {
    "7615": {"name": "Reparto Notte", "index": 2},
    "7663": {"name": "Esterno/Garage", "index": 3},
    "11919": {"name": "Reparto Giorno", "index": 1},
}


# ---------------------------------------------------------------------------
# The IDs
# ---------------------------------------------------------------------------


def test_queries_read_the_control_unit_numbers():
    assert "O.MSP AS GINDEX" in get_sai2_groups_query()
    assert "O.MSP AS ZINDEX" in get_sai2_zones_query()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", 1), (" 15 ", 15), (8, 8), ("", None), (None, None), ("0", None), ("x", None)],
)
def test_parse_sai2_index(raw, expected):
    assert parse_sai2_index(raw) == expected


def test_unique_ids_use_the_control_unit_number_not_the_web_server_id():
    assert sai2_area_unique_ids(PREFIX, GROUPS_NOW) == {
        "7615": "vimar_casa_sai2_area_2",
        "7663": "vimar_casa_sai2_area_3",
        "11919": "vimar_casa_sai2_area_1",
    }
    assert sai2_zone_unique_ids(PREFIX, {"20001": {"name": "vol. sala", "index": 13}}) == {
        "20001": "vimar_casa_sai2_zone_13"
    }


def test_missing_or_duplicate_number_keeps_the_legacy_id():
    groups = {
        "1": {"name": "A", "index": None},
        "2": {"name": "B", "index": 4},
        "3": {"name": "C", "index": 4},
        "4": {"name": "D", "index": 5},
    }
    assert sai2_area_unique_ids(PREFIX, groups) == {
        "1": "vimar_sai2_1",
        "2": "vimar_sai2_2",
        "3": "vimar_sai2_3",
        "4": "vimar_casa_sai2_area_5",
    }


def test_commands_target_the_area_number_not_the_id_order():
    # Sorted by ID, Giorno (11919) is now third: counting gave it bit 3,
    # i.e. "arm Giorno" armed Esterno/Garage.
    assert _sai2_area_indexes(GROUPS_NOW) == {"7615": 2, "7663": 3, "11919": 1}


def test_area_numbers_fall_back_to_id_order_when_unusable():
    groups = {"7560": {"name": "G", "index": None}, "7615": {"name": "N", "index": 2}}
    assert _sai2_area_indexes(groups) == {"7560": 1, "7615": 2}


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------


@pytest.fixture
def entry(hass):
    config_entry = MockConfigEntry(domain=DOMAIN, unique_id=PREFIX, title="Casa")
    config_entry.add_to_hass(hass)
    return config_entry


def _create(registry, entry, domain, unique_id, object_id, name):
    return registry.async_get_or_create(
        domain,
        DOMAIN,
        unique_id,
        config_entry=entry,
        suggested_object_id=object_id,
        original_name=name,
    )


async def test_live_entries_move_to_the_new_id_keeping_their_entity_id(hass, entry):
    registry = er.async_get(hass)
    notte = _create(registry, entry, ALARM, "vimar_sai2_7615", "sai_alarm_notte", "Reparto Notte")
    registry.async_update_entity(notte.entity_id, name="Notte", device_class=None)

    async_migrate_sai2_unique_ids(hass, entry, PREFIX, _project(GROUPS_NOW))

    migrated = registry.async_get(notte.entity_id)
    assert migrated is not None
    assert migrated.unique_id == "vimar_casa_sai2_area_2"
    assert migrated.id == notte.id
    assert migrated.name == "Notte"

    # A second start changes nothing.
    async_migrate_sai2_unique_ids(hass, entry, PREFIX, _project(GROUPS_NOW))
    assert registry.async_get(notte.entity_id).unique_id == "vimar_casa_sai2_area_2"


async def test_the_entity_lost_on_2026_10_05_gets_its_entity_id_and_settings_back(hass, entry):
    """What actually happened: old entry deleted, new one created with "_2"."""
    registry = er.async_get(hass)
    old = _create(registry, entry, ALARM, "vimar_sai2_7560", "sai_alarm_giorno", "Reparto Giorno")
    registry.async_update_entity(
        old.entity_id,
        name="Allarme giorno",
        icon="mdi:sofa",
        area_id="soggiorno",
        labels={"sicurezza"},
    )
    new = _create(registry, entry, ALARM, "vimar_sai2_11919", "sai_alarm_giorno", "Reparto Giorno")
    assert new.entity_id == "alarm_control_panel.sai_alarm_giorno_2"
    registry.async_remove(old.entity_id)  # async_remove_old_devices

    async_migrate_sai2_unique_ids(hass, entry, PREFIX, _project(GROUPS_NOW))

    restored = registry.async_get("alarm_control_panel.sai_alarm_giorno")
    assert restored is not None
    assert restored.unique_id == "vimar_casa_sai2_area_1"
    assert restored.name == "Allarme giorno"
    assert restored.icon == "mdi:sofa"
    assert restored.area_id == "soggiorno"
    assert restored.labels == {"sicurezza"}
    assert registry.async_get("alarm_control_panel.sai_alarm_giorno_2") is None


async def test_restore_never_overwrites_what_the_user_set_since(hass, entry):
    registry = er.async_get(hass)
    old = _create(
        registry, entry, ZONE, "vimar_sai2_zone_1324", "sai_alarm_vol_sala", "Giorno - vol. sala"
    )
    registry.async_update_entity(old.entity_id, name="Vecchio", device_class="occupancy")
    new = _create(
        registry, entry, ZONE, "vimar_sai2_zone_20013", "sai_alarm_vol_sala", "Giorno - vol. sala"
    )
    registry.async_update_entity(new.entity_id, name="Nuovo")
    registry.async_remove(old.entity_id)

    zones = {"20013": {"name": "vol. sala", "index": 13}}
    async_migrate_sai2_unique_ids(
        hass,
        entry,
        PREFIX,
        _project({"11919": {"name": "Giorno", "index": 1}}, zones, {"20013": "11919"}),
    )

    restored = registry.async_get("binary_sensor.sai_alarm_vol_sala")
    assert restored.unique_id == "vimar_casa_sai2_zone_13"
    assert restored.name == "Nuovo"  # kept
    assert restored.device_class == "occupancy"  # filled in


async def test_renumbered_while_running_the_original_entry_wins(hass, entry):
    """Both entries still live at the next start: matched by name, oldest kept."""
    registry = er.async_get(hass)
    old = _create(registry, entry, ALARM, "vimar_sai2_7560", "sai_alarm_giorno", "Reparto Giorno")
    new = _create(registry, entry, ALARM, "vimar_sai2_11919", "sai_alarm_giorno", "Reparto Giorno")

    async_migrate_sai2_unique_ids(hass, entry, PREFIX, _project(GROUPS_NOW))

    assert registry.async_get(old.entity_id).unique_id == "vimar_casa_sai2_area_1"
    assert registry.async_get(new.entity_id).unique_id == "vimar_sai2_11919"  # cleaned up later


async def test_zone_matched_by_name_with_its_area_prefix(hass, entry):
    registry = er.async_get(hass)
    old = _create(
        registry,
        entry,
        ZONE,
        "vimar_sai2_zone_1399",
        "sai_alarm_basculante",
        "Esterno/Garage - basculante garag",
    )
    zones = {"30015": {"name": "basculante garag", "index": 15}}
    async_migrate_sai2_unique_ids(
        hass, entry, PREFIX, _project(GROUPS_NOW, zones, {"30015": "7663"})
    )

    assert registry.async_get(old.entity_id).unique_id == "vimar_casa_sai2_zone_15"


async def test_nothing_discovered_touches_nothing(hass, entry):
    registry = er.async_get(hass)
    old = _create(registry, entry, ALARM, "vimar_sai2_7560", "sai_alarm_giorno", "Reparto Giorno")

    async_migrate_sai2_unique_ids(hass, entry, PREFIX, _project(None))

    assert registry.async_get(old.entity_id).unique_id == "vimar_sai2_7560"


async def test_ambiguous_names_are_not_guessed(hass, entry):
    registry = er.async_get(hass)
    old = _create(registry, entry, ALARM, "vimar_sai2_1", "sai_alarm_area", "Area")
    groups = {"10": {"name": "Area", "index": 1}, "11": {"name": "Area", "index": 2}}

    async_migrate_sai2_unique_ids(hass, entry, PREFIX, _project(groups))

    assert registry.async_get(old.entity_id).unique_id == "vimar_sai2_1"


# ---------------------------------------------------------------------------
# Zone device_class: this installation's zones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone_name", "expected"),
    [
        ("vol. corrid. P1", "motion"),
        ("vol. sala", "motion"),
        ("vol. garage", "motion"),  # was garage_door: "vol." wins over everything
        ("basculante garag", "garage_door"),
        ("manomis. sirena", "tamper"),
    ],
)
def test_zone_device_class_from_name(zone_name, expected):
    assert _guess_device_class(zone_name).value == expected


# ---------------------------------------------------------------------------
# Zone values are not area values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["0", "00000000"])
def test_a_one_character_zone_value_means_no_event(raw):
    """Verified on the web server 2026-10-05: in normal conditions many zones
    serve "0" and others "00000000". The 8-character rule of the areas
    (is_valid_sai2_bitmask) must never be applied to zones, or every zone
    serving "0" would read unknown."""
    from custom_components.vimar.binary_sensor import _parse_sai2_zone_value

    assert not any(_parse_sai2_zone_value(raw).values())
