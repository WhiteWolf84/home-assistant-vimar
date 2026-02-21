# Optimization A – Slim polling

This document describes the post-discovery **slim polling** path introduced on the `optimization-a-simple` branch.

## Goal

Keep the initial discovery query (heavy) but run it only once. Subsequent periodic updates should be as cheap as possible for the embedded Vimar webserver.

## How it works

1. **Discovery (heavy)**
- Runs on first coordinator update (and again if topology changes).
- Builds an index of all `status_id` values that belong to known devices.

2. **Polling (slim)**
- Runs on every periodic update after discovery.
- Executes a single-table query by primary key `ID IN (...)` to fetch only `CURRENT_VALUE`.
- Updates existing devices in memory by patching only `status_value`.

## Why it helps

The Vimar webserver is an embedded target. Heavy queries with multiple JOINs and wide resultsets can starve it, causing timeouts and `Unknown-Payload` responses.

The slim poll is designed to:
- minimize SQL planner work,
- avoid joins,
- reduce payload size,
- keep HA responsive even with small scan intervals.

## Error handling

If the webserver returns `Unknown-Payload`, the integration treats it as **transient**:
- returns `None` from SQL request
- coordinator keeps previous state for that cycle
- next update retries normally

This prevents a relogin storm during temporary overload.
