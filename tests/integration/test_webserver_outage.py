"""A web server that stopped answering must show (Home Assistant required).

In slim-poll mode a request that fails - connection refused, host unreachable,
connect timeout - makes get_status_only() return None, and the coordinator
used to take None as "transient, keep the previous state" and return the old
data as a successful update. Nothing counted them, so as long as the requests
failed FAST and the session id was still cached, every poll "succeeded" and
the values shown were those of the last good poll. An exception, on the other
hand - the read timeout of 2026-10-05 - failed the update at once and turned
every entity unavailable for one missed poll.

Both now count the same: a failed poll, whatever made it fail, keeps the last
values READ, and the third in a row fails the update. The "Connection" binary
sensor goes off at the first one.

How far the old behaviour reached, from the plant's history (2026-11-10): no
recorded episode shows an outage that went unnoticed. On 2026-10-05 the
entities did go unavailable, the session having been dropped first and the
login that followed having timed out. The fast-failing case is established
from the code. The sensor only ever went off for the 30 s after a session was
dropped - compatible with it reporting the session rather than the web
server, which is what the code said it did.

Two related gaps are still open, pinned here as expected failures:

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
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.alarm_control_panel import VimarAlarmControlPanel  # noqa: E402
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

READ_TIMEOUT = Exception(
    "HTTPSConnectionPool(host='192.168.1.110', port=443): Read timed out. (read timeout=7)"
)


def _fail_with(coordinator, how: str) -> None:
    connection = coordinator.vimarconnection
    if how == "no answer":
        connection.get_status_only.return_value = None
    elif how == "exception":
        connection.get_status_only.side_effect = READ_TIMEOUT
    else:  # the session was dropped and the login does not go through
        connection.is_logged.return_value = False
        connection.check_login.side_effect = VimarConnectionError("Error during login: timeout")


def _answer_again(coordinator) -> None:
    connection = coordinator.vimarconnection
    connection.is_logged.return_value = True
    connection.check_login.side_effect = None
    connection.get_status_only.side_effect = None
    connection.get_status_only.return_value = [{"status_id": STATUS_ID, "status_value": "1"}]


WAYS_TO_FAIL = ["no answer", "exception", "login"]


@pytest.mark.parametrize("how", WAYS_TO_FAIL)
async def test_two_failed_polls_keep_the_last_values(how):
    """One interruption - whatever its shape - does not blank the installation."""
    coordinator = _coordinator(slim_results=None)
    _fail_with(coordinator, how)

    assert await coordinator._async_update_data() == _devices()
    assert await coordinator._async_update_data() == _devices()


@pytest.mark.parametrize("how", WAYS_TO_FAIL)
async def test_the_third_failed_poll_in_a_row_fails_the_update(how):
    coordinator = _coordinator(slim_results=None)
    _fail_with(coordinator, how)
    await coordinator._async_update_data()
    await coordinator._async_update_data()

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_a_web_server_that_stays_down_keeps_failing():
    coordinator = _coordinator(slim_results=None)
    for _ in range(2):
        await coordinator._async_update_data()

    for _ in range(3):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


async def test_a_good_poll_starts_the_count_again():
    """Two failures, a good poll, two failures: never three in a row."""
    coordinator = _coordinator(slim_results=None)
    for _ in range(2):
        await coordinator._async_update_data()
    _answer_again(coordinator)
    data = await coordinator._async_update_data()
    assert data[DEVICE_ID]["status"]["on/off"]["status_value"] == "1"

    _fail_with(coordinator, "no answer")

    assert await coordinator._async_update_data() == data
    assert await coordinator._async_update_data() == data


async def test_the_first_update_of_an_entry_is_not_softened():
    """Nothing to keep yet, and the setup has to be retried."""
    coordinator = _coordinator(slim_results=None)
    coordinator._first_update_data_executed = False
    coordinator.vimarproject.update.side_effect = READ_TIMEOUT

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_a_rejected_login_is_not_softened():
    """Wrong credentials are not an interruption to sit out."""
    coordinator = _coordinator(slim_results=None)
    coordinator.entry = None
    coordinator.vimarconnection.is_logged.return_value = False
    coordinator.vimarconnection.check_login.side_effect = VimarConfigError(
        "Error during login: Log In Fallito"
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


# --- the Connection sensor --------------------------------------------------


def _status_sensor(coordinator) -> VimarStatusSensor:
    connection = MagicMock()
    connection.host = "192.168.0.13"
    connection.port = 443
    connection.schema = "https"
    connection.base_url = "https://192.168.0.13:443"
    connection.certificate = None
    coordinator.vimarconnection.connection = connection
    coordinator.vimarconfig = {"verify_ssl": False}
    coordinator.entity_unique_id_prefix = "casa"
    return VimarStatusSensor(coordinator)


async def test_the_connection_sensor_goes_off_at_the_first_failed_poll():
    """Two polls before the entities go unavailable, with the session still cached."""
    coordinator = _coordinator(slim_results=[{"status_id": STATUS_ID, "status_value": "1"}])
    sensor = _status_sensor(coordinator)
    await coordinator._async_update_data()
    assert sensor.is_on is True

    _fail_with(coordinator, "no answer")
    await coordinator._async_update_data()

    assert coordinator.vimarconnection.is_logged() is True
    assert sensor.is_on is False


async def test_the_connection_sensor_comes_back_with_the_first_good_poll():
    coordinator = _coordinator(slim_results=None)
    sensor = _status_sensor(coordinator)
    await coordinator._async_update_data()
    assert sensor.is_on is False

    _answer_again(coordinator)
    await coordinator._async_update_data()

    assert sensor.is_on is True


async def test_the_connection_sensor_is_pushed_by_the_coordinator():
    """It changes with the poll that changed it, not up to 30 s later."""
    coordinator = MagicMock()
    sensor = _status_sensor(coordinator)
    sensor.async_on_remove = MagicMock()

    await sensor.async_added_to_hass()

    assert sensor.should_poll is False
    coordinator.async_add_listener.assert_called_once_with(sensor.async_write_ha_state)


# --- the alarm areas --------------------------------------------------------


async def test_the_alarm_areas_keep_the_last_value_that_was_read():
    """Kept means untouched: no value is made up while the polls fail."""
    coordinator = _coordinator(slim_results=None)
    project = coordinator.vimarproject
    project.sai2_groups = {"1672": {"name": "Reparto Giorno", "children": {}}}
    project.sai2_area_values = {"1672": "00000011"}
    project.sai2_optimistic_until = {}

    for _ in range(2):
        await coordinator._async_update_data()

    assert project.sai2_area_values == {"1672": "00000011"}
    coordinator.vimarconnection.get_sai2_area_raw_values.assert_not_called()


@pytest.mark.parametrize(("update_ok", "available"), [(True, True), (False, False)])
def test_the_alarm_areas_go_unavailable_with_the_failed_update(update_ok, available):
    """After the third failed poll they follow every other entity."""
    coordinator = MagicMock()
    coordinator.last_update_success = update_ok
    coordinator.vimarproject.sai2_groups = {"1672": {"name": "Reparto Giorno"}}
    panel = VimarAlarmControlPanel.__new__(VimarAlarmControlPanel)
    panel.coordinator = coordinator
    panel._group_id = "1672"

    assert panel.available is available


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
