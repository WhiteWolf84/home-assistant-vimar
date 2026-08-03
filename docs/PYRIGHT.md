# Type checking status

`pyrightconfig.json` runs in `basic` mode with most diagnostic rules switched
off. They are being re-enabled one at a time. JSON has no comments, so the
reasoning lives here.

## How the order was chosen

Not alphabetically, and not by rule name: by **how many real defects each rule
actually found in this codebase**. A run with all 15 disabled rules turned on
produced 241 errors, and the distribution matters far more than the total.

| Rule | Diagnostics | Status |
| --- | ---: | --- |
| `reportIncompatibleVariableOverride` | 136 | Off, deliberately — see below |
| `reportArgumentType` | 30 | Not triaged |
| `reportOptionalMemberAccess` | 14 | **Enabled** |
| `reportOperatorIssue` | 14 | Not triaged |
| `reportOptionalSubscript` | 14 | **Enabled** |
| `reportIncompatibleMethodOverride` | 9 | **Enabled** |
| `reportOptionalOperand` | 8 | **Enabled** |
| `reportPrivateImportUsage` | 5 | Not triaged |
| `reportAttributeAccessIssue` | 5 | Not triaged |
| `reportGeneralTypeIssues` | 2 | Not triaged |
| `reportAssignmentType` | 2 | Not triaged |
| `reportCallIssue` | 1 | Not triaged |
| `reportOptionalIterable` | 1 | **Enabled** |

Reproduce the measurement by copying `pyrightconfig.json`, setting every
`"none"` to `"error"`, and running `pyright -p <copy>`.

## Enabled

### `reportIncompatibleMethodOverride`

The highest defect density per diagnostic. It found three properties that
overrode ones Home Assistant declares `@final`, each silently replacing an
entire HA pipeline with a raw value:

- `SensorEntity.state` and `SensorEntity.unit_of_measurement` (`sensor.py`) —
  where HA performs unit conversion, the user's per-entity unit override,
  display precision and the string-to-number conversion.
- `BaseScene.state` (`scene.py`) — where HA records and restores the last
  activation timestamp, which the class was reimplementing against a second,
  parallel timestamp that could disagree with HA's.

`@final` is HA's way of saying "feed this through the `native_*` hooks
instead". Nothing but a type checker catches the violation: at runtime the
override simply wins.

### The `reportOptional*` family

37 diagnostics, and the family that had already produced two live crashes
before it was turned on (the percent-formatting one in
`vimarlink/exceptions.py`, and an unguarded attribute access before that). Two
more were sitting in the code when it was enabled:

- `media_player.async_mute_volume` stored `volume_level`, which is `None` when
  the device has no `volume` status. Unmuting then raised
  `TypeError: unsupported operand type(s) for *: 'NoneType' and 'int'` and the
  player stayed silent, with only a traceback to explain why.
- `cover._tb_update_position` compared `_tb_position` against a number with no
  guarantee that tracking had set it, inside a timer callback where an
  exception kills the timer.

Clearing the family was not a config edit. `VimarEntity` declared `_device`,
`_vimarconnection`, `_vimarproject` and `_coordinator` as `Optional`, and about
half the readers checked for `None` while the other half did not — the
unchecked half being right in practice, since entities are only built from
devices that are in the project. That ambiguity is what stopped a checker (or a
reader) telling a real gap from a redundant guard. The contract is now stated:

- the coordinator builds its `VimarLink` and `VimarProject` in `__init__`
  rather than only in `init_vimarproject()`; neither touches the network, so
  there is nothing to defer;
- an entity whose device cannot be found carries `MISSING_DEVICE`, an inert
  placeholder compared by identity, so every guard that read
  `if self._device is None` fires in exactly the same circumstances.


## Off deliberately

### `reportIncompatibleVariableOverride`

136 diagnostics, essentially all noise. Home Assistant declares `device_info`,
`unique_id`, `extra_state_attributes` and friends as `cached_property`, and
overriding them with a plain `@property` is the normal pattern across
integrations. Enabling this rule would mean 136 suppressions for no defect
found.

## Still to triage

`reportArgumentType` (30), `reportOperatorIssue` (14),
`reportAttributeAccessIssue` (5), `reportPrivateImportUsage` (5),
`reportGeneralTypeIssues` (2), `reportAssignmentType` (2), `reportCallIssue`
(1). Same approach: measure what each one actually finds before deciding
whether the fix is worth the churn.

## Not covered at all

Both CI lint jobs are scoped to `./custom_components/vimar`, so the `tests/`
tree is checked by neither `ruff` nor `black`. `black` and `ruff format` also
disagree there — on how to lay out an `assert` with a message — so widening the
scope means first deciding which of the two is authoritative.
