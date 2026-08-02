"""Friendly-name formatting (NO Home Assistant required).

VimarProject.format_name turns the VIMAR bus name ("LUCE 11 CUCINA PIANO
TERRA") into the name shown in Home Assistant. It splits the name into
type / number / room / level, drops the redundant type word and reorders the
rest. It is user-visible on every single entity and had no test coverage,
despite already having produced one bug (FIX #21: the LUCE/LICHT guard).

These tests pin the current output. They are not a claim that the heuristic is
right for every installation - they make any change to it deliberate and
visible, because changing a friendly name renames entities for existing users.
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
    """format_name does not touch instance state, so skip __init__."""
    return VimarProject.__new__(VimarProject)


@pytest.mark.parametrize(
    ("bus_name", "expected"),
    [
        # 4+ words: TYPE NUMBER ROOM LEVEL... -> "Level Room Number"
        ("LUCE 11 CUCINA PIANO TERRA", "Piano Terra Cucina 11"),
        ("TAPPARELLA 3 SALA PIANO 1", "Piano 1 Sala 3"),
        ("THERMOSTAT 1 BAGNO PIANO 1", "Piano 1 Bagno 1"),
        ("STECKDOSE 2 KUECHE ERDGESCHOSS", "Erdgeschoss Kueche 2"),
        # The type word is only dropped when it is a known noise word.
        ("DIMMER 11 WOHNZIMMER", "11 Wohnzimmer Dimmer"),
        # F-FERNBEDIENUNG is rewritten, not dropped.
        ("F-FERNBEDIENUNG 1 BAD OG", "Og Bad Fenster 1"),
    ],
)
def test_known_naming_scheme(project, bus_name, expected):
    assert project.format_name(bus_name) == expected


def test_licht_alone_is_not_erased(project):
    """FIX #21 regression: a device named just 'LICHT' must keep its name.

    The noise-word stripping removes 'LICHT' from a compound type, but when
    the whole name IS 'LICHT' there would be nothing left to show.
    """
    assert project.format_name("LICHT") == "Licht"


def test_licht_with_a_number_keeps_the_word(project):
    assert project.format_name("LICHT 5") == "5 Licht"


@pytest.mark.parametrize(
    ("bus_name", "expected"),
    [
        ("", ""),
        ("   ", ""),
        ("CH_UNKNOWN", "Ch_Unknown"),
    ],
)
def test_degenerate_names_do_not_raise(project, bus_name, expected):
    assert project.format_name(bus_name) == expected


def test_result_has_no_leading_or_trailing_whitespace(project):
    """Dropped words leave gaps; the result must still be clean."""
    for bus_name in ("LUCE 11 CUCINA PIANO TERRA", "LUCE 1 SALA", "TAPPARELLA 2 BAGNO PIANO 1"):
        formatted = project.format_name(bus_name)
        assert formatted == formatted.strip()
        assert "  " not in formatted


def test_formatting_is_stable(project):
    """Same input, same output: entity names must not drift between reloads."""
    name = "LUCE 11 CUCINA PIANO TERRA"

    assert project.format_name(name) == project.format_name(name)


def test_room_and_level_words_are_never_lost(project):
    """Only type noise words may be dropped; location info must survive."""
    formatted = project.format_name("LUCE 11 CUCINA PIANO TERRA").upper()

    assert "CUCINA" in formatted
    assert "PIANO TERRA" in formatted
    assert "11" in formatted
