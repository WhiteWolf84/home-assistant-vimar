"""Nothing the integration holds is quietly absent (Home Assistant required).

`VimarEntity` declared `_device`, `_vimarconnection`, `_vimarproject` and
`_coordinator` as Optional, and about half the code that read them checked for
None while the other half did not. The unchecked half was right in practice -
entities are only ever built from devices that are in the project - but the
declaration said otherwise, so a type checker could not tell a real gap from a
redundant guard, and neither could a reader.

Two live defects were hiding in that ambiguity, both found by turning the
`reportOptional*` rules on:

  * `media_player.async_mute_volume` stored `volume_level`, which is None when
    the device has no "volume" status, and unmuting then raised TypeError on
    `None * 100`;
  * `_tb_update_position` compared `_tb_position` to a number without the
    guarantee that tracking had set it.

The contract is now stated instead of assumed: the coordinator builds its
connection and project in __init__, and an entity whose device cannot be found
carries MISSING_DEVICE rather than nothing at all.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.media_player import VimarMediaplayer  # noqa: E402
from custom_components.vimar.vimar_entity import MISSING_DEVICE, VimarEntity  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

DEVICE_ID = "768"


def _coordinator(devices):
    coordinator = MagicMock()
    coordinator.vimarproject.devices = devices
    coordinator.vimarproject.global_channel_id = None
    coordinator.entity_unique_id_prefix = "casa"
    return coordinator


def _device(status):
    return {
        "object_id": DEVICE_ID,
        "object_type": "CH_Audio",
        "object_name": "Radio",
        "device_class": None,
        "device_type": "media_player",
        "status": status,
    }


# ---------------------------------------------------------------------------
# media_player: unmuting a player with no volume status
# ---------------------------------------------------------------------------


async def test_unmuting_a_player_without_a_volume_status_does_not_raise():
    """THE regression: volume_level is None, so `None * 100` blew up.

    Muting recorded None as the volume to come back to; the unmute then died
    with TypeError and the player stayed silent, with only a traceback in the
    log to say why.
    """
    device = _device({"on/off": {"status_id": "1", "status_value": "1"}})  # no "volume"
    player = VimarMediaplayer(_coordinator({DEVICE_ID: device}), int(DEVICE_ID))
    player._device = device
    player.change_state = MagicMock()

    await player.async_mute_volume(True)
    await player.async_mute_volume(False)  # used to raise TypeError

    assert player.change_state.call_count == 2


async def test_unmuting_restores_the_volume_that_was_muted():
    """The fallback must not cost the normal case its actual volume."""
    device = _device(
        {
            "on/off": {"status_id": "1", "status_value": "1"},
            "volume": {"status_id": "2", "status_value": "40"},
        }
    )
    player = VimarMediaplayer(_coordinator({DEVICE_ID: device}), int(DEVICE_ID))
    player._device = device
    player.change_state = MagicMock()

    await player.async_mute_volume(True)
    await player.async_mute_volume(False)

    assert player.change_state.call_args_list[-1].args == ("volume", "40")


# ---------------------------------------------------------------------------
# The missing-device placeholder
# ---------------------------------------------------------------------------


class _Entity(VimarEntity):
    @property
    def entity_platform(self):
        return "switch"


def test_an_entity_for_an_unknown_device_is_inert_not_broken():
    """It used to end up with _device = None and raise on first use."""
    entity = _Entity(_coordinator({}), 999)

    assert entity._device is MISSING_DEVICE
    assert entity.device_name == "Unknown Device 999"
    assert entity.extra_state_attributes == {}
    assert entity.has_state("on/off") is False
    assert entity.device_info is None
    assert entity.device_class is None


def test_the_placeholder_carries_no_statuses():
    """`available` and has_state both key off this being empty."""
    assert MISSING_DEVICE["status"] == {}


def test_the_placeholder_is_a_single_shared_object():
    """The guards compare by identity, so a copy would silently stop matching."""
    first = _Entity(_coordinator({}), 1)
    second = _Entity(_coordinator({}), 2)

    assert first._device is second._device is MISSING_DEVICE


def test_a_known_device_is_not_replaced_by_the_placeholder():
    device = _device({"on/off": {"status_id": "1", "status_value": "1"}})
    entity = _Entity(_coordinator({DEVICE_ID: device}), int(DEVICE_ID))

    assert entity._device is device
    assert entity.has_state("on/off") is True


# ---------------------------------------------------------------------------
# The declared contract
# ---------------------------------------------------------------------------


def test_the_entity_takes_its_link_and_project_from_the_coordinator():
    """Both are built in the coordinator's __init__, so both are always there."""
    coordinator = _coordinator({})
    entity = _Entity(coordinator, 1)

    assert entity._vimarconnection is coordinator.vimarconnection
    assert entity._vimarproject is coordinator.vimarproject
    assert entity._coordinator is coordinator


def test_no_optional_defaults_remain_on_the_entity_class():
    """A class-level `= None` is what made the absence invisible."""
    for name in ("_device", "_vimarconnection", "_vimarproject", "_coordinator"):
        assert not hasattr(VimarEntity, name), (
            f"VimarEntity.{name} still has a class-level default; "
            "instances built with __new__ would silently carry None"
        )
