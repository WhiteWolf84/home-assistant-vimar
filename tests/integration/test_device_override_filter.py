"""A device override filter never raises on the config it is given (HA required).

`device_override_match` accepts a filter that is either the string `"*"`,
meaning "match everything", or a mapping of conditions. It read:

    if isinstance(filters, str) and filters == "*":
        ...
    elif filters is not None:
        for key, value in filters.items():

so every string that was NOT `"*"` fell into the second branch and had
`.items()` called on it. A filter written as plain text - a typo, or someone
expecting `"CH_Main_Automation"` to work as shorthand - raised AttributeError
instead of simply not matching, and took the override with it.

Found by `reportAttributeAccessIssue`, not by anyone hitting it.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.vimar_device_customizer import (  # noqa: E402
    DEVICE_OVERRIDE_FILTER,
    DEVICE_OVERRIDE_FILTER_RE,
    VimarDeviceCustomizer,
)

pytestmark = pytest.mark.integration  # Home Assistant required


def _customizer():
    return VimarDeviceCustomizer({}, [])


def _device():
    return {
        "object_id": "768",
        "object_type": "CH_Main_Automation",
        "object_name": "Lampada cucina",
        "device_type": "light",
        "status": {},
    }


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", [DEVICE_OVERRIDE_FILTER, DEVICE_OVERRIDE_FILTER_RE])
@pytest.mark.parametrize("value", ["CH_Main_Automation", "", "light", "**"])
def test_a_text_filter_does_not_raise(key, value):
    """THE regression: anything but "*" got .items() called on it."""
    result = _customizer().device_override_match(_device(), {key: value})

    assert result is False, "a filter it cannot interpret must not match"


@pytest.mark.parametrize("key", [DEVICE_OVERRIDE_FILTER, DEVICE_OVERRIDE_FILTER_RE])
def test_a_number_filter_does_not_raise(key):
    """Nothing guarantees the config holds a string either."""
    assert _customizer().device_override_match(_device(), {key: 42}) is False


# ---------------------------------------------------------------------------
# What must keep working
# ---------------------------------------------------------------------------


def test_the_wildcard_still_matches_everything():
    assert _customizer().device_override_match(_device(), {DEVICE_OVERRIDE_FILTER: "*"}) is True


def test_a_mapping_filter_still_matches_on_a_hit():
    override = {DEVICE_OVERRIDE_FILTER: {"object_type": "CH_Main_Automation"}}

    assert _customizer().device_override_match(_device(), override) is True


def test_a_mapping_filter_still_rejects_on_a_miss():
    override = {DEVICE_OVERRIDE_FILTER: {"object_type": "CH_Dimmer_RGB"}}

    assert _customizer().device_override_match(_device(), override) is False


def test_a_regex_filter_still_works():
    override = {DEVICE_OVERRIDE_FILTER_RE: {"object_name": "^Lampada"}}

    assert _customizer().device_override_match(_device(), override) is True


def test_an_override_with_no_filter_at_all_matches_nothing():
    assert _customizer().device_override_match(_device(), {}) is False
