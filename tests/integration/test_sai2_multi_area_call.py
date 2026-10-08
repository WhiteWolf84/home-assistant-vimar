"""One alarm action on several SAI2 areas, as Home Assistant runs it.

Replays two sequences from the 2026.10.0b0 debug log of 2026-10-05, with its
real timings, where PARALLEL_UPDATES = 1 makes HA handle the areas of one call
one after the other:

11:26 - alarm_arm_away on the 3 areas, the user's valid PIN. The PIN check of
  the 2nd area started 0.02 s after the 1st area's command, took 2.67 s and
  came back SAI2-3127 / UnknownUserCode: the call failed with "Wrong PIN" and
  that area stayed disarmed.
11:27 - the same call again, with two areas already armed_away: each of them
  was disarmed and re-armed ("is armed, disarming first").

The fake control unit runs on a fake clock. PIN checks take the logged times
(1.31 s normally; 2.67-4.40 s within 3 s of a command, when the 11:26 log shows
one SAI2-3127 to a valid PIN), SOAP calls 0.03 s, SQL reads 0.07 s. A command
reaches CURRENT_VALUE LAG seconds later: the log still read the old value 1.1 s
and 2.0 s after an arm, and cases B/C showed 7-15 s.
"""

import asyncio
import os
import sys
from itertools import pairwise
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar import alarm_control_panel as acp  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

PIN = "123456"
WRONG_PIN = "999999"
USER = "bruno-user-id"
# gid -> (area_index, name); area_index follows the GID order, as in setup.
AREAS = {
    "7560": (1, "Reparto Giorno"),
    "7615": (2, "Reparto Notte"),
    "7663": (3, "Esterno/Garage"),
}
GIORNO, NOTTE, GARAGE = "7560", "7615", "7663"
INDEX = {gid: idx for gid, (idx, _) in AREAS.items()}
DISARMED, AWAY, NIGHT = "00000000", "00000011", "00001001"
COMMAND_BITMASK = {0: DISARMED, 1: AWAY, 2: "00000101", 3: NIGHT}

LAG = 8.0  # command -> CURRENT_VALUE
PIN_CHECK = 1.31  # 11:26:21 / 11:27:50, centrale idle
PIN_CHECK_BUSY = [2.67, 2.74, 4.40, 3.00]  # within 3 s of a command
BUSY_WINDOW = 3.0
SOAP = 0.03
SQL = 0.07


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _ControlUnit:
    """Web server + control unit on a fake clock."""

    def __init__(self, clock: _Clock, live: dict[str, str], busy_answers=()) -> None:
        self.clock = clock
        # gid -> [(effective_from, value)], the CURRENT_VALUE history.
        self.history = {gid: [(-1e9, value)] for gid, value in live.items()}
        self.busy_answers = list(busy_answers)  # answers to busy PIN checks
        self.busy_durations = list(PIN_CHECK_BUSY)
        self.last_command = -1e9
        self.commands: list[tuple[float, int, int]] = []  # (time, command, area)
        self.unreadable = 0  # next N live reads return None

    def value(self, gid: str) -> str:
        current = None
        for since, value in self.history[gid]:
            if since <= self.clock.now:
                current = value
        return current

    def authenticate(self, pin):
        busy = self.clock.now - self.last_command < BUSY_WINDOW
        if busy:
            self.clock.now += self.busy_durations.pop(0) if self.busy_durations else 3.0
            if self.busy_answers:
                return self.busy_answers.pop(0)
        else:
            self.clock.now += PIN_CHECK
        return acp._SAI2_OK if pin == PIN else acp._SAI2_WRONG_PIN

    def set_status(self, command, area_index, pin):
        self.clock.now += SOAP
        self.last_command = self.clock.now
        self.commands.append((self.clock.now, command, area_index))
        if pin == PIN:
            gid = next(g for g, idx in INDEX.items() if idx == area_index)
            self.history[gid].append((self.clock.now + LAG, COMMAND_BITMASK[command]))
        return acp._SAI2_OK

    def raw_values(self, group_ids):
        self.clock.now += SQL
        if self.unreadable:
            self.unreadable -= 1
            return None
        return {gid: self.value(gid) for gid in group_ids}

    def sent(self):
        return [(command, area) for _, command, area in self.commands]


def _setup(monkeypatch, live, busy_answers=()):
    """Three panels sharing one project/connection, like one config entry."""
    clock = _Clock()
    monkeypatch.setattr(acp, "time", clock)
    monkeypatch.setattr(acp, "persistent_notification", MagicMock())

    real_sleep = asyncio.sleep

    async def _sleep(delay, *args, **kwargs):
        clock.now += delay
        await real_sleep(0)

    monkeypatch.setattr(acp.asyncio, "sleep", _sleep)

    unit = _ControlUnit(clock, live, busy_answers)
    connection = MagicMock()
    connection.authenticate_sai2_pin.side_effect = unit.authenticate
    connection.set_sai2_status.side_effect = unit.set_status
    connection.get_sai2_area_raw_values.side_effect = unit.raw_values

    project = MagicMock()
    project.sai2_groups = {gid: {"name": name, "children": {}} for gid, (_, name) in AREAS.items()}
    project.sai2_zones = None
    project.sai2_area_values = dict(live)  # what the last poll saw
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
        panel._pending_mode = None
        panel._pin_cache = pin_cache
        panel._last_command_at = None
        panel._context = None
        panel.written = []
        panel.async_write_ha_state = MagicMock(
            side_effect=lambda p=panel: p.written.append((clock.now, p.alarm_state))
        )
        panel._localized_exception = AsyncMock(return_value="msg")
        panels[gid] = panel
    return clock, unit, connection, panels


async def _ha_call(panels, method, code=None, context=None):
    """Mimic helpers/service.py: one context, gather, PARALLEL_UPDATES semaphore."""
    context = context or Context(user_id=USER)
    semaphore = asyncio.Semaphore(acp.PARALLEL_UPDATES)

    async def _one(panel):
        async with semaphore:
            panel._context = context
            return await getattr(panel, method)(code)

    results = await asyncio.gather(*(_one(p) for p in panels), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


def _ordered(panels):
    """The order of the log: Garage (area 3), Notte (2), Giorno (1)."""
    return [panels[GARAGE], panels[NOTTE], panels[GIORNO]]


# ---------------------------------------------------------------------------
# 11:26 - a valid PIN reported as wrong for the 2nd area
# ---------------------------------------------------------------------------


async def test_1126_every_area_checked_when_idle_armed_and_confirmed(monkeypatch):
    # A busy PIN check would answer SAI2-3127, as in the log.
    clock, unit, connection, panels = _setup(
        monkeypatch, {g: DISARMED for g in AREAS}, busy_answers=[acp._SAI2_WRONG_PIN]
    )

    await _ha_call(_ordered(panels), "async_alarm_arm_away")

    # One PIN check per area, each after the previous area was confirmed:
    # none lands on a busy centrale.
    assert connection.authenticate_sai2_pin.call_count == 3
    assert unit.busy_answers == [acp._SAI2_WRONG_PIN]  # never asked while busy
    assert unit.sent() == [(1, 3), (1, 2), (1, 1)]
    assert all(unit.value(g) == AWAY for g in AREAS)
    assert all(p.alarm_state is AlarmControlPanelState.ARMED_AWAY for p in panels.values())
    # Each command waits for the previous area's confirmation: no more
    # commands 50 ms apart without a check in between.
    times = [t for t, _, _ in unit.commands]
    assert all(later - earlier >= LAG for earlier, later in pairwise(times))


async def test_a_new_call_checks_the_pin_again(monkeypatch):
    """The remembered result belongs to one call, not to the next one."""
    clock, unit, connection, panels = _setup(monkeypatch, {g: DISARMED for g in AREAS})

    await _ha_call([panels[GARAGE]], "async_alarm_arm_away")
    await _ha_call([panels[NOTTE]], "async_alarm_arm_away")

    assert connection.authenticate_sai2_pin.call_count == 2


# ---------------------------------------------------------------------------
# A genuinely wrong PIN: immediate error, one attempt for the whole call
# ---------------------------------------------------------------------------


async def test_wrong_pin_fails_at_once_with_a_single_attempt(monkeypatch):
    clock, unit, connection, panels = _setup(monkeypatch, {g: AWAY for g in AREAS})

    with pytest.raises(ServiceValidationError) as err:
        await _ha_call(_ordered(panels), "async_alarm_disarm", code=WRONG_PIN)

    assert err.value.translation_key == "sai2_wrong_pin"
    # The centrale saw ONE wrong attempt, not one per area, and no retry.
    assert connection.authenticate_sai2_pin.call_count == 1
    assert unit.commands == []
    assert all(unit.value(g) == AWAY for g in AREAS)
    assert clock.now == pytest.approx(PIN_CHECK)  # immediate: one check, no waiting


async def test_wrong_pin_result_is_never_reused_by_another_call(monkeypatch):
    clock, unit, connection, panels = _setup(monkeypatch, {g: AWAY for g in AREAS})
    with pytest.raises(ServiceValidationError):
        await _ha_call([panels[GIORNO]], "async_alarm_disarm", code=WRONG_PIN)

    await _ha_call([panels[GIORNO]], "async_alarm_disarm", code=PIN)

    assert unit.value(GIORNO) == DISARMED


# ---------------------------------------------------------------------------
# 11:27 - areas already in the requested mode
# ---------------------------------------------------------------------------


async def test_1127_areas_already_armed_away_are_left_alone(monkeypatch):
    clock, unit, connection, panels = _setup(
        monkeypatch, {GARAGE: AWAY, NOTTE: DISARMED, GIORNO: AWAY}
    )

    await _ha_call(_ordered(panels), "async_alarm_arm_away")

    # Only Notte gets a command; nobody is disarmed, not even for a second.
    assert unit.sent() == [(1, 2)]
    assert all(unit.value(g) == AWAY for g in AREAS)
    assert all(state is not AlarmControlPanelState.DISARMED for _, state in panels[GARAGE].written)


async def test_same_mode_right_after_a_command_is_sent_again_without_disarm(monkeypatch):
    """The live value may still be the old one: do not trust it to skip."""
    clock, unit, connection, panels = _setup(monkeypatch, {g: DISARMED for g in AREAS})
    await _ha_call([panels[GIORNO]], "async_alarm_arm_away")  # confirmed after LAG
    first = clock.now

    clock.now = first + 5.0  # < _RECENT_COMMAND_SECONDS since the command
    await _ha_call([panels[GIORNO]], "async_alarm_arm_away")
    assert unit.sent() == [(1, 1), (1, 1)]  # resent, no (0, 1)

    clock.now += acp._RECENT_COMMAND_SECONDS + 1
    await _ha_call([panels[GIORNO]], "async_alarm_arm_away")
    assert unit.sent() == [(1, 1), (1, 1)]  # now trusted: nothing sent


async def test_switching_armed_mode_still_disarms_first(monkeypatch):
    """PAR -> ON needs the intermediate disarm; that must not change."""
    clock, unit, connection, panels = _setup(monkeypatch, {g: NIGHT for g in AREAS})

    await _ha_call([panels[GIORNO]], "async_alarm_arm_away")

    assert unit.sent() == [(0, 1), (1, 1)]
    assert panels[GIORNO].alarm_state is AlarmControlPanelState.ARMED_AWAY
    # Each command has its own PIN check; the arm waits for the disarm.
    assert connection.authenticate_sai2_pin.call_count == 2
    (disarm_at, _, _), (arm_at, _, _) = unit.commands
    assert arm_at >= disarm_at + LAG


async def test_cached_same_mode_without_a_live_reading_sends_no_disarm(monkeypatch):
    """Live pre-check unreadable, last poll says armed_away: no intermediate disarm."""
    clock, unit, connection, panels = _setup(monkeypatch, {g: AWAY for g in AREAS})
    unit.unreadable = 1

    await _ha_call([panels[GIORNO]], "async_alarm_arm_away")

    assert unit.sent() == [(1, 1)]


# ---------------------------------------------------------------------------
# Disarm: always sent, always confirmed
# ---------------------------------------------------------------------------


async def test_disarm_is_sent_and_confirmed_even_if_already_disarmed(monkeypatch):
    clock, unit, connection, panels = _setup(monkeypatch, {g: DISARMED for g in AREAS})

    await _ha_call(_ordered(panels), "async_alarm_disarm")

    assert unit.sent() == [(0, 3), (0, 2), (0, 1)]


async def test_disarm_3s_after_arm_with_a_stale_reading_waits_for_confirmation(monkeypatch):
    """arm, then disarm 3 s later while the live value still reads '00000000'.

    The stale '00000000' must neither skip the disarm command nor confirm
    anything: the disarm waits for the arm (same area), is sent, and the area
    reads disarmed only from a reading taken after the disarm took effect.
    Between the command and that reading the panel shows DISARMING.
    """
    clock, unit, connection, panels = _setup(monkeypatch, {g: DISARMED for g in AREAS})
    giorno = panels[GIORNO]

    arm = asyncio.create_task(_ha_call([giorno], "async_alarm_arm_away"))
    while clock.now < 3.0:  # 3 s later, a second call (another script/user)
        await asyncio.sleep(0)
    assert unit.value(GIORNO) == DISARMED  # the live value is still the old one
    disarm = asyncio.create_task(_ha_call([giorno], "async_alarm_disarm"))
    await asyncio.gather(arm, disarm)

    assert unit.sent() == [(1, 1), (0, 1)]  # the disarm command did go out
    (arm_at, _, _), (disarm_at, _, _) = unit.commands
    assert disarm_at >= arm_at + LAG  # ...after the arm was confirmed
    # The panel never showed 'disarmed' before the disarm took effect: the
    # stale '00000000' was ignored, and while waiting it read DISARMING.
    states = [state for _, state in giorno.written]
    assert states == [
        AlarmControlPanelState.ARMING,
        AlarmControlPanelState.ARMED_AWAY,
        AlarmControlPanelState.DISARMING,
        AlarmControlPanelState.DISARMED,
    ]
    confirmed_at, _ = giorno.written[-1]
    assert confirmed_at >= disarm_at + LAG  # from a reading after it took effect
    assert unit.value(GIORNO) == DISARMED


# ---------------------------------------------------------------------------
# Arm confirmation in a multi-area call
# ---------------------------------------------------------------------------


async def test_an_ignored_arm_fails_the_call_and_shows_the_area_disarmed(monkeypatch):
    clock, unit, connection, panels = _setup(monkeypatch, {g: DISARMED for g in AREAS})
    real_set = unit.set_status

    def _notte_ignored(command, area_index, pin):
        result = real_set(command, area_index, pin)
        if area_index == INDEX[NOTTE]:
            unit.history[NOTTE].pop()  # acknowledged, never executed
        return result

    connection.set_sai2_status.side_effect = _notte_ignored

    with pytest.raises(HomeAssistantError) as err:
        await _ha_call(_ordered(panels), "async_alarm_arm_away")

    assert err.value.translation_key == "sai2_arm_not_confirmed"
    assert unit.sent() == [(1, 3), (1, 2), (1, 1)]  # every area still attempted
    assert panels[NOTTE].alarm_state is AlarmControlPanelState.DISARMED
    assert panels[GARAGE].alarm_state is AlarmControlPanelState.ARMED_AWAY
    assert panels[GIORNO].alarm_state is AlarmControlPanelState.ARMED_AWAY


# ---------------------------------------------------------------------------
# The PIN stays out of the cache and the log
# ---------------------------------------------------------------------------


async def test_pin_is_not_stored_in_clear_nor_logged(monkeypatch, caplog):
    caplog.set_level("DEBUG")
    clock, unit, connection, panels = _setup(monkeypatch, {g: DISARMED for g in AREAS})

    await _ha_call(_ordered(panels), "async_alarm_arm_away")

    assert PIN not in caplog.text
    assert PIN not in repr(vars(panels[GIORNO]._pin_cache))
