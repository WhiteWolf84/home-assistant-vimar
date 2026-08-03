"""Change detection between polls (Home Assistant required).

After an optimistic local write the entity asks the coordinator to forget the
device's state hash, so the next poll republishes the device even if the
webserver answers with the value already in cache (a monostable device falling
back to 0 twice in a row, a thermostat rounding a setpoint, a shutter that did
not move). That behaviour must be preserved.

Regression: the hash entry was DELETED to achieve it. A missing entry means
"device never seen before", so every locally written device reappeared in the
log as `New device detected` on the next poll - on installations whose
topology had not changed at all. Confirmed on a live system: five thermostats
and a shutter were reported as new right after a scene wrote to them, three
polls in a row for the shutter (one report per command). A sentinel is stored
instead, so the two cases stay distinguishable.
"""

import logging
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.vimar_coordinator import (  # noqa: E402
    VimarDataUpdateCoordinator,
)
from custom_components.vimar.vimar_entity import VimarEntity  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

DEVICE_ID = "3942"


def _coordinator():
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)
    coordinator._device_state_hashes = {}
    coordinator._changed_device_ids = set()
    return coordinator


def _devices(value="0"):
    return {
        DEVICE_ID: {
            "object_id": DEVICE_ID,
            "device_friendly_name": "Tapparella Camera Multimediale",
            "status": {"up/down": {"status_id": "3946", "status_value": value}},
        }
    }


def _entity(coordinator):
    entity = VimarEntity.__new__(VimarEntity)
    entity._device_id = DEVICE_ID
    entity._coordinator = coordinator
    entity.async_write_ha_state = MagicMock()
    return entity


# ---------------------------------------------------------------------------
# The behaviour that must be preserved
# ---------------------------------------------------------------------------


def test_an_unchanged_value_after_a_local_write_is_still_republished():
    """THE point of the invalidation: the UI must resync even on an identical value."""
    coordinator = _coordinator()
    devices = _devices("1")
    coordinator._detect_state_changes(devices)  # first poll: learns the hash

    _entity(coordinator).request_statemachine_update()
    changed = coordinator._detect_state_changes(devices)  # same value as before

    assert DEVICE_ID in changed


def test_without_a_local_write_an_unchanged_device_is_skipped():
    coordinator = _coordinator()
    devices = _devices("1")
    coordinator._detect_state_changes(devices)

    assert coordinator._detect_state_changes(devices) == set()


def test_a_real_value_change_is_detected():
    coordinator = _coordinator()
    coordinator._detect_state_changes(_devices("0"))

    assert DEVICE_ID in coordinator._detect_state_changes(_devices("1"))


def test_the_invalidation_lasts_a_single_poll():
    """Once resynchronised the device goes back to normal hash comparison."""
    coordinator = _coordinator()
    devices = _devices("1")
    coordinator._detect_state_changes(devices)
    _entity(coordinator).request_statemachine_update()
    coordinator._detect_state_changes(devices)  # consumes the invalidation

    assert coordinator._detect_state_changes(devices) == set()


# ---------------------------------------------------------------------------
# The regression: the log must not lie
# ---------------------------------------------------------------------------


def test_a_locally_written_device_is_not_reported_as_new(caplog):
    """It is not new: it is one of ours, written a moment ago."""
    coordinator = _coordinator()
    devices = _devices("1")
    coordinator._detect_state_changes(devices)
    _entity(coordinator).request_statemachine_update()

    # Drop the first poll's records: that one legitimately says "New device".
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="custom_components.vimar"):
        coordinator._detect_state_changes(devices)

    assert "New device detected" not in caplog.text
    assert "resynchronised after a local write" in caplog.text


def test_a_genuinely_new_device_is_still_reported_as_new(caplog):
    """A real topology change must remain visible in the log."""
    coordinator = _coordinator()

    with caplog.at_level(logging.DEBUG, logger="custom_components.vimar"):
        changed = coordinator._detect_state_changes(_devices("0"))

    assert DEVICE_ID in changed
    assert "New device detected" in caplog.text


def test_the_sentinel_cannot_collide_with_a_real_hash():
    """The sentinel is only safe because a real hash is a 32-char hexdigest."""
    coordinator = _coordinator()
    real_hash = coordinator._hash_device_state(_devices("1")[DEVICE_ID])

    assert len(real_hash) == 32
    assert real_hash != ""


def test_request_statemachine_update_also_flags_the_device_as_changed():
    """The optimistic path must still push the entity immediately."""
    coordinator = _coordinator()
    entity = _entity(coordinator)

    entity.request_statemachine_update()

    assert DEVICE_ID in coordinator._changed_device_ids
    entity.async_write_ha_state.assert_called_once()
