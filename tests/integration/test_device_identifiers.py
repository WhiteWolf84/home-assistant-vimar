"""Our three-element device identifiers keep working (Home Assistant required).

Every device this integration registers is identified by
`(DOMAIN, <entry prefix>, <web server id>)` - three elements, where Home
Assistant's own type says two. The middle element is what keeps two web
servers exporting the same id apart. It cannot be changed: the identifiers ARE
the identity of every device already in the registry, so a new shape would
register new devices and lose the areas, names and labels of the old ones.

Home Assistant 2026.8 moved device lookups to per-config-entry APIs
(`async_get_device_by_identifier`, `async_get_devices`). They accept our
identifiers only because the registry matches an identifier as an exact dict
key, whatever its length; nothing in the type promises it. These tests pin
that behaviour, so a release that starts validating the length fails here,
in CI, before it reaches an installation.
"""

import os
import sys
from typing import cast

import pytest
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.const import DOMAIN  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

PREFIX = "192_168_1_110"
IDENTIFIER = cast("tuple[str, str]", (DOMAIN, PREFIX, "721"))


@pytest.fixture
def entries(hass):
    ours = MockConfigEntry(domain=DOMAIN, unique_id=PREFIX)
    ours.add_to_hass(hass)
    other = MockConfigEntry(domain=DOMAIN, unique_id="altra_casa")
    other.add_to_hass(hass)
    return ours, other


def _register(hass, entry):
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={IDENTIFIER},
        name="Tapparella",
    )


async def test_a_three_element_identifier_is_found_in_its_config_entry(hass, entries):
    ours, other = entries
    device = _register(hass, ours)
    registry = dr.async_get(hass)

    found = registry.async_get_device_by_identifier(IDENTIFIER, ours.entry_id)

    assert found is not None
    assert found.id == device.id
    assert found.identifiers == {IDENTIFIER}


async def test_the_lookup_is_scoped_to_the_config_entry(hass, entries):
    ours, other = entries
    mine = _register(hass, ours)
    theirs = _register(hass, other)
    registry = dr.async_get(hass)

    assert mine.id != theirs.id
    assert registry.async_get_device_by_identifier(IDENTIFIER, ours.entry_id).id == mine.id
    assert registry.async_get_device_by_identifier(IDENTIFIER, other.entry_id).id == theirs.id


@pytest.mark.parametrize(
    "identifier",
    [
        (DOMAIN, PREFIX),  # two elements: the middle one is not a device id
        (DOMAIN, "721"),  # two elements, without the entry prefix
        (DOMAIN, PREFIX, "721", "extra"),  # four elements
    ],
)
async def test_any_other_shape_matches_nothing(hass, entries, identifier):
    ours, _ = entries
    _register(hass, ours)

    assert dr.async_get(hass).async_get_device_by_identifier(identifier, ours.entry_id) is None


async def test_async_get_devices_finds_it_too(hass, entries):
    ours, other = entries
    mine = _register(hass, ours)
    _register(hass, other)

    found = dr.async_get(hass).async_get_devices(
        identifiers={IDENTIFIER}, config_entry_id=ours.entry_id
    )

    assert [device.id for device in found] == [mine.id]
