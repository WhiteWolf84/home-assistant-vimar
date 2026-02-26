# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> This fork is based on [h4de5/home-assistant-vimar](https://github.com/h4de5/home-assistant-vimar).
> All changes listed below are relative to the original upstream `master` branch.

---

## [Unreleased]

### Added

- **Re-authentication flow**: automatic reauth trigger when credentials expire or become invalid, with a user-friendly confirmation dialog in the HA UI. Implements the Home Assistant Silver quality requirement.
- **`available` property**: entities now correctly report `unavailable` when the Vimar web server is unreachable, authentication fails, or a device is removed from the Vimar configuration. Implements the Home Assistant Silver quality requirement.
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

- **UI desync after consecutive actions on monostable devices**: `request_statemachine_update()` now invalidates the device's cached hash after every optimistic write. Without this, a second consecutive action on a monostable device (e.g. a garage relay) could leave the entity stuck in the wrong state because the web server's second return-to-zero produced a hash identical to the first and was silently ignored by change detection.
- **`_changed_device_ids` overwritten by slim poll**: `_detect_state_changes()` now merges new IDs into the existing set (`.update()`) instead of replacing it, preserving IDs added by `request_statemachine_update()` during a manual action.
- **`_changed_device_ids` carrying stale IDs across cycles**: the set is now cleared at the beginning of each `_async_update_data()` cycle.
- **Class-level mutable attributes shared across config entries**: `_device_state_hashes`, `_changed_device_ids`, `_known_status_ids` in `VimarDataUpdateCoordinator`, and `_attributes` in `VimarEntity`, were class-level defaults shared by all instances. All moved to `__init__()` as proper instance attributes.
- **`_device_state_hashes` not reset on reload**: `init_vimarproject()` now clears the hash map so stale hashes do not mask real state changes after a config reload.
- **`RecursionError` on large installations**: `get_paged_results()` was recursive and could exceed Python's default call-stack limit (~1000 frames) with many pages of 300 devices. Converted to an iterative `while` loop.
- **`ToggleEntity` deprecation**: `switch.py` was inheriting from the removed `ToggleEntity`. Updated to `SwitchEntity`.
- **`is_default_state` wrong value for off state**: the expression `(self.is_on, True)[self.is_on is None]` evaluated to `False` when the device was off instead of the correct `True`. Fixed to `not self.is_on`.
- **`assumed_state` inverted logic**: `assumed_state` returned `True` when the state was known and `False` when assumed — the opposite of what HA expects.
- **`_LOGGER_isDebug` stale at import time**: in `vimarlink.py` and `vimar_device_customizer.py`, the debug flag was evaluated once at module import before HA configures log levels. Replaced with `_LOGGER.isEnabledFor(logging.DEBUG)` evaluated at runtime.
- **`_device_overrides` and `vimarconfig` shared across customizer instances**: both were class-level defaults in `VimarDeviceCustomizer`; moved to `__init__()`.
- **SSL ignore warning logged on every request**: replaced the module-level `SSL_IGNORED` flag in `connection.py` with an instance attribute `_ssl_ignore_logged` so the message appears only once per connection instance.
- **`AttributeError` on empty SQL payload**: `sql_parser.py` raised `AttributeError` when the web server returned an empty or malformed response. Added a `None` guard at the top of `parse_sql_payload()`.
- **`format_name()` silent truncation**: a loop-with-`continue` rewrite incorrectly skipped the `LICHT` guard. Restored the original sequential `replace()` chain.
- **`extra_state_attributes` accumulating stale keys**: the property mutated `self._attributes` in place; keys removed from device data remained visible in Lovelace indefinitely. Fixed by returning a fresh `dict` on every call.
- **`get_remote_devices_query` duplicate columns**: `object_name` and `object_type` appeared twice in the `SELECT` clause due to a copy-paste error, wasting bandwidth. Duplicates removed.
- **`async_remove_old_devices()` never removing stale devices**: the device registry identifier was compared as a plain string against `frozenset` objects; the comparison never matched. Fixed by using a native `frozenset`.
- **`entry.state.name` fragile string comparison**: replaced `entry.state.name == "SETUP_IN_PROGRESS"` with a direct call to `async_config_entry_first_refresh()`, removing a dependency on an internal HA enum name.
- **`CONF_OVERRIDE` propagated as `None`**: if the YAML key was absent, `None` was passed to the coordinator instead of an empty list, causing an iteration crash at startup. Fixed with `or []`.
- **Cover `TypeError` on first `set_cover_position` call**: `async_set_cover_position()` compared `self._tb_position` (which is `None` before the first tracking cycle) against an integer. Added a `None` guard that logs a warning and defaults the position to `0`.
- **Cover physical button false-positives**: HA UI commands incorrectly triggered the physical-button detection path. Added `_tb_ha_command_active` flag to distinguish HA-initiated movements from genuine button presses.
- **Duplicate device names in poll log**: `_log_poll_summary()` deduplicates by `device_id` using a `seen_ids` set, preventing multi-attribute sensors (power meters, etc.) from appearing multiple times per cycle.
- **Cover UI update granularity**: `UI_UPDATE_THRESHOLD` reduced from 2% to 1% for smoother position feedback during movement.
- **Optimized SQL queries**: removed duplicate columns from discovery queries, reordered `WHERE` clauses, added `DISTINCT` to `GROUP_CONCAT` in room queries for 10–20% faster discovery.
- **O(n) device-hash computation**: replaced the O(n²) string-concatenation loop in `_reload_entry_if_devices_changed()` with a single `"".join()` call.
- **`change_state()` code duplication**: extracted the identical args/kwargs state-write blocks into a single `_apply_state_change()` helper.

---

## Version Numbering

This project uses the `YYYY.MINOR.PATCH` scheme:
- `YYYY` — year of release
- `MINOR` — incremented for new features or significant changes
- `PATCH` — incremented for bug fixes and minor updates
