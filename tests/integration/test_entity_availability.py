"""Entity availability must follow the coordinator (Home Assistant required).

Regression: VimarEntity._handle_coordinator_update only forwarded the update
when the device id was in coordinator._changed_device_ids. The coordinator
empties that set at the START of every poll cycle and an exception aborts the
cycle before it is repopulated, so on a failed poll the set stayed empty and
EVERY entity skipped the write.

`available` is only ever published to the state machine by an
async_write_ha_state() call, so the practical effect was: unplug the VIMAR web
server and Home Assistant keeps showing the lights on, the last thermostat
reading and the last shutter position - forever, with no 'unavailable' marker
and no clue for automations. Symmetrically, once an entity did go unavailable
it stayed that way after recovery until its own state happened to change.

The entity now writes on every availability TRANSITION as well.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.vimar_entity import VimarEntity  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

DEVICE_ID = "768"


def _make_entity(device_id=DEVICE_ID):
    """Build a VimarEntity wired to a fake coordinator.

    CoordinatorEntity.__init__ is bypassed: the code under test only needs
    self.coordinator, the device id and a stubbed state-machine write.
    CoordinatorEntity.available is `self.coordinator.last_update_success`, so
    driving that flag is enough to simulate the web server going up and down.
    """
    entity = VimarEntity.__new__(VimarEntity)
    entity._device_id = device_id
    entity._device = {"object_id": device_id, "status": {}}
    entity._logger = MagicMock()

    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {DEVICE_ID: {"object_id": DEVICE_ID}}
    coordinator._changed_device_ids = set()

    entity.coordinator = coordinator
    entity._coordinator = coordinator
    entity.async_write_ha_state = MagicMock()
    return entity, coordinator


def _writes(entity):
    return entity.async_write_ha_state.call_count


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------


def test_failed_poll_pushes_the_unavailable_state():
    """A poll failure must reach the state machine even with no state change."""
    entity, coordinator = _make_entity()
    entity._handle_coordinator_update()  # first cycle: establishes availability
    before = _writes(entity)

    coordinator.last_update_success = False
    coordinator._changed_device_ids = set()  # exactly what a failed cycle leaves
    entity._handle_coordinator_update()

    assert _writes(entity) == before + 1
    assert entity.available is False


def test_recovery_pushes_the_available_state_again():
    """Coming back online must be published even if the device did not change."""
    entity, coordinator = _make_entity()
    entity._handle_coordinator_update()
    coordinator.last_update_success = False
    entity._handle_coordinator_update()
    before = _writes(entity)

    coordinator.last_update_success = True
    coordinator._changed_device_ids = set()  # unchanged device
    entity._handle_coordinator_update()

    assert _writes(entity) == before + 1
    assert entity.available is True


def test_device_disappearing_from_the_tree_is_published():
    """A device dropped by a topology change must be marked unavailable."""
    entity, coordinator = _make_entity()
    entity._handle_coordinator_update()
    before = _writes(entity)

    coordinator.data = {}  # device no longer in the coordinator payload
    entity._handle_coordinator_update()

    assert _writes(entity) == before + 1
    assert entity.available is False


# ---------------------------------------------------------------------------
# The optimisation the filter exists for must be preserved
# ---------------------------------------------------------------------------


def test_unchanged_device_on_a_successful_poll_is_still_skipped():
    """The whole point of the filter: no churn for devices that did not move."""
    entity, coordinator = _make_entity()
    entity._handle_coordinator_update()
    before = _writes(entity)

    for _ in range(5):
        coordinator._changed_device_ids = set()
        entity._handle_coordinator_update()

    assert _writes(entity) == before


def test_changed_device_is_written():
    """A device reported as changed is published as before."""
    entity, coordinator = _make_entity()
    entity._handle_coordinator_update()
    before = _writes(entity)

    coordinator._changed_device_ids = {DEVICE_ID}
    entity._handle_coordinator_update()

    assert _writes(entity) == before + 1


def test_only_one_write_per_outage_not_one_per_poll():
    """Availability is written on the transition, not on every failed cycle."""
    entity, coordinator = _make_entity()
    entity._handle_coordinator_update()
    before = _writes(entity)

    coordinator.last_update_success = False
    for _ in range(10):  # ~80 s of outage at the default 8 s interval
        entity._handle_coordinator_update()

    assert _writes(entity) == before + 1


def test_other_entities_are_not_written_for_a_foreign_change():
    """A change on device A must not wake up device B (unchanged filter)."""
    entity, coordinator = _make_entity(device_id="999")
    coordinator.data = {"999": {"object_id": "999"}}
    entity._handle_coordinator_update()
    before = _writes(entity)

    coordinator._changed_device_ids = {DEVICE_ID}  # a different device
    entity._handle_coordinator_update()

    assert _writes(entity) == before


def test_first_update_always_publishes():
    """An entity that has never written must publish its initial state."""
    entity, _coordinator = _make_entity()

    entity._handle_coordinator_update()

    assert _writes(entity) == 1
