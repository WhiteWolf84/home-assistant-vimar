"""A web server that stopped answering must show (Home Assistant required).

In slim-poll mode a request that fails - connection refused, host unreachable,
connect timeout - makes get_status_only() return None, and the coordinator
takes None as "transient, keep the previous state" and returns the old data as
a successful update. One None is indeed not worth an unavailable entity; but
nothing counts them, so as long as the requests fail FAST and the session id
is still cached, every poll "succeeds" and the values shown are those of the
last good poll.

How far this reaches, from the plant's history (checked 2026-11-10): the
outages recorded so far did show. On 2026-10-05 the entities went unavailable
because the web server answered LGMG-3019 first - which drops the session -
and the login that followed timed out; a slow failure also shows, when the
request outlasts the poll's own time budget. What does not show is the case
reproduced here: a web server that refuses the connection (or fails within
the budget) while the session is still cached. No such episode is in the
history; the behaviour is established from the code.

The "Connection" binary sensor reports whether a session id is cached, not
whether the web server answers: in that same history it only ever went off
for the 30 s after a session was dropped, including once (2026-10-07 03:27)
when no entity went unavailable at all.

Two related gaps are pinned here as well:

* a status id that no longer has a row is the cheapest sign that the web
  server's objects were renumbered (the SAI2 areas went 1410 -> 1672 on
  2026-10-17): nothing looks at it, and the "topology change" check in the
  slim poll compares the size of a dict the slim poll never changes;
* _is_auth_error() classifies by substring, "401" included, so a connection
  error that merely mentions port 4010 starts a re-authentication flow.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.binary_sensor import VimarStatusSensor  # noqa: E402
from custom_components.vimar.vimar_coordinator import (  # noqa: E402
    VimarDataUpdateCoordinator,
)
from custom_components.vimar.vimarlink.exceptions import (  # noqa: E402
    VimarConfigError,
    VimarConnectionError,
)

pytestmark = pytest.mark.integration  # Home Assistant required

DEVICE_ID = "768"
STATUS_ID = "769"
GONE_STATUS_ID = "999"


def _devices():
    return {
        DEVICE_ID: {
            "object_id": DEVICE_ID,
            "status": {"on/off": {"status_id": STATUS_ID, "status_value": "0"}},
        }
    }


def _coordinator(slim_results):
    """A coordinator in slim-poll mode, bypassing __init__."""
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)

    hass = MagicMock()
    hass.async_create_background_task = lambda coro, name=None: asyncio.create_task(coro)

    async def _job(func, *args):
        return func(*args)

    hass.async_add_executor_job = _job
    coordinator.hass = hass

    connection = MagicMock()
    connection.is_logged.return_value = True
    connection.get_status_only.return_value = slim_results
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
    coordinator._slim_poll_active = True
    coordinator._known_status_ids = [int(STATUS_ID)]
    coordinator._energy_refresh_ids = []
    coordinator._climate_refresh_ids = []
    coordinator._changed_device_ids = set()
    coordinator._device_state_hashes = {}
    coordinator._pending_write_guards = {}
    coordinator._last_device_count = 1
    coordinator._last_devices_hash = "unchanged"
    coordinator._consecutive_auth_failures = 0
    coordinator._reauth_triggered = False
    coordinator._refresh_tasks = set()
    coordinator._periodic_refresh_task = None
    coordinator._write_worker_task = None
    coordinator._update_lock = asyncio.Lock()
    coordinator._force_full_discovery = False
    coordinator.devices_for_platform = {}
    coordinator.last_update_success = True
    coordinator._maybe_refresh_energy_meters = AsyncMock()
    coordinator._maybe_refresh_climates = AsyncMock()
    return coordinator


# --- the outage -------------------------------------------------------------


async def test_one_failed_slim_poll_is_tolerated():
    """Not under discussion: a single hiccup keeps the previous state."""
    coordinator = _coordinator(slim_results=None)

    assert await coordinator._async_update_data() == _devices()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="review 2026-11 #1.1: failed slim polls are never counted, the outage never shows",
)
async def test_a_web_server_that_stays_down_fails_the_update():
    """Five polls in a row with no answer (40 s at the default interval)."""
    coordinator = _coordinator(slim_results=None)

    failed = False
    for _ in range(5):
        try:
            await coordinator._async_update_data()
        except UpdateFailed:
            failed = True
            break

    assert failed, "five unanswered polls were all reported as successful updates"


def _status_sensor(last_update_success: bool) -> VimarStatusSensor:
    connection = MagicMock()
    connection.host = "192.168.0.13"
    connection.port = 443
    connection.schema = "https"
    connection.base_url = "https://192.168.0.13:443"
    connection.certificate = None

    coordinator = MagicMock()
    coordinator.vimarconnection.connection = connection
    # A session obtained before the outage is still cached.
    coordinator.vimarconnection.is_logged.return_value = True
    coordinator.last_update_success = last_update_success
    coordinator.vimarconfig = {"verify_ssl": False}
    coordinator.entity_unique_id_prefix = "casa"
    sensor = VimarStatusSensor(coordinator)
    if hasattr(sensor, "update"):
        sensor.update()
    return sensor


def test_the_connection_sensor_is_on_while_updates_succeed():
    assert _status_sensor(last_update_success=True).is_on is True


@pytest.mark.xfail(
    strict=True,
    reason="review 2026-11 #1.1: the connection sensor only checks for a cached session id",
)
def test_the_connection_sensor_is_off_while_updates_fail():
    assert _status_sensor(last_update_success=False).is_on is False


# --- renumbered objects -----------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="review 2026-11 #3.3: a known status id without a row does not trigger a rediscovery",
)
async def test_a_status_id_that_lost_its_row_schedules_a_rediscovery():
    coordinator = _coordinator(slim_results=[{"status_id": STATUS_ID, "status_value": "1"}])
    coordinator._known_status_ids = [int(STATUS_ID), int(GONE_STATUS_ID)]

    await coordinator._async_update_data()

    assert not coordinator._slim_poll_active or coordinator._force_full_discovery


async def test_a_complete_slim_poll_schedules_nothing():
    coordinator = _coordinator(slim_results=[{"status_id": STATUS_ID, "status_value": "1"}])

    await coordinator._async_update_data()

    assert coordinator._slim_poll_active
    assert not coordinator._force_full_discovery


# --- error classification ---------------------------------------------------


def test_a_rejected_login_is_an_auth_error():
    coordinator = _coordinator(slim_results=[])

    assert coordinator._is_auth_error(VimarConfigError("Error during login: Log In Fallito"))


@pytest.mark.xfail(
    strict=True, reason="review 2026-11 #3.4: auth errors are recognised by substring ('401')"
)
def test_a_connection_error_mentioning_401_is_not_an_auth_error():
    coordinator = _coordinator(slim_results=[])
    error = VimarConnectionError(
        "Error during login: HTTPSConnectionPool(host='192.168.1.110', port=4010): "
        "Max retries exceeded"
    )

    assert not coordinator._is_auth_error(error)
