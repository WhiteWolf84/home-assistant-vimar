# Type checking status

`pyrightconfig.json` runs in `basic` mode. Of the 15 diagnostic rules that were
switched off wholesale, **12 are now enforced** and 3 remain off on purpose.
JSON has no comments, so the reasoning lives here.

## How the order was chosen

Not alphabetically, and not by rule name: by **how many real defects each rule
actually found in this codebase**. A run with all 15 turned on produced 241
errors, and the distribution mattered far more than the total.

| Rule | Diagnostics | Status |
| --- | ---: | --- |
| `reportIncompatibleVariableOverride` | 136 | Off — see below |
| `reportArgumentType` | 19 | Off — see below |
| `reportOptionalMemberAccess` | 14 | **Enabled** |
| `reportOptionalSubscript` | 14 | **Enabled** |
| `reportIncompatibleMethodOverride` | 9 | **Enabled** |
| `reportOptionalOperand` | 8 | **Enabled** |
| `reportPrivateImportUsage` | 5 | Off — see below |
| `reportOperatorIssue` | 5 | **Enabled** |
| `reportAttributeAccessIssue` | 5 | **Enabled** |
| `reportGeneralTypeIssues` | 2 | **Enabled** |
| `reportAssignmentType` | 2 | **Enabled** |
| `reportCallIssue` | 1 | **Enabled** |
| `reportOptionalIterable` | 1 | **Enabled** |

Reproduce the measurement by copying `pyrightconfig.json`, setting every
`"none"` to `"error"`, and running `pyright -p <copy>`.

## Enabled

### `reportIncompatibleMethodOverride`

The highest defect density per diagnostic. It found three properties overriding
ones Home Assistant declares `@final`, each silently replacing an entire HA
pipeline with a raw value:

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
  exception kills the timer for good.

Clearing the family was not a config edit. `VimarEntity` declared `_device`,
`_vimarconnection`, `_vimarproject` and `_coordinator` as `Optional`, and about
half the readers checked for `None` while the other half did not — the
unchecked half being right in practice, since entities are only built from
devices that are in the project. That ambiguity is what stopped a checker (or a
reader) telling a real gap from a redundant guard. The contract is now stated:

- the coordinator builds its `VimarLink` and `VimarProject` in `__init__`
  rather than only in `init_vimarproject()`; neither touches the network, so
  there was nothing to defer;
- an entity whose device cannot be found carries `MISSING_DEVICE`, an inert
  placeholder compared by identity, so every guard that read
  `if self._device is None` fires in exactly the same circumstances.

The rule then caught the very next change made to the codebase:
`_measurement_name` carried a class-level `None` default while `__init__`
always assigns it — the same anti-pattern, minutes after being switched on.

### `reportCallIssue`, `reportAssignmentType`, `reportGeneralTypeIssues`, `reportAttributeAccessIssue`, `reportOperatorIssue`

15 diagnostics between them, cleared in one pass. One was a live defect:

```python
filters = device_override.get(DEVICE_OVERRIDE_FILTER)
if isinstance(filters, str) and filters == "*":
    ...
elif filters is not None:                 # any OTHER string lands here
    for key, value in filters.items():    # AttributeError
```

A device override whose filter was written as a plain string rather than `"*"`
raised instead of simply not matching.

The rest were the declaration lagging behind reality: `VimarDevice` was missing
`room_friendly_name` (added at runtime by the customizer), `_request` declared
it could return `True` when it never does, and `check_ssl` was declared `bool`
while requests also accepts a CA-bundle path.

`device_info` keeps its three-element identifier, which Home Assistant's own
type says should have two. The middle element is the config-entry prefix, and
it is what keeps two web servers exporting the same device id apart. Changing
the shape would change every device's identity — HA would register new devices
and users would lose the areas and names attached to the old ones. That is a
migration, not a type fix, so the intent is recorded with a `cast`.

## Off, with a reason

### `reportIncompatibleVariableOverride` (136)

Essentially all noise. Home Assistant declares `device_info`, `unique_id`,
`extra_state_attributes` and friends as `cached_property`, and overriding them
with a plain `@property` is the normal pattern across integrations. Enabling
this would mean 136 suppressions for no defect found.

### `reportArgumentType` (19)

Twelve are the same shape:

```python
if self.has_state("volume"):
    return float(self.get_state("volume")) / 100
```

`get_state` returns `str | None` and only returns None when `has_state` is
False — so the call is guarded, and the checker cannot see it. Satisfying the
rule would mean writing `float(self.get_state("volume") or 0)` at every such
site, turning a loud failure into a silent 0: a real signal traded for a clean
report.

The honest fix is to give `get_state` a narrower contract, which is a change to
the base entity worth doing on its own.

### `reportPrivateImportUsage` (5)

`AlarmControlPanelEntityFeature`, `ColorMode`, `MediaPlayerEntityFeature` and
friends are the documented public API of their Home Assistant components; they
are simply missing from those modules' `__all__`. Five suppressions for a
Home Assistant packaging detail.

## Formatting and CI scope

`ruff` is the single linter **and** formatter, and both run over
`custom_components/vimar` and `tests/`.

`black` was dropped rather than kept alongside it. The two agree on every file
under `custom_components/vimar` and disagree on how to lay out an `assert` with
a message — which only comes up in tests, and is exactly why `tests/` had ended
up covered by neither: running either tool over it produced churn the other
would undo.

The `pyright` job was also installing `homeassistant==2026.1.3` on Python 3.13,
six months behind the version the integration supports and one the manifest
does not claim to run on at all. Rules such as
`reportIncompatibleMethodOverride` read `@final` off the installed Home
Assistant, so that job had been checking a different API from the one the code
runs against — the `@final` violations above would not necessarily have been
caught by it. It now uses Python 3.14 and the pinned `2026.7.0`, matching
`requirements_dev` and the integration tests.
