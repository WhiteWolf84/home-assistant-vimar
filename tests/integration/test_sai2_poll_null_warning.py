"""The SAI2 poll warns when an area's CURRENT_VALUE comes back NULL/empty.

A NULL CURRENT_VALUE has always read as '00000000' (disarmed) in the general
poll, and still does: changing that is out of scope. But a "disarmed" the web
server never actually reported must not go unnoticed, so the poll logs a
WARNING when an area enters that condition - once, not on every poll - and an
INFO line when a real value comes back.
"""

import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.vimar_coordinator import (  # noqa: E402
    VimarDataUpdateCoordinator,
)

pytestmark = pytest.mark.integration  # Home Assistant required

GIORNO = "7560"
NOTTE = "7615"
LOGGER = "custom_components.vimar"  # the coordinator logs via const._LOGGER


def _coordinator():
    """A coordinator wired for _refresh_sai2_live_state, bypassing __init__."""
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)

    async def _executor(func, *args):
        return func(*args)

    coordinator.hass = SimpleNamespace(async_add_executor_job=_executor)
    project = MagicMock()
    project.sai2_groups = {
        GIORNO: {"name": "Reparto Giorno", "children": {}},
        NOTTE: {"name": "Reparto Notte", "children": {}},
    }
    project.sai2_zones = None
    project.sai2_area_values = {}
    project.sai2_optimistic_until = {}
    coordinator.vimarproject = project
    coordinator.vimarconnection = MagicMock()
    coordinator._sai2_null_areas = set()
    return coordinator, project


def _serve(coordinator, values):
    coordinator.vimarconnection.get_sai2_area_raw_values.return_value = values


def _null_warnings(caplog):
    return [
        r
        for r in caplog.records
        if r.name == LOGGER and r.levelno == logging.WARNING and "NULL" in r.getMessage()
    ]


async def test_null_still_reads_disarmed_no_behaviour_change():
    coordinator, project = _coordinator()
    _serve(coordinator, {GIORNO: None, NOTTE: "00000101"})

    await coordinator._refresh_sai2_live_state()

    assert project.sai2_area_values == {GIORNO: "00000000", NOTTE: "00000101"}


async def test_warning_once_per_transition_with_gid_and_name(caplog):
    coordinator, project = _coordinator()
    caplog.set_level(logging.INFO, logger=LOGGER)

    _serve(coordinator, {GIORNO: None, NOTTE: "00000101"})
    for _ in range(3):  # three polls in a row with the same NULL
        await coordinator._refresh_sai2_live_state()

    warnings = _null_warnings(caplog)
    assert len(warnings) == 1
    assert GIORNO in warnings[0].getMessage()
    assert "Reparto Giorno" in warnings[0].getMessage()
    assert "Reparto Notte" not in warnings[0].getMessage()


async def test_recovery_is_logged_and_a_new_null_warns_again(caplog):
    coordinator, project = _coordinator()
    caplog.set_level(logging.INFO, logger=LOGGER)

    _serve(coordinator, {GIORNO: None})
    await coordinator._refresh_sai2_live_state()
    _serve(coordinator, {GIORNO: "00000101"})
    await coordinator._refresh_sai2_live_state()
    await coordinator._refresh_sai2_live_state()
    _serve(coordinator, {GIORNO: None})
    await coordinator._refresh_sai2_live_state()

    assert len(_null_warnings(caplog)) == 2
    back = [r for r in caplog.records if r.levelno == logging.INFO and "is back" in r.getMessage()]
    assert len(back) == 1
    assert "Reparto Giorno" in back[0].getMessage()
    assert project.sai2_area_values[GIORNO] == "00000000"


async def test_null_during_an_optimistic_guard_is_still_reported(caplog):
    """The warning is about the web server, whatever the panel shows."""
    coordinator, project = _coordinator()
    caplog.set_level(logging.INFO, logger=LOGGER)
    project.sai2_area_values = {GIORNO: "00000101"}
    project.sai2_optimistic_until = {GIORNO: float("inf")}
    _serve(coordinator, {GIORNO: None})

    await coordinator._refresh_sai2_live_state()

    assert len(_null_warnings(caplog)) == 1
    assert project.sai2_area_values[GIORNO] == "00000101"  # guard still honoured


async def test_failed_query_logs_nothing_and_keeps_values(caplog):
    coordinator, project = _coordinator()
    caplog.set_level(logging.INFO, logger=LOGGER)
    project.sai2_area_values = {GIORNO: "00000101"}
    _serve(coordinator, None)

    await coordinator._refresh_sai2_live_state()

    assert _null_warnings(caplog) == []
    assert project.sai2_area_values == {GIORNO: "00000101"}
