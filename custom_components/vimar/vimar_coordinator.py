"""Vimar Update State coordinator."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
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
    CLIMATE_REFRESH_STATUS_NAMES,
    CONF_CERTIFICATE,
    CONF_ENERGY_REFRESH_INTERVAL,
    CONF_GLOBAL_CHANNEL_ID,
    CONF_IGNORE_PLATFORM,
    CONF_OVERRIDE,
    CONF_SECURE,
    DEFAULT_CERTIFICATE,
    DEFAULT_CLIMATE_REFRESH_INTERVAL,
    DEFAULT_ENERGY_REFRESH_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_CLIMATES,
    DOMAIN,
    ENERGY_METER_OBJECT_TYPES,
    ENERGY_REFRESH_STATUS_NAMES,
    PLATFORMS,
)
from .vimar_device_customizer import VimarDeviceCustomizer
from .vimarlink.exceptions import VimarApiError
from .vimarlink.vimarlink import VimarLink, VimarProject

log = _LOGGER

# How long (seconds) a slim-poll is blocked from overwriting a status_id after
# change_state() enqueues a write. The VIMAR hardware typically applies a
# SETVALUE within 8-10 s; 15 s gives comfortable headroom.
_WRITE_GUARD_SECONDS = 15.0

# Sentinel stored in _device_state_hashes by invalidate_device_hash(). It can
# never collide with a real value: _hash_device_state() returns an md5
# hexdigest, which is always 32 characters.
_HASH_INVALIDATED = ""


class VimarDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    vimarconnection: VimarLink | None = None
    vimarproject: VimarProject | None = None
    _timeout: float = DEFAULT_TIMEOUT
    # Separate, generous budget for login + full discovery (see __init__).
    _setup_timeout: float = 30.0
    webserver_id = ""
    entity_unique_id_prefix = ""
    _first_update_data_executed = False
    _platforms_registered = False
    _last_devices_hash = ""
    _consecutive_auth_failures = 0
    _reauth_triggered = False

    # --- slim-poll state (class-level defaults, overridden as instance attrs in __init__) ---
    _slim_poll_active: bool = False
    _last_device_count: int = -1

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, vimarconfig: ConfigType) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.vimarconfig = vimarconfig
        self.devices_for_platform = {}
        # Platforms actually handed to async_forward_entry_setups. Unloading
        # must mirror THIS list, not devices_for_platform: a platform whose
        # setup returns early without registering entities (e.g.
        # alarm_control_panel on an installation without SAI2 areas) was
        # forwarded but would never be unloaded, leaving it half-loaded across
        # a reload.
        self.forwarded_platforms: list[str] = []
        if entry:
            self.entity_unique_id_prefix = entry.unique_id or ""

        # FIX #2: initialize mutable attributes as instance-level to avoid sharing
        # across multiple coordinator instances (e.g. two Vimar config entries).
        self._device_state_hashes: dict[str, str] = {}
        self._changed_device_ids: set[str] = set()
        self._known_status_ids: list[int] = []
        self._energy_refresh_ids: list[int] = []
        self._last_energy_refresh: float = 0.0
        self._climate_refresh_ids: list[int] = []
        self._last_climate_refresh: float = 0.0
        # Background tasks for delayed post-write GETVALUE refreshes
        # (see schedule_status_refresh); cancelled on unload.
        self._refresh_tasks: set[asyncio.Task] = set()
        # In-flight periodic GETVALUE refresh, at most one at a time
        # (see _schedule_periodic_refreshes).
        self._periodic_refresh_task: asyncio.Task | None = None

        # FIX: single global FIFO for all device writes (SETVALUE). Every
        # change_state() from every entity enqueues its batch here and one
        # worker drains them sequentially, so concurrent commands (e.g.
        # set_hvac_mode + set_temperature fired together by an automation or
        # scene) can never overlap on the shared SOAP session and are applied
        # strictly in the order change_state() was called. Previously each
        # change_state spawned its own executor job; with several pool threads
        # the SETVALUE requests reached the gateway out of order, corrupting
        # thermostat setpoints (cached value committing after the explicit one).
        self._write_queue: asyncio.Queue[list[tuple[str, str, str]]] = asyncio.Queue()
        self._write_worker_task: asyncio.Task | None = None

        # Write-guard: maps status_id → expiry timestamp (monotonic).
        # While now < expiry, slim-poll skips overwriting this status_id so
        # the optimistic value set by change_state() is not bounced back by a
        # poll that reads stale hardware state. Cleared lazily in _apply_slim_results.
        self._pending_write_guards: dict[str, float] = {}

        refresh = vimarconfig.get(CONF_ENERGY_REFRESH_INTERVAL)
        self._energy_refresh_interval: float = (
            float(refresh) if refresh is not None else float(DEFAULT_ENERGY_REFRESH_INTERVAL)
        )
        if self._energy_refresh_interval < 0:
            self._energy_refresh_interval = 0.0
        if self._energy_refresh_interval <= 0:
            # Energy meters will FREEZE on a stale value with no auto-recovery:
            # the GETVALUE refresh is disabled. This is a valid config (0 = off)
            # but a common foot-gun set via the options flow, so make it loud.
            _LOGGER.warning(
                "Vimar: energy meter GETVALUE refresh is DISABLED "
                "(%s=0); energy/power sensors will not update until re-enabled",
                CONF_ENERGY_REFRESH_INTERVAL,
            )
        else:
            _LOGGER.debug(
                "Vimar: energy meter refresh interval = %.0fs",
                self._energy_refresh_interval,
            )

        timeout = vimarconfig.get(CONF_TIMEOUT) or DEFAULT_TIMEOUT
        if timeout > 0:
            self._timeout = float(timeout)
        # Budget for logging in and for a full discovery, which are rare and
        # inherently much slower than a slim poll: measured on real hardware,
        # a cold login alone took 4.3 s of the 6 s default budget while the
        # slim polls that follow it run in ~0.07 s. Timing the setup phase with
        # the poll's stopwatch meant a slightly slower web server - or a busy
        # Home Assistant start, when every integration competes for the
        # executor - failed the very first refresh and left the integration
        # "not ready". Floor and ceiling keep it sane at both ends of the
        # configurable range (2..60 s): never less than 30 s, never so long
        # that a genuinely stuck setup hangs instead of failing and retrying.
        self._setup_timeout = min(max(self._timeout * 5, 30.0), 120.0)
        uptade_interval = float(vimarconfig.get(CONF_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL)
        if uptade_interval < 1:
            uptade_interval = DEFAULT_SCAN_INTERVAL
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=uptade_interval),
            config_entry=entry,
        )

    # --- serialized device writes ---------------------------------------

    def enqueue_device_writes(self, writes: list[tuple[str, str, str]]) -> None:
        """Queue a batch of SETVALUE writes for serialized, ordered execution.

        Called (in the event loop thread) by VimarEntity.change_state(). The
        batch is a list of (status_id, value, optionals) tuples already in the
        caller's intended order. A single worker task drains the queue one
        batch at a time, guaranteeing global ordering and no overlap on the
        shared Vimar SOAP session. See __init__ for the rationale.

        Each status_id in the batch is registered in _pending_write_guards so
        that _apply_slim_results() skips overwriting the optimistic value until
        the hardware has had time to process the write (see _WRITE_GUARD_SECONDS).
        """
        self._ensure_write_worker()
        expiry = time.monotonic() + _WRITE_GUARD_SECONDS
        for status_id, _value, _optionals in writes:
            # Normalize to str: _apply_slim_results looks up guards with a
            # str-ified status_id, so storing a non-str key (if status_id ever
            # became an int) would silently never match.
            self._pending_write_guards[str(status_id)] = expiry
        self._write_queue.put_nowait(writes)

    def _ensure_write_worker(self) -> None:
        """Start the write worker lazily on first use / after cancellation."""
        if self._write_worker_task is None or self._write_worker_task.done():
            self._write_worker_task = self.hass.loop.create_task(self._write_worker())

    async def _write_worker(self) -> None:
        """Drain the write queue, executing one batch at a time, in order."""
        while True:
            writes = await self._write_queue.get()
            try:
                if self.vimarconnection is not None:
                    await self.hass.async_add_executor_job(self._execute_device_writes, writes)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Vimar: error while writing device states")
            finally:
                self._write_queue.task_done()

    def _execute_device_writes(self, writes: list[tuple[str, str, str]]) -> None:
        """Send a batch of SETVALUE requests sequentially on one thread.

        Runs in an executor thread. Order matters for thermostats: the
        activating mode (funzionamento) must be sent before the setpoint so the
        setpoint wins within a batch.
        """
        for status_id, value, optionals in writes:
            self.vimarconnection.set_device_status(status_id, value, optionals)

    async def async_shutdown_write_worker(self) -> None:
        """Cancel the write worker and pending refresh tasks (on unload/reload)."""
        if self._write_worker_task is not None:
            self._write_worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._write_worker_task
            self._write_worker_task = None
        for task in list(self._refresh_tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._refresh_tasks.clear()
        self._periodic_refresh_task = None

    async def async_close_connection(self) -> None:
        """Release the pooled HTTP connections (on unload/reload).

        The connection keeps one HTTP session per executor thread alive for
        reuse; without this the sockets would linger after a reload.
        """
        if self.vimarconnection is None:
            return
        with contextlib.suppress(Exception):
            await self.hass.async_add_executor_job(self.vimarconnection.close)

    # --- delayed GETVALUE refresh after writes ---------------------------

    def schedule_status_refresh(self, status_ids: list, delay: float) -> None:
        """Schedule a GETVALUE refresh of the given status object ids.

        Used by climate entities after a write: the webserver DB does not
        track the physical thermostat (e.g. the regulation setpoint applied
        by a mode change) unless a GETVALUE is issued on the status object.
        The delay must outlast the write-guard (_WRITE_GUARD_SECONDS) so the
        refreshed value is applied by the next poll instead of being skipped
        as an in-flight optimistic write.
        """
        ids: list[int] = []
        for sid in status_ids:
            with contextlib.suppress(ValueError, TypeError):
                ids.append(int(sid))
        if not ids:
            return
        task = self.hass.async_create_background_task(
            self._delayed_status_refresh(ids, delay),
            name=f"vimar_status_refresh_{ids[0]}",
        )
        self._refresh_tasks.add(task)
        task.add_done_callback(self._refresh_tasks.discard)

    async def _delayed_status_refresh(self, status_ids: list[int], delay: float) -> None:
        """Wait for the device to apply the write, then GETVALUE + repoll."""
        await asyncio.sleep(delay)
        if self.vimarconnection is None:
            return
        try:
            await self.hass.async_add_executor_job(
                self.vimarconnection.request_value_refresh, status_ids
            )
        except Exception as err:  # noqa: BLE001 - best-effort refresh
            _LOGGER.debug("Vimar: post-write status refresh failed: %s", err)
            return
        # Pull the refreshed DB values into HA right away instead of waiting
        # for the next scheduled poll tick.
        await self.async_request_refresh()

    async def _async_update_data(self):
        """Fetch data from API endpoint."""
        _LOGGER.debug("Updating coordinator..")

        # FIX #23b: svuota il set a inizio ciclo cosi' ogni poll parte pulito.
        # I listener (_handle_coordinator_update) vengono chiamati DOPO il
        # return di questo metodo, quindi il set e' ancora pieno quando serve.
        # request_statemachine_update() puo' aggiungere device_id in qualsiasi
        # momento: se arriva DOPO questo reset ma PRIMA di _detect_state_changes
        # verra' comunque incluso nel newly_changed del ciclo successivo tramite
        # il confronto hash (lo stato locale e' gia' stato aggiornato da
        # _apply_state_change).
        self._changed_device_ids = set()

        try:
            if self.vimarproject is None:
                raise PlatformNotReady

            if self.vimarconnection is None or not self.vimarconnection.is_logged():
                async with asyncio.timeout(self._setup_timeout):
                    await self.validate_vimar_credentials()

            forced = not self._first_update_data_executed or not self._platforms_registered
            full_discovery = forced or not self._slim_poll_active
            # Discovery gets the generous budget, the slim poll keeps the tight
            # one: see _setup_timeout.
            budget = self._setup_timeout if full_discovery else self._timeout

            async with asyncio.timeout(budget):
                if full_discovery:
                    _LOGGER.debug("Vimar: running full discovery")
                    devices = await self.hass.async_add_executor_job(self.vimarproject.update, True)

                    if devices and len(devices) > 0:
                        self._known_status_ids = self._collect_status_ids(devices)
                        self._energy_refresh_ids = self._collect_energy_refresh_ids(devices)
                        self._climate_refresh_ids = self._collect_climate_refresh_ids(devices)
                        # Include SAI2 alarm CIDs in slim poll
                        if self.vimarproject.sai2_groups or self.vimarproject.sai2_zones:
                            sai2_ids = self.vimarconnection.get_sai2_status_ids(
                                self.vimarproject.sai2_groups,
                                self.vimarproject.sai2_zones,
                            )
                            self._known_status_ids.extend(sai2_ids)
                        self._last_device_count = len(devices)
                        self._slim_poll_active = True
                        _LOGGER.debug(
                            "Vimar: discovery complete - %d devices, %d status IDs indexed "
                            "for slim poll, %d energy meter refresh IDs",
                            len(devices),
                            len(self._known_status_ids),
                            len(self._energy_refresh_ids),
                        )
                        if not self._energy_refresh_ids:
                            # No energy refresh IDs after a full discovery means
                            # either there are no energy meters, or their status
                            # rows were missing from the discovery payload. The
                            # slim-poll self-heal can no longer help here (it
                            # rebuilds from the same tree), so surface it.
                            _LOGGER.debug(
                                "Vimar: no energy meter refresh IDs collected at discovery"
                            )
                else:
                    _LOGGER.debug("Vimar: slim poll (%d status IDs)", len(self._known_status_ids))
                    self._schedule_periodic_refreshes()
                    slim_results = await self.hass.async_add_executor_job(
                        self.vimarconnection.get_status_only, self._known_status_ids
                    )

                    if slim_results is None:
                        _LOGGER.debug(
                            "Vimar: slim poll returned None (transient), keeping previous state"
                        )
                        return self.vimarproject.devices

                    self._apply_slim_results(self.vimarproject.devices, slim_results)
                    # Update SAI2 zone/group children from slim poll results
                    if self.vimarproject.sai2_groups or self.vimarproject.sai2_zones:
                        self.vimarconnection.update_sai2_from_slim(
                            self.vimarproject.sai2_groups,
                            self.vimarproject.sai2_zones,
                            slim_results,
                        )
                    await self._refresh_sai2_live_state()
                    devices = self.vimarproject.devices

                    current_count = len(devices)
                    if current_count != self._last_device_count:
                        _LOGGER.info(
                            "Vimar: topology change detected (%d \u2192 %d devices), scheduling rediscovery",
                            self._last_device_count,
                            current_count,
                        )
                        self._slim_poll_active = False
                        self._last_device_count = current_count

            if not devices or len(devices) == 0:
                raise UpdateFailed("Could not find any devices on Vimar Webserver")

            if not self._first_update_data_executed:
                self._first_update_data_executed = True

            # FIX #22 + #23b: _changed_device_ids e' stato resettato a inizio
            # ciclo; ora lo popola con le novita' rilevate in questo poll.
            newly_changed = self._detect_state_changes(devices)
            self._changed_device_ids.update(newly_changed)

            if not self.last_update_success or self._last_devices_hash == "":
                self._reload_entry_if_devices_changed()

            self._consecutive_auth_failures = 0

            # FIX #23: log compatto, solo slim poll (platforms_registered=True).
            if _LOGGER.isEnabledFor(logging.DEBUG) and self._platforms_registered:
                self._log_poll_summary(devices)

            return devices

        except ConfigEntryAuthFailed:
            self._handle_auth_failure()
            raise
        except TimeoutError:
            _LOGGER.warning("Timeout communicating with Vimar web server")
            raise UpdateFailed("Timeout communicating with Vimar web server")
        except aiohttp.ClientError as err:
            _LOGGER.warning("Client error communicating with Vimar: %s", err)
            raise UpdateFailed(f"Client error: {err}")
        except Exception as err:
            if self._is_auth_error(err):
                self._handle_auth_failure()
                raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
            if not isinstance(err, VimarApiError):
                # Not a known Vimar API/communication error: this is almost
                # certainly a bug in our own parsing/state logic (KeyError,
                # TypeError, AttributeError, ...) surfacing here and getting
                # disguised as a comms failure. Log the full traceback so the
                # real cause (file+line) is diagnosable instead of being hidden
                # behind a generic "Error communicating with API" one-liner.
                # We still raise UpdateFailed below, so the integration fails
                # softly (entities go unavailable and it retries) rather than
                # crashing or disabling itself.
                _LOGGER.exception("Vimar: unexpected non-network error during update")
            raise UpdateFailed(f"Error communicating with API: {err}")

    # ------------------------------------------------------------------
    # Poll summary log
    # ------------------------------------------------------------------

    def _log_poll_summary(self, devices: dict) -> None:
        """Emit two DEBUG lines: updated device names and skipped device names.

        FIX #23b: usa un set seen_ids per deduplicare i device fisici.
        I sensori multi-entity (es. CHMisuratore con 7 sub-sensori) hanno
        tutte le sub-entity con lo stesso _device_id: senza deduplicazione
        lo stesso nome verrebbe listato N volte. Con seen_ids ogni device
        fisico appare una sola volta, indipendentemente da quante entity
        HA ha creato per esso.
        """
        updated_names: list[str] = []
        skipped_names: list[str] = []
        seen_ids: set[str] = set()

        for platform_entities in self.devices_for_platform.values():
            for entity in platform_entities:
                device_id = getattr(entity, "_device_id", None)
                if device_id is None:
                    continue
                # VimarStatusSensor non e' nel device tree
                if device_id not in devices:
                    continue
                # deduplicazione: ogni device fisico una sola volta
                if device_id in seen_ids:
                    continue
                seen_ids.add(device_id)

                friendly = (
                    devices[device_id].get("device_friendly_name")
                    or devices[device_id].get("object_name")
                    or device_id
                )
                if device_id in self._changed_device_ids:
                    updated_names.append(friendly)
                else:
                    skipped_names.append(friendly)

        if updated_names:
            _LOGGER.debug("Updated  (%d): %s", len(updated_names), ", ".join(updated_names))
        if skipped_names:
            _LOGGER.debug("Skipped  (%d): %s", len(skipped_names), ", ".join(skipped_names))

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _is_auth_error(self, error: Exception) -> bool:
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
        """Handle authentication failure by triggering reauth flow."""
        self._consecutive_auth_failures += 1

        if not self._reauth_triggered and self._consecutive_auth_failures >= 2:
            _LOGGER.warning(
                "Authentication failed %d times, triggering re-authentication flow",
                self._consecutive_auth_failures,
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
                    with contextlib.suppress(ValueError, TypeError):
                        ids.add(int(sid))
        return list(ids)

    def _collect_energy_refresh_ids(self, devices: dict) -> list[int]:
        """Collect status object IDs that need an explicit GETVALUE trigger.

        VIMAR firmware updates DPADD_OBJECT.CURRENT_VALUE for energy meter
        statuses (energia_*, potenza_*) only when a client issues a
        runonelement GETVALUE on the status object id. Without it, the
        slim-poll SELECT keeps reading stale values.
        """
        ids: list[int] = []
        for device in devices.values():
            if device.get("object_type") not in ENERGY_METER_OBJECT_TYPES:
                continue
            for status_name, status in device.get("status", {}).items():
                if status_name not in ENERGY_REFRESH_STATUS_NAMES:
                    continue
                sid = status.get("status_id")
                if sid is None:
                    continue
                with contextlib.suppress(ValueError, TypeError):
                    ids.append(int(sid))
        return ids

    def _collect_climate_refresh_ids(self, devices: dict) -> list[int]:
        """Collect climate status object IDs that need periodic GETVALUE.

        Same firmware behavior as energy meters: the webserver DB tracks the
        physical thermostat only when a GETVALUE is issued on the status
        object. Without it, setpoint changes made by the device itself (mode
        change to absence/reduction, wall panel adjustments) never reach HA.
        """
        ids: list[int] = []
        for device in devices.values():
            if device.get("device_type") != DEVICE_TYPE_CLIMATES:
                continue
            for status_name, status in device.get("status", {}).items():
                if status_name not in CLIMATE_REFRESH_STATUS_NAMES:
                    continue
                sid = status.get("status_id")
                if sid is None:
                    continue
                with contextlib.suppress(ValueError, TypeError):
                    ids.append(int(sid))
        return ids

    def _schedule_periodic_refreshes(self) -> None:
        """Run the periodic GETVALUE refreshes off the poll's critical path.

        The energy-meter and thermostat refreshes issue ONE SOAP request per
        status object id, sequentially. They used to be awaited inside the
        poll's `asyncio.timeout(self._timeout)` block, so they spent the very
        same budget (6 s by default) that the slim poll needs: on an
        installation with a handful of meters and thermostats the refresh
        cycle alone could exhaust it, and every entity went unavailable for
        that cycle. Worse, `asyncio.timeout` cannot cancel a job already
        running on an executor thread, so the work carried on in the
        background while the poll had given up.

        They are best-effort by nature (a missed refresh is retried on the
        next tick), so they now run as a tracked background task instead. A
        single task at a time: if the previous refresh is still in flight the
        tick is skipped, which also protects the shared connection from a
        pile-up when the web server is slow.
        """
        if self._periodic_refresh_task is not None and not self._periodic_refresh_task.done():
            _LOGGER.debug("Vimar: previous periodic refresh still running, skipping this tick")
            return

        task = self.hass.async_create_background_task(
            self._run_periodic_refreshes(), name="vimar_periodic_refresh"
        )
        self._periodic_refresh_task = task
        # Tracked like the post-write refreshes so unload cancels it too.
        self._refresh_tasks.add(task)
        task.add_done_callback(self._refresh_tasks.discard)

    async def _run_periodic_refreshes(self) -> None:
        """Body of the background refresh task (throttles live in the callees)."""
        await self._maybe_refresh_energy_meters()
        await self._maybe_refresh_climates()

    async def _maybe_refresh_climates(self) -> None:
        """Send GETVALUE to thermostat setpoint/mode statuses if the throttle elapsed."""
        # Self-heal like _maybe_refresh_energy_meters: rebuild the id list if
        # a reload left the slim poll active without a fresh discovery.
        if not self._climate_refresh_ids and self.vimarproject is not None:
            self._climate_refresh_ids = self._collect_climate_refresh_ids(self.vimarproject.devices)

        if not self._climate_refresh_ids or self.vimarconnection is None:
            return
        now = time.monotonic()
        if now - self._last_climate_refresh < DEFAULT_CLIMATE_REFRESH_INTERVAL:
            return
        self._last_climate_refresh = now

        # Skip ids with an in-flight write-guard: a GETVALUE would make the
        # next poll overwrite the optimistic value with pre-write hardware
        # state. They will be covered by the post-write refresh instead.
        guarded = self._pending_write_guards
        ids = [sid for sid in self._climate_refresh_ids if str(sid) not in guarded]
        if not ids:
            return
        _LOGGER.debug("Vimar: refreshing %d climate statuses via GETVALUE", len(ids))
        try:
            await self.hass.async_add_executor_job(self.vimarconnection.request_value_refresh, ids)
        except Exception as err:  # noqa: BLE001 - best-effort refresh
            _LOGGER.debug("Vimar: climate refresh failed: %s", err)

    async def _maybe_refresh_energy_meters(self) -> None:
        """Send GETVALUE to energy meter statuses if the throttle elapsed."""
        if self._energy_refresh_interval <= 0:
            return

        # Self-heal: _energy_refresh_ids is normally populated only in the
        # full-discovery branch. If a reload/re-auth leaves the slim poll
        # active without ever re-running discovery, the list can stay empty
        # and the GETVALUE refresh dies SILENTLY: energy meters freeze on a
        # stale value until the user opens the native VIMAR UI by hand (which
        # issues the GETVALUE for us). Rebuild the list from the current device
        # tree so a long-lived slim poll recovers on its own. If there are
        # genuinely no energy meters, _collect_energy_refresh_ids returns []
        # and we fall through to the silent return below (no spurious warning).
        if not self._energy_refresh_ids and self.vimarproject is not None:
            rebuilt = self._collect_energy_refresh_ids(self.vimarproject.devices)
            if rebuilt:
                _LOGGER.warning(
                    "Vimar: energy refresh id list was empty during slim poll; "
                    "rebuilt %d ids from the device tree (meters were not refreshing)",
                    len(rebuilt),
                )
                self._energy_refresh_ids = rebuilt

        if not self._energy_refresh_ids:
            return
        now = time.monotonic()
        if now - self._last_energy_refresh < self._energy_refresh_interval:
            return
        if self.vimarconnection is None:
            return
        self._last_energy_refresh = now
        _LOGGER.debug(
            "Vimar: refreshing %d energy meter statuses via GETVALUE",
            len(self._energy_refresh_ids),
        )
        try:
            await self.hass.async_add_executor_job(
                self.vimarconnection.request_value_refresh,
                self._energy_refresh_ids,
            )
        except Exception as err:  # noqa: BLE001 - best-effort refresh
            _LOGGER.debug("Vimar: energy meter refresh failed: %s", err)

    async def _refresh_sai2_live_state(self) -> None:
        """Refresh SAI2 group/zone live values from DPADD_OBJECT.

        DPADD_OBJECT.CURRENT_VALUE for SAI2 group IDs updates immediately
        after commands, unlike the DPAD_SAI2GATEWAY_SAI2GROUPCHILDREN view.
        Group values respect the per-group optimistic-update guard so
        in-flight commands aren't overwritten by stale reads.
        """
        if self.vimarproject is None or self.vimarconnection is None:
            return

        if self.vimarproject.sai2_groups:
            group_ids = list(self.vimarproject.sai2_groups.keys())
            fresh_values = await self.hass.async_add_executor_job(
                self.vimarconnection.get_sai2_area_values, group_ids
            )
            if fresh_values is not None:
                now = time.monotonic()
                guard = self.vimarproject.sai2_optimistic_until
                if self.vimarproject.sai2_area_values is None:
                    self.vimarproject.sai2_area_values = {}
                for gid, val in fresh_values.items():
                    if guard.get(gid, 0) > now:
                        continue  # optimistic value still protected
                    self.vimarproject.sai2_area_values[gid] = val

        if self.vimarproject.sai2_zones:
            zone_ids = list(self.vimarproject.sai2_zones.keys())
            fresh_zone_values = await self.hass.async_add_executor_job(
                self.vimarconnection.get_sai2_area_values, zone_ids
            )
            if fresh_zone_values is not None:
                self.vimarproject.sai2_zone_values = fresh_zone_values

    def _apply_slim_results(self, devices: dict, slim_results: list) -> None:
        """Patch CURRENT_VALUE from slim poll into existing device tree.

        Guarded status_ids (written optimistically by change_state() and not yet
        processed by the hardware) are skipped so the optimistic value is not
        bounced back by a poll that still reads the pre-write hardware state.
        Guards expire after _WRITE_GUARD_SECONDS and are cleaned up lazily here.
        """
        index: dict[str, tuple[str, str]] = {}
        for device_id, device in devices.items():
            for status_name, status in device.get("status", {}).items():
                sid = status.get("status_id")
                if sid is not None:
                    index[str(sid)] = (device_id, status_name)

        now = time.monotonic()
        # Sweep expired guards: a status_id written once and never polled again
        # (e.g. device removed by a topology change) would otherwise linger in
        # the dict forever, since cleanup below only fires when the sid recurs.
        if self._pending_write_guards:
            self._pending_write_guards = {
                k: v for k, v in self._pending_write_guards.items() if v > now
            }
        for row in slim_results:
            sid = str(row.get("status_id", ""))
            val = row.get("status_value")
            if sid in index:
                # After the sweep above, every remaining guard is still in
                # flight (expiry > now), so its presence alone means skip.
                if sid in self._pending_write_guards:
                    continue  # write in flight — keep optimistic value
                dev_id, sname = index[sid]
                devices[dev_id]["status"][sname]["status_value"] = val

    # ------------------------------------------------------------------
    # Existing methods
    # ------------------------------------------------------------------

    async def init_vimarproject(self) -> None:
        """Init VimarLink and VimarProject from entry config."""
        self._last_devices_hash = ""
        self._first_update_data_executed = False
        self._platforms_registered = False
        self._slim_poll_active = False
        self._known_status_ids = []
        self._energy_refresh_ids = []
        self._last_energy_refresh = 0.0
        self._climate_refresh_ids = []
        self._last_climate_refresh = 0.0
        self._last_device_count = -1
        self._consecutive_auth_failures = 0
        self._reauth_triggered = False
        self._device_state_hashes = {}
        self._pending_write_guards = {}
        self.devices_for_platform = {}
        self.forwarded_platforms = []
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
        device_overrides = vimarconfig.get(CONF_OVERRIDE) or []

        vimarconnection = VimarLink(schema, host, port, username, password, certificate, timeout)
        device_customizer = VimarDeviceCustomizer(vimarconfig, device_overrides)

        def device_customizer_fn(device):
            device_customizer.customize_device(device)

        vimarproject = VimarProject(vimarconnection, device_customizer_fn)

        if global_channel_id is not None:
            vimarproject.global_channel_id = global_channel_id

        self.vimarconnection = vimarconnection
        self.vimarproject = vimarproject

    async def validate_vimar_credentials(self) -> None:
        """Validate Vimar credential config."""
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
        except Exception as err:
            if self._is_auth_error(err):
                raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
            raise err

    async def async_register_devices_platforms(self):
        """Execute async_forward_entry_setup for each platform."""
        self.devices_for_platform = {}
        ignored_platforms = self.vimarconfig.get(CONF_IGNORE_PLATFORM) or []
        platforms = [
            i for i in PLATFORMS if i not in ignored_platforms or i == DEVICE_TYPE_BINARY_SENSOR
        ]
        # Recorded BEFORE awaiting the forward so async_unload_entry can undo
        # a setup that failed halfway through.
        self.forwarded_platforms = list(platforms)
        await self.hass.config_entries.async_forward_entry_setups(self.entry, platforms)

        self._platforms_registered = True
        if len(self.devices_for_platform) > 0:
            await self.async_remove_old_devices()

    def _reload_entry_if_devices_changed(self):
        if self.vimarproject:
            devices = self.vimarproject.devices
            if devices is not None and len(devices) > 0:
                # FIX #13: O(n) join instead of O(n^2) string concatenation in loop.
                hash_parts: list[str] = []
                for device_id, device in devices.items():
                    hash_parts.append(
                        str(device["object_id"])
                        + "_"
                        + str(device["room_ids"])
                        + device["object_type"]
                        + device["object_name"]
                        + device["room_name"]
                    )
                devices_hash = "_".join(hash_parts)
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
        """Clear unused devices and entities.

        FIX #12: configured_devices was built as a list of str(identifiers),
        but device_registry entries expose identifiers as a frozenset of tuples.
        The string comparison `str(frozenset) in list_of_str` would almost never
        match because Python's frozenset str representation is non-deterministic
        in ordering. Fix: store identifiers as the native frozenset and compare
        with the frozenset from the registry directly.
        """
        configured_device_ids: set[frozenset] = set()
        configured_entities: list[str] = []
        entities_to_be_removed: list[str] = []
        devices_to_be_removed: list[str] = []

        for devices in self.devices_for_platform.values():
            for device in devices:
                if hasattr(device, "device_info") and device.device_info:
                    raw_identifiers = (device.device_info or {}).get("identifiers")
                    if raw_identifiers:
                        configured_device_ids.add(frozenset(raw_identifiers))
                unique_id = device.unique_id
                if unique_id:
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

        for entity_id in entities_to_be_removed:
            entity_registry.async_remove(entity_id)

        device_registry = dr.async_get(self.hass)
        device_registry_entries = dr.async_entries_for_config_entry(
            device_registry, self.entry.entry_id
        )
        for device_entry in device_registry_entries:
            device_identifiers_frozen = frozenset(device_entry.identifiers)
            if (
                device_identifiers_frozen not in configured_device_ids
                and device_entry.id not in devices_to_be_removed
            ):
                devices_to_be_removed.append(device_entry.id)

        for device_id in devices_to_be_removed:
            device_registry.async_remove_device(device_id)

    def _hash_device_state(self, device: dict) -> str:
        """Generate hash of device state for change detection."""
        state_data = {
            "object_id": device["object_id"],
            "status": device.get("status", {}),
        }
        state_json = json.dumps(state_data, sort_keys=True)
        return hashlib.md5(state_json.encode(), usedforsecurity=False).hexdigest()

    def invalidate_device_hash(self, device_id: str) -> None:
        """Force the next poll to treat this device as changed.

        Called after an optimistic local write (see
        VimarEntity.request_statemachine_update): the webserver may answer the
        next poll with the very value we already have in cache - a monostable
        device falling back to 0 for the second time, a thermostat rounding a
        setpoint, a shutter that did not move - and the hash comparison would
        then find nothing changed and leave the UI out of sync.

        A sentinel is stored instead of deleting the entry, because a MISSING
        entry means 'device never seen before'. Deleting made every locally
        written device reappear as `New device detected` on the next poll,
        which is plainly wrong on an installation whose topology never changed
        and sent whoever read the log looking for a discovery problem.
        """
        self._device_state_hashes[device_id] = _HASH_INVALIDATED

    def _detect_state_changes(self, devices: dict[str, dict]) -> set[str]:
        """Detect which devices have changed states.

        Returns only the set of newly-changed device IDs detected in this
        poll cycle. The caller is responsible for merging into
        _changed_device_ids (use .update(), NOT direct assignment) so that
        IDs added by request_statemachine_update() between two poll cycles
        are preserved.
        """
        changed_ids = set()

        for device_id, device in devices.items():
            new_hash = self._hash_device_state(device)
            old_hash = self._device_state_hashes.get(device_id)

            if old_hash is None:
                changed_ids.add(device_id)
                log.debug("New device detected: %s", device_id)
            elif old_hash == _HASH_INVALIDATED:
                # Invalidated by our own write, not a newly discovered device.
                changed_ids.add(device_id)
                if log.isEnabledFor(logging.DEBUG):
                    log.debug(
                        "Device %s (%s) resynchronised after a local write",
                        device_id,
                        device.get("device_friendly_name", "unknown"),
                    )
            elif new_hash != old_hash:
                changed_ids.add(device_id)
                if log.isEnabledFor(10):
                    log.debug(
                        "Device %s (%s) state changed",
                        device_id,
                        device.get("device_friendly_name", "unknown"),
                    )

            self._device_state_hashes[device_id] = new_hash

        return changed_ids
