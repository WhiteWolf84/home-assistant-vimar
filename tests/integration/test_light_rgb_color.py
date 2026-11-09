"""Setting the colour of an RGB light (Home Assistant required).

VimarLight declares ColorMode.RGB as its only colour mode, and async_turn_on()
looks for `hs_color` in the call. Home Assistant never passes it: before the
entity is called, a colour given in any form is converted to a mode the light
supports (light/helper.py, "convert to the color space supported by the
light"), so a light that supports RGB and not HS receives `rgb_color`.

Neither branch of async_turn_on() matches, and because the call has arguments
the plain "switch on" branch is skipped as well: choosing a colour sends
nothing at all - no colour, and no on/off if the light was off.

Read from the code of Home Assistant, not tried on a CH_Dimmer_RGB: the plant
this fork is developed on has no dimmer and no light at all (checked on
2026-11-10), so a fix here cannot be validated on hardware by us.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ColorMode

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.light import VimarLight  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

PENDING = "review 2026-11 #1.5: async_turn_on reads hs_color, Home Assistant passes rgb_color"


def _rgb_light(on: bool = True) -> VimarLight:
    light = VimarLight.__new__(VimarLight)
    light._device = {
        "object_type": "CH_Dimmer_RGB",
        "status": {
            "on/off": {"status_id": "1", "status_value": "1" if on else "0"},
            "value": {"status_id": "2", "status_value": "100"},
            "red": {"status_id": "3", "status_value": "0"},
            "green": {"status_id": "4", "status_value": "0"},
            "blue": {"status_id": "5", "status_value": "0"},
        },
    }
    light._device_id = "100"
    light.change_state = MagicMock()
    return light


def _written(light: VimarLight) -> dict[str, str]:
    """Every state written by the call, whatever the number of change_state()."""
    written: dict[str, str] = {}
    for call in light.change_state.call_args_list:
        args = iter(call.args)
        written.update({state: str(value) for state, value in zip(args, args, strict=True)})
        written.update({state: str(value) for state, value in call.kwargs.items()})
    return written


def test_the_light_only_declares_rgb():
    """The premise: with this, Home Assistant hands over rgb_color, never hs."""
    assert _rgb_light().supported_color_modes == {ColorMode.RGB}


async def test_brightness_is_written():
    light = _rgb_light()

    await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})

    assert _written(light) == {"value": "100", "on/off": "1"}


@pytest.mark.xfail(strict=True, reason=PENDING)
async def test_a_colour_is_written():
    light = _rgb_light()

    await light.async_turn_on(**{ATTR_RGB_COLOR: (255, 128, 0)})

    written = _written(light)
    assert (written.get("red"), written.get("green"), written.get("blue")) == (
        "255",
        "128",
        "0",
    )


@pytest.mark.xfail(strict=True, reason=PENDING)
async def test_choosing_a_colour_switches_an_off_light_on():
    light = _rgb_light(on=False)

    await light.async_turn_on(**{ATTR_RGB_COLOR: (255, 128, 0)})

    assert _written(light).get("on/off") == "1"
