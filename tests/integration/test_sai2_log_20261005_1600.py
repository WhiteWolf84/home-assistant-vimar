"""Replay of the 2026-10-05 15:59-16:01 log (beta 2026.10.0b2).

The HA user's permissions were changed on the control unit and the web server
restarted. Afterwards the SAI2 area values it served were not readable:

    15:59:29  Giorno  command 2 -> FSMC-007 (rejected)
    15:59:30  Garage  command 2 -> FSMC-007 (rejected)
    16:00:29  Notte   command 2 -> DPCM-0000; 20 s of confirm raw=None
              -> sai2_arm_not_confirmed
    16:00:51  Giorno  same
    16:01:14  Garage  same
    16:01     the Vimar UI shows the 3 areas ARMED (INT); HA shows them
              disarmed, sai2_raw '0'

2026.10.0b2 reported every arm as not confirmed - right - but then showed the
area "disarmed": with no valid reading it fell back on the value from before
the command ('0', which the parser decodes as disarmed), and the poll kept
serving '0' or nothing at all.

Which of the two the web server did is not in the log - the confirmation's
raw=None fits rows missing from the result, the poll's '0' fits a '0' value -
so the fake web server is run both ways.
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar import alarm_control_panel as acp  # noqa: E402
from custom_components.vimar import vimar_coordinator as vc  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

PIN = "123456"
USER = "ha-user-usercode-3"
AREAS = {"7560": (1, "Reparto Giorno"), "7615": (2, "Reparto Notte"), "7663": (3, "Esterno/Garage")}
GIORNO, NOTTE, GARAGE = "7560", "7615", "7663"
INDEX = {gid: idx for gid, (idx, _) in AREAS.items()}
DISARMED, HOME = "00000000", "00000101"
COMMAND_BITMASK = {0: DISARMED, 1: "00000011", 2: HOME, 3: "00001001"}

# What the restarted web server served for an area: the value '0', or no row.
SERVED = ["zero", "no-row"]


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _RestartedWebServer:
    """The control unit carries out commands; the web server cannot tell."""

    def __init__(self, clock: _Clock, served: str) -> None:
        self.clock = clock
        self.served = served
        self.truth = dict.fromkeys(AREAS, DISARMED)  # what the Vimar UI shows
        self.reject_next = 0  # FSMC-007 for the next N commands

    def authenticate(self, pin):
        self.clock.now += 1.3
        return acp._SAI2_OK

    def set_status(self, command, area_index, pin):
        self.clock.now += 0.03
        if self.reject_next:
            self.reject_next -= 1
            return "FSMC-007"
        gid = next(g for g, idx in INDEX.items() if idx == area_index)
        self.truth[gid] = COMMAND_BITMASK[command]
        return acp._SAI2_OK

    def raw_values(self, group_ids):
        self.clock.now += 0.02
        if self.served == "zero":
            return dict.fromkeys(group_ids, "0")
        return {}


def _setup(monkeypatch, served):
    clock = _Clock()
    monkeypatch.setattr(acp, "time", clock)
    monkeypatch.setattr(vc, "time", clock)
    monkeypatch.setattr(acp, "persistent_notification", MagicMock())
    real_sleep = asyncio.sleep

    async def _sleep(delay, *args, **kwargs):
        clock.now += delay
        await real_sleep(0)

    monkeypatch.setattr(acp.asyncio, "sleep", _sleep)

    server = _RestartedWebServer(clock, served)
    connection = MagicMock()
    connection.authenticate_sai2_pin.side_effect = server.authenticate
    connection.set_sai2_status.side_effect = server.set_status
    connection.get_sai2_area_raw_values.side_effect = server.raw_values

    project = MagicMock()
    project.sai2_groups = {gid: {"name": name, "children": {}} for gid, (_, name) in AREAS.items()}
    project.sai2_zones = None
    # What b2 had cached when the log starts: sai2_raw '0'.
    project.sai2_area_values = dict.fromkeys(AREAS, "0")
    project.sai2_optimistic_until = {}

    async def _executor(func, *args):
        return func(*args)

    poller = vc.VimarDataUpdateCoordinator.__new__(vc.VimarDataUpdateCoordinator)
    poller.vimarproject = project
    poller.vimarconnection = connection
    poller.hass = SimpleNamespace(async_add_executor_job=_executor)
    poller._sai2_invalid_areas = set()
    poller._sai2_last_valid_at = {}

    coordinator = MagicMock()
    coordinator.vimarproject = project
    coordinator.vimarconnection = connection
    coordinator.async_request_refresh = AsyncMock()

    hass = MagicMock()
    hass.async_add_executor_job = _executor

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
        panel._pin_cache = acp._Sai2PinCache()
        panel._last_command_at = None
        panel._context = None
        panel.written = []
        panel.async_write_ha_state = MagicMock(
            side_effect=lambda p=panel: p.written.append(p.alarm_state)
        )
        panel._localized_exception = AsyncMock(return_value="msg")
        panels[gid] = panel
    return clock, server, poller, panels


async def _ha_call(panels, method):
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


async def _poll(poller, panels):
    """One coordinator poll, then the update every entity gets."""
    await poller._refresh_sai2_live_state()
    for panel in panels.values():
        panel._handle_coordinator_update()


@pytest.mark.parametrize("served", SERVED)
async def test_1600_armed_areas_never_shown_disarmed(monkeypatch, served):
    clock, server, poller, panels = _setup(monkeypatch, served)
    order = [panels[NOTTE], panels[GIORNO], panels[GARAGE]]  # as at 16:00

    with pytest.raises(HomeAssistantError) as err:
        await _ha_call(order, "async_alarm_arm_home")

    assert err.value.translation_key == "sai2_arm_not_confirmed"
    # The control unit did arm them (the Vimar UI said so)...
    assert all(server.truth[g] == HOME for g in AREAS)
    # ...so HA must not say "disarmed": no reading after the command, unknown.
    for panel in panels.values():
        assert panel.alarm_state is None
        assert AlarmControlPanelState.DISARMED not in panel.written

    # The following polls still serve '0' / nothing: still unknown.
    for _ in range(3):
        clock.now += 8.0
        await _poll(poller, panels)
    for panel in panels.values():
        assert panel.alarm_state is None
        assert panel._current_raw() is None


@pytest.mark.parametrize("served", SERVED)
async def test_1559_rejected_command_on_an_unreadable_area_is_unknown(monkeypatch, served):
    """FSMC-007 during the instability: nothing valid to show but unknown."""
    clock, server, poller, panels = _setup(monkeypatch, served)
    server.reject_next = 1

    with pytest.raises(HomeAssistantError) as err:
        await _ha_call([panels[GIORNO]], "async_alarm_arm_home")

    assert err.value.translation_key == "sai2_command_rejected"
    assert panels[GIORNO].alarm_state is None
    assert AlarmControlPanelState.DISARMED not in panels[GIORNO].written


@pytest.mark.parametrize("served", SERVED)
async def test_poll_after_the_restart_makes_the_areas_unknown(monkeypatch, served):
    """Even without any command, '0' / no row is never shown as disarmed."""
    clock, server, poller, panels = _setup(monkeypatch, served)

    await _poll(poller, panels)

    for panel in panels.values():
        assert panel.alarm_state is None


@pytest.mark.parametrize("served", SERVED)
async def test_valid_values_coming_back_end_the_unknown_state(monkeypatch, served):
    clock, server, poller, panels = _setup(monkeypatch, served)
    with pytest.raises(HomeAssistantError):
        await _ha_call([panels[NOTTE]], "async_alarm_arm_home")
    assert panels[NOTTE].alarm_state is None

    # The web server is back to normal.
    poller.vimarconnection.get_sai2_area_raw_values.side_effect = lambda group_ids: {
        g: server.truth[g] for g in group_ids
    }
    await _poll(poller, panels)

    assert panels[NOTTE].alarm_state is AlarmControlPanelState.ARMED_HOME
    assert panels[GIORNO].alarm_state is AlarmControlPanelState.DISARMED
