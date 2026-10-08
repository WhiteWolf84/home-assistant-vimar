"""The VIMAR room survives as a label (Home Assistant required).

The room used to BE the device name: "TAPPARELLA BAGNETTO" was registered as
the device "Bagnetto". It no longer is - Home Assistant already prefixes a
device with its area, so the name said it twice - but the room is data the web
server hands us and dropping it from the name must not throw it away.

It is kept as a label, which is the one place it stays addressable: a script or
automation can target `label_id: bagnetto` and reach every Vimar device in that
room without listing them.

The risk in writing to the label registry is that labels are the USER's: these
tests pin that the integration only ever touches labels named after a room of
this web server, carries every other label across untouched, and writes nothing
at all when nothing changed - a restart must not churn the registry.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.const import CONF_ROOM_LABELS, DOMAIN  # noqa: E402
from custom_components.vimar.vimar_coordinator import (  # noqa: E402
    VimarDataUpdateCoordinator,
)

pytestmark = pytest.mark.integration  # Home Assistant required

PREFIX = "casa"
ENTRY_ID = "entry-1"


class _FakeLabelRegistry:
    """Just enough of homeassistant.helpers.label_registry to observe writes."""

    def __init__(self, existing: tuple[str, ...] = ()):
        self._by_name = {name: f"label_{name.lower()}" for name in existing}
        self.created: list[str] = []

    def async_get_label_by_name(self, name):
        label_id = self._by_name.get(name)
        return SimpleNamespace(label_id=label_id, name=name) if label_id else None

    def async_create(self, name):
        label_id = f"label_{name.lower()}"
        self._by_name[name] = label_id
        self.created.append(name)
        return SimpleNamespace(label_id=label_id, name=name)


class _FakeDeviceRegistry:
    """Devices keyed by the three-element identifier VimarEntity registers."""

    def __init__(self, devices: dict[str, set[str]]):
        self._devices = {
            device_id: SimpleNamespace(id=f"reg_{device_id}", labels=set(labels))
            for device_id, labels in devices.items()
        }
        self.updates: list[tuple[str, set[str]]] = []

    def async_get_device_by_identifier(self, identifier, config_entry_id):
        domain, prefix, device_id = identifier
        if (
            config_entry_id == ENTRY_ID
            and domain == DOMAIN
            and prefix == PREFIX
            and device_id in self._devices
        ):
            return self._devices[device_id]
        return None

    def async_update_device(self, device_id, *, labels=None, **kwargs):
        self.updates.append((device_id, set(labels or ())))
        for device in self._devices.values():
            if device.id == device_id:
                device.labels = set(labels or ())


def _coordinator(vimar_devices, config=None):
    """A coordinator with just the state _async_apply_room_labels reads."""
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)
    coordinator.hass = MagicMock()
    coordinator.entry = SimpleNamespace(entry_id=ENTRY_ID)
    coordinator.vimarconfig = config if config is not None else {}
    coordinator.entity_unique_id_prefix = PREFIX
    coordinator.vimarproject = MagicMock()
    coordinator.vimarproject.devices = vimar_devices
    return coordinator


def _run(coordinator, label_registry, device_registry):
    with (
        patch(
            "custom_components.vimar.vimar_coordinator.lr.async_get",
            return_value=label_registry,
        ),
        patch(
            "custom_components.vimar.vimar_coordinator.dr.async_get",
            return_value=device_registry,
        ),
    ):
        coordinator._async_apply_room_labels()


def _devices(**rooms):
    return {
        device_id: {"room_friendly_name": room, "object_id": device_id}
        for device_id, room in rooms.items()
    }


def test_each_device_is_labelled_with_its_room():
    labels = _FakeLabelRegistry()
    devices = _FakeDeviceRegistry({"721": set(), "8335": set()})

    _run(_coordinator(_devices(**{"721": "Bagnetto", "8335": "Cameretta"})), labels, devices)

    assert sorted(labels.created) == ["Bagnetto", "Cameretta"]
    assert dict(devices.updates) == {
        "reg_721": {"label_bagnetto"},
        "reg_8335": {"label_cameretta"},
    }


def test_an_existing_label_is_reused_not_duplicated():
    """Two devices in one room share a label, and a label already in the
    registry - the user's, or ours from last time - is picked up as it is."""
    labels = _FakeLabelRegistry(existing=("Bagnetto",))
    devices = _FakeDeviceRegistry({"721": set(), "722": set()})

    _run(_coordinator(_devices(**{"721": "Bagnetto", "722": "Bagnetto"})), labels, devices)

    assert labels.created == []
    assert {label_id for _, applied in devices.updates for label_id in applied} == {
        "label_bagnetto"
    }


def test_the_users_own_labels_are_carried_across():
    """THE thing that must not break: labels are the user's, not ours."""
    labels = _FakeLabelRegistry()
    devices = _FakeDeviceRegistry({"721": {"label_piano_terra", "label_da_controllare"}})

    _run(_coordinator(_devices(**{"721": "Bagnetto"})), labels, devices)

    assert devices.updates == [
        ("reg_721", {"label_piano_terra", "label_da_controllare", "label_bagnetto"})
    ]


def test_a_device_that_moved_room_loses_only_the_old_room():
    """Without this the device accumulates every room it has ever been in."""
    labels = _FakeLabelRegistry(existing=("Bagnetto", "Cameretta"))
    devices = _FakeDeviceRegistry({"721": {"label_bagnetto", "label_da_controllare"}})

    # 721 is now in Cameretta; Bagnetto is still a room of this web server.
    _run(_coordinator(_devices(**{"721": "Cameretta", "999": "Bagnetto"})), labels, devices)

    assert ("reg_721", {"label_cameretta", "label_da_controllare"}) in devices.updates


def test_nothing_is_written_when_nothing_changed():
    """A restart must be a no-op, not a registry write per device."""
    labels = _FakeLabelRegistry(existing=("Bagnetto",))
    devices = _FakeDeviceRegistry({"721": {"label_bagnetto"}})

    _run(_coordinator(_devices(**{"721": "Bagnetto"})), labels, devices)

    assert devices.updates == []


def test_a_device_with_no_room_is_left_alone():
    """The web server hub and the SAI alarm belong to no VIMAR room."""
    labels = _FakeLabelRegistry()
    devices = _FakeDeviceRegistry({"721": set(), "hub": {"label_mio"}})

    _run(_coordinator(_devices(**{"721": "Bagnetto", "hub": "   "})), labels, devices)

    assert devices.updates == [("reg_721", {"label_bagnetto"})]


def test_a_device_not_in_the_registry_is_skipped():
    """An ignored or not-yet-added device must not raise."""
    labels = _FakeLabelRegistry()
    devices = _FakeDeviceRegistry({"721": set()})

    _run(_coordinator(_devices(**{"721": "Bagnetto", "sconosciuto": "Cucina"})), labels, devices)

    assert devices.updates == [("reg_721", {"label_bagnetto"})]


def test_the_option_switches_it_off_entirely():
    """Off must touch neither registry - not even to create the labels."""
    labels = _FakeLabelRegistry()
    devices = _FakeDeviceRegistry({"721": set()})

    _run(
        _coordinator(_devices(**{"721": "Bagnetto"}), config={CONF_ROOM_LABELS: False}),
        labels,
        devices,
    )

    assert labels.created == []
    assert devices.updates == []


def test_it_is_on_when_the_option_was_never_set():
    """Existing installations get the labels without touching their options."""
    labels = _FakeLabelRegistry()
    devices = _FakeDeviceRegistry({"721": set()})

    _run(_coordinator(_devices(**{"721": "Bagnetto"}), config={}), labels, devices)

    assert devices.updates == [("reg_721", {"label_bagnetto"})]


async def test_it_runs_once_the_devices_exist():
    """The wiring, not the rule.

    Labels can only be attached to devices the registry already holds, and the
    platforms are what create them - so this has to run after the forward, not
    before it. Called too early it would silently do nothing at all, which is
    exactly the kind of failure no assertion on the rule itself would catch.
    """
    coordinator = _coordinator(_devices(**{"721": "Bagnetto"}))
    coordinator.entry = MagicMock(entry_id=ENTRY_ID)
    coordinator._async_register_webserver_device = MagicMock()
    forward = AsyncMock()
    coordinator.hass.config_entries.async_forward_entry_setups = forward
    labels = _FakeLabelRegistry()
    devices = _FakeDeviceRegistry({"721": set()})

    with (
        patch(
            "custom_components.vimar.vimar_coordinator.lr.async_get",
            return_value=labels,
        ),
        patch(
            "custom_components.vimar.vimar_coordinator.dr.async_get",
            return_value=devices,
        ),
    ):
        await coordinator.async_register_devices_platforms()

    assert forward.await_count == 1
    assert devices.updates == [("reg_721", {"label_bagnetto"})]


# ---------------------------------------------------------------------------
# Against the real registries
# ---------------------------------------------------------------------------


def _register(hass, entry, device_id):
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, PREFIX, device_id)},  # pyright: ignore[reportArgumentType]
        name=f"Device {device_id}",
    )


async def test_room_labels_reach_the_real_registry_without_deprecations(hass, caplog):
    """async_get_device is deprecated (2026.8, removed 2027.8): not used any more."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PREFIX)
    entry.add_to_hass(hass)
    device = _register(hass, entry, "721")
    coordinator = _coordinator(_devices(**{"721": "Bagnetto"}))
    coordinator.hass = hass
    coordinator.entry = entry

    coordinator._async_apply_room_labels()

    assert [r.getMessage() for r in caplog.records if "deprecated" in r.getMessage()] == []
    bagnetto = lr.async_get(hass).async_get_label_by_name("Bagnetto")
    assert bagnetto is not None
    assert dr.async_get(hass).async_get(device.id).labels == {bagnetto.label_id}


async def test_room_labels_stay_within_their_config_entry(hass):
    """The same identifier under another entry is another device: not ours."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PREFIX)
    entry.add_to_hass(hass)
    other_entry = MockConfigEntry(domain=DOMAIN, unique_id="altra_casa")
    other_entry.add_to_hass(hass)
    ours = _register(hass, entry, "721")
    theirs = _register(hass, other_entry, "721")
    coordinator = _coordinator(_devices(**{"721": "Bagnetto"}))
    coordinator.hass = hass
    coordinator.entry = entry

    coordinator._async_apply_room_labels()

    registry = dr.async_get(hass)
    assert registry.async_get(ours.id).labels
    assert registry.async_get(theirs.id).labels == set()
