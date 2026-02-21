# Changelog

## 2026.02.21.001

- perf(coordinator): discovery once, then slim polling by status_id; topology-change rediscovery.
- perf(vimarlink): add `get_status_only()` and treat `Unknown-Payload` as transient (no relogin storm).
- perf(entity): skip `async_write_ha_state()` when the device state hash did not change.

### Notes
- `Unknown-Payload` is a known transient failure mode of the Vimar embedded webserver under load.
- Slim polling reduces CPU/SQLite pressure on 01945/01946 drastically because it avoids heavy JOINs.
