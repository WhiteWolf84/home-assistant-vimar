"""Service registration and platform load/unload symmetry (HA required).

Three regressions are covered here.

1. `vimar.exec_vimar_sql` runs arbitrary SQL against the VIMAR web server
   database. It was registered with hass.services.async_register, which makes
   it callable by ANY Home Assistant user - including non-admin and
   script-only accounts - while the far less dangerous `reload` service right
   next to it was correctly admin-gated.

2. The unload path derived the platform list from
   coordinator.devices_for_platform, i.e. the platforms that ended up
   registering entities. A platform whose setup returns early without
   registering any (alarm_control_panel on an installation with no SAI2 areas)
   had been forwarded but was never unloaded, so it stayed half-loaded across
   every reload. The list of forwarded platforms is now recorded at setup and
   is the single source of truth for unloading.

3. The SAI2 alarm device was meant to hang off the "Vimar WebServer" device
   as its `via_device`, but the branch that did so read
   `coordinator.webserver_id`, which nothing ever assigned - and would not
   have worked anyway: it built a two-element identifier, while the hub is
   registered with three. Home Assistant resolves a via_device by looking the
   identifier up verbatim, so both halves have to come from one place, and the
   hub has to exist before the alarm platform (forwarded first) asks for it.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar import (  # noqa: E402
    SERVICE_EXEC_VIMAR_SQL,
    SERVICE_UPDATE,
    add_services,
    async_unload_entry,
)
from custom_components.vimar.alarm_control_panel import (  # noqa: E402
    async_setup_entry as alarm_async_setup_entry,
)
from custom_components.vimar.const import (  # noqa: E402
    DEVICE_TYPE_ALARM,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_LIGHTS,
    DOMAIN,
    PLATFORMS,
)
from custom_components.vimar.vimar_coordinator import (  # noqa: E402
    VimarDataUpdateCoordinator,
)

pytestmark = pytest.mark.integration  # Home Assistant required

ENTRY_ID = "entry-1"


# ---------------------------------------------------------------------------
# 1. Service registration
# ---------------------------------------------------------------------------


def _fake_hass_with_coordinator():
    """A hass whose DOMAIN data holds one coordinator, ready for service calls."""
    coordinator = MagicMock()
    coordinator.validate_vimar_credentials = AsyncMock()
    hass = MagicMock()
    hass.data = {DOMAIN: {ENTRY_ID: coordinator}}
    hass.services.async_register = MagicMock()
    hass.async_add_executor_job = AsyncMock(return_value=[{"ID": "1"}])
    return hass, coordinator


async def _registered_services(hass=None):
    """Run add_services() against a fake hass and report how each was registered.

    Returns (plain_service_names, admin_service_names, admin_calls).
    """
    if hass is None:
        hass, _ = _fake_hass_with_coordinator()

    with patch("custom_components.vimar.async_register_admin_service") as admin:
        await add_services(hass)

    plain = {call.args[1] for call in hass.services.async_register.call_args_list}
    admin_names = {call.args[2] for call in admin.call_args_list}
    return plain, admin_names, admin.call_args_list


async def test_exec_vimar_sql_is_admin_only():
    """Arbitrary SQL execution must be gated behind an admin check."""
    plain, admin_names, _ = await _registered_services()

    assert SERVICE_EXEC_VIMAR_SQL in admin_names
    assert SERVICE_EXEC_VIMAR_SQL not in plain


async def test_exec_vimar_sql_keeps_its_validation_schema():
    """Admin gating must not have dropped the 'sql' field validation."""
    _, _, admin_calls = await _registered_services()

    sql_call = next(c for c in admin_calls if c.args[2] == SERVICE_EXEC_VIMAR_SQL)

    assert sql_call.kwargs.get("schema") is not None


async def test_update_entities_stays_available_to_normal_users():
    """The harmless refresh service must not become admin-only by accident."""
    plain, admin_names, _ = await _registered_services()

    assert SERVICE_UPDATE in plain
    assert SERVICE_UPDATE not in admin_names


async def test_sql_service_uses_the_public_connection_api():
    """The service must not reach into VimarLink's private _request_vimar_sql."""
    hass, coordinator = _fake_hass_with_coordinator()
    _, _, admin_calls = await _registered_services(hass)
    handler = next(c for c in admin_calls if c.args[2] == SERVICE_EXEC_VIMAR_SQL).args[3]

    await handler(MagicMock(data={"sql": "SELECT 1"}))

    coordinator.validate_vimar_credentials.assert_awaited_once()
    hass.async_add_executor_job.assert_awaited_once_with(
        coordinator.vimarconnection.execute_sql, "SELECT 1"
    )


# ---------------------------------------------------------------------------
# 2. Load / unload symmetry
# ---------------------------------------------------------------------------


def _coordinator(vimarconfig=None):
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)
    coordinator.vimarconfig = vimarconfig or {}
    coordinator.devices_for_platform = {}
    coordinator.forwarded_platforms = []
    coordinator.entry = MagicMock(entry_id=ENTRY_ID)
    coordinator.hass = MagicMock()
    coordinator.hass.config_entries.async_forward_entry_setups = AsyncMock()
    # Built in __init__ and never unset (see the class docstring), so a
    # coordinator without it is not one the code has to cope with. Empty here
    # because these tests are about which platforms get forwarded; the room
    # labels applied at the end of the same method are covered in
    # test_room_labels.py.
    coordinator.vimarproject = MagicMock()
    coordinator.vimarproject.devices = {}
    return coordinator


async def test_forwarded_platforms_records_every_forwarded_platform():
    """What we record must be exactly what we hand to Home Assistant."""
    coordinator = _coordinator()

    await coordinator.async_register_devices_platforms()

    forwarded_arg = coordinator.hass.config_entries.async_forward_entry_setups.call_args.args[1]
    assert coordinator.forwarded_platforms == forwarded_arg
    assert coordinator.forwarded_platforms == PLATFORMS


async def test_ignored_platforms_are_not_forwarded_but_binary_sensor_survives():
    """binary_sensor carries the connection sensor and is never skipped."""
    coordinator = _coordinator(
        {"ignore": [DEVICE_TYPE_LIGHTS, DEVICE_TYPE_BINARY_SENSOR]},
    )

    await coordinator.async_register_devices_platforms()

    assert DEVICE_TYPE_LIGHTS not in coordinator.forwarded_platforms
    assert DEVICE_TYPE_BINARY_SENSOR in coordinator.forwarded_platforms


def _unload_fixture(forwarded, devices_for_platform, unload_result=True):
    """A coordinator + hass pair ready for async_unload_entry."""
    coordinator = MagicMock()
    coordinator.async_shutdown_write_worker = AsyncMock()
    coordinator.async_close_connection = AsyncMock()
    coordinator.forwarded_platforms = forwarded
    coordinator.devices_for_platform = devices_for_platform

    entry = MagicMock(entry_id=ENTRY_ID)
    hass = MagicMock()
    hass.data = {DOMAIN: {ENTRY_ID: coordinator}}
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=unload_result)
    return coordinator, hass, entry


async def test_unload_covers_a_platform_that_registered_no_entities():
    """THE regression: alarm_control_panel with no SAI2 areas must be unloaded.

    devices_for_platform is deliberately left empty, reproducing an
    installation where every platform setup returned early.
    """
    _coord, hass, entry = _unload_fixture(list(PLATFORMS), {})

    assert await async_unload_entry(hass, entry) is True

    unloaded = hass.config_entries.async_unload_platforms.call_args.args[1]
    assert DEVICE_TYPE_ALARM in unloaded
    assert unloaded == PLATFORMS
    assert ENTRY_ID not in hass.data[DOMAIN]


async def test_unload_falls_back_to_devices_for_platform():
    """An entry set up before this change has no recorded list; still unload."""
    _coord, hass, entry = _unload_fixture(
        [], {DEVICE_TYPE_LIGHTS: [], DEVICE_TYPE_BINARY_SENSOR: []}
    )

    await async_unload_entry(hass, entry)

    unloaded = hass.config_entries.async_unload_platforms.call_args.args[1]
    assert set(unloaded) == {DEVICE_TYPE_LIGHTS, DEVICE_TYPE_BINARY_SENSOR}


async def test_failed_unload_keeps_the_coordinator_registered():
    """If HA refuses the unload, the entry must stay in hass.data."""
    _coord, hass, entry = _unload_fixture(list(PLATFORMS), {}, unload_result=False)

    assert await async_unload_entry(hass, entry) is False
    assert ENTRY_ID in hass.data[DOMAIN]


async def test_unload_releases_the_pooled_http_connections():
    """Sessions are kept alive for reuse, so unload must close them."""
    coordinator, hass, entry = _unload_fixture(list(PLATFORMS), {})

    await async_unload_entry(hass, entry)

    coordinator.async_close_connection.assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. The alarm hangs off the web server device
# ---------------------------------------------------------------------------


async def test_the_hub_device_is_registered_before_any_platform_is_forwarded():
    """The alarm is forwarded first, so the device it points at must pre-exist."""
    coordinator = _coordinator()
    coordinator.entity_unique_id_prefix = "casa"
    calls: list[str] = []
    coordinator.hass.config_entries.async_forward_entry_setups = AsyncMock(
        side_effect=lambda *a, **kw: calls.append("forward")
    )

    with patch("custom_components.vimar.vimar_coordinator.dr.async_get") as dev_reg:
        dev_reg.return_value.async_get_or_create.side_effect = lambda **kw: calls.append("hub")
        await coordinator.async_register_devices_platforms()

    assert calls == ["hub", "forward"]
    created = dev_reg.return_value.async_get_or_create.call_args.kwargs
    assert created["identifiers"] == {(DOMAIN, "casa", "status")}
    assert created["name"] == "Vimar WebServer"


async def test_the_alarm_device_points_at_the_hub_identifier():
    """A two-element via_device would never resolve against a three-element hub."""
    coordinator = MagicMock()
    coordinator.devices_for_platform = {}
    coordinator.vimarproject.sai2_groups = {}
    coordinator.webserver_identifiers = (DOMAIN, "casa", "status")

    hass = MagicMock()
    hass.data = {DOMAIN: {ENTRY_ID: coordinator}}
    entry = MagicMock(entry_id=ENTRY_ID)
    entry.data = {}
    entry.options = {}

    with patch("custom_components.vimar.alarm_control_panel.dr.async_get") as dev_reg:
        await alarm_async_setup_entry(hass, entry, MagicMock())

    registered = dev_reg.return_value.async_get_or_create.call_args.kwargs
    assert registered["via_device"] == (DOMAIN, "casa", "status")


def test_the_hub_identifier_has_one_definition():
    """Hub and via_device must not be able to drift apart again."""
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)
    coordinator.entity_unique_id_prefix = "casa"

    # The same tuple the connection binary_sensor puts in its device_info.
    assert coordinator.webserver_identifiers == (DOMAIN, "casa", "status")
    assert not hasattr(VimarDataUpdateCoordinator, "webserver_id"), (
        "webserver_id was never assigned; reintroducing it brings the dead branch back"
    )


async def test_alarm_platform_without_sai2_registers_an_empty_entity_list():
    """The early-return path must leave devices_for_platform coherent."""
    coordinator = MagicMock()
    coordinator.devices_for_platform = {}
    coordinator.vimarproject.sai2_groups = None

    hass = MagicMock()
    hass.data = {DOMAIN: {ENTRY_ID: coordinator}}
    entry = MagicMock(entry_id=ENTRY_ID)
    add_entities = MagicMock()

    await alarm_async_setup_entry(hass, entry, add_entities)

    assert coordinator.devices_for_platform[DEVICE_TYPE_ALARM] == []
    add_entities.assert_not_called()
