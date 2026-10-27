"""Off/unknown thermostats are never switched on by preset or temperature calls.

Regression for the 2026-10-27 restart: an automation fired at Home Assistant
start called climate.set_preset_mode("none") on five thermostats that were off
(funzionamento=6). The preset lives in 'funzionamento', the same status that
holds off, so each call wrote funzionamento=1 (manual) and the zones really
started cooling. Only set_hvac_mode (and turn_on/turn_off, which go through it)
may switch a thermostat on or off; any other call on an off thermostat, or on
one whose mode is not a valid reading, is refused before anything is queued or
published.
"""

import json
import os
import re
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.exceptions import ServiceValidationError

from custom_components.vimar.climate import VimarClimate
from custom_components.vimar.vimarlink import device_queries as q

pytestmark = pytest.mark.integration  # Home Assistant required

COMPONENT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")

# Type II (CH_HVAC_NoZonaNeutra) raw codes, see const.py.
FUNZ_AUTO = "0"
FUNZ_MANUAL = "1"
FUNZ_ECO = "2"
FUNZ_AWAY = "3"
FUNZ_OFF = "6"
REG_COOL = "1"
REG_HEAT = "2"

NO_OPT = "NO-OPTIONALS"

REFUSAL_KEYS = (
    "climate_mode_unknown",
    "climate_preset_while_off",
    "climate_temperature_while_off",
)


def _zone(funz=FUNZ_OFF, reg=REG_COOL):
    """Build a CH_HVAC_NoZonaNeutra VimarClimate with mocked HA/bus dependencies.

    Same approach as test_climate_state_writes._make_climate: bypasses
    CoordinatorEntity.__init__ and captures the queued writes without sending.
    """
    climate = VimarClimate.__new__(VimarClimate)
    climate._device = {
        "object_type": "CH_HVAC_NoZonaNeutra",
        "status": {
            "funzionamento": {"status_id": "F", "status_value": funz},
            "regolazione": {"status_id": "R", "status_value": reg},
            "setpoint": {"status_id": "S", "status_value": "25.00"},
            "temperatura_misurata": {"status_id": "T", "status_value": "23.1"},
            "stato_principale_condizionamento on/off": {"status_id": "C", "status_value": "0"},
            "stato_principale_riscaldamento on/off": {"status_id": "H", "status_value": "0"},
        },
    }
    climate._device_id = "462"
    climate.entity_id = "climate.test_termostato"

    connection = MagicMock()
    connection.get_optionals_param.return_value = NO_OPT
    climate._vimarconnection = connection

    coordinator = MagicMock()
    coordinator._changed_device_ids = set()
    coordinator._device_state_hashes = {}
    coordinator.enqueue_device_writes = MagicMock()
    climate._coordinator = coordinator
    climate.coordinator = coordinator

    climate.hass = MagicMock()
    climate._logger = MagicMock()
    climate.async_write_ha_state = MagicMock()
    return climate


def _batches(climate):
    """Every batch queued for writing, in order."""
    return [c.args[0] for c in climate.coordinator.enqueue_device_writes.call_args_list]


def _assert_untouched(climate, funz=FUNZ_OFF, reg=REG_COOL):
    """Nothing queued, nothing published, local cache unchanged."""
    climate.coordinator.enqueue_device_writes.assert_not_called()
    climate.async_write_ha_state.assert_not_called()
    climate.coordinator.invalidate_device_hash.assert_not_called()
    climate.coordinator.schedule_status_refresh.assert_not_called()
    assert climate.coordinator._changed_device_ids == set()
    status = climate._device["status"]
    assert status["funzionamento"]["status_value"] == funz
    assert status["regolazione"]["status_value"] == reg


# --- T1: replay of the real restart -----------------------------------------


async def test_t1_replay_restart_20261027_keeps_off_zones_off():
    """preset none + set_temperature(25.0) on 5 off zones: 10 refusals, 0 writes."""
    zones = [_zone() for _ in range(5)]
    refused = 0
    for climate in zones:
        with pytest.raises(ServiceValidationError):
            await climate.async_set_preset_mode("none")
        refused += 1
    for climate in zones:
        with pytest.raises(ServiceValidationError):
            await climate.async_set_temperature(temperature=25.0)
        refused += 1

    assert refused == 10
    for climate in zones:
        _assert_untouched(climate)
        assert climate.hvac_mode == HVACMode.OFF


# --- T2, T3: off zone ---------------------------------------------------------


async def test_t2_preset_away_refused_while_off():
    climate = _zone()
    with pytest.raises(ServiceValidationError) as err:
        await climate.async_set_preset_mode("away")
    assert err.value.translation_key == "climate_preset_while_off"
    _assert_untouched(climate)


async def test_t3_set_temperature_refused_while_off_no_setpoint_either():
    climate = _zone()
    with pytest.raises(ServiceValidationError) as err:
        await climate.async_set_temperature(temperature=26)
    assert err.value.translation_key == "climate_temperature_while_off"
    _assert_untouched(climate)


# --- T4: explicit hvac_mode in set_temperature ------------------------------


async def test_t4_set_temperature_with_hvac_mode_cool_switches_on_then_setpoint():
    """set_hvac_mode first (the only method allowed to switch on), then setpoint."""
    climate = _zone()
    await climate.async_set_temperature(temperature=26, hvac_mode=HVACMode.COOL)
    assert _batches(climate) == [
        [("F", FUNZ_MANUAL, NO_OPT), ("R", REG_COOL, NO_OPT)],
        [("S", "26", NO_OPT)],
    ]


# --- T5: zones that are on behave exactly as before (I4) --------------------


@pytest.mark.parametrize(
    ("funz", "call", "expected"),
    [
        (FUNZ_MANUAL, ("preset", "away"), [("F", FUNZ_AWAY, NO_OPT)]),
        (FUNZ_AWAY, ("preset", "none"), [("F", FUNZ_MANUAL, NO_OPT)]),
        (FUNZ_MANUAL, ("temp", 26), [("S", "26", NO_OPT)]),
        (FUNZ_AWAY, ("temp", 26), [("S", "26", NO_OPT)]),
        (FUNZ_ECO, ("temp", 26), [("F", FUNZ_MANUAL, NO_OPT), ("S", "26", NO_OPT)]),
        (FUNZ_AUTO, ("temp", 26), [("F", FUNZ_MANUAL, NO_OPT), ("S", "26", NO_OPT)]),
    ],
)
async def test_t5_zones_that_are_on_write_exactly_as_before(funz, call, expected):
    """Regression guard: passes on the current code by design."""
    climate = _zone(funz)
    kind, arg = call
    if kind == "preset":
        await climate.async_set_preset_mode(arg)
    else:
        await climate.async_set_temperature(temperature=arg)
    assert _batches(climate) == [expected]


# --- T6: unrecognised raw values -> unknown ---------------------------------


@pytest.mark.parametrize("raw", ["", "99", "5"])
async def test_t6_unrecognised_funzionamento_is_unknown_and_only_off_is_accepted(raw, caplog):
    climate = _zone(raw)
    assert climate.hvac_mode is None
    assert climate.hvac_action is None
    assert climate.preset_mode is None
    with pytest.raises(ServiceValidationError) as err:
        await climate.async_set_temperature(temperature=26)
    assert err.value.translation_key == "climate_mode_unknown"
    with pytest.raises(ServiceValidationError) as err:
        await climate.async_set_preset_mode("none")
    assert err.value.translation_key == "climate_mode_unknown"
    climate.coordinator.enqueue_device_writes.assert_not_called()

    await climate.async_set_hvac_mode(HVACMode.OFF)
    assert _batches(climate) == [[("F", FUNZ_OFF, NO_OPT)]]

    unrecognised = [r for r in caplog.records if "unrecognised funzionamento" in r.getMessage()]
    assert len(unrecognised) == 1  # once per entity, however often it is read


@pytest.mark.parametrize("raw", ["", "0", "99"])
async def test_t6b_unrecognised_regolazione_on_zone_refuses_setpoint_and_preset(raw):
    """I5: an unknown direction is enough to refuse setpoint and preset writes."""
    climate = _zone(FUNZ_MANUAL, raw)
    assert climate.hvac_mode is None
    assert climate.hvac_action is None
    with pytest.raises(ServiceValidationError):
        await climate.async_set_temperature(temperature=26)
    with pytest.raises(ServiceValidationError):
        await climate.async_set_preset_mode("eco")
    _assert_untouched(climate, FUNZ_MANUAL, raw)

    # Writing the direction explicitly is how a valid state is restored.
    await climate.async_set_hvac_mode(HVACMode.COOL)
    assert _batches(climate) == [[("R", REG_COOL, NO_OPT)]]


async def test_t6c_off_zone_with_unrecognised_regolazione_still_reads_off():
    """Off is a valid reading on its own: the direction does not matter."""
    climate = _zone(FUNZ_OFF, "")
    assert climate.hvac_mode == HVACMode.OFF
    assert climate.hvac_action == HVACAction.OFF


# --- T7: a refusal changes nothing that is published -------------------------


async def test_t7_refusal_does_not_change_published_state():
    climate = _zone()
    before = (climate.hvac_mode, climate.preset_mode, climate.hvac_action)
    with pytest.raises(ServiceValidationError):
        await climate.async_set_preset_mode("eco")
    with pytest.raises(ServiceValidationError):
        await climate.async_set_temperature(temperature=22)
    assert (climate.hvac_mode, climate.preset_mode, climate.hvac_action) == before
    _assert_untouched(climate)


# --- T8: SQL aliases (regression guard, passes on the current code) ---------

JOIN_QUERIES = [
    q.get_room_devices_query("1,2", 0, 300),
    q.get_remote_devices_query(0, 300),
    q.get_device_status_query("462"),
    q.get_sai2_groups_query(),
    q.get_sai2_zones_query(),
]


def _select_items(sql: str) -> list[str]:
    body = re.search(r"SELECT\s+(.*?)\s+FROM\s", sql, re.S | re.I).group(1)
    items, depth, cur = [], 0, ""
    for ch in body:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return [*items, cur.strip()]


@pytest.mark.parametrize(
    "sql",
    JOIN_QUERIES,
    ids=["room_devices", "remote_devices", "device_status", "sai2_groups", "sai2_zones"],
)
def test_t8_every_join_column_has_an_alias(sql):
    assert "JOIN" in sql.upper()
    missing = [i for i in _select_items(sql) if not re.search(r"\s+AS\s+\w+$", i, re.I)]
    assert missing == []


# --- T9: turn_on keeps the current direction --------------------------------


async def test_t9_turn_on_keeps_cool_direction():
    """HA's default turn_on would pick HEAT (first of HEAT_COOL/HEAT/COOL)."""
    climate = _zone(FUNZ_OFF, REG_COOL)
    await climate.async_turn_on()
    assert _batches(climate) == [[("F", FUNZ_MANUAL, NO_OPT), ("R", REG_COOL, NO_OPT)]]
    assert all(value != REG_HEAT for batch in _batches(climate) for _, value, _ in batch[1:])


@pytest.mark.parametrize("raw", ["", "0", "99"])
async def test_t9b_turn_on_with_unknown_direction_is_refused(raw):
    climate = _zone(FUNZ_OFF, raw)
    with pytest.raises(ServiceValidationError) as err:
        await climate.async_turn_on()
    assert err.value.translation_key == "climate_mode_unknown"
    _assert_untouched(climate, FUNZ_OFF, raw)


# --- T10: set_temperature with hvac_mode=off ---------------------------------


@pytest.mark.parametrize("funz", [FUNZ_MANUAL, FUNZ_OFF])
async def test_t10_set_temperature_with_hvac_mode_off_is_refused_as_a_whole(funz):
    climate = _zone(funz)
    with pytest.raises(ServiceValidationError) as err:
        await climate.async_set_temperature(temperature=26, hvac_mode=HVACMode.OFF)
    assert err.value.translation_key == "climate_temperature_while_off"
    _assert_untouched(climate, funz)


# --- T11: set_hvac_mode from an unknown mode --------------------------------


@pytest.mark.parametrize("mode", [HVACMode.HEAT, HVACMode.COOL])
async def test_t11_heat_or_cool_from_unknown_mode_is_refused(mode):
    climate = _zone("99")
    with pytest.raises(ServiceValidationError) as err:
        await climate.async_set_hvac_mode(mode)
    assert err.value.translation_key == "climate_mode_unknown"
    _assert_untouched(climate, "99")


async def test_t11b_off_from_unknown_mode_is_accepted():
    climate = _zone("99")
    await climate.async_set_hvac_mode(HVACMode.OFF)
    assert _batches(climate) == [[("F", FUNZ_OFF, NO_OPT)]]


# --- T12: hvac_action -------------------------------------------------------


async def test_t12_hvac_action_is_none_when_mode_unknown():
    climate = _zone("")
    assert climate.hvac_action is None


# --- T13: translations ------------------------------------------------------


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/it.json"])
def test_t13_refusal_keys_exist_with_the_same_placeholders(path):
    with open(os.path.join(COMPONENT_DIR, path), encoding="utf-8") as fh:
        exceptions = json.load(fh)["exceptions"]
    for key in REFUSAL_KEYS:
        assert key in exceptions, f"{key} missing from {path}"
        placeholders = set(re.findall(r"{(\w+)}", exceptions[key]["message"]))
        assert placeholders == {"entity_id"}, f"{key} in {path}: {placeholders}"
