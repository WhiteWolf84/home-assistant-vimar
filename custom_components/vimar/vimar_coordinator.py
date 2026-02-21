"""Vimar Update State coordinator."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import aiohttp
import async_timeout
from homeassistant.config_entries import ConfigEntry, SOURCE_REAUTH
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TIMEOUT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, PlatformNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    _LOGGER,
    CONF_CERTIFICATE,
    CONF_GLOBAL_CHANNEL_ID,
    CONF_IGNORE_PLATFORM,
    CONF_OVERRIDE,
    CONF_SECURE,
    DEFAULT_CERTIFICATE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DEVICE_TYPE_BINARY_SENSOR,
    DOMAIN,
    PLATFORMS,
)
from .vimar_device_customizer import VimarDeviceCustomizer
from .vimarlink.vimarlink import VimarLink, VimarProject

log = _LOGGER


class VimarDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    vimarconnection: VimarLink | None = None
    vimarproject: VimarProject | None = None
    _timeout: float = DEFAULT_TIMEOUT
    webserver_id = ""
    entity_unique_id_prefix = ""
    _first_update_data_executed = False
    _platforms_registered = False
    _last_devices_hash = ""
    _device_state_hashes: dict[str, str] = {}
    _changed_device_ids: set[str] = set()
    _consecutive_auth_failures = 0
    _reauth_triggered = False

    # --- slim-poll state ---
    _known_status_ids: list[int] = []
    _slim_poll_active: bool = False
    _last_device_count: int = -1

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, vimarconfig: ConfigType) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.vimarconfig = vimarconfig
        self.devices_for_platform = {}
        if entry:
            self.entity_unique_id_prefix = entry.unique_id or ""
        timeout = vimarconfig.get(CONF_TIMEOUT) or DEFAULT_TIMEOUT
        if timeout > 0:
            self._timeout = float(timeout)
        uptade_interval = float(vimarconfig.get(CONF_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL)
        if uptade_interval < 1:
            uptade_interval = DEFAULT_SCAN_INTERVAL
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=uptade_interval), config_entry=entry
        )

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        _LOGGER.debug("Updating coordinator..")

        try:
            if self.vimarproject is None:
                raise PlatformNotReady

            # if not logged, execute login with another timeout
            if self.vimarconnection is None or not self.vimarconnection.is_logged():
                async with async_timeout.timeout(self._timeout):
                    await self.validate_vimar_credentials()

            async with async_timeout.timeout(self._timeout):
                forced = not self._first_update_data_executed or not self._platforms_registered

                if forced or not self._slim_poll_active:
                    # FULL DISCOVERY: first run, login refresh, or topology change
                    _LOGGER.debug("Vimar: running full discovery")
                    devices = await self.hass.async_add_executor_job(self.vimarproject.update, True)

                    if devices and len(devices) > 0:
                        # Build the status-id index for subsequent slim polls
                        self._known_status_ids = self._collect_status_ids(devices)
                        self._last_device_count = len(devices)
                        self._slim_poll_active = True
                        _LOGGER.debug(
                            "Vimar: discovery complete - %d devices, %d status IDs indexed for slim poll",
                            len(devices),
                            len(self._known_status_ids),
                        )
                else:
                    # SLIM POLL: single-table query on known primary keys
                    _LOGGER.debug(
                        "Vimar: slim poll (%d status IDs)", len(self._known_status_ids)
                    )
                    slim_results = await self.hass.async_add_executor_job(
                        self.vimarconnection.get_status_only, self._known_status_ids
                    )

                    if slim_results is None:
                        # Transient error (Unknown-Payload, timeout) - keep previous state
                        _LOGGER.debug(
                            "Vimar: slim poll returned None (transient), keeping previous state"
                        )
                        self._changed_device_ids = set()
                        return self.vimarproject.devices

                    # Patch status values into the existing device tree (no meta rewrite)
                    self._apply_slim_results(self.vimarproject.devices, slim_results)
                    devices = self.vimarproject.devices

                    # Topology check: if device count drifted, force rediscovery next cycle
                    current_count = len(devices)
                    if current_count != self._last_device_count:
                        _LOGGER.info(
                            "Vimar: topology change detected (%d → %d devices), scheduling rediscovery",
                            self._last_device_count,
                            current_count,
                        )
                        self._slim_poll_active = False
                        self._last_device_count = current_count

            if not devices or len(devices) == 0:
                raise UpdateFailed("Could not find any devices on Vimar Webserver")

            if not self._first_update_data_executed:
                self._first_update_data_executed = True

            # Hash-based change detection: populate _changed_device_ids for entity filtering
            self._changed_device_ids = self._detect_state_changes(devices)

            # if last update failed, check devices changes and reload if need
            if not self.last_update_success or self._last_devices_hash == "":
                self._reload_entry_if_devices_changed()

            # Reset auth failure counter on success
            self._consecutive_auth_failures = 0

            return devices

        except ConfigEntryAuthFailed:
            # Authentication error - trigger reauth flow
            self._handle_auth_failure()
            raise
        except TimeoutError:
            _LOGGER.warning("Timeout communicating with Vimar web server")
            raise UpdateFailed("Timeout communicating with Vimar web server")
        except aiohttp.ClientError as err:
            _LOGGER.warning("Client error communicating with Vimar: %s", err)
            raise UpdateFailed(f"Client error: {err}")
        except BaseException as err:
            # Check if error is authentication related
            if self._is_auth_error(err):
                self._handle_auth_failure()
                raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
            raise UpdateFailed(f"Error communicating with API: {err}")

    def _is_auth_error(self, error: BaseException) -> bool:
        """Check if error is authentication related."""
        error_str = str(error).lower()
        auth_indicators = [
            "log in fallito",
            "invalid credentials",
            "unauthorized",
            "401",
            "authentication failed",
            "login failed",
        ]
        return any(indicator in error_str for indicator in auth_indicators)

    def _handle_auth_failure(self) -> None:
        """Handle authentication failure by triggering reauth flow.
        
        Only triggers once to avoid spamming the user with reauth prompts.
        """
        self._consecutive_auth_failures += 1
        
        if not self._reauth_triggered and self._consecutive_auth_failures >= 2:
            _LOGGER.warning(
                "Authentication failed %d times, triggering re-authentication flow",
                self._consecutive_auth_failures
            )
            self._reauth_triggered = True
            
            if self.entry:
                self.entry.async_start_reauth(self.hass)

    # ------------------------------------------------------------------
    # Slim-poll helpers
    # ------------------------------------------------------------------

    def _collect_status_ids(self, devices: dict) -> list[int]:
        """Extract all status_id integers from all known devices."""
        ids: set[int] = set()
        for device in devices.values():
            for status in device.get("status", {}).values():
                sid = status.get("status_id")
                if sid is not None:
                    try:
                        ids.add(int(sid))
                    except (ValueError, TypeError):
                        pass
        return list(ids)

    def _apply_slim_results(self, devices: dict, slim_results: list) -> None:
        """Patch CURRENT_VALUE from slim poll into existing device tree.

        Builds a reverse index (status_id -> (device_id, status_name)) once,
        then applies all updates in O(n) without touching metadata.
        """
        # Build reverse index on first call or if devices changed
        index: dict[str, tuple[str, str]] = {}
        for device_id, device in devices.items():
            for status_name, status in device.get("status", {}).items():
                sid = status.get("status_id")
                if sid is not None:
                    index[str(sid)] = (device_id, status_name)

        for row in slim_results:
            sid = str(row.get("status_id", ""))
            val = row.get("status_value")
            if sid in index:
                dev_id, sname = index[sid]
                devices[dev_id]["status"][sname]["status_value"] = val

    # ------------------------------------------------------------------
    # Existing methods (unchanged)
    # ------------------------------------------------------------------

    async def init_vimarproject(self) -> None:
        """Init VimarLink and VimarProject from entry config."""
        self._last_devices_hash = ""
        self._first_update_data_executed = False
        self._platforms_registered = False
        self._slim_poll_active = False
        self._known_status_ids = []
        self._last_device_count = -1
        self._consecutive_auth_failures = 0
        self._reauth_triggered = False
        self.devices_for_platform = {}
        vimarconfig = self.vimarconfig
        schema = "https" if vimarconfig.get(CONF_SECURE) else "http"
        host = vimarconfig.get(CONF_HOST)
        port = vimarconfig.get(CONF_PORT)
        username = vimarconfig.get(CONF_USERNAME)
        password = vimarconfig.get(CONF_PASSWORD)
        certificate = None
        if schema == "https" and vimarconfig.get(CONF_VERIFY_SSL):
            certificate = vimarconfig.get(CONF_CERTIFICATE, DEFAULT_CERTIFICATE)
        timeout = vimarconfig.get(CONF_TIMEOUT)
        global_channel_id = vimarconfig.get(CONF_GLOBAL_CHANNEL_ID)
        device_overrides = vimarconfig.get(CONF_OVERRIDE, [])

        # initialize a new VimarLink object
        vimarconnection = VimarLink(schema, host, port, username, password, certificate, timeout)

        device_customizer = VimarDeviceCustomizer(vimarconfig, device_overrides)

        def device_customizer_fn(device):
            device_customizer.customize_device(device)

        # will hold all the devices and their states
        vimarproject = VimarProject(vimarconnection, device_customizer_fn)

        if global_channel_id is not None:
            vimarproject.global_channel_id = global_channel_id

        self.vimarconnection = vimarconnection
        self.vimarproject = vimarproject

    async def validate_vimar_credentials(self) -> None:
        """Validate Vimar credential config.
        
        Raises:
            ConfigEntryAuthFailed: If authentication fails
            PlatformNotReady: If connection cannot be established
        """
        if self.vimarconnection is None:
            await self.init_vimarproject()
        try:
            if self.vimarconnection is None:
                raise PlatformNotReady("Vimar connection not initialized")
            valid_login = await self.hass.async_add_executor_job(self.vimarconnection.check_login)
            if not valid_login:
                raise ConfigEntryAuthFailed("Invalid credentials")
        except ConfigEntryAuthFailed:
            raise
        except BaseException as err:
            if self._is_auth_error(err):
                raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
            raise err

    async def async_register_devices_platforms(self):
        """Execute async_forward_entry_setup for each platform."""
        self.devices_for_platform = {}
        ignored_platforms = self.vimarconfig.get(CONF_IGNORE_PLATFORM) or []
        # DEVICE_TYPE_BINARY_SENSOR needed for webserver status sensor
        platforms = [
            i for i in PLATFORMS if i not in ignored_platforms or i == DEVICE_TYPE_BINARY_SENSOR
        ]
        await self.hass.config_entries.async_forward_entry_setups(self.entry, platforms)

        self._platforms_registered = True
        if len(self.devices_for_platform) > 0:
            await self.async_remove_old_devices()

    def _reload_entry_if_devices_changed(self):
        if self.vimarproject:
            devices = self.vimarproject.devices
            if devices is not None and len(devices) > 0:
                devices_hash = ""
                for device_id, device in devices.items():
                    device_hash = (
                        str(device["object_id"])
                        + "_"
                        + str(device["room_ids"])
                        + device["object_type"]
                        + device["object_name"]
                        + device["room_name"]
                    )
                    devices_hash = devices_hash + "_" + device_hash
                if devices_hash != self._last_devices_hash:
                    if self._last_devices_hash == "":
                        self._last_devices_hash = devices_hash
                    else:
                        self._last_devices_hash = devices_hash
                        if self._platforms_registered:
                            self.reload_entry()

    def reload_entry(self):
        """Reload_entry function if platforms_registered (updating entry)."""
        options = self.entry.options.copy()
        if options.get("fake_update_value", "") == "1":
            options.pop("fake_update_value")
        else:
            options["fake_update_value"] = "1"
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    async def async_remove_old_devices(self):
        """Clear unused devices and entities."""
        configured_devices = []
        configured_entities = []
        entities_to_be_removed = []
        devices_to_be_removed = []
        for devices in self.devices_for_platform.values():
            for device in devices:
                if hasattr(device, "device_info"):
                    identifier = str((device.device_info or {}).get("identifiers", ""))
                    configured_devices.append(identifier)
                unique_id = device.unique_id
                configured_entities.append(unique_id)

        entity_registry = er.async_get(self.hass)
        entity_entries = er.async_entries_for_config_entry(entity_registry, self.entry.entry_id)
        for entity_entry in entity_entries:
            identifier = entity_entry.unique_id
            if (
                identifier
                and identifier not in configured_entities
                and entity_entry.entity_id not in entities_to_be_removed
            ):
                entities_to_be_removed.append(entity_entry.entity_id)

        for enity_id in entities_to_be_removed:
            entity_registry.async_remove(enity_id)

        device_registry = dr.async_get(self.hass)
        device_registry_entities = dr.async_entries_for_config_entry(
            device_registry, self.entry.entry_id
        )
        for device_entry in device_registry_entities:
            identifier = str(device_entry.identifiers)
            if (
                identifier
                and identifier not in configured_devices
                and device_entry.id not in devices_to_be_removed
            ):
                devices_to_be_removed.append(device_entry.id)

        for device_id in devices_to_be_removed:
            device_registry.async_remove_device(device_id)

    def _hash_device_state(self, device: dict) -> str:
        """Generate hash of device state for change detection.

        Only includes dynamic state values, not static properties.
        object_id is included to prevent hash collisions.
        """
        state_data = {
            "object_id": device["object_id"],
            "status": device.get("status", {}),
        }
        state_json = json.dumps(state_data, sort_keys=True)
        return hashlib.md5(state_json.encode()).hexdigest()

    def _detect_state_changes(self, devices: dict[str, dict]) -> set[str]:
        """Detect which devices have changed states.

        Returns:
            Set of object_ids that changed
        """
        changed_ids = set()

        for device_id, device in devices.items():
            new_hash = self._hash_device_state(device)
            old_hash = self._device_state_hashes.get(device_id)

            if old_hash is None:
                # New device
                changed_ids.add(device_id)
                log.debug("New device detected: %s", device_id)
            elif new_hash != old_hash:
                # State changed
                changed_ids.add(device_id)
                if log.isEnabledFor(10):  # DEBUG level
                    log.debug(
                        "Device %s (%s) state changed",
                        device_id,
                        device.get("device_friendly_name", "unknown"),
                    )

            # Update hash
            self._device_state_hashes[device_id] = new_hash

        return changed_ids
