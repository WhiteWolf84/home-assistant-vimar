"""Rules survive a trip through the options form (NO Home Assistant required).

The stored shape is the one `configuration.yaml` has always used and the one
VimarDeviceCustomizer parses. The form is a lossy view of it: the override
language also has regex substitutions, filters on arbitrary fields and flags
with no control on the form.

So the property that matters is not "the form can express every rule" - it
cannot - but that editing a rule through it never silently changes something
the user did not touch. These tests pin the round trip, and pin that the parts
the form does not own come out the other side intact.
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from override_editor import (  # noqa: E402
    FORM_DEVICE_CLASS,
    FORM_DEVICE_TYPE,
    FORM_FILTER_FIELD,
    FORM_ICON_OFF,
    FORM_ICON_ON,
    FORM_MATCH_MODE,
    FORM_MATCH_VALUE,
    FORM_USE_VIMAR_NAME,
    MATCH_ALL,
    MATCH_EXACT,
    MATCH_REGEX,
    describe_rule,
    form_to_rule,
    parse_filter_key,
    rule_to_form,
    validate_form,
)

pytestmark = pytest.mark.no_ha  # No HA required

#: The rules a real installation has in configuration.yaml today.
REAL_RULES = [
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
    {
        "filter_vimar_name": "Collettore Primo Piano",
        "device_type": "switches",
        "icon": "mdi:heat-pump,mdi:heat-pump-outline",
    },
]


@pytest.mark.parametrize("rule", REAL_RULES, ids=lambda r: r["filter_vimar_name"])
def test_a_real_rule_survives_the_round_trip(rule):
    """Opening a rule and saving it unchanged must not rewrite it.

    `device_type: switches` stays plural on purpose: device_type_singolarize
    accepts both, and rewriting it would be an edit the user did not ask for.
    """
    assert form_to_rule(rule_to_form(rule), rule) == rule


def test_a_wildcard_rule_reads_as_match_all():
    form = rule_to_form({"filter_vimar_name": "*", "object_name_as_vimar": True})

    assert form[FORM_MATCH_MODE] == MATCH_ALL
    assert form[FORM_MATCH_VALUE] == ""
    assert form[FORM_USE_VIMAR_NAME] is True


def test_match_all_is_written_back_as_the_wildcard():
    rule = form_to_rule({FORM_FILTER_FIELD: "vimar_name", FORM_MATCH_MODE: MATCH_ALL})

    assert rule == {"filter_vimar_name": "*"}


def test_the_icon_pair_splits_and_rejoins():
    form = rule_to_form({"filter_vimar_name": "X", "icon": "mdi:garage-open,mdi:garage"})

    assert form[FORM_ICON_ON] == "mdi:garage-open"
    assert form[FORM_ICON_OFF] == "mdi:garage"
    assert form_to_rule(form)["icon"] == "mdi:garage-open,mdi:garage"


def test_a_single_icon_stays_single():
    """Not every device has a two-state icon; a lone one must not gain a comma."""
    rule = form_to_rule(
        {FORM_FILTER_FIELD: "vimar_name", FORM_MATCH_VALUE: "X", FORM_ICON_ON: "mdi:hvac"}
    )

    assert rule["icon"] == "mdi:hvac"


@pytest.mark.parametrize(
    "key",
    ["filter_vimar_name_re", "filter_vimar_name_regex", "filter_re_vimar_name"],
)
def test_every_spelling_of_a_regex_filter_is_understood(key):
    """device_override_check accepts four spellings. Reading all of them and
    writing one keeps a hand-written rule editable without changing it."""
    field, mode = parse_filter_key(key)

    assert (field, mode) == ("vimar_name", MATCH_REGEX)


def test_a_regex_rule_is_written_in_the_canonical_spelling():
    form = rule_to_form({"filter_re_vimar_name": "^Luce", "device_type": "light"})

    assert form[FORM_MATCH_MODE] == MATCH_REGEX
    assert form[FORM_MATCH_VALUE] == "^Luce"
    assert form_to_rule(form) == {"filter_vimar_name_re": "^Luce", "device_type": "light"}


def test_parts_the_form_does_not_own_are_carried_across():
    """THE thing that must not break: the form is a lossy view of the rule."""
    original = {
        "filter_vimar_name": "Tapparella",
        "device_type": "cover",
        # No control on the form for either of these.
        "friendly_name_regexsub_pattern": "^TAPP",
        "friendly_name_regexsub_repl": "Tapparella",
        "friendly_name_room_name_at_begin": True,
    }

    edited = form_to_rule({**rule_to_form(original), FORM_DEVICE_CLASS: "shutter"}, original)

    assert edited["friendly_name_regexsub_pattern"] == "^TAPP"
    assert edited["friendly_name_regexsub_repl"] == "Tapparella"
    assert edited["friendly_name_room_name_at_begin"] is True
    assert edited["device_class"] == "shutter"


def test_only_one_filter_survives_an_edit():
    """The form shows a single filter, so leaving a second would make the rule
    match on something the user cannot see."""
    original = {"filter_vimar_name": "X", "filter_vimar_object_type": "CH_Scene"}

    edited = form_to_rule(
        {FORM_FILTER_FIELD: "vimar_name", FORM_MATCH_VALUE: "Y", FORM_MATCH_MODE: MATCH_EXACT},
        original,
    )

    assert [key for key in edited if key.startswith("filter_")] == ["filter_vimar_name"]
    assert edited["filter_vimar_name"] == "Y"


def test_clearing_a_field_clears_the_attribute():
    """An emptied control means "stop forcing this", not "keep what was there"."""
    original = {"filter_vimar_name": "X", "device_type": "switch", "device_class": "outlet"}

    edited = form_to_rule({**rule_to_form(original), FORM_DEVICE_CLASS: ""}, original)

    assert "device_class" not in edited
    assert edited["device_type"] == "switch"


def test_a_filter_on_an_unknown_field_is_preserved():
    """Rules can filter on fields with no entry in the form's list."""
    original = {"filter_vimar_object_id": "721", "device_type": "light"}

    form = rule_to_form(original)

    assert form[FORM_FILTER_FIELD] == "vimar_object_id"
    assert form_to_rule(form, original) == original


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_an_empty_match_value_is_refused():
    errors = validate_form({FORM_MATCH_MODE: MATCH_EXACT, FORM_MATCH_VALUE: "  "})

    assert errors == {FORM_MATCH_VALUE: "match_value_required"}


def test_match_all_needs_no_value():
    assert validate_form({FORM_MATCH_MODE: MATCH_ALL, FORM_MATCH_VALUE: ""}) == {}


def test_an_uncompilable_regex_is_refused():
    """match_name only logs and moves on, so the rule would never fire."""
    errors = validate_form({FORM_MATCH_MODE: MATCH_REGEX, FORM_MATCH_VALUE: "^Luce("})

    assert errors == {FORM_MATCH_VALUE: "regex_not_valid"}


def test_an_off_icon_without_an_on_icon_is_refused():
    """It would be stored as the single (on) icon: a silent inversion."""
    errors = validate_form(
        {FORM_MATCH_MODE: MATCH_EXACT, FORM_MATCH_VALUE: "X", FORM_ICON_OFF: "mdi:hvac-off"}
    )

    assert errors == {FORM_ICON_ON: "icon_on_required"}


def test_a_valid_form_reports_nothing():
    assert (
        validate_form(
            {
                FORM_MATCH_MODE: MATCH_REGEX,
                FORM_MATCH_VALUE: "^Luce",
                FORM_ICON_ON: "mdi:a",
                FORM_ICON_OFF: "mdi:b",
                FORM_DEVICE_TYPE: "light",
            }
        )
        == {}
    )


# ---------------------------------------------------------------------------
# The picker labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ({"filter_vimar_name": "*", "object_name_as_vimar": True}, "* -> vimar name"),
        (
            {"filter_vimar_name": "Garage", "device_type": "switches", "device_class": "garage"},
            "Garage -> switches, garage",
        ),
        ({"filter_vimar_name_re": "^Luce", "device_type": "light"}, "/^Luce/ -> light"),
        (
            {"filter_vimar_object_type": "CH_Scene", "icon": "mdi:a"},
            "vimar_object_type=CH_Scene -> icon",
        ),
    ],
)
def test_a_rule_describes_itself_for_the_picker(rule, expected):
    assert describe_rule(rule) == expected


def test_a_rule_the_form_cannot_show_is_still_described_as_doing_something():
    """Otherwise an advanced rule reads as "no change" and invites deletion."""
    described = describe_rule({"filter_vimar_name": "X", "friendly_name_regexsub_pattern": "^TAPP"})

    assert described == "X -> advanced"
