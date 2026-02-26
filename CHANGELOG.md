# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Performance

- **Slim polling**: after the initial full discovery, subsequent update cycles use `get_status_only()` — a lightweight query that fetches only the status values indexed at startup, skipping all device/room JOINs. Reduces per-poll database workload by ~90% on embedded hardware.
- **Hash-based change detection**: each poll computes a hash of every device's status values. Only devices whose hash changed are propagated to Home Assistant, eliminating redundant entity state writes.
- **Selective entity updates**: `_handle_coordinator_update()` early-exits for entities whose device has not changed (`_changed_device_ids` filter), reducing HA event-bus pressure proportionally to idle devices.
- **Optimized SQL queries**: removed duplicate columns from `get_remote_devices_query`, reordered `WHERE` clauses, added `DISTINCT` to `GROUP_CONCAT` in room queries — 10–20% faster discovery queries.
- **O(n) device-hash computation**: replaced the O(n²) string-concatenation loop in `_reload_entry_if_devices_changed()` with a single `"".join()` call.

### Fixed

- **UI desync after consecutive actions on monostable devices** (all device types): `request_statemachine_update()` now removes the device's cached hash immediately after any optimistic write (`turn_on`, `turn_off`, `set_temperature`, `open_cover`, etc.). Previously, a second consecutive action on a monostable device (e.g. a garage relay) could leave the entity stuck in the wrong state because the web server's second return-to-zero produced a hash identical to the first and was silently ignored.
- **`_changed_device_ids` overwritten by slim poll**: `_detect_state_changes()` now merges newly detected IDs into the existing set (`.update()`) instead of replacing it, preserving IDs added by `request_statemachine_update()` during a manual action.
- **`_changed_device_ids` carrying stale IDs across cycles**: the set is now cleared at the beginning of each `_async_update_data()` cycle so every poll starts clean.
- **Duplicate device names in poll log**: `_log_poll_summary()` deduplicates by `device_id` using a `seen_ids` set, preventing multi-attribute sensors (power meters, etc.) from appearing multiple times in summary lines.
- **Class-level mutable attributes shared across config entries**: `_device_state_hashes`, `_changed_device_ids`, and `_known_status_ids` in `VimarDataUpdateCoordinator`, and `_attributes` in `VimarEntity`, were class-level defaults shared by all instances in multi-entry setups. All moved to `__init__()` as proper instance attributes.
- **`_device_state_hashes` not reset on reload**: `init_vimarproject()` now clears the hash map so stale hashes from a previous session do not mask real state changes after a config reload.
- **`RecursionError` on large installations**: `get_paged_results()` was recursive and could exceed Python's default call-stack limit with many pages of 300 devices. Converted to an iterative `while` loop with identical return semantics.
- **`ToggleEntity` deprecation**: `switch.py` was inheriting from the removed `ToggleEntity`. Updated to `SwitchEntity` from `homeassistant.components.switch`.
- **`is_default_state` wrong value for off state**: the expression `(self.is_on, True)[self.is_on is None]` evaluated to `self.is_on` (i.e. `False`) when the device was off, instead of the correct `True`. Fixed to `not self.is_on`.
- **`_LOGGER_isDebug` stale at import time**: in `vimarlink.py` and `vimar_device_customizer.py` the flag was evaluated once at module import, before HA configures log levels. Replaced with `_LOGGER.isEnabledFor(logging.DEBUG)` evaluated at runtime.
- **`_device_overrides` and `vimarconfig` shared across customizer instances**: both were class-level defaults in `VimarDeviceCustomizer`; moved to `__init__()` as instance attributes.
- **SSL ignore warning logged on every request**: the module-level `SSL_IGNORED` flag in `connection.py` was re-evaluated per request. Replaced with an instance attribute `_ssl_ignore_logged` so the warning appears once per connection instance.
- **`AttributeError` on empty SQL payload**: `sql_parser.py` raised `AttributeError` when the web server returned an empty or malformed response. Added a `None` guard at the top of `parse_sql_payload()`.
- **`format_name()` silent truncation**: the loop-with-`continue` rewrite of `format_name()` incorrectly skipped the `LICHT` guard. Restored the original sequential `replace()` chain with an explicit guard.
- **`extra_state_attributes` accumulating stale keys**: the property was mutating `self._attributes` in place; keys removed from the device data remained visible in Lovelace indefinitely. Fixed by building and returning a fresh `dict` on every call.
- **`get_remote_devices_query` duplicate columns**: `object_name` and `object_type` appeared twice in the `SELECT` clause. The SQL parser silently used the second value, wasting bandwidth. Duplicates removed.
- **`async_remove_old_devices()` never removing stale devices**: the device registry identifier was compared as a plain string against actual `frozenset` objects; the comparison never matched. Fixed by constructing the identifier as a native `frozenset`.
- **`entry.state.name` fragile string comparison**: replaced `entry.state.name == "SETUP_IN_PROGRESS"` with a direct call to `async_config_entry_first_refresh()`, removing a dependency on an internal HA enum name that is not part of the public API.
- **`CONF_OVERRIDE` propagated as `None`**: if the YAML key was absent, `None` was passed to the coordinator instead of an empty list, causing an iteration crash at startup. Fixed with `or []`.
- **Cover `TypeError` on first `set_cover_position` call**: `async_set_cover_position()` compared `self._tb_position` (which is `None` before the first tracking cycle) against an integer, raising `TypeError`. Added a `None` guard that logs a warning and defaults the position to `0`.
- **Cover physical button false-positives**: HA UI commands incorrectly triggered the physical-button detection path. Added `_tb_ha_command_active` flag to distinguish HA-initiated movements from genuine button presses.
- **`assumed_state` inverted logic**: `assumed_state` returned `True` when the state was known and `False` when assumed — the opposite of what HA expects.
- **Coordinator logger incorrect reference**: fixed the logger instantiation in `vimar_coordinator.py` to use the correct module path.

### Changed

- **Poll logging**: replaced one DEBUG line per entity per cycle with two compact summary lines per cycle: `Updated (N): name1, name2, …` and `Skipped (N): name1, name2, …`. The first full-discovery cycle is excluded to avoid noise at startup.
- **`change_state()` refactored**: extracted the duplicated args/kwargs state-write blocks into a single `_apply_state_change()` helper, reducing code duplication without any functional change.

### Added

- **`_log_poll_summary()` helper**: centralised, deduplication-aware summary emitted once per cycle by the coordinator.
- **Hash invalidation on optimistic writes**: `request_statemachine_update()` calls `_device_state_hashes.pop(device_id, None)` to guarantee the next poll always re-evaluates the device against the web server's actual state.

---

## [2026.2.0] - 2026-02-21

### Added

- **Re-authentication flow** (Silver quality requirement): automatic reauth trigger when credentials become invalid, user-friendly confirmation dialog, failure counter to prevent authentication storms.
- **Proper unavailable state handling** (Silver quality requirement): entities correctly report `unavailable` when the web server is offline, authentication fails, network connectivity is lost, or a device is removed from the Vimar configuration.
- **Complete internationalization (i18n)**: config flow, options flow, and reauth flow fully translated into 7 languages — 🇬🇧 English, 🇮🇹 Italian, 🇩🇪 German, 🇫🇷 French, 🇪🇸 Spanish, 🇳🇱 Dutch, 🇵🇹 Portuguese — covering ~85% of European HA users.
- **Modular `vimarlink` architecture**: `vimarlink` package split into `connection.py`, `device_queries.py`, `sql_parser.py`, `http_adapter.py`, `exceptions.py`, and a streamlined `vimarlink.py` facade.
- **`ConfigEntryAuthFailed` exception propagation**: coordinator raises the correct HA exception type on authentication errors, enabling the automatic reauth flow.
- **Graceful transient error recovery**: SQL parsing errors return `None` instead of forcing re-authentication; prevents SSL handshake storms on overloaded web servers.
- **GitHub project scaffolding**: issue templates (bug report, feature request), PR template, CI/CD workflow, `CONTRIBUTING.md`, `CODEOWNERS`, `SILVER_COMPLIANCE.md`.

### Fixed

- `AttributeError` in `VimarStatusSensor` when accessing connection attributes after architecture refactoring.
- Cover physical button detection incorrectly triggered during HA UI commands; added `_tb_ha_command_active` flag to separate HA-initiated movements from physical button presses.

### Changed

- Cover UI position update threshold reduced from 2% to 1% for smoother movement feedback.
- `_request_vimar_sql()` returns `None` on transient errors instead of raising.
- SQL parser handles malformed payloads gracefully without propagating exceptions.

---

## [2026.1.0] - 2026-01-XX

### Added

- Time-based cover position tracking.
- Custom travel times configuration per cover.
- Four operating modes for covers: `legacy`, `native`, `time_based`, `auto`.
- Relay delay compensation for accurate positioning.

### Fixed

- Database spam during cover operations.
- Position sync issues with physical buttons.

---

## Previous Versions

See [GitHub Releases](https://github.com/h4de5/home-assistant-vimar/releases) for older versions.

---

## Version Numbering

This project uses the `YYYY.MINOR.PATCH` scheme:
- `YYYY` — year of release
- `MINOR` — incremented for new features or significant changes
- `PATCH` — incremented for bug fixes and minor updates

Example: `2026.2.0` = second major release of 2026, initial version.
