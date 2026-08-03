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
| `reportOptionalMemberAccess` | 14 | Off — needs a behaviour change |
| `reportOperatorIssue` | 14 | Not triaged |
| `reportOptionalSubscript` | 14 | Off — needs a behaviour change |
| `reportIncompatibleMethodOverride` | 9 | **Enabled** |
| `reportOptionalOperand` | 8 | Off — needs a behaviour change |
| `reportPrivateImportUsage` | 5 | Not triaged |
| `reportAttributeAccessIssue` | 5 | Not triaged |
| `reportGeneralTypeIssues` | 2 | Not triaged |
| `reportAssignmentType` | 2 | Not triaged |
| `reportCallIssue` | 1 | Not triaged |
| `reportOptionalIterable` | 1 | Off — needs a behaviour change |

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

## Off deliberately

### `reportIncompatibleVariableOverride`

136 diagnostics, essentially all noise. Home Assistant declares `device_info`,
`unique_id`, `extra_state_attributes` and friends as `cached_property`, and
overriding them with a plain `@property` is the normal pattern across
integrations. Enabling this rule would mean 136 suppressions for no defect
found.

## Off, pending a real change

### The `reportOptional*` family (37 diagnostics)

These are genuine null-safety gaps, and the same family as the crash fixed in
`vimarlink/exceptions.py`. Clearing them is not a config edit: `VimarEntity`
declares `_device`, `_vimarconnection` and `_vimarproject` as `Optional`, and
roughly half the call sites guard for `None` while the other half assume it is
set. The half without guards are right in practice — an entity is only ever
constructed after the coordinator has initialised — but making that hold for
the type checker means changing what happens when a device *is* missing at
construction time, which today produces an entity that raises `AttributeError`
on first use. That is a runtime behaviour change and deserves its own commit,
with its own tests.
