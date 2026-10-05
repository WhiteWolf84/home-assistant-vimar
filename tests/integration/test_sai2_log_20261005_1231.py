"""Replay of the 2026-10-05 12:31-12:33 hardware test (beta 2026.10.0b0).

One alarm_arm_home on the 3 areas, then one alarm_disarm. On the control
unit's own event log only Esterno/Garage was armed:

    12:31:45.105  Garage  command 2 -> DPCM-0000         (executed)
    12:31:48.727  Notte   PIN check DPCM-0000 in 3.62 s   (centrale busy)
    12:31:49.029  Notte   command 2 -> DPCM-0000 in 0.29 s (acknowledged, LOST)
    12:31:51.934  Giorno  PIN check SAI2-3127 in 2.91 s   (valid PIN, busy)
                  -> ServiceValidationError "Wrong PIN"; Notte silently stays
                     disarmed (the next poll reads 00000000 and the panel
                     follows, with no error anywhere)
    12:33:10.961  Garage  disarm; confirmation reads 00000101, 00000101,
                  then 00000000 after 3.1 s
    12:33:14.874  Notte   PIN check in 0.89 s - right after the confirmation,
                  the centrale is idle again

The fake control unit models exactly that: a command keeps it busy until it
shows up in CURRENT_VALUE (3 s for a disarm, as logged; 5 s for an arm, within
what the polls showed); while busy, PIN checks are slow and may answer
SAI2-3127, and a command is acknowledged with DPCM-0000 but dropped. A command
that changes nothing (a disarm of a disarmed area) does not make it busy - the
12:33 log is consistent with that, but it is not proven.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from homeassistant.core import Context

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar import alarm_control_panel as acp  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

PIN = "123456"
USER = "ha-user-usercode-3"
AREAS = {"7560": (1, "Reparto Giorno"), "7615": (2, "Reparto Notte"), "7663": (3, "Esterno/Garage")}
GIORNO, NOTTE, GARAGE = "7560", "7615", "7663"
INDEX = {gid: idx for gid, (idx, _) in AREAS.items()}
DISARMED, HOME = "00000000", "00000101"
COMMAND_BITMASK = {0: DISARMED, 1: "00000011", 2: HOME, 3: "00001001"}

APPLY_SECONDS = {0: 3.0, 1: 5.0, 2: 5.0, 3: 5.0}  # command -> CURRENT_VALUE
PIN_CHECK_IDLE = [1.18, 0.89, 1.32, 2.45]  # logged, centrale idle
PIN_CHECK_BUSY = [3.62, 2.91]  # logged, centrale busy
SOAP_IDLE, SOAP_BUSY, SQL = 0.02, 0.29, 0.07


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _BusyControlUnit:
    """Busy until a command shows up in CURRENT_VALUE; drops commands meanwhile."""

    def __init__(self, clock: _Clock, live: dict[str, str]) -> None:
        self.clock = clock
        self.history = {gid: [(-1e9, value)] for gid, value in live.items()}
        self.busy_until = -1e9
        self.busy_answers = [acp._SAI2_OK, acp._SAI2_WRONG_PIN]  # 12:31:48, 12:31:51
        self.idle_checks = list(PIN_CHECK_IDLE)
        self.busy_checks = list(PIN_CHECK_BUSY)
        self.executed: list[tuple[int, int]] = []
        self.dropped: list[tuple[int, int]] = []

    def busy(self) -> bool:
        return self.clock.now < self.busy_until

    def value(self, gid: str) -> str:
        return [v for since, v in self.history[gid] if since <= self.clock.now][-1]

    def authenticate(self, pin):
        if self.busy():
            self.clock.now += self.busy_checks.pop(0) if self.busy_checks else 3.0
            return self.busy_answers.pop(0) if self.busy_answers else acp._SAI2_OK
        self.clock.now += self.idle_checks.pop(0) if self.idle_checks else 1.2
        return acp._SAI2_OK

    def set_status(self, command, area_index, pin):
        busy = self.busy()
        self.clock.now += SOAP_BUSY if busy else SOAP_IDLE
        if busy:
            self.dropped.append((command, area_index))  # acknowledged anyway
            return acp._SAI2_OK
        self.executed.append((command, area_index))
        gid = next(g for g, idx in INDEX.items() if idx == area_index)
        if self.history[gid][-1][1] == COMMAND_BITMASK[command]:
            # Nothing to change: not busy. At 12:33 Giorno's PIN check, 1.06 s
            # after Notte's no-op disarm, took the idle 1.32 s.
            return acp._SAI2_OK
        applied_at = self.clock.now + APPLY_SECONDS[command]
        self.history[gid].append((applied_at, COMMAND_BITMASK[command]))
        self.busy_until = applied_at
        return acp._SAI2_OK

    def raw_values(self, group_ids):
        self.clock.now += SQL
        return {gid: self.value(gid) for gid in group_ids}


def _setup(monkeypatch, live):
    clock = _Clock()
    monkeypatch.setattr(acp, "time", clock)
    monkeypatch.setattr(acp, "persistent_notification", MagicMock())
    real_sleep = asyncio.sleep

    async def _sleep(delay, *args, **kwargs):
        clock.now += delay
        await real_sleep(0)

    monkeypatch.setattr(acp.asyncio, "sleep", _sleep)

    unit = _BusyControlUnit(clock, live)
    connection = MagicMock()
    connection.authenticate_sai2_pin.side_effect = unit.authenticate
    connection.set_sai2_status.side_effect = unit.set_status
    connection.get_sai2_area_raw_values.side_effect = unit.raw_values

    project = MagicMock()
    project.sai2_groups = {gid: {"name": name, "children": {}} for gid, (_, name) in AREAS.items()}
    project.sai2_zones = None
    project.sai2_area_values = dict(live)
    project.sai2_optimistic_until = {}

    coordinator = MagicMock()
    coordinator.vimarproject = project
    coordinator.vimarconnection = connection
    coordinator.async_request_refresh = AsyncMock()

    async def _executor(func, *args):
        return func(*args)

    hass = MagicMock()
    hass.async_add_executor_job = _executor

    pin_cache = acp._Sai2PinCache()
    panels = {}
    for gid, (area_index, name) in AREAS.items():
        panel = acp.VimarAlarmControlPanel.__new__(acp.VimarAlarmControlPanel)
        panel.coordinator = coordinator
        panel.hass = hass
        panel._group_id = gid
        panel._group_data = project.sai2_groups[gid]
        panel._area_index = area_index
        panel._user_pins = {USER: PIN}
        panel._automation_pin = ""
        panel._attr_name = name
        panel._command_lock = asyncio.Lock()
        panel._state_unknown = False
        panel._pin_cache = pin_cache
        panel._last_command_at = None
        panel._context = None
        panel.async_write_ha_state = MagicMock()
        panel._localized_exception = AsyncMock(return_value="msg")
        panels[gid] = panel
    return clock, unit, connection, panels


async def _ha_call(panels, method):
    """Mimic helpers/service.py: one context, gather, PARALLEL_UPDATES semaphore."""
    context = Context(user_id=USER)
    semaphore = asyncio.Semaphore(acp.PARALLEL_UPDATES)

    async def _one(panel):
        async with semaphore:
            panel._context = context
            return await getattr(panel, method)(None)

    results = await asyncio.gather(*(_one(p) for p in panels), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


def _log_order(panels):
    return [panels[GARAGE], panels[NOTTE], panels[GIORNO]]


async def test_1231_arm_home_reaches_every_area(monkeypatch):
    """The beta lost Notte silently and failed Giorno with a false wrong PIN."""
    clock, unit, connection, panels = _setup(monkeypatch, {g: DISARMED for g in AREAS})

    await _ha_call(_log_order(panels), "async_alarm_arm_home")

    assert unit.dropped == []  # no command reached a busy control unit
    assert unit.executed == [(2, 3), (2, 2), (2, 1)]
    assert connection.authenticate_sai2_pin.call_count == 1  # no busy PIN check
    assert all(unit.value(g) == HOME for g in AREAS)
    for panel in panels.values():
        assert panel.alarm_state is AlarmControlPanelState.ARMED_HOME


async def test_1231_a_lost_arm_is_an_error_not_silence(monkeypatch):
    """If the centrale still drops a command, the call fails and says which area."""
    clock, unit, connection, panels = _setup(monkeypatch, {g: DISARMED for g in AREAS})
    real_set = unit.set_status

    def _drop_notte(command, area_index, pin):
        if area_index == INDEX[NOTTE]:
            clock.now += SOAP_BUSY
            unit.dropped.append((command, area_index))
            return acp._SAI2_OK
        return real_set(command, area_index, pin)

    connection.set_sai2_status.side_effect = _drop_notte

    with pytest.raises(acp.HomeAssistantError) as err:
        await _ha_call(_log_order(panels), "async_alarm_arm_home")

    assert err.value.translation_key == "sai2_arm_not_confirmed"
    assert err.value.translation_placeholders["area"] == "Reparto Notte"
    assert panels[NOTTE].alarm_state is AlarmControlPanelState.DISARMED
    assert panels[GARAGE].alarm_state is AlarmControlPanelState.ARMED_HOME
    assert panels[GIORNO].alarm_state is AlarmControlPanelState.ARMED_HOME


async def test_1233_disarm_waits_through_two_stale_readings(monkeypatch):
    """Garage armed INT, the others disarmed - the state after the 12:31 call."""
    clock, unit, connection, panels = _setup(
        monkeypatch, {GARAGE: HOME, NOTTE: DISARMED, GIORNO: DISARMED}
    )
    readings = []
    real_raw = unit.raw_values

    def _record(group_ids):
        values = real_raw(group_ids)
        readings.append((group_ids[0], values[group_ids[0]]))
        return values

    connection.get_sai2_area_raw_values.side_effect = _record

    await _ha_call(_log_order(panels), "async_alarm_disarm")

    # Every disarm is sent and confirmed, even on areas already disarmed.
    assert unit.executed == [(0, 3), (0, 2), (0, 1)]
    assert unit.dropped == []
    garage = [value for gid, value in readings if gid == GARAGE]
    # pre-check, then two stale readings, then the confirmation - as logged.
    assert garage == [HOME, HOME, HOME, DISARMED]
    for panel in panels.values():
        assert panel.alarm_state is AlarmControlPanelState.DISARMED
