"""Device overrides move from YAML into the config entry (Home Assistant required).

`device_override` was the last setting reachable only from configuration.yaml,
and not for want of a form: async_setup_entry re-read it from
`hass.data[DOMAIN_CONFIG_YAML]` on every start and overwrote whatever the entry
held, so YAML was not the default source - it was the only one that could ever
win. Storing an override in the entry would have been silently pointless.

The entry owns them now. These tests pin the migration that gets them there
without the user retyping anything, and - the part that is easy to get wrong -
that it happens exactly once. The tempting condition, "import from YAML when
the entry has no overrides", re-imports the YAML the first time someone deletes
their last rule in the UI, handing back a rule they removed on purpose.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar import _async_migrate_yaml_overrides  # noqa: E402
from custom_components.vimar.config_flow import VimarFlowHandler  # noqa: E402
from custom_components.vimar.const import (  # noqa: E402
    CONF_OVERRIDE,
    CONF_OVERRIDE_IMPORTED,
    DOMAIN_CONFIG_YAML,
)
from custom_components.vimar.vimar_coordinator import (  # noqa: E402
    VimarDataUpdateCoordinator,
)
from custom_components.vimar.vimar_device_customizer import (  # noqa: E402
    VimarDeviceCustomizer,
)

pytestmark = pytest.mark.integration  # Home Assistant required

# The shape a real installation uses, from the integration's own README.
YAML_RULES = [
    {"filter_vimar_name": "*", "object_name_as_vimar": True},
    {
        "filter_vimar_name": "VMC Rinnovo",
        "device_type": "switches",
        "icon": "mdi:hvac,mdi:hvac-off",
    },
    {
        "filter_vimar_name": "Garage",
        "device_type": "switches",
        "device_class": "garage",
        "icon": "mdi:garage-open,mdi:garage",
    },
]


def _hass(yaml_conf=None):
    hass = MagicMock()
    hass.data = {DOMAIN_CONFIG_YAML: yaml_conf} if yaml_conf is not None else {}
    return hass


def _entry(data=None):
    entry = MagicMock()
    entry.data = data if data is not None else {"host": "192.168.0.13"}
    return entry


def _written(hass):
    """The data dict the migration persisted, or None if it wrote nothing."""
    calls = hass.config_entries.async_update_entry.call_args_list
    return calls[-1].kwargs["data"] if calls else None


def test_yaml_rules_are_moved_into_the_entry():
    hass = _hass({CONF_OVERRIDE: YAML_RULES})
    entry = _entry()

    _async_migrate_yaml_overrides(hass, entry)

    written = _written(hass)
    assert written[CONF_OVERRIDE] == YAML_RULES
    assert written[CONF_OVERRIDE_IMPORTED] is True
    assert written["host"] == "192.168.0.13", "the rest of the entry must survive"


def test_the_rules_are_copied_not_shared():
    """VimarDeviceCustomizer rewrites the dicts it parses, in place."""
    hass = _hass({CONF_OVERRIDE: YAML_RULES})

    _async_migrate_yaml_overrides(hass, _entry())

    migrated = _written(hass)[CONF_OVERRIDE]
    migrated[1]["device_type"] = "mutato"

    assert YAML_RULES[1]["device_type"] == "switches"


def test_it_runs_only_once():
    """The flag records that the window closed, whatever the entry holds now."""
    hass = _hass({CONF_OVERRIDE: YAML_RULES})
    entry = _entry({"host": "192.168.0.13", CONF_OVERRIDE_IMPORTED: True})

    _async_migrate_yaml_overrides(hass, entry)

    assert hass.config_entries.async_update_entry.call_count == 0


def test_deleting_the_last_rule_does_not_resurrect_the_yaml():
    """THE reason the flag exists rather than an "is it empty?" check.

    The user has migrated, then removed every rule in the options. YAML is
    still on disk - it usually is, nobody tidies it up the same day - and must
    not be read back.
    """
    hass = _hass({CONF_OVERRIDE: YAML_RULES})
    entry = _entry({"host": "x", CONF_OVERRIDE: [], CONF_OVERRIDE_IMPORTED: True})

    _async_migrate_yaml_overrides(hass, entry)

    assert hass.config_entries.async_update_entry.call_count == 0


def test_rules_already_in_the_entry_are_not_overwritten():
    """Belt and braces for an entry carrying rules but no flag."""
    own = [{"filter_vimar_name": "Deumidifica", "device_type": "switches"}]
    hass = _hass({CONF_OVERRIDE: YAML_RULES})

    _async_migrate_yaml_overrides(hass, _entry({"host": "x", CONF_OVERRIDE: own}))

    written = _written(hass)
    assert written[CONF_OVERRIDE] == own
    assert written[CONF_OVERRIDE_IMPORTED] is True


@pytest.mark.parametrize(
    "yaml_conf",
    [None, {}, {CONF_OVERRIDE: None}, {CONF_OVERRIDE: []}],
    ids=["no yaml at all", "yaml without the key", "key set to null", "empty list"],
)
def test_the_flag_is_written_even_with_nothing_to_migrate(yaml_conf):
    """Otherwise a `device_override:` block added to YAML later would be
    adopted silently, behind the back of the UI that now owns the setting."""
    hass = _hass(yaml_conf)
    entry = _entry()

    _async_migrate_yaml_overrides(hass, entry)

    written = _written(hass)
    assert written[CONF_OVERRIDE_IMPORTED] is True
    assert CONF_OVERRIDE not in written


def test_a_null_override_key_does_not_crash():
    """dict.get() returns None both for a missing key and for `key:` with no
    value; VimarDeviceCustomizer used to be handed the None and crash."""
    hass = _hass({CONF_OVERRIDE: None})

    _async_migrate_yaml_overrides(hass, _entry())

    assert _written(hass)[CONF_OVERRIDE_IMPORTED] is True


# ---------------------------------------------------------------------------
# The YAML import flow, which is the migration for a never-configured system
# ---------------------------------------------------------------------------


async def test_the_import_flow_keeps_the_overrides_it_used_to_drop():
    """It popped them, commented "non gestito da config_flow" - which was true
    and self-fulfilling: the entry could not hold overrides, so YAML had to."""
    flow = VimarFlowHandler()
    flow.hass = MagicMock()
    flow._async_current_entries = MagicMock(return_value=[])
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    await flow.async_step_import(
        {
            "title": "Casa",
            "host": "192.168.0.13",
            "username": "admin",
            "password": "secret",
            CONF_OVERRIDE: YAML_RULES,
        }
    )

    data = flow.async_create_entry.call_args.kwargs["data"]
    assert data[CONF_OVERRIDE] == YAML_RULES
    assert data[CONF_OVERRIDE_IMPORTED] is True, "importing IS the migration"


# ---------------------------------------------------------------------------
# The parser rewrites what it is given, so it must not be given the config
# ---------------------------------------------------------------------------


def _coordinator_with(rules):
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)
    coordinator.vimarconfig = {
        CONF_HOST: "192.168.0.13",
        CONF_PORT: 443,
        CONF_OVERRIDE: rules,
    }
    return coordinator


def test_building_the_customizer_does_not_rewrite_the_config():
    """device_override_check() turns `filter_*` keys into `filter`/`actions`
    entries IN PLACE. Those dicts are now entry.data, not a throwaway."""
    rules = [dict(rule) for rule in YAML_RULES]
    coordinator = _coordinator_with(rules)

    coordinator._build_vimar_objects()

    assert rules == YAML_RULES, "the config must come back exactly as it went in"


def _action_counts_per_build(coordinator, builds):
    """Actions parsed out of each rule, once per _build_vimar_objects() call.

    The customizer is a local of that method, so it is captured on the way
    through rather than reached for afterwards.

    Counted after each build and never at the end: without the copy both
    customizers hold the SAME dicts, so a tally taken once at the end reads the
    final, already-doubled numbers off each of them and compares equal - which
    is exactly the bug slipping through the test written to catch it.
    """
    created: list[VimarDeviceCustomizer] = []
    counts: list[list[int]] = []

    def _spy(vimarconfig, device_overrides):
        customizer = VimarDeviceCustomizer(vimarconfig, device_overrides)
        created.append(customizer)
        return customizer

    with patch("custom_components.vimar.vimar_coordinator.VimarDeviceCustomizer", side_effect=_spy):
        for _ in range(builds):
            coordinator._build_vimar_objects()
            counts.append([len(rule["actions"]) for rule in created[-1]._device_overrides])

    return counts


def test_reloading_does_not_duplicate_the_parsed_actions():
    """`actions` is only initialised when absent, so parsing an already-parsed
    rule appended a second copy of every action instead of starting over.

    A reload rebuilds the objects from the same config, so without the copy the
    second pass saw rules the first pass had already rewritten.
    """
    coordinator = _coordinator_with([dict(rule) for rule in YAML_RULES])

    first, second = _action_counts_per_build(coordinator, builds=2)

    assert first == second
