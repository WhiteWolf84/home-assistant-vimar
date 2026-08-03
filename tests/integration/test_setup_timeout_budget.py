"""Login and discovery get their own timeout budget (Home Assistant required).

Regression: every phase of an update was timed with the same
`asyncio.timeout(self._timeout)` - 6 s by default, tuned for a slim poll that
normally completes in ~0.07 s. Logging in and running a full discovery are
neither frequent nor fast: measured on real hardware, a cold login alone took
**4.3 s of the 6 s budget**, and that was on a healthy web server. A slightly
slower one - or a busy Home Assistant start, when every integration competes
for the executor pool - would blow the budget on the very first refresh and
leave the integration "not ready", retrying.

The setup phase now has its own budget while the slim poll keeps the tight one,
because a slow slim poll IS a real symptom and must still fail fast.
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


def _coordinator(timeout=0.3, setup_timeout=3.0, slim_poll_active=True):
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

    coordinator._timeout = timeout
    coordinator._setup_timeout = setup_timeout
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
    # _async_update_data serialises itself on this; the fixture bypasses
    # __init__, so it has to be provided here.
    coordinator._update_lock = asyncio.Lock()
    coordinator._force_full_discovery = False
    coordinator._write_worker_task = None
    coordinator.devices_for_platform = {}
    coordinator.last_update_success = True
    coordinator._maybe_refresh_energy_meters = AsyncMock()
    coordinator._maybe_refresh_climates = AsyncMock()
    return coordinator, hass


def _slow_executor_job(delay):
    """Stand-in for hass.async_add_executor_job that takes `delay` seconds."""

    async def _job(func, *args):
        await asyncio.sleep(delay)
        return func(*args)

    return _job


# ---------------------------------------------------------------------------
# The budget itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (6.0, 30.0),  # default: the floor applies
        (2.0, 30.0),  # minimum configurable timeout
        (10.0, 50.0),  # scales with the configured value
        (60.0, 120.0),  # maximum configurable timeout: the ceiling applies
    ],
)
def test_setup_budget_has_a_floor_and_a_ceiling(configured, expected):
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)
    coordinator._timeout = configured

    assert min(max(coordinator._timeout * 5, 30.0), 120.0) == expected


def test_setup_budget_is_never_shorter_than_the_poll_budget():
    """A configuration where setup had less room than a poll would be absurd."""
    for configured in (2.0, 6.0, 15.0, 60.0):
        assert min(max(configured * 5, 30.0), 120.0) >= configured


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------


async def test_a_discovery_slower_than_the_poll_budget_still_succeeds():
    """THE regression: 1 s of discovery against a 0.3 s poll budget.

    Reproduces the measured case (a cold login/discovery taking most of the
    poll's budget) at test speed. Before the fix this raised UpdateFailed and
    the integration reported itself as not ready.
    """
    coordinator, hass = _coordinator(timeout=0.3, setup_timeout=3.0, slim_poll_active=False)
    hass.async_add_executor_job = _slow_executor_job(1.0)

    devices = await coordinator._async_update_data()

    assert devices
    assert coordinator._slim_poll_active is True  # discovery completed


async def test_login_slower_than_the_poll_budget_still_succeeds():
    """The cold login is the slowest step of all; it must not fail the setup."""
    coordinator, hass = _coordinator(timeout=0.3, setup_timeout=3.0, slim_poll_active=False)
    hass.async_add_executor_job = _slow_executor_job(0.1)
    coordinator.vimarconnection.is_logged.return_value = False

    async def slow_login():
        await asyncio.sleep(1.0)  # >> the 0.3 s poll budget
        coordinator.vimarconnection.is_logged.return_value = True

    coordinator.validate_vimar_credentials = slow_login

    assert await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# What must NOT change: a slow slim poll is still a failure
# ---------------------------------------------------------------------------


async def test_a_slow_slim_poll_still_fails_fast():
    """The tight budget is the point of the poll: keep it."""
    from homeassistant.helpers.update_coordinator import UpdateFailed

    coordinator, hass = _coordinator(timeout=0.3, setup_timeout=3.0, slim_poll_active=True)
    hass.async_add_executor_job = _slow_executor_job(1.0)  # > poll budget

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_a_normal_slim_poll_is_unaffected():
    coordinator, hass = _coordinator(timeout=0.3, setup_timeout=3.0, slim_poll_active=True)
    hass.async_add_executor_job = _slow_executor_job(0.01)

    devices = await coordinator._async_update_data()

    assert devices[DEVICE_ID]["status"]["on/off"]["status_value"] == "1"
