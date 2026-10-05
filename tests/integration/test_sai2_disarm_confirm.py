"""A SAI2 disarm is only reported once the control unit confirms it.

service-vimarsai2allgroupsset answers DPCM-0000 even when the control unit
does not execute the command. On 2026-10-05 a single alarm_disarm on three
areas ran the three commands concurrently: the gateway acknowledged all of
them, executed one, and the panel showed "disarmed" for 7 s before the next
poll silently put two areas back to armed_home. The script carried on and the
alarm went off an hour later.

Covered here:
- PARALLEL_UPDATES = 1 makes HA run the area commands one at a time;
- after the set call, a disarm waits for CURRENT_VALUE to read disarmed and
  raises HomeAssistantError if it does not within the confirmation window;
- an empty/NULL CURRENT_VALUE is a missing reading, never a confirmation;
- the disarm guard is set before the PIN check, so a slow SOAP call cannot
  let a stale poll flip the panel back;
- on failure the entity already shows the real state when the error is raised.
"""

import asyncio
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar import alarm_control_panel as acp  # noqa: E402
from custom_components.vimar import vimar_coordinator as vc  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

GROUP_ID = "7560"
ARMED_HOME = "00000101"
DISARMED = "00000000"


class _Clock:
    """A monotonic clock the test moves by hand (patched into both modules)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _panel(monkeypatch, area_reads):
    """A panel wired to a fake project/connection, bypassing HA setup.

    area_reads: successive raw CURRENT_VALUE values returned by the
    confirmation reads (None = NULL); the last one repeats once exhausted.
    """
    monkeypatch.setattr(acp, "_DISARM_CONFIRM_POLL_SECONDS", 0)
    monkeypatch.setattr(acp, "_DISARM_CONFIRM_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(acp, "persistent_notification", MagicMock())

    project = MagicMock()
    project.sai2_groups = {GROUP_ID: {"name": "Reparto Giorno", "children": {}}}
    project.sai2_zones = None
    project.sai2_area_values = {GROUP_ID: ARMED_HOME}
    project.sai2_optimistic_until = {}

    reads = list(area_reads)

    def _get_raw_values(group_ids):
        value = reads.pop(0) if len(reads) > 1 else reads[0]
        return {GROUP_ID: value}

    connection = MagicMock()
    connection.authenticate_sai2_pin.return_value = acp._SAI2_OK
    connection.set_sai2_status.return_value = acp._SAI2_OK
    connection.get_sai2_area_raw_values.side_effect = _get_raw_values

    coordinator = MagicMock()
    coordinator.vimarproject = project
    coordinator.vimarconnection = connection
    coordinator.async_request_refresh = AsyncMock()

    async def _executor(func, *args):
        return func(*args)

    hass = MagicMock()
    hass.async_add_executor_job = _executor

    panel = acp.VimarAlarmControlPanel.__new__(acp.VimarAlarmControlPanel)
    panel.coordinator = coordinator
    panel.hass = hass
    panel._group_id = GROUP_ID
    panel._group_data = project.sai2_groups[GROUP_ID]
    panel._area_index = 1
    panel._user_pins = {}
    panel._automation_pin = ""
    panel._attr_name = "Reparto Giorno"
    panel._command_lock = asyncio.Lock()
    panel._state_unknown = False
    # Record the state the entity actually writes to HA, in order.
    panel.written = []
    panel.async_write_ha_state = MagicMock(
        side_effect=lambda: panel.written.append(panel.alarm_state)
    )
    panel._localized_exception = AsyncMock(return_value="msg")
    return panel, project, connection, coordinator


def test_area_commands_are_serialised_by_home_assistant():
    """HA builds the per-platform semaphore from this constant."""
    assert acp.PARALLEL_UPDATES == 1


async def test_disarm_waits_through_stale_reads_until_confirmed(monkeypatch):
    panel, project, connection, coordinator = _panel(
        monkeypatch, [ARMED_HOME, ARMED_HOME, DISARMED]
    )

    await panel.async_alarm_disarm("1234")

    assert connection.get_sai2_area_raw_values.call_count == 3
    assert project.sai2_area_values[GROUP_ID] == DISARMED
    assert GROUP_ID not in project.sai2_optimistic_until  # guard dropped
    coordinator.async_request_refresh.assert_not_awaited()


# ---------------------------------------------------------------------------
# Failure: the entity shows the real state BEFORE the error reaches the caller
# ---------------------------------------------------------------------------


async def test_unconfirmed_disarm_raises_and_already_shows_armed(monkeypatch):
    """THE regression: DPCM-0000 but the area stays armed."""
    panel, project, connection, coordinator = _panel(monkeypatch, [ARMED_HOME])
    # The refresh must not be what fixes the state: it is debounced and may
    # not run at once, so here it does nothing at all.
    coordinator.async_request_refresh = AsyncMock()

    with pytest.raises(HomeAssistantError) as err:
        await panel.async_alarm_disarm("1234")

    assert err.value.translation_key == "sai2_disarm_not_confirmed"
    assert err.value.translation_placeholders["area"] == "Reparto Giorno"
    # What a script reads right after the failed action:
    assert panel.alarm_state is AlarmControlPanelState.ARMED_HOME
    assert panel.written[-1] is AlarmControlPanelState.ARMED_HOME
    assert project.sai2_area_values[GROUP_ID] == ARMED_HOME
    assert GROUP_ID not in project.sai2_optimistic_until
    acp.persistent_notification.async_create.assert_called_once()


async def test_no_valid_reading_restores_the_pre_command_state(monkeypatch):
    """Only NULLs came back: fall back to the last known real value."""
    panel, project, connection, coordinator = _panel(monkeypatch, [None])

    with pytest.raises(HomeAssistantError):
        await panel.async_alarm_disarm("1234")

    assert panel.alarm_state is AlarmControlPanelState.ARMED_HOME
    assert panel.written[-1] is AlarmControlPanelState.ARMED_HOME


@pytest.mark.parametrize(
    "area_values",
    [None, {}],  # no live values at all / no value for this area
    ids=["no-area-values", "area-missing"],
)
async def test_nothing_known_reports_unknown_never_disarmed(monkeypatch, area_values):
    """No valid reading and no pre-command value: unknown, not 'disarmed'.

    Without a live value alarm_state falls back to the children dict, which
    the optimistic update set to "Disinserito" - the one answer we must not
    give for a disarm the control unit never confirmed.
    """
    panel, project, connection, coordinator = _panel(monkeypatch, [None])
    project.sai2_area_values = area_values

    with pytest.raises(HomeAssistantError):
        await panel.async_alarm_disarm("1234")

    assert panel.alarm_state is None  # HA shows "unknown"
    assert panel.written[-1] is None
    assert GROUP_ID not in (project.sai2_area_values or {})
    assert GROUP_ID not in project.sai2_optimistic_until

    # A poll that still has nothing for this area keeps it unknown...
    panel._handle_coordinator_update()
    assert panel.alarm_state is None
    # ...the first real value ends it.
    project.sai2_area_values = {GROUP_ID: ARMED_HOME}
    panel._handle_coordinator_update()
    assert panel.alarm_state is AlarmControlPanelState.ARMED_HOME


async def test_a_new_command_leaves_the_unknown_state(monkeypatch):
    panel, project, connection, coordinator = _panel(monkeypatch, [None])
    project.sai2_area_values = None
    with pytest.raises(HomeAssistantError):
        await panel.async_alarm_disarm("1234")
    assert panel.alarm_state is None

    connection.get_sai2_area_raw_values.side_effect = lambda ids: {GROUP_ID: DISARMED}
    await panel.async_alarm_disarm("1234")

    assert panel.alarm_state is AlarmControlPanelState.DISARMED


async def test_real_reading_is_shown_even_without_live_area_values(monkeypatch):
    """sai2_area_values None: the armed reading must not lose to the children."""
    panel, project, connection, coordinator = _panel(monkeypatch, [ARMED_HOME])
    project.sai2_area_values = None

    with pytest.raises(HomeAssistantError):
        await panel.async_alarm_disarm("1234")

    assert panel.alarm_state is AlarmControlPanelState.ARMED_HOME


# ---------------------------------------------------------------------------
# NULL / malformed CURRENT_VALUE never confirms a disarm
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    # NULL/empty, text, a truncated value, and the int(..., 2) quirks the
    # parser would decode as 0 but that are never a real bitmask.
    [None, "", "NULL", "0000000x", "0b0", " 0 ", "0_0", "+0"],
)
async def test_null_or_malformed_reading_is_not_a_confirmation(monkeypatch, missing):
    """The general poll reads NULL as '00000000'; the confirmation must not."""
    panel, project, connection, coordinator = _panel(monkeypatch, [missing])

    with pytest.raises(HomeAssistantError) as err:
        await panel.async_alarm_disarm("1234")

    assert err.value.translation_key == "sai2_disarm_not_confirmed"
    assert panel.alarm_state is not AlarmControlPanelState.DISARMED


@pytest.mark.parametrize("length", [1, 8, 16])
async def test_bitmasks_of_any_length_are_readings(monkeypatch, length):
    """Like the parser: other control units may not use 8 characters."""
    armed = "0" * (length - 3) + "101" if length >= 3 else "1"
    panel, project, connection, coordinator = _panel(monkeypatch, [armed, "0" * length])

    await panel.async_alarm_disarm("1234")

    assert project.sai2_area_values[GROUP_ID] == "0" * length
    assert panel.alarm_state is AlarmControlPanelState.DISARMED


async def test_armed_reading_of_another_length_is_not_a_disarm(monkeypatch):
    panel, project, connection, coordinator = _panel(monkeypatch, ["0000000000000101"])

    with pytest.raises(HomeAssistantError):
        await panel.async_alarm_disarm("1234")

    assert panel.alarm_state is AlarmControlPanelState.ARMED_HOME


async def test_null_reading_is_retried_until_a_real_one(monkeypatch):
    panel, project, connection, coordinator = _panel(monkeypatch, [None, None, DISARMED])

    await panel.async_alarm_disarm("1234")

    assert connection.get_sai2_area_raw_values.call_count == 3
    assert project.sai2_area_values[GROUP_ID] == DISARMED


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


async def test_slow_soap_with_a_poll_in_between_does_not_flip_back(monkeypatch):
    """A set call slower than the 5 s optimistic guard, with a poll mid-call.

    The guard used to start at the optimistic write and last 5 s, so a poll
    landing after that - while the SOAP call was still in flight - put the
    stale 'armed' back on the panel.
    """
    panel, project, connection, coordinator = _panel(monkeypatch, [DISARMED])
    # The real window: the guard length derives from it. The fake clock only
    # moves where we move it, so nothing actually waits 20 s.
    monkeypatch.setattr(acp, "_DISARM_CONFIRM_TIMEOUT_SECONDS", 20.0)
    clock = _Clock()
    monkeypatch.setattr(acp, "time", clock)
    monkeypatch.setattr(vc, "time", clock)

    # The coordinator's real SAI2 poll, reading the stale pre-command value.
    poll_connection = MagicMock()
    poll_connection.get_sai2_area_raw_values.return_value = {GROUP_ID: ARMED_HOME}

    async def _poll_executor(func, *args):
        return func(*args)

    poller = vc.VimarDataUpdateCoordinator.__new__(vc.VimarDataUpdateCoordinator)
    poller.vimarproject = project
    poller.vimarconnection = poll_connection
    poller.hass = SimpleNamespace(async_add_executor_job=_poll_executor)
    poller._sai2_null_areas = set()
    seen_mid_call = {}

    async def _executor(func, *args):
        if func is connection.set_sai2_status:
            clock.now += 3.0  # authenticate took a while too...
            await poller._refresh_sai2_live_state()
            clock.now += 4.0  # ...and the SOAP call is 7 s in when the poll lands
            await poller._refresh_sai2_live_state()
            seen_mid_call["raw"] = project.sai2_area_values[GROUP_ID]
            seen_mid_call["state"] = panel.alarm_state
        return func(*args)

    panel.hass.async_add_executor_job = _executor

    await panel.async_alarm_disarm("1234")

    assert seen_mid_call["raw"] == DISARMED  # the stale poll was ignored
    assert seen_mid_call["state"] is AlarmControlPanelState.DISARMED
    assert AlarmControlPanelState.ARMED_HOME not in panel.written
    assert project.sai2_area_values[GROUP_ID] == DISARMED


async def test_guard_covers_the_whole_confirmation_window(monkeypatch):
    """A scheduled poll during confirmation must not flip the panel back."""
    panel, project, connection, coordinator = _panel(monkeypatch, [ARMED_HOME])
    seen = []

    def _record(group_ids):
        seen.append(project.sai2_optimistic_until.get(GROUP_ID, 0) - time.monotonic())
        return {GROUP_ID: ARMED_HOME}

    connection.get_sai2_area_raw_values.side_effect = _record

    with pytest.raises(HomeAssistantError):
        await panel.async_alarm_disarm("1234")

    # At every confirmation read a scheduled poll would still be ignored.
    assert seen
    assert all(left > 0 for left in seen)


async def test_wrong_pin_releases_the_up_front_guard(monkeypatch):
    """Nothing was sent: polls must keep updating this area straight away."""
    panel, project, connection, coordinator = _panel(monkeypatch, [DISARMED])
    connection.authenticate_sai2_pin.return_value = acp._SAI2_WRONG_PIN

    with pytest.raises(ServiceValidationError):
        await panel.async_alarm_disarm("0000")

    assert GROUP_ID not in project.sai2_optimistic_until
    assert project.sai2_area_values[GROUP_ID] == ARMED_HOME  # untouched
    connection.set_sai2_status.assert_not_called()


async def test_a_failed_read_is_retried_not_fatal(monkeypatch):
    panel, project, connection, coordinator = _panel(monkeypatch, [DISARMED])
    calls = {"n": 0}

    def _flaky(group_ids):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("boom")
        return {GROUP_ID: DISARMED}

    connection.get_sai2_area_raw_values.side_effect = _flaky

    await panel.async_alarm_disarm("1234")

    assert project.sai2_area_values[GROUP_ID] == DISARMED


async def test_arming_is_not_confirmed_yet(monkeypatch):
    """Arming keeps the old behaviour for now: confirmation is disarm-only."""
    panel, project, connection, coordinator = _panel(monkeypatch, [DISARMED])
    project.sai2_area_values[GROUP_ID] = DISARMED

    await panel.async_alarm_arm_home("1234")

    connection.get_sai2_area_raw_values.assert_not_called()
    assert project.sai2_area_values[GROUP_ID] == acp._MODE_ARM_HOME.bitmask
    # Arming still uses the short optimistic guard only.
    assert project.sai2_optimistic_until[GROUP_ID] - time.monotonic() <= (
        acp._OPTIMISTIC_GUARD_SECONDS
    )
