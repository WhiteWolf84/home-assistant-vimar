"""Translate a device_override rule between stored form and options form.

A stored rule is the same dict shape `configuration.yaml` has always used, and
VimarDeviceCustomizer is the one that understands it:

    {"filter_vimar_name": "Garage", "device_type": "switches",
     "device_class": "garage", "icon": "mdi:garage-open,mdi:garage"}

The options form cannot express all of it - the language also has regex
substitutions, filters on arbitrary fields and per-rule flags this form has no
control for - so the conversion back deliberately does NOT rebuild a rule from
scratch. It starts from the rule as stored and replaces only the keys the form
owns, which lets a rule written by hand in YAML keep the parts the form has
never heard of after being edited in the UI.

Nothing here builds the PARSED shape (`filter`, `filter_re`, `actions`): those
are VimarDeviceCustomizer's internals, it derives them itself, and it does so
by rewriting its input - which is why the coordinator hands it a deep copy.
"""

from __future__ import annotations

import re
from typing import Any

#: Device attributes a rule can be matched on, as the user's YAML spells them.
#: `get_attr_key` in the customizer maps these onto the device dict:
#: vimar_name -> object_name, friendly_name -> device_friendly_name, and
#: vimar_object_type / device_type through unchanged.
FILTER_FIELD_VIMAR_NAME = "vimar_name"
FILTER_FIELDS = [
    FILTER_FIELD_VIMAR_NAME,
    "vimar_object_type",
    "friendly_name",
    "device_type",
]

MATCH_EXACT = "exact"
MATCH_REGEX = "regex"
MATCH_ALL = "all"
MATCH_MODES = [MATCH_EXACT, MATCH_REGEX, MATCH_ALL]

#: Value the customizer reads as "every device" (see match_name).
MATCH_ALL_VALUE = "*"

#: Left empty in the form to mean "do not touch this attribute".
UNSET = ""

#: Keys the form owns and therefore replaces wholesale on save. Anything else
#: on a rule - regex substitutions, friendly_name_room_name_at_begin, a filter
#: on a field with no control here - is carried across untouched.
_FORM_OWNED_KEYS = frozenset(
    {
        "device_type",
        "device_class",
        "icon",
        "object_name_as_vimar",
        "friendly_name_as_vimar",
    }
)

FORM_FILTER_FIELD = "filter_field"
FORM_MATCH_MODE = "match_mode"
FORM_MATCH_VALUE = "match_value"
FORM_DEVICE_TYPE = "device_type"
FORM_DEVICE_CLASS = "device_class"
FORM_ICON_ON = "icon_on"
FORM_ICON_OFF = "icon_off"
FORM_USE_VIMAR_NAME = "use_vimar_name"


def filter_key_of(rule: dict[str, Any]) -> str | None:
    """Return the rule's `filter_*` key, or None if it filters on nothing."""
    for key in rule:
        if str(key).startswith("filter_"):
            return str(key)
    return None


def parse_filter_key(key: str) -> tuple[str, str]:
    """Split a `filter_*` key into the field it matches and how it matches it.

    Mirrors device_override_check(): the same field can be written four ways
    (`filter_x_re`, `filter_x_regex`, `filter_re_x`, `filter_regex_x`) and they
    all mean a regular expression. Reading all four and writing one keeps a
    hand-written rule editable here without changing what it does.
    """
    field = key[len("filter_") :]
    if field.endswith("_regex"):
        return field[: -len("_regex")], MATCH_REGEX
    if field.endswith("_re"):
        return field[: -len("_re")], MATCH_REGEX
    if field.startswith("regex_"):
        return field[len("regex_") :], MATCH_REGEX
    if field.startswith("re_"):
        return field[len("re_") :], MATCH_REGEX
    return field, MATCH_EXACT


def rule_to_form(rule: dict[str, Any] | None) -> dict[str, Any]:
    """Pre-fill the edit form from a stored rule (None for a new one)."""
    form: dict[str, Any] = {
        FORM_FILTER_FIELD: FILTER_FIELD_VIMAR_NAME,
        FORM_MATCH_MODE: MATCH_EXACT,
        FORM_MATCH_VALUE: UNSET,
        FORM_DEVICE_TYPE: UNSET,
        FORM_DEVICE_CLASS: UNSET,
        FORM_ICON_ON: UNSET,
        FORM_ICON_OFF: UNSET,
        FORM_USE_VIMAR_NAME: False,
    }
    if not rule:
        return form

    key = filter_key_of(rule)
    if key is not None:
        field, mode = parse_filter_key(key)
        value = str(rule.get(key) or "")
        # `*` is the customizer's "match everything", whatever field it is on.
        if value == MATCH_ALL_VALUE:
            mode = MATCH_ALL
            value = UNSET
        form[FORM_FILTER_FIELD] = field
        form[FORM_MATCH_MODE] = mode
        form[FORM_MATCH_VALUE] = value

    form[FORM_DEVICE_TYPE] = str(rule.get("device_type") or UNSET)
    form[FORM_DEVICE_CLASS] = str(rule.get("device_class") or UNSET)

    icon = str(rule.get("icon") or UNSET)
    if icon:
        on_icon, _, off_icon = icon.partition(",")
        form[FORM_ICON_ON] = on_icon.strip()
        form[FORM_ICON_OFF] = off_icon.strip()

    form[FORM_USE_VIMAR_NAME] = bool(
        rule.get("object_name_as_vimar") or rule.get("friendly_name_as_vimar")
    )
    return form


def form_to_rule(form: dict[str, Any], original: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the stored rule from submitted form values.

    Keys the form does not own are carried over from `original`, so editing a
    rule that came from YAML does not quietly drop the parts of the override
    language this form has no control for.
    """
    rule: dict[str, Any] = {
        key: value
        for key, value in (original or {}).items()
        # Every filter key goes: the form shows exactly one filter, so keeping
        # a second one would leave the rule matching on something invisible.
        if not str(key).startswith("filter_") and key not in _FORM_OWNED_KEYS
    }

    field = (form.get(FORM_FILTER_FIELD) or FILTER_FIELD_VIMAR_NAME).strip()
    mode = form.get(FORM_MATCH_MODE) or MATCH_EXACT
    value = (form.get(FORM_MATCH_VALUE) or "").strip()

    if mode == MATCH_ALL:
        rule[f"filter_{field}"] = MATCH_ALL_VALUE
    elif mode == MATCH_REGEX:
        rule[f"filter_{field}_re"] = value
    else:
        rule[f"filter_{field}"] = value

    if device_type := (form.get(FORM_DEVICE_TYPE) or "").strip():
        rule["device_type"] = device_type
    if device_class := (form.get(FORM_DEVICE_CLASS) or "").strip():
        rule["device_class"] = device_class

    on_icon = (form.get(FORM_ICON_ON) or "").strip()
    off_icon = (form.get(FORM_ICON_OFF) or "").strip()
    if on_icon and off_icon:
        # The customizer splits this back into the (on, off) pair; see
        # device_override_action_execute.
        rule["icon"] = f"{on_icon},{off_icon}"
    elif on_icon:
        rule["icon"] = on_icon

    if form.get(FORM_USE_VIMAR_NAME):
        rule["object_name_as_vimar"] = True

    return rule


def validate_form(form: dict[str, Any]) -> dict[str, str]:
    """Return {field: error_key} for anything the customizer would choke on."""
    errors: dict[str, str] = {}
    mode = form.get(FORM_MATCH_MODE) or MATCH_EXACT
    value = (form.get(FORM_MATCH_VALUE) or "").strip()

    if mode != MATCH_ALL and not value:
        errors[FORM_MATCH_VALUE] = "match_value_required"
    elif mode == MATCH_REGEX:
        try:
            re.compile(value)
        except re.error:
            # An invalid pattern is not caught at match time either: match_name
            # logs and moves on, so the rule would silently never fire.
            errors[FORM_MATCH_VALUE] = "regex_not_valid"

    off_icon = (form.get(FORM_ICON_OFF) or "").strip()
    if off_icon and not (form.get(FORM_ICON_ON) or "").strip():
        # A lone off icon would be stored as the single (on) icon and show up
        # in the wrong state - a silent inversion rather than an error.
        errors[FORM_ICON_ON] = "icon_on_required"

    return errors


def describe_rule(rule: dict[str, Any]) -> str:
    """One line naming a rule in the picker: what it matches, what it does."""
    key = filter_key_of(rule)
    if key is None:
        target = "?"
    else:
        field, mode = parse_filter_key(key)
        value = str(rule.get(key) or "")
        if value == MATCH_ALL_VALUE:
            target = "*"
        elif mode == MATCH_REGEX:
            target = f"/{value}/"
        else:
            target = value
        if field != FILTER_FIELD_VIMAR_NAME:
            target = f"{field}={target}"

    effects = []
    if device_type := rule.get("device_type"):
        effects.append(str(device_type))
    if device_class := rule.get("device_class"):
        effects.append(str(device_class))
    if rule.get("icon"):
        effects.append("icon")
    if rule.get("object_name_as_vimar") or rule.get("friendly_name_as_vimar"):
        effects.append("vimar name")
    # Anything the form has no control for still counts as an effect, so a rule
    # is never described as doing nothing when it does something.
    if not effects and len(rule) > (1 if key else 0):
        effects.append("advanced")

    return f"{target} -> {', '.join(effects) or 'no change'}"
