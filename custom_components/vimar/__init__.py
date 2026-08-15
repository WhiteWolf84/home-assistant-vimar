"""Vimar Platform integration."""

from copy import deepcopy

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
    SERVICE_RELOAD,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify

from .const import (
    _LOGGER,
    CONF_CERTIFICATE,
    CONF_DELETE_AND_RELOAD_ALL_ENTITIES,
    CONF_GLOBAL_CHANNEL_ID,
    CONF_IGNORE_PLATFORM,
    CONF_OVERRIDE,
    CONF_OVERRIDE_IMPORTED,
    CONF_SCHEMA,
    DEFAULT_CERTIFICATE,
    DEFAULT_PORT,
    DEFAULT_SCHEMA,
    DEFAULT_TIMEOUT,
    DEFAULT_USERNAME,
    DOMAIN,
    DOMAIN_CONFIG_YAML,
)
from .vimar_coordinator import VimarDataUpdateCoordinator

log = _LOGGER

CONFIG_DOMAIN_SCHEMA = {
    vol.Optional(CONF_HOST): cv.string,
    vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
    vol.Optional(CONF_PASSWORD): cv.string,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Optional(CONF_SCHEMA, default=DEFAULT_SCHEMA): cv.string,
    vol.Optional(CONF_CERTIFICATE, default=DEFAULT_CERTIFICATE): vol.Any(cv.string, None),
    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): vol.Range(min=2, max=60),
    vol.Optional(CONF_GLOBAL_CHANNEL_ID): vol.Range(min=1, max=99999),
    vol.Optional(CONF_IGNORE_PLATFORM, default=[]): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_OVERRIDE, default=[]): cv.ensure_list,
}
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema(CONFIG_DOMAIN_SCHEMA)},
    extra=vol.ALLOW_EXTRA,
)

SERVICE_UPDATE = "update_entities"
SERVICE_UPDATE_SCHEMA = vol.Schema({vol.Optional("forced", default=True): cv.boolean})
SERVICE_EXEC_VIMAR_SQL = "exec_vimar_sql"
SERVICE_EXEC_VIMAR_SQL_SCHEMA = vol.Schema({vol.Required("sql"): cv.string})


async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Set up from config."""
    hass.data.setdefault(DOMAIN, {})

    await add_services(hass)

    # if there are no configuration.yaml settings then terminate
    if config.get(DOMAIN) is None:
        # We get here if the integration is set up using config flow
        return True

    conf = config.get(DOMAIN, {})
    hass.data.setdefault(DOMAIN_CONFIG_YAML, conf)

    if CONF_USERNAME in conf:
        configured = set(entry for entry in hass.config_entries.async_entries(DOMAIN))

        if len(configured) == 0:
            log.info("Importing configuration from yaml...after you can remove from yaml")
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": config_entries.SOURCE_IMPORT},
                    data=conf.copy(),
                )
            )
        else:
            log.debug("Configuration from yaml already imported: you can remove from yaml")

    return True


def _async_migrate_yaml_overrides(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move `device_override` out of configuration.yaml and into the entry, once.

    Device overrides were the last setting reachable only from YAML, and not
    because nobody had written a form for them: async_setup_entry re-read them
    from `hass.data[DOMAIN_CONFIG_YAML]` on every start and overwrote whatever
    the entry held, so YAML was not the default source, it was the only one.
    The entry now owns them, which is what makes a UI possible - and this is
    how the setting gets there without the user having to retype it.

    The YAML import flow does not cover this case. It only runs when NO entry
    exists yet (see async_setup), so anyone who set the integration up through
    the UI and kept their overrides in YAML - the normal state of affairs,
    since that was the only place they worked - would never have been migrated
    by it.

    Guarded by a flag rather than by "the entry has no overrides yet": see
    CONF_OVERRIDE_IMPORTED for why the two are not the same thing. The flag is
    written even when there is nothing to migrate, because what it records is
    that the window has closed, so a `device_override:` block added to YAML
    later is not silently adopted behind the UI's back.
    """
    if entry.data.get(CONF_OVERRIDE_IMPORTED):
        return

    yaml_overrides = (hass.data.get(DOMAIN_CONFIG_YAML) or {}).get(CONF_OVERRIDE) or []
    data = {**entry.data, CONF_OVERRIDE_IMPORTED: True}

    if yaml_overrides and not entry.data.get(CONF_OVERRIDE):
        # Deep copy: VimarDeviceCustomizer rewrites the dicts it is handed
        # (device_override_check turns `filter_*` keys into `filter`/`actions`
        # entries in place), and these are about to be persisted.
        data[CONF_OVERRIDE] = deepcopy(yaml_overrides)
        log.warning(
            "Imported %d device_override rule(s) from configuration.yaml into the "
            "Vimar config entry. They are now managed in the integration's options, "
            "and the `device_override:` block can be removed from configuration.yaml - "
            "it will no longer be read",
            len(yaml_overrides),
        )

    hass.config_entries.async_update_entry(entry, data=data)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Vimar from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    if entry.unique_id is None:
        log.info("vimar unique id was None")
        unique_id = slugify(entry.title)
        hass.config_entries.async_update_entry(entry, unique_id=unique_id)

    # Before anything reads the config: the entry is the only source of device
    # overrides from here on, so whatever is still in YAML has to be moved in
    # first. Runs before the coordinator exists and before the update listener
    # is registered (below), so the async_update_entry it may perform cannot
    # trigger a reload underneath us.
    _async_migrate_yaml_overrides(hass, entry)

    vimarconfig = (entry.options or {}).copy()
    if CONF_HOST not in vimarconfig:
        vimarconfig.update(entry.data or {})

    # No YAML fallback here on purpose. It used to overwrite the key
    # unconditionally, which meant an override could ONLY ever come from
    # configuration.yaml - the config entry had no say, and storing overrides
    # in it would have been silently pointless. Anything still in YAML has been
    # migrated into the entry above; consulting it again afterwards would
    # resurrect rules the user has since deleted in the UI.
    vimarconfig[CONF_OVERRIDE] = vimarconfig.get(CONF_OVERRIDE) or []

    coordinator = VimarDataUpdateCoordinator(hass, entry=entry, vimarconfig=vimarconfig)
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.init_vimarproject()

    # FIX #6: always use async_config_entry_first_refresh() unconditionally.
    # async_setup_entry is only ever called by HA when the entry is in state
    # SETUP_IN_PROGRESS, so the old if/else branch was redundant.
    # More importantly, the string comparison `entry.state.name == "SETUP_IN_PROGRESS"`
    # used a non-public API (enum .name) that could silently break on any HA rename.
    # async_config_entry_first_refresh() is the HA-recommended call here: it
    # propagates ConfigEntryNotReady correctly on first-run failures.
    await coordinator.async_config_entry_first_refresh()

    if (entry.data or {}).get(CONF_DELETE_AND_RELOAD_ALL_ENTITIES):
        options = entry.data.copy()
        options.pop(CONF_DELETE_AND_RELOAD_ALL_ENTITIES)
        await coordinator.async_remove_old_devices()
        hass.config_entries.async_update_entry(entry, data=options)

    await coordinator.async_register_devices_platforms()
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def add_services(hass: HomeAssistant):
    """Add services."""

    async def service_update_call(call):
        # Goes through the coordinator instead of running its own executor job.
        # The old version rebuilt the whole device tree on a separate thread
        # while a scheduled poll could be reading and mutating it, and wrote
        # the result behind the coordinator's back, so entities only saw it
        # whenever the next poll happened to run.
        forced = call.data.get("forced")
        for item in hass.data[DOMAIN].values():
            coordinator: VimarDataUpdateCoordinator = item
            await coordinator.async_force_refresh(forced)

    hass.services.async_register(DOMAIN, SERVICE_UPDATE, service_update_call, SERVICE_UPDATE_SCHEMA)

    async def service_exec_vimar_sql_call(call):
        data = call.data
        sql = data.get("sql")
        for item in hass.data[DOMAIN].values():
            coordinator: VimarDataUpdateCoordinator = item
            await coordinator.validate_vimar_credentials()
            if coordinator.vimarconnection:
                payload = await hass.async_add_executor_job(
                    coordinator.vimarconnection.execute_sql, sql
                )
                _LOGGER.info(
                    SERVICE_EXEC_VIMAR_SQL + " done: SQL: %s . Result: %s",
                    sql,
                    str(payload),
                )

    # Admin-only: this runs arbitrary SQL against the VIMAR web server
    # database. Registered with hass.services.async_register it was callable
    # by ANY Home Assistant user (including non-admin and script-only
    # accounts), unlike the far less dangerous reload service next to it.
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_EXEC_VIMAR_SQL,
        service_exec_vimar_sql_call,
        schema=SERVICE_EXEC_VIMAR_SQL_SCHEMA,
    )

    async def _handle_reload(service):
        entries_to_reload = []
        for item in hass.data[DOMAIN].values():
            coordinator: VimarDataUpdateCoordinator = item
            entries_to_reload.append(coordinator.entry)
        for entry in entries_to_reload:
            await async_reload_entry(hass, entry)

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD,
        _handle_reload,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if entry.entry_id not in hass.data[DOMAIN]:
        return True
    coordinator: VimarDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_shutdown_write_worker()
    await coordinator.async_close_connection()
    # Unload exactly what was forwarded at setup. Deriving the list from
    # devices_for_platform missed any platform that returned early without
    # registering entities (alarm_control_panel on installations without SAI2),
    # leaving it loaded forever and breaking the next reload.
    platforms = coordinator.forwarded_platforms or list(coordinator.devices_for_platform.keys())
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded and entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
