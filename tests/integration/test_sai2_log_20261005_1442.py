"""Replay of the 2026-10-05 14:42 hardware test (beta 2026.10.0b1).

One alarm_arm_home on the 3 areas. 2026.10.0b1 checked the PIN once per call:

    14:42:53.238  Garage  PIN check DPCM-0000 in 1.23 s
    14:42:53.304  Garage  command 2 -> DPCM-0000           (executed)
    14:42:56.566  Garage  confirmed 00000101 after 3.3 s
    14:42:56.567  Notte   PIN "checked earlier in this call" - no check
    14:42:56.615  Notte   command 2 -> DPCM-0000           (never executed)
    14:43:17.089  Notte   20 s of 00000000 -> sai2_arm_not_confirmed
    14:43:17.246  Giorno  command 2 -> DPCM-0000, no check (never executed)
    14:43:37.847  Giorno  20 s of 00000000 -> sai2_arm_not_confirmed

Notte and Giorno got their command on an idle control unit and still lost it.
What they lacked was a PIN check right before the command: a check seems to
authorise the command that follows it, not to validate the PIN for good. Every
log of the day fits that rule:

- 2026.10.0b0 12:33 and 11:27: a check per area, right before its command -> ok
  (11:27: one check, then disarm at +0.01 s and arm at +1.04 s, both ok);
- 2026.10.0b0 12:31: Notte's check made while Garage's command was being
  carried out -> Notte's command lost;
- 2026.8.2 08:31: three checks in parallel, then three commands -> only one
  carried out.

How long the authorisation lasts is not known: one command, or every command
within a short window (more than the 1.04 s of 11:27, less than the 3.36 s of
14:42). The fake control unit models both; the tests run on each.
"""

import asyncio
import os
import sys
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
USER = "ha-user-usercode-3"
AREAS = {"7560": (1, "Reparto Giorno"), "7615": (2, "Reparto Notte"), "7663": (3, "Esterno/Garage")}
GIORNO, NOTTE, GARAGE = "7560", "7615", "7663"
INDEX = {gid: idx for gid, (idx, _) in AREAS.items()}
DISARMED, AWAY, HOME, NIGHT = "00000000", "00000011", "00000101", "00001001"
COMMAND_BITMASK = {0: DISARMED, 1: AWAY, 2: HOME, 3: NIGHT}

PIN_CHECK = 1.23  # 14:42:53, centrale idle
SOAP = 0.04
SQL = 0.02
APPLY_SECONDS = {0: 3.0, 1: 3.3, 2: 3.3, 3: 3.3}  # command -> CURRENT_VALUE
WINDOW = 2.0  # between the 1.04 s that worked and the 3.36 s that did not

MODELS = ["one_command", "short_window"]


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _AuthorisingControlUnit:
    """A valid PIN check authorises what follows it; nothing else does."""

    def __init__(self, clock: _Clock, live: dict[str, str], model: str) -> None:
        self.clock = clock
        self.model = model
        self.history = {gid: [(-1e9, value)] for gid, value in live.items()}
        self.authorised = False  # one_command
        self.authorised_until = -1e9  # short_window
        self.wrong_attempts = 0
        self.events: list[tuple[str, float, int | None, int | None]] = []
        self.executed: list[tuple[int, int]] = []
        self.dropped: list[tuple[int, int]] = []

    def value(self, gid: str) -> str:
        return [v for since, v in self.history[gid] if since <= self.clock.now][-1]

    def authenticate(self, pin):
        self.clock.now += PIN_CHECK
        self.events.append(("auth", self.clock.now, None, None))
        if pin != PIN:
            self.wrong_attempts += 1
            return acp._SAI2_WRONG_PIN
        self.authorised = True
        self.authorised_until = self.clock.now + WINDOW
        return acp._SAI2_OK

    def _take_authorisation(self) -> bool:
        if self.model == "one_command":
            granted, self.authorised = self.authorised, False
            return granted
        return self.clock.now <= self.authorised_until

    def set_status(self, command, area_index, pin):
        self.clock.now += SOAP
        self.events.append(("cmd", self.clock.now, command, area_index))
        if pin != PIN or not self._take_authorisation():
            self.dropped.append((command, area_index))
            return acp._SAI2_OK  # acknowledged anyway, as on hardware
        self.executed.append((command, area_index))
        gid = next(g for g, idx in INDEX.items() if idx == area_index)
        self.history[gid].append(
            (self.clock.now + APPLY_SECONDS[command], COMMAND_BITMASK[command])
        )
        return acp._SAI2_OK

    def raw_values(self, group_ids):
        self.clock.now += SQL
        return {gid: self.value(gid) for gid in group_ids}


def _setup(monkeypatch, live, model):
    clock = _Clock()
    monkeypatch.setattr(acp, "time", clock)
    monkeypatch.setattr(acp, "persistent_notification", MagicMock())
    real_sleep = asyncio.sleep

    async def _sleep(delay, *args, **kwargs):
        clock.now += delay
        await real_sleep(0)

    monkeypatch.setattr(acp.asyncio, "sleep", _sleep)

    unit = _AuthorisingControlUnit(clock, live, model)
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
        panel._pending_mode = None
        panel._pin_cache = pin_cache
        panel._last_command_at = None
        panel._context = None
        panel.written = []
        panel.async_write_ha_state = MagicMock(
            side_effect=lambda p=panel: p.written.append(p.alarm_state)
        )
        panel._localized_exception = AsyncMock(return_value="msg")
        panels[gid] = panel
    return clock, unit, connection, panels


async def _ha_call(panels, method, code=None):
    """Mimic helpers/service.py: one context, gather, PARALLEL_UPDATES semaphore."""
    context = Context(user_id=USER)
    semaphore = asyncio.Semaphore(acp.PARALLEL_UPDATES)

    async def _one(panel):
        async with semaphore:
            panel._context = context
            return await getattr(panel, method)(code)

    results = await asyncio.gather(*(_one(p) for p in panels), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


def _log_order(panels):
    return [panels[GARAGE], panels[NOTTE], panels[GIORNO]]


@pytest.mark.parametrize("model", MODELS)
def test_the_fake_unit_reproduces_the_b1_log(model):
    """The 2026.10.0b1 sequence, with the logged timings, loses Notte and Giorno."""
    clock = _Clock()
    unit = _AuthorisingControlUnit(clock, {g: DISARMED for g in AREAS}, model)

    assert unit.authenticate(PIN) == acp._SAI2_OK  # 14:42:53.238, once
    unit.set_status(2, INDEX[GARAGE], PIN)  # 14:42:53.304
    clock.now += 3.3  # Garage confirmed at 14:42:56.566
    assert unit.value(GARAGE) == HOME
    unit.set_status(2, INDEX[NOTTE], PIN)  # 14:42:56.615, no new check
    clock.now += 20.5
    unit.set_status(2, INDEX[GIORNO], PIN)  # 14:43:17.246, no new check
    clock.now += 20.6

    assert unit.executed == [(2, 3)]
    assert unit.dropped == [(2, 2), (2, 1)]
    assert (unit.value(GARAGE), unit.value(NOTTE), unit.value(GIORNO)) == (HOME, DISARMED, DISARMED)


@pytest.mark.parametrize("model", MODELS)
async def test_1442_arm_home_reaches_every_area(monkeypatch, model):
    clock, unit, connection, panels = _setup(monkeypatch, {g: DISARMED for g in AREAS}, model)

    await _ha_call(_log_order(panels), "async_alarm_arm_home")

    assert unit.dropped == []
    assert unit.executed == [(2, 3), (2, 2), (2, 1)]
    assert all(unit.value(g) == HOME for g in AREAS)
    for panel in panels.values():
        assert panel.alarm_state is AlarmControlPanelState.ARMED_HOME
    # Check, command; check, command; ... - each check right before its
    # command, and only after the previous area was carried out.
    kinds = [kind for kind, *_ in unit.events]
    assert kinds == ["auth", "cmd"] * 3
    cmd_times = [t for kind, t, *_ in unit.events if kind == "cmd"]
    auth_times = [t for kind, t, *_ in unit.events if kind == "auth"]
    for previous_cmd, auth in zip(cmd_times, auth_times[1:], strict=False):
        assert auth - PIN_CHECK >= previous_cmd + APPLY_SECONDS[2]


@pytest.mark.parametrize("model", MODELS)
async def test_wrong_pin_one_attempt_for_the_whole_call(monkeypatch, model):
    clock, unit, connection, panels = _setup(monkeypatch, {g: HOME for g in AREAS}, model)

    with pytest.raises(ServiceValidationError) as err:
        await _ha_call(_log_order(panels), "async_alarm_disarm", code=WRONG_PIN)

    assert err.value.translation_key == "sai2_wrong_pin"
    assert unit.wrong_attempts == 1  # remembered for the other two areas
    assert connection.authenticate_sai2_pin.call_count == 1
    assert unit.events == [("auth", PIN_CHECK, None, None)]  # no command, no wait
    assert all(unit.value(g) == HOME for g in AREAS)


@pytest.mark.parametrize("model", MODELS)
async def test_a_transient_check_answer_is_not_reused(monkeypatch, model):
    """Only SAI2-3127 is remembered: after a hiccup, the next area checks again."""
    clock, unit, connection, panels = _setup(monkeypatch, {g: DISARMED for g in AREAS}, model)
    answers = iter([None])

    def _first_times_out(pin):
        answer = next(answers, "check")
        return unit.authenticate(pin) if answer == "check" else answer

    connection.authenticate_sai2_pin.side_effect = _first_times_out

    with pytest.raises(HomeAssistantError) as err:
        await _ha_call(_log_order(panels), "async_alarm_arm_home")

    assert err.value.translation_key == "sai2_no_response"
    assert unit.executed == [(2, 2), (2, 1)]
    assert panels[GARAGE].alarm_state is AlarmControlPanelState.DISARMED
    assert panels[NOTTE].alarm_state is AlarmControlPanelState.ARMED_HOME
    assert panels[GIORNO].alarm_state is AlarmControlPanelState.ARMED_HOME


@pytest.mark.parametrize("model", MODELS)
async def test_switching_mode_checks_the_pin_before_each_command(monkeypatch, model):
    """PAR -> ON: check, disarm, wait for it, check, arm - behind ARMING."""
    clock, unit, connection, panels = _setup(monkeypatch, {g: NIGHT for g in AREAS}, model)
    giorno = panels[GIORNO]

    await _ha_call([giorno], "async_alarm_arm_away")

    assert unit.executed == [(0, 1), (1, 1)]
    assert unit.dropped == []
    assert [kind for kind, *_ in unit.events] == ["auth", "cmd", "auth", "cmd"]
    (_, disarm_at, _, _), (_, second_check, _, _) = unit.events[1:3]
    assert second_check - PIN_CHECK >= disarm_at + APPLY_SECONDS[0]
    assert unit.value(GIORNO) == AWAY
    assert giorno.written == [AlarmControlPanelState.ARMING, AlarmControlPanelState.ARMED_AWAY]


@pytest.mark.parametrize("model", MODELS)
async def test_switching_mode_second_check_refused_shows_the_area_disarmed(monkeypatch, model):
    clock, unit, connection, panels = _setup(monkeypatch, {g: NIGHT for g in AREAS}, model)
    giorno = panels[GIORNO]
    answers = iter(["check", acp._SAI2_WRONG_PIN])

    def _second_refused(pin):
        answer = next(answers)
        return unit.authenticate(pin) if answer == "check" else answer

    connection.authenticate_sai2_pin.side_effect = _second_refused

    with pytest.raises(ServiceValidationError) as err:
        await _ha_call([giorno], "async_alarm_arm_away")

    assert err.value.translation_key == "sai2_wrong_pin"
    assert unit.executed == [(0, 1)]  # the arm was never sent
    assert unit.value(GIORNO) == DISARMED
    assert giorno.alarm_state is AlarmControlPanelState.DISARMED  # the real state


@pytest.mark.parametrize("model", MODELS)
async def test_switching_mode_unconfirmed_disarm_stops_before_the_arm(monkeypatch, model):
    clock, unit, connection, panels = _setup(monkeypatch, {g: NIGHT for g in AREAS}, model)
    giorno = panels[GIORNO]
    real_set = unit.set_status

    def _disarm_lost(command, area_index, pin):
        if command == 0:
            clock.now += SOAP
            unit.dropped.append((command, area_index))
            return acp._SAI2_OK
        return real_set(command, area_index, pin)

    connection.set_sai2_status.side_effect = _disarm_lost

    with pytest.raises(HomeAssistantError) as err:
        await _ha_call([giorno], "async_alarm_arm_away")

    assert err.value.translation_key == "sai2_arm_not_confirmed"
    assert connection.set_sai2_status.call_count == 1  # no arm after a lost disarm
    assert connection.authenticate_sai2_pin.call_count == 1
    assert giorno.alarm_state is AlarmControlPanelState.ARMED_NIGHT  # still PAR
