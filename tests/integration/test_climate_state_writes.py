"""Regression tests for climate state writes (Home Assistant required).

These cover the fix for the thermostat setpoint bug:
- change_state() must enqueue all values as a SINGLE ordered batch onto the
  coordinator's global FIFO write queue, preserving the caller's order. The
  coordinator drains the queue one batch at a time on a single thread, so
  concurrent commands (e.g. set_hvac_mode + set_temperature fired together)
  can never reach the webserver out of order. Before the fix each value, then
  each command, was dispatched as a separate fire-and-forget executor job, so
  requests raced and the firmware reloaded its stored manual setpoint,
  discarding our write.
- async_set_temperature() must write ONLY the setpoint when the thermostat is
  already in manual mode (matching the native VIMAR web UI), and must apply
  funzionamento=MANUAL before the setpoint when activating from off/auto.
- async_set_hvac_mode() must NOT write the setpoint when activating from off;
  the setpoint is owned solely by set_temperature().
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# Import the integration as a package so relative imports resolve.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.climate import VimarClimate

pytestmark = pytest.mark.integration  # Home Assistant required

# Type II thermostat (heat_cool_fancoil) funzionamento values, see const.py.
FUNZ_MANUAL = "1"  # VIMAR_CLIMATE_MANUAL_II
FUNZ_OFF = "6"  # VIMAR_CLIMATE_OFF_II


def _make_climate(status):
    """Build a VimarClimate with mocked HA/bus dependencies.

    Bypasses CoordinatorEntity.__init__: the tested methods only read
    self._device and enqueue writes via self.coordinator.enqueue_device_writes,
    which we capture without executing.
    """
    climate = VimarClimate.__new__(VimarClimate)
    climate._device = {"object_type": "CH_HVAC_FanCoil", "status": status}
    climate._device_id = "100"

    connection = MagicMock()
    connection.get_optionals_param.return_value = "NO-OPTIONALS"
    climate._vimarconnection = connection

    coordinator = MagicMock()
    coordinator._changed_device_ids = set()
    coordinator._device_state_hashes = {}
    # enqueue_device_writes records the batch without executing it.
    coordinator.enqueue_device_writes = MagicMock()
    climate._coordinator = coordinator
    climate.coordinator = coordinator

    climate.hass = MagicMock()
    climate._logger = MagicMock()
    # Neutralize the HA state-machine write triggered after a change.
    climate.async_write_ha_state = MagicMock()
    return climate, connection


def _manual_fancoil_status():
    return {
        "funzionamento": {"status_id": "F", "status_value": FUNZ_MANUAL},
        "regolazione": {"status_id": "R", "status_value": "1"},  # -> heat_cool_fancoil
        "setpoint": {"status_id": "S", "status_value": "26.0"},
        "temperatura_misurata": {"status_id": "T", "status_value": "26.4"},
    }


def _scheduled_writes(climate):
    """Return the (status_id, value, optionals) batch enqueued for writing."""
    enqueue = climate.coordinator.enqueue_device_writes
    assert enqueue.call_count == 1
    (writes,) = enqueue.call_args.args
    return writes


def test_change_state_batches_into_single_ordered_job():
    """Multiple values -> one enqueued batch, writes kept in the given order."""
    climate, _ = _make_climate(_manual_fancoil_status())

    climate.change_state(
        "funzionamento", FUNZ_MANUAL, "regolazione", "1", "setpoint", "21.5"
    )

    assert _scheduled_writes(climate) == [
        ("F", FUNZ_MANUAL, "NO-OPTIONALS"),
        ("R", "1", "NO-OPTIONALS"),
        ("S", "21.5", "NO-OPTIONALS"),
    ]


def test_change_state_updates_local_cache():
    """The optimistic local cache is updated for each written value."""
    climate, _ = _make_climate(_manual_fancoil_status())

    climate.change_state("setpoint", "21.5")

    assert climate._device["status"]["setpoint"]["status_value"] == "21.5"


def test_coordinator_executes_writes_in_order():
    """The coordinator drains a batch sending SETVALUE one-by-one, in order."""
    from custom_components.vimar.vimar_coordinator import VimarDataUpdateCoordinator

    coord = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)
    coord.vimarconnection = MagicMock()
    writes = [("F", FUNZ_MANUAL, "O"), ("R", "1", "O"), ("S", "21.5", "O")]

    coord._execute_device_writes(writes)

    sent = [tuple(c.args) for c in coord.vimarconnection.set_device_status.call_args_list]
    assert sent == writes


async def test_set_temperature_in_manual_writes_only_setpoint():
    """Already in manual: a single setpoint SETVALUE, like the VIMAR web UI."""
    climate, _ = _make_climate(_manual_fancoil_status())

    await climate.async_set_temperature(temperature=21.5)

    assert _scheduled_writes(climate) == [("S", "21.5", "NO-OPTIONALS")]


async def test_set_temperature_when_off_activates_manual_then_setpoint():
    """From off: funzionamento=MANUAL is written first, setpoint last."""
    status = _manual_fancoil_status()
    status["funzionamento"]["status_value"] = FUNZ_OFF
    climate, _ = _make_climate(status)

    await climate.async_set_temperature(temperature=21.5)

    writes = _scheduled_writes(climate)
    # Only mode + setpoint are written; the heat/cool direction is untouched.
    assert [status_id for status_id, _, _ in writes] == ["F", "S"]
    assert writes[0] == ("F", FUNZ_MANUAL, "NO-OPTIONALS")
    assert writes[-1] == ("S", "21.5", "NO-OPTIONALS")


async def test_set_hvac_mode_from_off_does_not_write_setpoint():
    """Activating from off writes funzionamento+direction only, never setpoint."""
    from homeassistant.components.climate.const import HVACMode

    status = _manual_fancoil_status()
    status["funzionamento"]["status_value"] = FUNZ_OFF
    climate, _ = _make_climate(status)

    await climate.async_set_hvac_mode(HVACMode.COOL)

    writes = _scheduled_writes(climate)
    status_ids = [status_id for status_id, _, _ in writes]
    assert "S" not in status_ids  # setpoint untouched
    assert writes[0] == ("F", FUNZ_MANUAL, "NO-OPTIONALS")
    assert ("R", "1", "NO-OPTIONALS") in writes  # regolazione -> cool
