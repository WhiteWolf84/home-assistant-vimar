"""Device names say what the device is, not where it is (NO Home Assistant).

Home Assistant builds what the user reads from area + device + entity. A device
whose name is its own room therefore says the room twice, and no entity-side
fix can reach it: `cover.bagnetto_bagnetto` is the area and the device
colliding. `format_name` guaranteed that collision - it reordered the bus name
to put the location first and deleted the word naming the function, so
"TAPPARELLA BAGNETTO" came out as "Bagnetto".

`device_name_from_object_name` subtracts the room using the rooms the web
server assigns to the object, rather than guessing from word positions. These
tests pin both halves: what it now produces, and the two cases where it
deliberately changes nothing.
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink.vimarlink import VimarProject  # noqa: E402

pytestmark = pytest.mark.no_ha  # No HA required


@pytest.fixture
def project():
    """The naming helpers touch no instance state, so skip __init__."""
    return VimarProject.__new__(VimarProject)


@pytest.mark.parametrize(
    ("bus_name", "room_names", "expected"),
    [
        # THE regression: the function word survives, the room goes.
        ("TAPPARELLA BAGNETTO", ["Bagnetto"], "Tapparella"),
        ("PRESA BAGNO PADRONALE", ["Bagno Padronale"], "Presa"),
        ("TERMOSTATO CAMERETTA", ["Cameretta"], "Termostato"),
        # A floor modelled as a second room is subtracted as well.
        ("LUCE 11 CUCINA PIANO TERRA", ["Cucina", "Piano Terra"], "Luce 11"),
        # Only the rooms the object actually belongs to are removed: the floor
        # stays when the web server does not model it as a room of its own.
        ("LUCE 11 CUCINA PIANO TERRA", ["Cucina"], "Luce 11 Piano Terra"),
        # Matching is per whole word and case-insensitive, never a substring:
        # "SALA" must not eat the "SALA" inside "SALAMANDRA".
        ("LUCE SALAMANDRA SALA", ["sala"], "Luce Salamandra"),
    ],
)
def test_the_room_is_removed_and_the_function_kept(project, bus_name, room_names, expected):
    assert project.device_name_from_object_name(bus_name, room_names) == expected


@pytest.mark.parametrize(
    ("bus_name", "room_names"),
    [
        ("BAGNETTO", ["Bagnetto"]),
        ("bagnetto", ["BAGNETTO"]),
        ("BAGNO PADRONALE", ["Bagno Padronale"]),
        # Every word is a room word, even though they come from two rooms.
        ("CUCINA PIANO TERRA", ["Cucina", "Piano Terra"]),
    ],
)
def test_a_name_that_is_only_its_room_is_left_alone(project, bus_name, room_names):
    """Subtracting everything would leave the device with no name at all."""
    assert project.device_name_from_object_name(bus_name, room_names) == project.format_name(
        bus_name
    )


@pytest.mark.parametrize(
    ("bus_name", "expected"),
    [
        # No room means no duplication to fix, so the old output is preserved
        # exactly - including the fact that it is a poor name.
        ("CONTROLLO CARICHI GLOBALE", "Carichi Globale Controllo"),
        ("LUCE 11 CUCINA PIANO TERRA", "Piano Terra Cucina 11"),
        ("LICHT", "Licht"),
    ],
)
@pytest.mark.parametrize("room_names", [None, []])
def test_an_object_with_no_room_keeps_the_old_name(project, bus_name, room_names, expected):
    assert project.device_name_from_object_name(bus_name, room_names) == expected


def test_the_result_is_clean(project):
    """Removed words leave gaps; what is left must not carry them."""
    name = project.device_name_from_object_name("LUCE 11 CUCINA PIANO TERRA", ["Cucina"])

    assert name == name.strip()
    assert "  " not in name


def test_naming_is_stable(project):
    """Same input, same output: device names must not drift between reloads."""
    args = ("TAPPARELLA BAGNETTO", ["Bagnetto"])

    assert project.device_name_from_object_name(*args) == project.device_name_from_object_name(
        *args
    )


def test_degenerate_names_do_not_raise(project):
    assert project.device_name_from_object_name("", ["Bagnetto"]) == ""
    assert project.device_name_from_object_name("   ", ["Bagnetto"]) == ""


@pytest.mark.parametrize(
    ("bus_name", "room_names", "expected"),
    [
        ("TAPPARELLA BAGNETTO", ["Bagnetto"], "Tapparella"),
        # No room: parse_device_type must still get a name out of the fallback.
        ("CONTROLLO CARICHI GLOBALE", [], "Carichi Globale Controllo"),
    ],
)
def test_parse_device_type_publishes_the_new_name(project, bus_name, room_names, expected):
    """The wiring, not the rule: device_friendly_name is what device_info reads.

    The rule above is only worth anything if parse_device_type actually calls
    it with the object's rooms - it used to call format_name with the name
    alone, and the rooms were right there on the device the whole time.
    """
    project._platforms_exists = {}
    project._device_customizer_action = None
    device = {
        "object_id": "721",
        "object_name": bus_name,
        "object_type": "CH_ShutterWithoutPosition_Automation",
        "room_names": room_names,
        "status": {},
    }

    project.parse_device_type(device)

    assert device["device_friendly_name"] == expected
