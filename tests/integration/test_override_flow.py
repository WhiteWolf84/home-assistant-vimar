"""The device override editor in the options flow (Home Assistant required).

Two steps, because a form cannot re-render itself with the values of a rule the
user has just picked: `overrides` chooses, `override_edit` changes, and the
edit step hands back to the picker so several rules can be managed in one pass.
The picker left empty is what moves the flow on.

What these tests guard is the list itself. Rules apply in order and a later one
overwrites what an earlier one set, so an edit that quietly reordered them, or
an invalid submission that dropped one, would change behaviour nowhere near
where the user was looking.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
import voluptuous as vol

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.config_flow import OptionsFlowHandler  # noqa: E402
from custom_components.vimar.const import CONF_OVERRIDE  # noqa: E402
from custom_components.vimar.override_editor import (  # noqa: E402
    FORM_DEVICE_TYPE,
    FORM_FILTER_FIELD,
    FORM_ICON_OFF,
    FORM_ICON_ON,
    FORM_MATCH_MODE,
    FORM_MATCH_VALUE,
    MATCH_ALL,
    MATCH_EXACT,
    MATCH_REGEX,
)

pytestmark = pytest.mark.integration  # Home Assistant required

RULES = [
    {"filter_vimar_name": "*", "object_name_as_vimar": True},
    {
        "filter_vimar_name": "VMC Rinnovo",
        "device_type": "switches",
        "icon": "mdi:hvac,mdi:hvac-off",
    },
    {"filter_vimar_name": "Garage", "device_type": "switches", "device_class": "garage"},
]


def _flow(rules=None):
    """An options flow sitting on an entry that holds `rules`."""
    flow = OptionsFlowHandler()
    flow.hass = MagicMock()
    flow.async_show_form = MagicMock(side_effect=lambda **kw: {"type": "form", **kw})
    # The step the picker hands off to when the user is done; stubbed so the
    # tests observe the hand-off without running the PIN step.
    flow.async_step_pins = AsyncMock(return_value={"type": "form", "step_id": "pins"})

    entry = MagicMock()
    entry.data = {"host": "192.168.0.13", CONF_OVERRIDE: [dict(r) for r in (rules or [])]}
    entry.options = {}
    flow._entry = entry
    return flow


def _config_entry_of(flow):
    return patch.object(
        OptionsFlowHandler, "config_entry", new_callable=PropertyMock, return_value=flow._entry
    )


def _stored(flow):
    return flow.options.get(CONF_OVERRIDE)


def _labels(result):
    """The picker's options, as the user sees them."""
    schema = result["data_schema"].schema
    selector = next(iter(schema.values()))
    return [option["label"] for option in selector.config["options"]]


def _prefilled(result):
    """What the form comes back showing, however the field carries it.

    voluptuous markers hold an unset default as the truthy UNDEFINED sentinel,
    so `if key.default` is not the same question as "has a default".
    """
    values = {}
    for key in result["data_schema"].schema:
        if key.default is not vol.UNDEFINED:
            values[key.schema] = key.default()
        elif isinstance(key.description, dict) and "suggested_value" in key.description:
            values[key.schema] = key.description["suggested_value"]
    return values


# ---------------------------------------------------------------------------
# The picker
# ---------------------------------------------------------------------------


async def test_the_picker_lists_every_rule_in_order():
    flow = _flow(RULES)

    with _config_entry_of(flow):
        result = await flow.async_step_overrides()

    assert _labels(result) == [
        "1. * -> vimar name",
        "2. VMC Rinnovo -> switches, icon",
        "3. Garage -> switches, garage",
        "+ …",
    ]


async def test_an_empty_choice_moves_the_flow_on():
    """There is no "done" button: leaving the picker empty is the way out."""
    flow = _flow(RULES)

    with _config_entry_of(flow):
        result = await flow.async_step_overrides({})

    assert result["step_id"] == "pins"
    assert flow.async_step_pins.await_count == 1


async def test_with_no_rules_the_picker_still_offers_to_add_one():
    flow = _flow([])

    with _config_entry_of(flow):
        result = await flow.async_step_overrides()

    assert _labels(result) == ["+ …"]


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


async def test_choosing_a_rule_opens_it_prefilled():
    flow = _flow(RULES)

    with _config_entry_of(flow):
        await flow.async_step_overrides({"rule": "1"})
        result = await flow.async_step_override_edit()

    values = _prefilled(result)
    assert values[FORM_FILTER_FIELD] == "vimar_name"
    assert values[FORM_DEVICE_TYPE] == "switches"
    assert values[FORM_MATCH_VALUE] == "VMC Rinnovo"
    assert values[FORM_ICON_ON] == "mdi:hvac"
    assert result["description_placeholders"]["position"] == "2"


async def test_saving_an_edited_rule_keeps_its_position():
    """Order is behaviour: a rule that moved would start losing to another."""
    flow = _flow(RULES)

    with _config_entry_of(flow):
        await flow.async_step_overrides({"rule": "1"})
        await flow.async_step_override_edit(
            {
                FORM_FILTER_FIELD: "vimar_name",
                FORM_MATCH_MODE: MATCH_EXACT,
                FORM_MATCH_VALUE: "VMC",
                FORM_DEVICE_TYPE: "switch",
            }
        )

    stored = _stored(flow)
    assert len(stored) == 3
    assert stored[1] == {"filter_vimar_name": "VMC", "device_type": "switch"}
    assert stored[0] == RULES[0] and stored[2] == RULES[2]


async def test_a_new_rule_is_appended():
    """Appended, not inserted: a new rule wins over the ones already there."""
    flow = _flow(RULES)

    with _config_entry_of(flow):
        await flow.async_step_overrides({"rule": "new"})
        await flow.async_step_override_edit(
            {
                FORM_FILTER_FIELD: "vimar_name",
                FORM_MATCH_MODE: MATCH_EXACT,
                FORM_MATCH_VALUE: "Deumidifica",
                FORM_DEVICE_TYPE: "switch",
                FORM_ICON_ON: "mdi:water-minus",
                FORM_ICON_OFF: "mdi:water-minus-outline",
            }
        )

    stored = _stored(flow)
    assert stored[:3] == RULES
    assert stored[3] == {
        "filter_vimar_name": "Deumidifica",
        "device_type": "switch",
        "icon": "mdi:water-minus,mdi:water-minus-outline",
    }


async def test_saving_returns_to_the_picker():
    flow = _flow(RULES)

    with _config_entry_of(flow):
        await flow.async_step_overrides({"rule": "0"})
        result = await flow.async_step_override_edit(
            {FORM_FILTER_FIELD: "vimar_name", FORM_MATCH_MODE: MATCH_ALL}
        )

    assert result["step_id"] == "overrides"


async def test_a_wildcard_rule_can_be_edited_without_losing_the_wildcard():
    """`*` is a mode in the form, not a value, so it must survive the trip."""
    flow = _flow(RULES)

    with _config_entry_of(flow):
        await flow.async_step_overrides({"rule": "0"})
        await flow.async_step_override_edit(
            {FORM_FILTER_FIELD: "vimar_name", FORM_MATCH_MODE: MATCH_ALL}
        )

    assert _stored(flow)[0]["filter_vimar_name"] == "*"


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


async def test_deleting_removes_only_that_rule():
    flow = _flow(RULES)

    with _config_entry_of(flow):
        await flow.async_step_overrides({"rule": "1"})
        result = await flow.async_step_override_edit({"delete": True})

    assert _stored(flow) == [RULES[0], RULES[2]]
    assert result["step_id"] == "overrides"


async def test_deleting_a_rule_being_added_changes_nothing():
    flow = _flow(RULES)

    with _config_entry_of(flow):
        await flow.async_step_overrides({"rule": "new"})
        await flow.async_step_override_edit({"delete": True})

    assert flow.options.get(CONF_OVERRIDE, RULES) == RULES


async def test_delete_wins_over_an_invalid_form():
    """Deleting a rule must not be blocked by the state it is being deleted in."""
    flow = _flow(RULES)

    with _config_entry_of(flow):
        await flow.async_step_overrides({"rule": "2"})
        await flow.async_step_override_edit(
            {"delete": True, FORM_MATCH_MODE: MATCH_REGEX, FORM_MATCH_VALUE: "^("}
        )

    assert _stored(flow) == [RULES[0], RULES[1]]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_an_invalid_regex_is_reported_and_nothing_is_saved():
    flow = _flow(RULES)

    with _config_entry_of(flow):
        await flow.async_step_overrides({"rule": "new"})
        result = await flow.async_step_override_edit(
            {
                FORM_FILTER_FIELD: "vimar_name",
                FORM_MATCH_MODE: MATCH_REGEX,
                FORM_MATCH_VALUE: "^Luce(",
            }
        )

    assert result["errors"] == {FORM_MATCH_VALUE: "regex_not_valid"}
    assert flow.options.get(CONF_OVERRIDE, RULES) == RULES


async def test_a_rejected_form_comes_back_with_what_was_typed():
    """Re-prefilling from the stored rule would throw away the user's edits."""
    flow = _flow(RULES)

    with _config_entry_of(flow):
        await flow.async_step_overrides({"rule": "1"})
        result = await flow.async_step_override_edit(
            {
                FORM_FILTER_FIELD: "vimar_name",
                FORM_MATCH_MODE: MATCH_REGEX,
                FORM_MATCH_VALUE: "^VMC(",
                FORM_DEVICE_TYPE: "light",
            }
        )

    values = _prefilled(result)
    assert values[FORM_MATCH_VALUE] == "^VMC("
    assert values[FORM_DEVICE_TYPE] == "light", "the typed platform, not the stored one"
