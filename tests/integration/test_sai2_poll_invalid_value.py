"""The SAI2 poll never turns an unreadable area value into "disarmed".

A NULL/empty CURRENT_VALUE used to read as '00000000', and any other non-empty
string was decoded by int(value, 2): on 2026-10-05, after a web server
restart, the areas read '0' and HA showed them disarmed while the control unit
had them armed. Now only an 8-character 0/1 bitmask is a value; anything else
- NULL/empty, a missing row, '0', another length - leaves the area unknown, is
logged once as a WARNING when the area enters that condition, and once at INFO
when a valid value comes back. A failing query keeps the values already read
for up to 60 s from an area's last valid reading; then the area is unknown too.
"""

import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar import vimar_coordinator as vc  # noqa: E402
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
    coordinator._sai2_invalid_areas = set()
    coordinator._sai2_last_valid_at = {}
    return coordinator, project


def _serve(coordinator, values):
    coordinator.vimarconnection.get_sai2_area_raw_values.return_value = values


def _invalid_warnings(caplog):
    return [
        r
        for r in caplog.records
        if r.name == LOGGER
        and r.levelno == logging.WARNING
        and "not a valid bitmask" in r.getMessage()
    ]


@pytest.mark.parametrize(
    "value",
    [None, "", "0", "0 ", "1", "0000000", "000000000", "0000010x", "0b000101"],
)
async def test_an_invalid_value_is_unknown_not_disarmed(value):
    coordinator, project = _coordinator()
    project.sai2_area_values = {GIORNO: "00000101", NOTTE: "00000101"}
    _serve(coordinator, {GIORNO: value, NOTTE: "00000000"})

    await coordinator._refresh_sai2_live_state()

    assert project.sai2_area_values == {GIORNO: None, NOTTE: "00000000"}


async def test_a_missing_row_is_unknown():
    coordinator, project = _coordinator()
    project.sai2_area_values = {GIORNO: "00000101", NOTTE: "00000101"}
    _serve(coordinator, {NOTTE: "00000101"})

    await coordinator._refresh_sai2_live_state()

    assert project.sai2_area_values == {GIORNO: None, NOTTE: "00000101"}


async def test_warning_once_per_transition_with_gid_name_and_value(caplog):
    coordinator, project = _coordinator()
    caplog.set_level(logging.INFO, logger=LOGGER)

    _serve(coordinator, {GIORNO: "0", NOTTE: "00000101"})
    for _ in range(3):  # three polls in a row with the same bad value
        await coordinator._refresh_sai2_live_state()

    warnings = _invalid_warnings(caplog)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert GIORNO in message
    assert "Reparto Giorno" in message
    assert "'0'" in message
    assert "Reparto Notte" not in message


async def test_recovery_is_logged_and_a_new_invalid_value_warns_again(caplog):
    coordinator, project = _coordinator()
    caplog.set_level(logging.INFO, logger=LOGGER)

    _serve(coordinator, {GIORNO: None, NOTTE: "00000000"})
    await coordinator._refresh_sai2_live_state()
    _serve(coordinator, {GIORNO: "00000101", NOTTE: "00000000"})
    await coordinator._refresh_sai2_live_state()
    await coordinator._refresh_sai2_live_state()
    _serve(coordinator, {GIORNO: "0", NOTTE: "00000000"})
    await coordinator._refresh_sai2_live_state()

    assert len(_invalid_warnings(caplog)) == 2
    back = [r for r in caplog.records if r.levelno == logging.INFO and "is back" in r.getMessage()]
    assert len(back) == 1
    assert "Reparto Giorno" in back[0].getMessage()
    assert project.sai2_area_values[GIORNO] is None


async def test_invalid_value_during_a_guard_is_reported_value_kept(caplog):
    """The warning is about the web server; the guard still protects the value."""
    coordinator, project = _coordinator()
    caplog.set_level(logging.INFO, logger=LOGGER)
    project.sai2_area_values = {GIORNO: "00000101"}
    project.sai2_optimistic_until = {GIORNO: float("inf")}
    _serve(coordinator, {GIORNO: None, NOTTE: "00000000"})

    await coordinator._refresh_sai2_live_state()

    assert len(_invalid_warnings(caplog)) == 1
    assert project.sai2_area_values[GIORNO] == "00000101"  # guard still honoured


async def test_failed_query_logs_nothing_and_keeps_values(caplog):
    coordinator, project = _coordinator()
    caplog.set_level(logging.INFO, logger=LOGGER)
    project.sai2_area_values = {GIORNO: "00000101"}
    _serve(coordinator, None)

    await coordinator._refresh_sai2_live_state()

    assert _invalid_warnings(caplog) == []
    assert project.sai2_area_values == {GIORNO: "00000101"}


# ---------------------------------------------------------------------------
# A failing query: values kept for 60 s, then unknown
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _stale_warnings(caplog):
    return [
        r
        for r in caplog.records
        if r.name == LOGGER and r.levelno == logging.WARNING and "keeps failing" in r.getMessage()
    ]


async def test_failed_query_keeps_values_for_60s_then_unknown(monkeypatch, caplog):
    clock = _Clock()
    monkeypatch.setattr(vc, "time", clock)
    coordinator, project = _coordinator()
    caplog.set_level(logging.INFO, logger=LOGGER)
    _serve(coordinator, {GIORNO: "00000101", NOTTE: "00000000"})
    await coordinator._refresh_sai2_live_state()  # last valid reading at t=1000

    _serve(coordinator, None)  # the query starts failing
    for _ in range(7):  # 56 s of failed polls: values kept
        clock.now += 8.0
        await coordinator._refresh_sai2_live_state()
    assert project.sai2_area_values == {GIORNO: "00000101", NOTTE: "00000000"}
    assert _stale_warnings(caplog) == []

    clock.now += 8.0  # 64 s without a valid reading
    await coordinator._refresh_sai2_live_state()
    assert project.sai2_area_values == {GIORNO: None, NOTTE: None}
    warnings = _stale_warnings(caplog)
    assert len(warnings) == 2  # one per area
    assert "Reparto Giorno" in warnings[0].getMessage()

    for _ in range(3):  # still failing: no new warning
        clock.now += 8.0
        await coordinator._refresh_sai2_live_state()
    assert len(_stale_warnings(caplog)) == 2


async def test_first_valid_reading_after_a_stale_period_brings_the_area_back(monkeypatch, caplog):
    clock = _Clock()
    monkeypatch.setattr(vc, "time", clock)
    coordinator, project = _coordinator()
    caplog.set_level(logging.INFO, logger=LOGGER)
    _serve(coordinator, {GIORNO: "00000101", NOTTE: "00000101"})
    await coordinator._refresh_sai2_live_state()
    _serve(coordinator, None)
    clock.now += 61.0
    await coordinator._refresh_sai2_live_state()
    assert project.sai2_area_values == {GIORNO: None, NOTTE: None}

    # The area was disarmed meanwhile (e.g. from the Vimar UI): the state
    # comes from the new reading.
    _serve(coordinator, {GIORNO: "00000000", NOTTE: "00000101"})
    clock.now += 8.0
    await coordinator._refresh_sai2_live_state()

    assert project.sai2_area_values == {GIORNO: "00000000", NOTTE: "00000101"}
    back = [r for r in caplog.records if r.levelno == logging.INFO and "is back" in r.getMessage()]
    assert len(back) == 2


async def test_failing_from_the_first_poll_counts_from_that_poll(monkeypatch):
    """No valid reading yet: the 60 s start at the first failed poll."""
    clock = _Clock()
    monkeypatch.setattr(vc, "time", clock)
    coordinator, project = _coordinator()
    project.sai2_area_values = {GIORNO: "00000101", NOTTE: "00000101"}  # from startup
    _serve(coordinator, None)

    await coordinator._refresh_sai2_live_state()
    clock.now += 60.0
    await coordinator._refresh_sai2_live_state()
    assert project.sai2_area_values[GIORNO] == "00000101"

    clock.now += 8.0
    await coordinator._refresh_sai2_live_state()
    assert project.sai2_area_values[GIORNO] is None


async def test_an_area_with_a_command_in_flight_is_not_expired(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(vc, "time", clock)
    coordinator, project = _coordinator()
    _serve(coordinator, {GIORNO: "00000101", NOTTE: "00000101"})
    await coordinator._refresh_sai2_live_state()
    project.sai2_optimistic_until = {GIORNO: clock.now + 1000.0}
    _serve(coordinator, None)

    clock.now += 61.0
    await coordinator._refresh_sai2_live_state()

    assert project.sai2_area_values == {GIORNO: "00000101", NOTTE: None}


async def test_invalid_values_do_not_count_as_valid_readings(monkeypatch):
    """A query that answers, but with '0', does not reset the 60 s either."""
    clock = _Clock()
    monkeypatch.setattr(vc, "time", clock)
    coordinator, project = _coordinator()
    _serve(coordinator, {GIORNO: "00000101", NOTTE: "00000101"})
    await coordinator._refresh_sai2_live_state()

    _serve(coordinator, {GIORNO: "0", NOTTE: "00000101"})
    clock.now += 30.0
    await coordinator._refresh_sai2_live_state()  # Giorno unknown at once
    _serve(coordinator, None)
    clock.now += 35.0  # 65 s since Giorno's last valid reading, 35 s for Notte
    await coordinator._refresh_sai2_live_state()

    assert project.sai2_area_values == {GIORNO: None, NOTTE: "00000101"}
