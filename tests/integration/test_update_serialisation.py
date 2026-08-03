"""Updates never run on top of each other (Home Assistant required).

`vimar.update_entities` used to do its own work, on its own thread:

    await hass.async_add_executor_job(coordinator.vimarproject.update, forced)

That call rebuilds the entire device tree from scratch. A scheduled poll can be
halfway through the same tree at that moment - and the two halves of a poll run
in different places, `vimarproject.update()` on an executor thread and
`_apply_slim_results()` on the event loop - so the overlap is a real data race
over a plain dict, not merely duplicated work. Nothing prevented it:
DataUpdateCoordinator's `_async_refresh` has no lock of its own.

Writing the result straight into the project had a second consequence: the
coordinator never learned about it, so entities kept showing the old values
until the next scheduled poll happened to come round.

The service now goes through `async_force_refresh()`, and every update holds
`_update_lock`.
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


def _devices():
    return {
        DEVICE_ID: {
            "object_id": DEVICE_ID,
            "status": {"on/off": {"status_id": STATUS_ID, "status_value": "0"}},
        }
    }


def _coordinator(slim_poll_active=True):
    """A coordinator wired for _async_update_data, bypassing __init__."""
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)

    hass = MagicMock()
    hass.async_create_background_task = lambda coro, name=None: asyncio.create_task(coro)
    coordinator.hass = hass

    connection = MagicMock()
    connection.is_logged.return_value = True
    connection.get_status_only.return_value = [{"status_id": STATUS_ID, "status_value": "1"}]
    coordinator.vimarconnection = connection

    project = MagicMock()
    project.devices = _devices()
    project.update.return_value = _devices()
    project.sai2_groups = None
    project.sai2_zones = None
    coordinator.vimarproject = project

    coordinator._timeout = 5.0
    coordinator._setup_timeout = 30.0
    coordinator._first_update_data_executed = True
    coordinator._platforms_registered = True
    coordinator._slim_poll_active = slim_poll_active
    coordinator._known_status_ids = [int(STATUS_ID)]
    coordinator._changed_device_ids = set()
    coordinator._device_state_hashes = {}
    coordinator._pending_write_guards = {}
    coordinator._last_device_count = 1
    coordinator._last_devices_hash = "unchanged"
    coordinator._consecutive_auth_failures = 0
    coordinator._refresh_tasks = set()
    coordinator._periodic_refresh_task = None
    coordinator._write_worker_task = None
    coordinator._update_lock = asyncio.Lock()
    coordinator._force_full_discovery = False
    coordinator.devices_for_platform = {}
    coordinator.last_update_success = True
    coordinator._maybe_refresh_energy_meters = AsyncMock()
    coordinator._maybe_refresh_climates = AsyncMock()
    return coordinator, hass


def _overlap_detecting_executor(record):
    """Stand in for async_add_executor_job, recording concurrent entries."""

    async def _job(func, *args):
        record["in_flight"] += 1
        record["max_in_flight"] = max(record["max_in_flight"], record["in_flight"])
        await asyncio.sleep(0.02)  # long enough for a second caller to barge in
        try:
            return func(*args)
        finally:
            record["in_flight"] -= 1

    return _job


# ---------------------------------------------------------------------------
# The race
# ---------------------------------------------------------------------------


async def test_two_updates_never_overlap():
    """THE regression: a scheduled poll and an on-demand refresh at once."""
    coordinator, hass = _coordinator()
    record = {"in_flight": 0, "max_in_flight": 0}
    hass.async_add_executor_job = _overlap_detecting_executor(record)

    await asyncio.gather(
        coordinator._async_update_data(),
        coordinator._async_update_data(),
    )

    assert record["max_in_flight"] == 1, "two updates were inside the project at once"


async def test_a_forced_refresh_does_not_overlap_a_running_poll():
    """The exact shape of the service call racing the scheduled poll."""
    coordinator, hass = _coordinator()
    record = {"in_flight": 0, "max_in_flight": 0}
    hass.async_add_executor_job = _overlap_detecting_executor(record)
    coordinator.async_refresh = coordinator._async_update_data  # bypass HA plumbing

    await asyncio.gather(
        coordinator._async_update_data(),
        coordinator.async_force_refresh(True),
    )

    assert record["max_in_flight"] == 1


async def test_both_updates_still_complete():
    """Serialising must not drop one of them."""
    coordinator, hass = _coordinator()
    record = {"in_flight": 0, "max_in_flight": 0}
    hass.async_add_executor_job = _overlap_detecting_executor(record)

    results = await asyncio.gather(
        coordinator._async_update_data(),
        coordinator._async_update_data(),
    )

    assert all(results), "an update returned no devices"


# ---------------------------------------------------------------------------
# async_force_refresh
# ---------------------------------------------------------------------------


async def test_a_forced_refresh_asks_for_a_full_discovery():
    """`forced: true` means rediscover, not just re-read the known statuses."""
    coordinator, hass = _coordinator(slim_poll_active=True)
    refreshed = asyncio.Event()

    async def fake_refresh():
        refreshed.set()

    coordinator.async_refresh = fake_refresh

    await coordinator.async_force_refresh(True)

    assert refreshed.is_set()
    assert coordinator._force_full_discovery is True


async def test_the_flag_makes_the_next_cycle_a_full_discovery():
    coordinator, hass = _coordinator(slim_poll_active=True)
    hass.async_add_executor_job = AsyncMock(return_value=_devices())
    coordinator._force_full_discovery = True

    await coordinator._async_update_data()

    coordinator.vimarproject.update.assert_not_called()  # runs via executor job
    hass.async_add_executor_job.assert_awaited_once()
    assert hass.async_add_executor_job.await_args[0][0] is coordinator.vimarproject.update


async def test_the_flag_is_cleared_so_discovery_does_not_repeat_forever():
    """Leaving it set would turn every poll into a full rediscovery."""
    coordinator, hass = _coordinator(slim_poll_active=True)
    hass.async_add_executor_job = AsyncMock(return_value=_devices())
    coordinator._force_full_discovery = True

    await coordinator._async_update_data()
    assert coordinator._force_full_discovery is False

    hass.async_add_executor_job = AsyncMock(
        return_value=[{"status_id": STATUS_ID, "status_value": "1"}]
    )
    await coordinator._async_update_data()

    assert hass.async_add_executor_job.await_args[0][0] is (
        coordinator.vimarconnection.get_status_only
    ), "the second cycle should be a slim poll again"


async def test_a_non_forced_refresh_leaves_the_poll_as_it_is():
    coordinator, hass = _coordinator()
    coordinator.async_refresh = AsyncMock()

    await coordinator.async_force_refresh(False)

    assert coordinator._force_full_discovery is False
    coordinator.async_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


async def test_the_service_goes_through_the_coordinator():
    """It must not touch vimarproject itself, on any thread."""
    from custom_components.vimar import SERVICE_UPDATE, add_services
    from custom_components.vimar.const import DOMAIN

    coordinator = MagicMock()
    coordinator.async_force_refresh = AsyncMock()

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": coordinator}}
    handlers = {}
    hass.services.async_register = lambda domain, service, handler, *a, **k: handlers.setdefault(
        service, handler
    )

    await add_services(hass)

    call = MagicMock()
    call.data = {"forced": True}
    await handlers[SERVICE_UPDATE](call)

    coordinator.async_force_refresh.assert_awaited_once_with(True)
    coordinator.vimarproject.update.assert_not_called()
    hass.async_add_executor_job.assert_not_called()
