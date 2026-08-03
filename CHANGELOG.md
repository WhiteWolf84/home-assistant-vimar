# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Calendar Versioning](https://calver.org/) (`YYYY.M.INCREMENT`).

> This fork is based on [h4de5/home-assistant-vimar](https://github.com/h4de5/home-assistant-vimar).
> All changes listed below are relative to the original upstream `master` branch.

---

## [Unreleased]

---

## [2026.8.0] - 2026-08-03

> First stable release since `2026.7.1`. It contains everything from the
> `2026.8.0b0`–`b5` pre-releases and from `2026.7.2b0`, all validated against
> real hardware (web server 01945). The sections below describe the net effect
> of upgrading from `2026.7.1`; the individual pre-release entries are kept
> further down for reference.
>
> **Minimum Home Assistant version is now 2026.5.0.**

### Action required after upgrading — reactive power sensors only

Skip this unless you have "Potenza Reattiva" sensors. Home Assistant will log
this once and **stop recording their long-term statistics** until you act:

> The unit of sensor.… (kvar) cannot be converted to the unit of previously
> compiled statistics (kW).

Those sensors were being recorded as if they measured real power in kW, and kW
cannot be converted to kvar because the two measure different things. Clear it
in **Settings → Devices & services → Repairs**, opening the "units changed"
issue for each sensor and choosing to delete the old statistics. The sensor
then starts a clean series in the correct unit.

### Security

- The VIMAR password is no longer written to `home-assistant.log`. A network error during login produced a message containing the full login URL, credentials included, which was logged and shown in the configuration dialog — and log files are routinely attached to bug reports. Passwords, usernames and session ids are now masked in everything the connection layer logs or raises.
- The connection status sensor no longer publishes the VIMAR **username** and **session id** as state attributes. Attributes are readable by every Home Assistant user and kept in the recorder database for weeks; the session id in particular is a live credential for the web server. Host, port, URL, TLS settings and certificate are still reported.
- The `vimar.exec_vimar_sql` service, which runs arbitrary SQL against the web server database, is now restricted to **administrator** accounts. It could previously be called by any Home Assistant user, including non-admin and script-only ones, while the far less dangerous `vimar.reload` was already admin-only.

### Fixed

- Entities now go **unavailable** when the VIMAR web server is unreachable. An entity refreshed itself only when the poll reported its device as changed, and a failed poll reports nothing as changed — so Home Assistant kept showing the last known values indefinitely: lights still "on", the last thermostat reading, the last shutter position, with no sign the connection was gone and no way for automations to notice.
- Logins with a password containing `&`, `=`, `#`, `+`, `%` or a space now work. The credentials were pasted straight into the login URL, so those characters broke the request apart and the web server received a truncated password — the user was told the credentials were invalid while they were perfectly correct.
- Starting the integration no longer risks failing on a slower web server. Logging in and reading the whole configuration were given the same few seconds allowed for a routine status poll, though they are far slower by nature: measured on real hardware, the login alone used 72% of that budget on a perfectly healthy system.
- Connection errors are reported properly instead of turning into a second, confusing error. Any failure message containing a `%` — common, because web addresses encode special characters that way — made the error handling itself crash while displaying the message, replacing a clear "cannot connect" with an obscure internal error.
- Reloading the integration no longer leaves a platform half-loaded on installations without a SAI2 alarm. The unload step only undid platforms that had registered at least one entity, so the alarm platform — loaded, then skipped for lack of alarm areas — was never released.
- Changing the temperature while a thermostat is in Absence no longer kicks it out of Absence. The integration forced manual mode before writing; it now writes only the setpoint, exactly like the native By-Web "T Assenza" panel, and reads back what the firmware actually applied.
- Sensors now go through Home Assistant's standard handling of measurements instead of bypassing it. The raw text from the web server was written straight into the sensor value, skipping unit conversion, any unit you had chosen for that sensor, sensible rounding, and the conversion from text to number. Values are now real numbers, and a reading the web server cannot express shows as "unknown" for that one reading instead of leaving stray text in the history.
- Four measurements were described to Home Assistant incorrectly, which made it log a warning at every start and refuse to handle their units: brightness was declared in lumen (Home Assistant uses lux for light level); the wind sensor was declared a **pressure** sensor; reactive power was declared as ordinary power in kW, so it looked like real consumption and could be added to the Energy dashboard as such; and date/time readings on energy meters were declared as timestamps in a format the web server does not use, so displaying one raised an error instead of showing the value.
- Fields that are switches, modes or labels are no longer published as measurements. The integration works out what a value means from the name the web server gives it, and the web server names its flags after the thing they relate to — so `temperature_alarm`, only ever 0 or 1, was published as a temperature of **0 °C**, indistinguishable from a genuinely freezing reading. On load-control hardware the same fault affected the operating mode, the forcing flag and the counter resets, all published as "0 kW". Thirteen such entities were found on a single test installation.
- The "Fase" field on load-control devices is shown as text rather than a current in ampere: it holds the type of electrical supply, `monofase` or `trifase`. Genuine per-phase currents are unaffected.
- An unrecognised field on an energy meter is no longer assumed to be power in kW. A wrong unit that looks plausible cannot be spotted; such a field now appears without a unit and is named in the debug log so a proper rule can be added.
- Temperature, brightness and wind sensors now keep long-term statistics. They were missing the marker that tells Home Assistant a value is worth recording over time, so their history disappeared with the normal database cleanup and they could not be used in long-term graphs.
- Scenes use Home Assistant's own record of when they were last activated, instead of a second copy of the same information that could disagree with it.
- The debug log no longer reports devices as newly discovered when the integration itself has just written to them. After every command the affected device was listed as `New device detected` on the next poll — five thermostats at once after a scene — which looked like a discovery problem on installations whose configuration had not changed at all.

### Changed

- The integration now **reuses its connection** to the VIMAR web server. Every single request — each poll, each command, each meter reading — used to open a brand new HTTPS connection and negotiate a full TLS handshake, thousands of times a day against a small embedded device. Connections are now kept alive and reused, which makes commands respond faster and takes a constant load off the web server.
- Energy-meter and thermostat refreshes no longer compete with the poll for the same time budget. They ran inside the poll's timeout while issuing one request per meter and per thermostat, so on larger installations they could use it up on their own and make every entity flicker to "unavailable" for that cycle. They now run in the background, and a refresh still in progress is never started twice.
- Minimum supported Home Assistant version raised to **2026.5.0** (development happens on 2026.7.0).
- Internal: the list of which VIMAR device models are energy meters, thermostats, shutters and so on now exists in exactly one place instead of three files that had to be kept in agreement by hand.
- Internal: test count 53 → **360**, with the sensor rules pinned against the fields of a real installation rather than against what their names suggest.

### Removed

- Four modules that were never used by the integration (338 lines), including a duplicate copy of the code that handles the web server's legacy encryption — the kind of duplicate where a fix can silently be applied to the wrong copy. The README described two of them as part of the architecture.
- The `vimar.reload_default` service, which appeared in the service list with a name and description but was never implemented: calling it returned "service not found". Use `vimar.reload`, or the "delete and reload all entities" option in the integration settings.

---

## [2026.8.0b5] - 2026-08-03

> **Beta.** Fixes a regression introduced by `2026.8.0b4`, which it replaces.
> If you are on `b4`, six power readings on your load-control device currently
> have no unit; this restores them. The action required for reactive power
> sensors, first described in `b3`, is unchanged and does not repeat if you
> have already done it.

### Fixed

- The six instantaneous power readings on a load-control device keep their unit. `2026.8.0b4` stopped guessing that any unrecognised field on a meter was a power reading, which was right in principle but stripped the unit from `consumo_totale`, `produzione_totale`, `autoconsumo_totale`, `immissione_totale`, `prelievo_totale` and `scambio_totale` — genuine readings in kW. They are now listed by name. Verified against live hardware: each has a cumulative `energia_totale_*` counterpart in kWh, and the values add up (consumption = self-consumption + withdrawal), which only holds for instantaneous power.
- Load-control modes and flags are no longer published as power. `forzatura`, `funzionamento`, `dynamic_mode`, `reset_history`, `reset_partial` and `produzione_presente` were all reported as readings in kW, sitting at "0 kW" — the same fault as the weather station flags, on hardware that is far more common. Thirteen such entities were found on a single test installation.
- A field the web server itself declares as limited to 0 or 1 is never treated as a measurement, whatever its name suggests. The device states this in the data it sends, which is better evidence than a guess based on the field name.

---

## [2026.8.0b4] - 2026-08-03

> **Beta.** Includes everything in `2026.8.0b3`, which it replaces, and fixes
> what field-testing that beta uncovered.

### Action required after upgrading — reactive power sensors only

Unchanged from `2026.8.0b3`; skip this if you already did it. If you have
"Potenza Reattiva" sensors, Home Assistant will log this once and **stop
recording their long-term statistics** until you act:

> The unit of sensor.… (kvar) cannot be converted to the unit of previously
> compiled statistics (kW).

Those sensors were recording as if they measured real power in kW, and kW
cannot be converted to kvar because the two measure different things. Clear it
in **Settings → Devices & services → Repairs**, opening the "units changed"
issue for each sensor and choosing to delete the old statistics.

### Fixed

- Fields that are switches or labels are no longer presented as measurements. The integration works out what a value means from the name the web server gives it, and the web server names its flags after the thing they relate to — so a field called `temperature_alarm`, which is only ever 0 or 1, was being published as a temperature of **0 °C**, indistinguishable from a genuinely freezing reading. The same happened to `temperature_reset`, `wind_speed_alarm` and their siblings. The integration now asks "is this a reading at all?" before asking "a reading of what?".
- The "Fase" field on load-control devices is shown as text instead of a current in ampere. It holds the type of electrical supply — `monofase` or `trifase` — and was matching the rule written for per-phase currents. Since `2026.8.0b3` this showed as "unknown", because text cannot be turned into a number; it now simply shows the value. Genuine per-phase currents are unaffected.
- An unrecognised field on an energy meter is no longer assumed to be power in kW. Anything the integration did not have a rule for was labelled as a power reading, which looks entirely plausible and therefore cannot be spotted. Such a field now appears without a unit — visibly incomplete rather than convincingly wrong — and is named in the debug log so a proper rule can be added.

---

## [2026.8.0b3] - 2026-08-03

> **Beta.** Field-testing the sensor changes before they are merged. Unlike
> `2026.8.0b1` and `2026.8.0b2`, which only removed failure modes, this one
> changes what you will see: sensor values become numbers rather than text
> (`11.00` becomes `11.0`), the brightness sensor changes unit and the wind
> sensor changes type, so Home Assistant may ask you to confirm the unit for
> those entities. Recorded history keeps the old format up to the upgrade and
> the new one after it.

### Action required after upgrading — reactive power sensors only

If you have "Potenza Reattiva" sensors, Home Assistant will log this once and
**stop recording their long-term statistics** until you act:

> The unit of sensor.… (kvar) cannot be converted to the unit of previously
> compiled statistics (kW). Generation of long term statistics will be
> suppressed unless the unit changes back to kW or a compatible unit.

This is the correction working as intended, not a fault. Those sensors were
recording as if they measured real power in kW; the statistics already stored
under that unit are wrong, and kW cannot be converted to kvar because the two
measure different things.

To clear it: **Settings → Devices & services → Repairs**, open the
"units changed" issue for each sensor and choose to delete the old statistics.
The sensor then starts a clean series in kvar.

Only reactive power is affected. Brightness, wind speed and temperature also
changed, but they had no state class before and so had no statistics to
contradict — they simply start recording for the first time.

### Fixed

- Starting the integration no longer risks failing on a slower web server. Logging in and reading the whole configuration were given the same few seconds allowed for a routine status poll, even though they are far slower by nature: measured on real hardware, the login alone used 72% of that budget on a perfectly healthy system. Anything slower — a busier web server, or a Home Assistant start where every integration competes for resources — made the first attempt fail and the integration report itself as unavailable. Setup now gets its own, generous allowance, while routine polling keeps the short one, because a slow poll really is a symptom worth reporting quickly.
- Connection errors are reported properly instead of turning into a second, confusing error. Any failure message containing a `%` character — which is common, because web addresses encode special characters that way — made the error handling itself crash while trying to display the message. The result was an obscure internal error in place of a clear "cannot connect".
- Sensors now go through Home Assistant's standard handling of measurements instead of bypassing it. The integration was writing the raw text it received from the web server straight into the sensor's value, skipping the step where Home Assistant converts units, applies the unit you may have chosen for that specific sensor, rounds to a sensible number of decimals, and turns the text into a number. Values are now real numbers, and a reading the web server cannot express (a momentary blank, say) shows as "unknown" for that one reading instead of leaving a stray piece of text in the history.
- Four measurements were described to Home Assistant incorrectly, which made it log a warning at every start and refuse to handle their units:
  - the brightness sensor was declared in lumen, a unit Home Assistant does not accept for light level (it uses lux);
  - the wind sensor was declared a **pressure** sensor;
  - reactive power was declared as ordinary power in kW, so it looked like real consumption and could be added to the Energy dashboard as if it were;
  - date and time readings on energy meters were declared as timestamps, which Home Assistant only accepts in a specific format that the web server does not use — displaying one raised an error instead of showing the value.
- Temperature, brightness and wind sensors now keep long-term statistics. They were missing the marker that tells Home Assistant a value is worth recording over time, so their history disappeared with the normal database cleanup (10 days by default) and they could not be used in long-term graphs.
- Scenes now use Home Assistant's own record of when they were last activated, instead of keeping a second copy of the same information that could disagree with it.

### Changed

- The list of which VIMAR device models are energy meters, thermostats, shutters and so on now exists in exactly one place. It was written out by hand in three files that had to be kept in agreement, with a comment asking whoever edited one to remember the others. Forgetting produced a meter that appears in Home Assistant but never updates, or updates but shows no unit. A test now fails if the lists and the code that uses them ever disagree.

### Removed

- Four modules that were never used by the integration (338 lines), including a duplicate copy of the code that handles the VIMAR web server's legacy encryption — the kind of duplicate where a fix can silently be applied to the wrong copy. The README described two of them as part of the architecture.
- The `vimar.reload_default` service, which appeared in the service list with a name and description but was never implemented: calling it returned "service not found". Use `vimar.reload`, or the "delete and reload all entities" option in the integration settings.

---

## [2026.8.0b2] - 2026-08-03

> **Beta.** Same purpose as `2026.8.0b1`, which it replaces and fully
> includes: field-testing the connection reuse before it is merged.
>
> First 10 minutes on real hardware were clean — no connection errors, polls
> between 0.065 s and 0.15 s for 268 status objects, no drift over time. The
> measurements also confirmed why the refresh change was needed: the periodic
> meter + thermostat refresh takes 4–6 s of sequential requests, against a 6 s
> poll budget it used to share.

### Fixed

- The debug log no longer reports devices as newly discovered when the
  integration itself has just written to them. After every command, the
  affected device was listed as `New device detected` on the next poll — five
  thermostats at once after a scene, a shutter three polls in a row — which
  looked like a discovery problem on installations whose configuration had not
  changed at all. The underlying resynchronisation, which is what makes the
  interface follow a device that answers with the value it already had, is
  unchanged; only the way it is recorded, and reported, was wrong.

---

## [2026.8.0b1] - 2026-08-03

> **Beta.** Published as a HACS pre-release from the `perf/connection-reuse`
> branch, for on-device testing before it is merged. It builds on `2026.8.0b0`
> and contains everything that release did.
>
> **What to watch while testing:** the connection reuse is a structural change.
> If the VIMAR web server closes idle keep-alive connections aggressively you
> may see isolated errors when one is reused; read requests retry once by
> themselves, commands are never replayed. Anything recurring in the log is
> worth reporting.

### Changed

- The integration now **reuses its connection** to the VIMAR web server. Every single request — each poll, each command, each meter reading — used to open a brand new HTTPS connection and negotiate a full TLS handshake, thousands of times a day against a small embedded device. Connections are now kept alive and reused, which makes commands respond faster and takes a constant load off the web server.
- Energy-meter and thermostat refreshes no longer compete with the poll for the same time budget. They ran inside the poll's timeout while issuing one request per meter and per thermostat, so on larger installations they could use it up on their own and make every entity flicker to "unavailable" for that cycle. They now run in the background, and a refresh that is still in progress is never started twice.

### Security

- The connection status sensor no longer publishes the VIMAR **username** and **session id** as state attributes. Attributes are readable by every Home Assistant user and are kept in the recorder database for weeks; the session id in particular is a live credential for the web server. The sensor still reports host, port, URL, TLS settings and certificate.

---

## [2026.8.0b0] - 2026-08-02

> **Beta.** Published as a HACS pre-release for on-device testing before a
> stable `2026.8.0`. It supersedes `2026.7.2b0` and **includes its fix** (the
> Absence setpoint write), so beta testers can move straight to this version.

### Fixed

- Entities now go **unavailable** when the VIMAR web server is unreachable. To avoid useless work, an entity refreshed itself only when the poll reported its device as changed; a failed poll reports nothing as changed, so the update was skipped and Home Assistant kept showing the last known values — lights still "on", the last thermostat reading, the last shutter position — indefinitely, with no sign that the connection was gone and no way for automations to notice. Availability changes are now always published, in both directions, so an outage is visible immediately and entities come back by themselves once the web server answers again.
- Logins with a password containing `&`, `=`, `#`, `+`, `%` or a space now work. The credentials were pasted straight into the login URL, so those characters broke the request apart and the web server received a truncated password: the user was told the credentials were invalid while they were perfectly correct. They are now encoded properly.
- The VIMAR password is no longer written to `home-assistant.log`. A network error during login produced an error message containing the full login URL, credentials included, which was logged and shown in the configuration dialog — and log files are routinely attached to bug reports. Passwords, usernames and session ids are now masked in every message the connection layer logs or raises.
- Reloading the integration no longer leaves a platform half-loaded on installations without a SAI2 alarm. The unload step only undid platforms that had registered at least one entity, so the alarm platform — loaded, then skipped for lack of alarm areas — was never released. What gets loaded is now recorded at setup and is exactly what gets unloaded.

### Security

- The `vimar.exec_vimar_sql` service, which runs arbitrary SQL against the VIMAR web server database, is now restricted to **administrator** accounts. It could previously be called by any Home Assistant user, including non-admin and script-only accounts, while the far less dangerous `vimar.reload` service was already admin-only.

### Changed

- Minimum supported Home Assistant version raised to **2026.5.0** (development happens on 2026.7.0).
- Internal: added 88 tests covering the four fixes above plus the SQL response parser, the SAI2 alarm bitmask decoding (armed/disarmed/triggered state and zone open/tamper flags) and friendly-name formatting. Test count 53 → 141, coverage 26% → 34%.

---

## [2026.7.2b0] - 2026-07-12

> **Beta.** Published as a HACS pre-release for on-device testing before a
> stable `2026.7.2`. Not yet validated against real hardware.

### Fixed

- Changing the temperature while a thermostat is in Absence ("away") no longer
  kicks it out of Absence. In away mode the firmware keeps per-mode setpoints and
  applies a plain SETVALUE to the active mode, so `set_temperature` now writes
  only the `setpoint` and stays in Absence, exactly like the native By-Web UI
  "T Assenza" panel (verified by capturing its SOAP traffic). Previously the
  integration forced manual mode before writing, dropping the thermostat out of
  any non-manual preset. A post-write GETVALUE reads back what the firmware
  actually applied so Home Assistant converges to the device truth.

---

## [2026.7.1] - 2026-07-05

### Fixed

- Thermostat setpoint no longer freezes on the old value after a mode change. The VIMAR webserver DB does not track the physical thermostat unless a GETVALUE is issued on the status object (the native UI popup does this during its "device synchronization" phase — same firmware behavior already handled for energy meters). Switching to Absence/Reduction made the device regulate on its stored per-mode setpoint while Home Assistant kept showing the manual one. Every mode-changing command (preset, on/off, set temperature from another mode) now schedules a GETVALUE on `setpoint` + `funzionamento` right after the write-guard window and repolls, and a periodic GETVALUE (every 120 s) keeps thermostats in sync with changes made from the wall panel or the native VIMAR UI, which previously never reached Home Assistant at all.
- Type I thermostats in frost-protection mode no longer report an inconsistent preset. These devices have no absence mode, so the absence constant falls back to the protection value (`funzionamento=3`); the preset detection matched absence first and reported `away`, a preset not offered for Type I. Protection is now checked first, so the state correctly shows "Protection".

### Changed

- The "Eco" preset is now labeled with the official VIMAR term in every language ("Riduzione" in Italian, "Reduction" in English, …) so Home Assistant matches the mode names shown on the physical thermostat and in the By-me UI. The underlying preset key stays `eco`: automations referencing it keep working.

---

## [2026.7.0] - 2026-07-03

### Fixed

- Unexpected internal errors during a poll are no longer masked as a generic "Error communicating with API". The data coordinator's catch-all disguised real parsing/state bugs (`KeyError`, `TypeError`, …) as a network failure, sending troubleshooting down the wrong path; such non-network exceptions now log a full traceback (file + line) before the integration fails soft (entities go unavailable and it retries), so the true cause is diagnosable instead of hidden.

### Changed

- Internal: added type hints to the base entity (`get_state`, `has_state`, `change_state`) so unguarded `None` usage is caught statically. No behavior change.
- Development toolchain aligned to Home Assistant 2026.7.0 on Python 3.14 (minimum supported Python stays 3.13.2); repaired the cover test suite after the recent `dt_util`/background-task refactor. No runtime change for users.

---

## [2026.6.10] - 2026-06-28

### Fixed

- SAI2 alarm no longer reports a transient hiccup as "Wrong PIN". The PIN check classified *any* authenticate result other than `DPCM-0000` as a rejected PIN, but only `SAI2-3127` actually means the centrale refused the PIN. A stale session or a momentarily busy SAI2 sub-service returns other codes that were misread as a wrong PIN, producing bursts of bogus "Wrong PIN" errors that resolved themselves once the session renewed. The authenticate now self-heals: on any non-definitive code it drops the session, re-logs in and retries once; only a genuine `SAI2-3127` raises a wrong-PIN error, while a persistent transient surfaces a dedicated "alarm temporarily unreachable, retry" message (translated in all 7 languages) instead of blaming the PIN.

### Changed

- Internal modernization of the time-based cover (no behavior change): switched the time math from `datetime.now()` to Home Assistant's timezone/DST-safe `dt_util.utcnow()`; recovery and stop-tracking work is now scheduled with `async_create_background_task` and cancelled when the entity is removed, so reloading the integration mid-movement no longer leaves orphaned tasks raising runtime errors; added missing type hints.

---

## [2026.6.9] - 2026-06-16

### Added

- Time-based covers now recover their position when Home Assistant is restarted (or the integration reloaded) while a cover is moving. The pending STOP is otherwise lost, so the shutter overruns to a mechanical end-stop while HA restores a stale intermediate position. On restart the affected cover is driven to the end-stop **in the direction it was already travelling** — a guaranteed known reference, reached regardless of where it actually stopped — and then resumes to the position it was heading to. Only covers interrupted within the last 30 minutes are recovered (fresh-flag guard); a move already in progress, or an external command / physical button during recovery, takes precedence and skips the resume. This is a corrected rework of the recovery attempt reverted in 2026.6.6 (which forced a full close regardless of direction). Verified on hardware (01945).

---

## [2026.6.8] - 2026-06-15

### Fixed

- Time-based covers got stuck ~1% short of their end-stops (regression introduced in 2026.6.7): a full close settled at 1% and reported "open", and pressing close again just bounced back to 1% — the closed-end recalibration never happened. Two causes: the stop-overshoot margin was applied to the 0%/100% end-stops too (stopping the run a margin before the mechanical limit), and `_tb_stop_tracking` recalculated the position from elapsed time, overriding the snap to the target. The margin is now applied only to intermediate targets (the 0%/100% end-stops are reached by their dedicated branches), and a planned stop preserves the finalized position while manual/physical interruptions still recalculate. Full open/close now reach exactly 100%/0%, and intermediate moves land on the exact target instead of a margin short. Verified on hardware (01945).

---

## [2026.6.7] - 2026-06-15

### Fixed

- Time-based covers: definitive fix for the drift and the micro-movements (replaces the 2026.6.5 attempt reverted in 2026.6.6). The root causes were identified from Home Assistant history: (1) the position jumped to 0%/100% because the post-STOP grace period (`6s`) was shorter than the polling interval (`8s`), so the first poll after a stop fell *outside* the grace and mistook the latched `up/down` value for a physical button press; (2) the stop-overshoot compensation, although analytically correct, turned small position nudges into start-then-immediate-stop relay pulses. The grace period is now computed as `max(GRACE_SECONDS, poll_interval + margin)` so the first post-STOP poll always lands inside the grace and resyncs the latch, and the stop-overshoot compensation is paired with a deadband — both derived from the same relay-coast quantity (`RELAY_DELAY / travel`) — so any move that passes the deadband can never stop at its first tick. Partial moves now land on target without creeping open and without micro-movements. Validated on hardware (01945). The "recover position when restarted mid-movement" feature from 2026.6.5 is intentionally **not** reintroduced (end-stop open/close already recalibrate); a corrected version may follow.

### Note

- Includes the cover revert from 2026.6.6 and the 2026.6.5 thermostat `NO-OPTIONALS` fix.

---

## [2026.6.6] - 2026-06-15

### Reverted

- Reverted the two time-based cover changes shipped in 2026.6.5 (`fix(cover): compensate stop overshoot` and `feat(cover): recover position when restarted mid-movement`). On real hardware they caused covers to make repeated micro-movements: the stop-overshoot margin turned small position nudges (e.g. from automations) into start-then-immediate-stop relay pulses, and the mid-movement recovery triggered full close+reopen cycles on restart. `cover.py` is restored byte-for-byte to the known-good 2026.6.4 behaviour. The original drift issue (partial opens creeping up over repeated moves) remains open and will be re-addressed on a separate branch with on-hardware validation.

### Note

- This release keeps the 2026.6.5 thermostat fix (setpoint / `stagione` season written with `NO-OPTIONALS` instead of `SYNCDB`).

---

## [2026.6.5] - 2026-06-14

### Fixed

- Thermostat setpoint (and Type I `stagione` season) are now written with `NO-OPTIONALS` instead of `SYNCDB`. With `SYNCDB` the setpoint was written only to the webserver database and the By-me thermostat did **not** recompute its outputs, so Home Assistant showed the new setpoint while the device stayed latched on its previous state (e.g. "stuck cooling" with the measured temperature already on the idle side of the setpoint). `NO-OPTIONALS` matches the native VIMAR web UI and makes the firmware re-evaluate the thermostat and publish the recomputed output state within a few seconds (the DB value is still updated). Verified on hardware (01945) via an A/B test on a stuck thermostat. `regolazione` (Type II season) was already `NO-OPTIONALS`; `unita`/`temporizzazione` and the media_player states keep `SYNCDB`.
- Time-based covers no longer drift open over repeated partial moves. The relay/stop latency was compensated only at the start of a move, not at the stop, so each partial open ran the motor ~0.5s too long and the shutter physically overshot the target (Home Assistant showed e.g. 40% while the shutter sat higher). The STOP is now issued a matching margin before the target so the shutter coasts onto it. End stops (0%/100%) are unaffected.

### Added

- Time-based covers now recover when Home Assistant is restarted mid-movement. Previously a partial-position move whose STOP was lost to the restart left the shutter running to a mechanical end while HA restored a stale intermediate position. The in-flight movement is now persisted; on restart the affected cover does a full close to a known 0 reference and then reopens to the position it was heading to. Only the cover that was actually moving is touched; a hard crash (no clean shutdown) falls back to the previous behaviour and is fixed by the next full close.

---

## [2026.6.4] - 2026-06-12

### Internal

- `cover.py` refactor: `_tb_position=None` is now guarded centrally in `_tb_start_tracking`, so `open_cover`/`close_cover` issued before `async_added_to_hass` can no longer raise `TypeError` (previously only `set_cover_position` was protected). Merged the duplicated LEGACY/native branches in `is_closed` and `current_cover_position`, rewrote `is_default_state` in readable form, added a missing `super()` call in `async_will_remove_from_hass`, accepted string values in the `set_travel_times` service schema via `vol.Coerce(int)`, and added HA 2026.5-style type hints. No behavior change in time-based tracking.

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
