# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Calendar Versioning](https://calver.org/) (`YYYY.M.INCREMENT`).

> This fork is based on [h4de5/home-assistant-vimar](https://github.com/h4de5/home-assistant-vimar).
> All changes listed below are relative to the original upstream `master` branch.

---

## [2026.6.3] - 2026-06-11

### Fixed

- **Polling no longer gets stuck after a server-side session expiry**: the VIMAR webserver can expire a session on its own; when that happens, SQL polls return result code `LGMG-3019` with an `Unknown-Payload` body. Previously `check_login()` only checked that a session ID was cached, so the stale session was reused forever and every poll cycle logged two warnings without ever recovering. `_request_vimar_sql` now inspects the `<result>` code: an `LGMG-*` code invalidates the cached session so the next coordinator cycle re-authenticates automatically.
- **Fixed false-positive "SQL request rejected" warnings introduced by the above fix**: successful SQL responses also carry a non-`DBMG` code (`DPCM-0000`) in the `<result>` tag, which the first iteration of the fix mistook for a session error on every poll. Session invalidation is now triggered only by `LGMG-*` result codes; all other codes proceed to normal payload parsing.

---

## [2026.6.2] - 2026-06-09

### Fixed

- **Energy meters could freeze permanently after a reload/re-auth**: the periodic GETVALUE refresh relies on a list of meter IDs that is only populated during full discovery. If a reload kept the coordinator on slim polling without ever re-running discovery, the list stayed empty and energy/power sensors silently froze on a stale value until the VIMAR native UI was opened by hand. The slim poll now rebuilds the list from the live device tree when it's empty (with a warning), and the discovery step logs how many meter IDs it collected.
- **Energy refresh interval `0` (which disables the periodic refresh) had no warning and no input guard**: the options field is now a `NumberSelector` (0–3600s) instead of a raw unbounded `int`, and a warning is logged at startup if the refresh is disabled, so the freeze above is diagnosable and harder to trigger by accident.
- Hardened the write-guard bookkeeping used to suppress stale polled values right after a write: guard keys are normalized to `str` at enqueue time, and expired guards are swept proactively instead of lingering for status IDs that are never polled again (e.g. after a topology change).
- Config flow option fields (port, timeout, scan interval, global channel ID) now validate their numeric ranges instead of accepting any integer.
- Reopening the options form no longer drops intentionally-set falsy values (energy refresh interval `0`, `Secure=False`) back to their defaults.

### Internal

- `config_flow.set_errors_from_ex` now classifies login/connection errors primarily by exception type (`VimarConfigError` → `invalid_auth`, `VimarConnectionError` → `cannot_connect`) instead of relying on message text, with string matching kept only as a fallback (SSL/cert-save cases). Added `tests/integration/test_config_flow_errors.py` covering the classification.
- Removed an in-place mutation of the live options dict in the options-flow schema builder.

---

## [2026.6.1] - 2026-06-08

### Changed

- **SAI2 alarm — PIN handling reworked, no global PIN stored.** Removed the single `SAI PIN` option. The code is forwarded to the SAI2 control unit as the user PIN, so each person can use their own PIN and the panel logs the operation against the right user. Existing `sai_pin` values are ignored.
- **No keypad by default** (`code_format = None`): a logged-in Home Assistant user with a mapped PIN arms/disarms with a single tap.

### Added

- **Options step "Alarm PIN per user"** that maps each Home Assistant user to their SAI2 PIN, plus a **fallback PIN for automations** used when a command has no explicit `code` and no user in context (trigger-based automations, which run without a user). PINs are stored in plain text in the config entry (same protection as the VIMAR admin password — filesystem permissions on `.storage`).
- **Up-front PIN validation** via `service-vimarsai2authenticate`: a wrong PIN is reported immediately and unambiguously, before any command is sent. Confirmed on hardware that the set service returns `DPCM-0000` even with a wrong PIN, so its response alone cannot detect a bad code.
- **Localized persistent notification** on alarm command failures (wrong PIN, no response, area unavailable), in addition to the raised error, so failures aren't easy to miss; cleared automatically on the next successful command.
- Translated alarm exception messages across all 7 languages.

### Internal

- `VimarLink.authenticate_sai2_pin()`; `set_sai2_status()` now returns the parsed result code. The alarm panel uses authenticate-first and resolves the PIN as `explicit code → logged-in user's PIN → automation fallback`, serializes commands per area, and surfaces failures via translated `HomeAssistantError`/`ServiceValidationError`. Removed `CONF_SAI_PIN`; added `CONF_USER_PINS` and `CONF_AUTOMATION_PIN`.

---

## [2026.6.0] - 2026-06-02

### Fixed

- **Thermostat setpoint race when an automation/scene set mode and temperature together**: when `climate.set_hvac_mode` and `climate.set_temperature` were issued on the same VIMAR thermostat in quick succession (e.g. by a scene restore or a climate-curve automation), the resulting setpoint could be wrong. The previous fix serialized only the writes *within a single* `change_state()` call, but two separate commands each dispatched their own fire-and-forget executor job onto different pool threads, so their `SETVALUE` requests still raced on the shared SOAP session and reached the gateway out of order — a stale cached setpoint could commit after the explicit one. All device writes now go through a single global FIFO queue in the coordinator, drained one batch at a time on one thread, so every `SETVALUE` is applied in the exact order `change_state()` was called and never overlaps on the session.
- **`set_hvac_mode` no longer overwrites the setpoint when activating from off**: turning a thermostat on used to re-send the cached target temperature alongside the mode, which is what made the race above possible (and could clobber an explicit `set_temperature`). It now sends only the operating mode and heat/cool direction; the device keeps its stored manual setpoint and `set_temperature()` is the sole owner of the setpoint.

### Internal

- Coordinator gains a serialized write queue (`enqueue_device_writes` / `_write_worker` / `_execute_device_writes`), cancelled on unload; `VimarEntity.change_state()` enqueues batches instead of dispatching its own executor job. Regression tests updated to the queue mechanism, plus a new test asserting `set_hvac_mode` from off never writes the setpoint.

---

## [2026.5.5] - 2026-05-31

### Fixed

- **Thermostat commands from Home Assistant never reached the physical device**: setting a thermostat setpoint (or any climate state) from HA updated only the web server's database, not the physical By-me thermostat — HA displayed the new value while the room kept being regulated to the old setpoint, and a manual sync from the VIMAR interface appeared to "revert" the change. Root cause: a By-me thermostat only applies a `SETVALUE` to the physical device once a live device session is open; the native VIMAR web UI opens it by issuing a `GETVALUE` on the object right before saving, while the integration sent a bare `SETVALUE`. `set_device_status()` now primes the session with a `GETVALUE` on the same object before the `SETVALUE` for states that require device synchronisation (`optionals == "SYNCDB"`, i.e. climate states such as setpoint/season/mode). Covers, lights and switches use `NO-OPTIONALS` and are unaffected. Verified on hardware (01945).

### Changed

- Added diagnostic debug logging to `set_device_status()`: logs the `idobject`/value/optionals sent and the web server's `result`/`payload`, plus a warning when a write receives no response. Makes future write-path issues diagnosable from the logs.

---

## [2026.5.4] - 2026-05-31

### Fixed

- **Thermostat setpoint lost or reset to a different value**: setting the target temperature (or switching from off to heat/cool) could fail to take effect, with the thermostat snapping back to a previously stored setpoint. `change_state()` dispatched each value as a separate fire-and-forget executor job, so concurrent `SETVALUE` requests reached the web server in non-deterministic order; when `funzionamento=MANUAL` arrived after the setpoint, the firmware reloaded its stored manual setpoint and discarded the value just written. Writes are now batched into a single executor job and sent sequentially, preserving the caller's order (setpoint last wins). In addition, `async_set_temperature()` now writes **only** the setpoint when the thermostat is already in manual mode — matching the native VIMAR web UI, which sends a single `SETVALUE` — and applies `funzionamento=MANUAL` before the setpoint when activating from off or another preset, without overwriting the heat/cool direction.

### Internal

- Added regression tests (`tests/integration/test_climate_state_writes.py`) covering the single ordered executor job, sequential `SETVALUE` delivery, and the manual setpoint-only / off-activation write paths.

---

## [2026.5.3] - 2026-05-26

### Fixed

- **"Vimar WebServer" device missing after restart**: after the `VimarStatusSensor` relocation in 2026.5.2, the status sensor was no longer tracked in `coordinator.devices_for_platform`. This caused `async_remove_old_devices()` to treat the "Vimar WebServer" device as orphaned and delete it from the device registry on every reload, making the connection status entity disappear from the UI. Fixed by re-appending the status sensor to `devices_for_platform[binary_sensor]` after `vimar_setup_entry()` overwrites the list.

---

## [2026.5.2] - 2026-05-26

### Fixed

- **`manifest.json`**: removed invalid top-level `homeassistant` key (key is not recognized by Home Assistant 2026.x manifest schema and caused hassfest validation warnings).

### Internal

- **`VimarStatusSensor` relocated** from `vimar_entity.py` to `binary_sensor.py` where it belongs (it extends `BinarySensorEntity` and is only instantiated for the binary_sensor platform). `vimar_setup_entry()` is now purely generic.
- **`_refresh_sai2_live_state()` helper** extracted from `VimarDataUpdateCoordinator._async_update_data()`: ~25 lines of SAI2-specific group/zone live-value refresh (with optimistic-update guard) moved out of the polling loop. No behavior change.
- **CI**: `actions/checkout` bumped to v5 (Node 24).
- **Code hygiene**: removed unused `UnitOfElectricPotential` import in `sensor.py`; formatted `climate.py` with Black.
- **`.gitignore`**: added `graphify-out/` (local knowledge-graph artifacts) and `CLAUDE.local.md` (per-contributor AI instructions).

---

## [2026.5.1] - 2026-05-26

### Fixed

- **Stale energy meter values**: VIMAR firmware updates `DPADD_OBJECT.CURRENT_VALUE` for energy meter statuses (`energia_assoluta`, `energia_parziale`, `potenza_attiva`, `potenza_reattiva`) only when a client explicitly issues a `service-runonelement` `GETVALUE` on the status object id (this is what the VIMAR web UI does on the energy management screen). Without that trigger the slim-poll `SELECT` kept returning stale values, freezing energy sensors unless the heat pump page was open in a browser.
- **`CH_Carichi*` sensor unit/class mapping**: corrected unit-of-measure and device-class assignment for `CH_Carichi`, `CH_Carichi_Custom` and `CH_Carichi_3F` measurements (energy / power / current / timestamp).

### Added

- **`energy_refresh_interval` option**: new options-flow setting (default `30` s, `0` disables) that controls how often the integration sends the `GETVALUE` refresh on energy meter statuses. Throttled independently from the regular scan interval.

### Internal

- `hacs.json` `homeassistant` minimum aligned to `2026.1.0` to match `manifest.json` (was lagging at `2025.10.2`).
- `.mcp.json` added to `.gitignore` (local MCP server config).

---

## [2026.5.0] - 2026-05-01

### Added

- **Climate preset modes Eco / Away / Schedule / Protection / Manual**: full preset coverage for both Type I and Type II thermostats, mapped to the corresponding VIMAR `funzionamento` values (Auto schedule, Manuale, Riduzione/Eco, Assenza, Antigielo/Protezione).
- **`translation_key = "vimar_climate"`** on the climate entity: the `preset_mode` attribute label is now rendered as **"Modalità"** in Italian (and "Mode" in English), with localized state names for each preset. Translations updated for English, Italian, German, French, Spanish, Dutch, Portuguese.
- **`icons.json`**: per-state MDI icons for every preset (`hand-back-right` for Manual, `calendar-clock` for Schedule, `leaf` for Eco, `home-thermometer` for Protection, `home-export-outline` for Away).
- **Scene last-activation timestamp**: scenes now report their last activation time as state, persisted across HA restarts via `RestoreEntity`.

### Changed

- **`hvac_mode` semantics aligned with VIMAR thermostats**: `hvac_mode` now represents only the heating/cooling direction (HEAT / COOL / OFF). The operating mode (auto schedule / manual / eco / away / protection) is exposed exclusively as `preset_mode`. Selecting HEAT/COOL only changes the direction; the current preset is preserved when the device is ON and MANUAL is activated only when transitioning from OFF.
- **Dev toolchain**: pyright targets Python 3.14, Black 26.x, Ruff 0.11+, with aligned development requirements.

### Fixed

- **Cannot exit AUTO mode from Home Assistant**: in Type II thermostats the device stayed in AUTO when switching to HEAT/COOL because the previous `funzionamento` value was being preserved. The integration now forces MANUAL when explicitly setting a direction on an active device.
- **`Could not find state unita` log spam**: `async_set_temperature` no longer sends the `unita` (temperature unit) key when the device does not expose it.
- **Scene transient "unknown" state**: `_last_activated` is now set before `change_state`, so the state attribute is never written as `None`.
- **SAI2 alarm bit 4 misclassification**: bit 4 is correctly treated as alarm memory, not as an active alarm.
- **Python 3.13 compliance**: `async_timeout.timeout` replaced with `asyncio.timeout`; `hashlib.md5(..., usedforsecurity=False)` for FIPS environments; `target-version` set to `py313`.

### Internal

- `pyrightconfig.json` resolves the project venv for type checking.
- VSCode project settings (`.vscode/settings.json`) with Ruff format-on-save.
- `.claude/`, `.playwright-mcp/` added to `.gitignore`.
- `manifest.json` documentation and issue_tracker URLs updated to the WhiteWolf84 fork; @WhiteWolf84 added to codeowners.

---

## [2026.4.0] - 2026-04-01

### Fixed

- **`async_setup_entry` deadlock**: `async_forward_entry_setups` is now awaited directly instead of being scheduled as a task, preventing partial setup races.

### Changed

- Version bump for Home Assistant 2026.1 compatibility line.

---

## [2026.3.0] - 2026-03-17

### Added

- **SAI2 alarm control panel**: full integration with the VIMAR SAI2 domestic alarm system. Each named area (group) is exposed as an `alarm_control_panel` entity with Disarm, Arm Away, Arm Home, and Arm Night actions. Automatic disarm-before-rearm when switching between armed modes. PIN protection via integration options. All entities grouped under a single "SAI Alarm" device.
- **SAI2 zone binary sensors**: each SAI2 zone (door contact, motion detector, tamper sensor, etc.) is exposed as a `binary_sensor` with automatic device class detection based on zone name keywords. Live state from parent object DPADD_OBJECT bitmask. Extra attributes: `raw_value`, `excluded`, `alarm`, `tampered`, `masked`, `memory`, `area`.
- **Re-authentication flow**: automatic reauth trigger when credentials expire or become invalid, with a user-friendly confirmation dialog in the HA UI.
- **`available` property**: entities now correctly report `unavailable` when the Vimar web server is unreachable, authentication fails, or a device is removed from the Vimar configuration.
- **Internationalization (i18n)**: config flow, options flow, and reauth flow fully translated into 7 languages — English, Italian, German, French, Spanish, Dutch, Portuguese.
- **Time-based cover position tracking**: covers report an estimated current position calculated from configurable travel times (`travel_time_up` / `travel_time_down`), with four operating modes: `legacy`, `native`, `time_based`, `auto`.
- **Relay delay compensation**: configurable offset to account for mechanical relay switching latency in cover position calculations.
- **Cover physical button detection**: movement triggered by physical wall switches is detected and distinguished from HA-initiated commands, keeping position tracking accurate.
- **Slim polling**: after the initial full discovery, subsequent update cycles query only the status IDs indexed at startup (`get_status_only()`), skipping all device/room JOINs. Reduces per-poll database workload by ~90% on embedded hardware.
- **Hash-based change detection**: each poll computes a lightweight hash of every device's status values. Only devices whose hash changed since the last cycle are propagated to Home Assistant.
- **Selective entity state writes**: `_handle_coordinator_update()` skips entities whose device has not changed (`_changed_device_ids` filter), reducing HA event-bus pressure on large installations.
- **Modular `vimarlink` architecture**: `vimarlink` refactored into a proper package with dedicated modules — `connection.py`, `device_queries.py`, `sql_parser.py`, `http_adapter.py`, `exceptions.py` — and a streamlined `vimarlink.py` facade.
- **`ConfigEntryAuthFailed` propagation**: the coordinator raises the correct HA exception type on authentication errors, enabling the automatic reauth flow.
- **Graceful transient error recovery**: SQL parsing errors return `None` instead of triggering re-authentication, preventing SSL handshake storms on overloaded web servers.
- **Compact poll logging**: two summary DEBUG lines per cycle (`Updated (N): name1, name2, ...` / `Skipped (N): name1, name2, ...`) replacing one line per entity per cycle.
- **GitHub project scaffolding**: issue templates (bug report, feature request), pull request template, CI/CD workflow, `CONTRIBUTING.md`, `CODEOWNERS`.

### Fixed

- **UI desync after consecutive actions on monostable devices**: `request_statemachine_update()` now invalidates the device's cached hash after every optimistic write.
- **`_changed_device_ids` overwritten by slim poll**: `_detect_state_changes()` now merges new IDs into the existing set (`.update()`) instead of replacing it.
- **`_changed_device_ids` carrying stale IDs across cycles**: the set is now cleared at the beginning of each `_async_update_data()` cycle.
- **Class-level mutable attributes shared across config entries**: `_device_state_hashes`, `_changed_device_ids`, `_known_status_ids` in `VimarDataUpdateCoordinator`, and `_attributes` in `VimarEntity`, moved to `__init__()`.
- **`_device_state_hashes` not reset on reload**: `init_vimarproject()` now clears the hash map so stale hashes do not mask real state changes after a config reload.
- **`RecursionError` on large installations**: `get_paged_results()` converted from recursive to iterative `while` loop.
- **`ToggleEntity` deprecation**: `switch.py` updated to inherit from `SwitchEntity`.
- **`is_default_state` wrong value for off state**: fixed to `not self.is_on`.
- **`assumed_state` inverted logic**: corrected to return `True` when state is assumed, `False` when known.
- **`_LOGGER_isDebug` stale at import time**: replaced with `_LOGGER.isEnabledFor(logging.DEBUG)` evaluated at runtime.
- **`_device_overrides` and `vimarconfig` shared across customizer instances**: moved to `__init__()`.
- **SSL ignore warning logged on every request**: replaced with instance attribute `_ssl_ignore_logged`.
- **`AttributeError` on empty SQL payload**: added `None` guard in `parse_sql_payload()`.
- **`format_name()` silent truncation**: restored original sequential `replace()` chain.
- **`extra_state_attributes` accumulating stale keys**: fixed by returning a fresh `dict` on every call.
- **`get_remote_devices_query` duplicate columns**: removed duplicate `object_name` and `object_type` from `SELECT` clause.
- **`async_remove_old_devices()` never removing stale devices**: fixed identifier comparison to use `frozenset`.
- **`entry.state.name` fragile string comparison**: replaced with `async_config_entry_first_refresh()`.
- **`CONF_OVERRIDE` propagated as `None`**: fixed with `or []` guard.
- **Cover `TypeError` on first `set_cover_position` call**: added `None` guard for `_tb_position`.
- **Cover physical button false-positives**: added `_tb_ha_command_active` flag.
- **Duplicate device names in poll log**: deduplicated by `device_id` using a `seen_ids` set.
- **Cover UI update granularity**: `UI_UPDATE_THRESHOLD` reduced from 2% to 1%.
- **Optimized SQL queries**: removed duplicate columns, reordered `WHERE` clauses, added `DISTINCT` to `GROUP_CONCAT`.
- **O(n²) device-hash computation**: replaced with a single `"".join()` call.
- **`change_state()` code duplication**: extracted into `_apply_state_change()` helper.

---

## Version Numbering

This project uses [Calendar Versioning](https://calver.org/) with the `YYYY.M.INCREMENT` scheme:
- `YYYY` — year of release
- `M` — month of release (1–12, no leading zero)
- `INCREMENT` — incremental release within the same month (starting from 0)
