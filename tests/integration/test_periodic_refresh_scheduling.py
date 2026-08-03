"""Periodic GETVALUE refreshes must stay off the poll's critical path (HA required).

Regression: the energy-meter and thermostat refreshes were awaited INSIDE the
poll's `asyncio.timeout(self._timeout)` block:

    async with asyncio.timeout(self._timeout):     # 6 s by default
        ...
        await self._maybe_refresh_energy_meters()  # 1 SOAP call per meter id
        await self._maybe_refresh_climates()       # 1 SOAP call per thermostat
        slim_results = await ...                   # the actual poll

Both refreshes issue one sequential SOAP request per status object, so on an
installation with a handful of meters and thermostats they could burn the
whole budget on their own: the poll timed out, UpdateFailed was raised and
every entity went unavailable for that cycle. `asyncio.timeout` cannot cancel
a job already running on an executor thread either, so the work carried on in
the background while the poll had given up.

They now run as a single tracked background task, with the next tick skipped
while one is still in flight.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.vimar_coordinator import (  # noqa: E402
    VimarDataUpdateCoordinator,
)

pytestmark = pytest.mark.integration  # Home Assistant required

DEVICE_ID = "768"
STATUS_ID = "769"


def _coordinator(timeout=0.3):
    """A coordinator wired for the slim-poll path, bypassing __init__.

    hass.async_create_background_task is a real asyncio task factory so the
    in-flight guard is exercised for real rather than against a mock.
    """
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)

    hass = MagicMock()
    hass.async_create_background_task = lambda coro, name=None: asyncio.create_task(coro)
    coordinator.hass = hass

    connection = MagicMock()
    connection.is_logged.return_value = True
    coordinator.vimarconnection = connection

    project = MagicMock()
    project.devices = {
        DEVICE_ID: {
            "object_id": DEVICE_ID,
            "status": {"on/off": {"status_id": STATUS_ID, "status_value": "0"}},
        }
    }
    project.sai2_groups = None
    project.sai2_zones = None
    coordinator.vimarproject = project

    coordinator._timeout = timeout
    coordinator._first_update_data_executed = True
    coordinator._platforms_registered = True
    coordinator._slim_poll_active = True
    coordinator._known_status_ids = [int(STATUS_ID)]
    coordinator._changed_device_ids = set()
    coordinator._device_state_hashes = {}
    coordinator._pending_write_guards = {}
    coordinator._last_device_count = 1
    coordinator._last_devices_hash = "unchanged"
    coordinator._consecutive_auth_failures = 0
    coordinator._refresh_tasks = set()
    coordinator._periodic_refresh_task = None
    # _async_update_data serialises itself on this; the fixture bypasses
    # __init__, so it has to be provided here.
    coordinator._update_lock = asyncio.Lock()
    coordinator._force_full_discovery = False
    coordinator._write_worker_task = None
    coordinator.devices_for_platform = {}
    coordinator.last_update_success = True

    # The slim poll itself: returns one row, instantly.
    hass.async_add_executor_job = AsyncMock(
        return_value=[{"status_id": STATUS_ID, "status_value": "1"}]
    )
    return coordinator


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------


async def test_a_slow_refresh_does_not_time_out_the_poll():
    """THE regression: refreshes slower than the timeout must not fail the poll."""
    coordinator = _coordinator(timeout=0.3)
    started = asyncio.Event()

    async def slow_refresh():
        started.set()
        await asyncio.sleep(1.0)  # >> the 0.3 s poll budget

    coordinator._maybe_refresh_energy_meters = slow_refresh
    coordinator._maybe_refresh_climates = AsyncMock()

    devices = await coordinator._async_update_data()

    # The poll completed (no UpdateFailed) and applied the new value...
    assert devices
    assert devices[DEVICE_ID]["status"]["on/off"]["status_value"] == "1"
    # ...and only now, once the poll released the loop, does the slow refresh
    # get to run - it was never on the poll's critical path at all.
    await asyncio.sleep(0)
    assert started.is_set()

    for task in list(coordinator._refresh_tasks):
        task.cancel()


async def test_the_refresh_is_scheduled_not_awaited():
    """The poll must return before the refresh finishes."""
    coordinator = _coordinator()
    finished = False

    async def slow_refresh():
        nonlocal finished
        await asyncio.sleep(0.5)
        finished = True

    coordinator._maybe_refresh_energy_meters = slow_refresh
    coordinator._maybe_refresh_climates = AsyncMock()

    await coordinator._async_update_data()

    assert finished is False

    for task in list(coordinator._refresh_tasks):
        task.cancel()


# ---------------------------------------------------------------------------
# Scheduling behaviour
# ---------------------------------------------------------------------------


async def test_both_refresh_kinds_run_in_the_background_task():
    coordinator = _coordinator()
    coordinator._maybe_refresh_energy_meters = AsyncMock()
    coordinator._maybe_refresh_climates = AsyncMock()

    coordinator._schedule_periodic_refreshes()
    await coordinator._periodic_refresh_task

    coordinator._maybe_refresh_energy_meters.assert_awaited_once()
    coordinator._maybe_refresh_climates.assert_awaited_once()


async def test_a_second_tick_is_skipped_while_one_is_in_flight():
    """No pile-up on the shared connection when the web server is slow."""
    coordinator = _coordinator()
    release = asyncio.Event()
    calls = 0

    async def blocking_refresh():
        nonlocal calls
        calls += 1
        await release.wait()

    coordinator._maybe_refresh_energy_meters = blocking_refresh
    coordinator._maybe_refresh_climates = AsyncMock()

    coordinator._schedule_periodic_refreshes()
    await asyncio.sleep(0)  # let the task start
    first = coordinator._periodic_refresh_task

    for _ in range(3):
        coordinator._schedule_periodic_refreshes()

    assert coordinator._periodic_refresh_task is first
    release.set()
    await first
    assert calls == 1


async def test_a_new_tick_is_scheduled_once_the_previous_one_finished():
    coordinator = _coordinator()
    coordinator._maybe_refresh_energy_meters = AsyncMock()
    coordinator._maybe_refresh_climates = AsyncMock()

    coordinator._schedule_periodic_refreshes()
    first = coordinator._periodic_refresh_task
    await first

    coordinator._schedule_periodic_refreshes()

    assert coordinator._periodic_refresh_task is not first
    await coordinator._periodic_refresh_task


async def test_the_task_is_tracked_so_unload_can_cancel_it():
    """Untracked background tasks survive a reload and blow up later."""
    coordinator = _coordinator()
    release = asyncio.Event()

    async def blocking_refresh():
        await release.wait()

    coordinator._maybe_refresh_energy_meters = blocking_refresh
    coordinator._maybe_refresh_climates = AsyncMock()

    coordinator._schedule_periodic_refreshes()
    await asyncio.sleep(0)

    assert coordinator._periodic_refresh_task in coordinator._refresh_tasks

    await coordinator.async_shutdown_write_worker()

    assert coordinator._refresh_tasks == set()
    assert coordinator._periodic_refresh_task is None


async def test_finished_tasks_are_discarded_from_the_tracking_set():
    """The set must not grow one entry per poll for the life of the entry."""
    coordinator = _coordinator()
    coordinator._maybe_refresh_energy_meters = AsyncMock()
    coordinator._maybe_refresh_climates = AsyncMock()

    for _ in range(3):
        coordinator._schedule_periodic_refreshes()
        await coordinator._periodic_refresh_task
        await asyncio.sleep(0)  # let the done-callback run

    assert coordinator._refresh_tasks == set()
