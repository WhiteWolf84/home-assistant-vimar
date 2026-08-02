"""Decoding of the SAI2 alarm bitmasks (Home Assistant required).

The SAI2 gateway publishes area and zone state as an 8-character binary
string in DPADD_OBJECT.CURRENT_VALUE. These two decoders turn it into the
armed/disarmed/triggered state of the alarm panel and the open/closed state
of every door, window and motion sensor - the most safety-relevant mapping in
the integration, and it had no test coverage.

Bit mapping (confirmed on real hardware, see the module docstrings):

    areas: bit5 Allarme, bit4 alarm memory, bit3 PAR, bit2 INT, bit1 ON,
           bit0 armed-active flag
    zones: bit0 open, bit2 memory, bit3 alarm, bit4 tamper, bit5 masked
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.alarm_control_panel import (  # noqa: E402
    _MODE_ARM_AWAY,
    _MODE_ARM_HOME,
    _MODE_ARM_NIGHT,
    _MODE_DISARM,
    SAI2_STATE_MAP,
    _parse_sai2_area_value,
)
from custom_components.vimar.binary_sensor import (  # noqa: E402
    _guess_device_class,
    _parse_sai2_zone_value,
)

pytestmark = pytest.mark.integration  # Home Assistant required


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00000000", "Disinserito"),
        ("00000011", "Inserito ON"),  # away
        ("00000101", "Inserito INT"),  # home
        ("00001001", "Inserito PAR"),  # night
        ("00100001", "Allarme"),  # active alarm wins over the armed mode
        ("00101001", "Allarme"),  # alarm while armed PAR
    ],
)
def test_area_bitmask_maps_to_the_expected_label(raw, expected):
    assert _parse_sai2_area_value(raw)[0] == expected


def test_every_decoded_label_has_a_home_assistant_state():
    """A label the state map does not know would silently read as DISARMED."""
    for raw in ("00000000", "00000011", "00000101", "00001001", "00100001"):
        assert _parse_sai2_area_value(raw)[0] in SAI2_STATE_MAP


def test_optimistic_bitmasks_decode_back_to_their_own_mode():
    """The optimistic value written on command must round-trip to that mode.

    _apply_optimistic() patches CURRENT_VALUE with mode.bitmask so the UI shows
    the target immediately. If the bitmask and the decoder ever disagree, the
    panel would flip to a different state on the next poll.
    """
    for mode in (_MODE_DISARM, _MODE_ARM_HOME, _MODE_ARM_AWAY, _MODE_ARM_NIGHT):
        assert _parse_sai2_area_value(mode.bitmask)[0] == mode.label


def test_alarm_memory_is_reported_without_arming_the_panel():
    """Bit 4 alone means 'an alarm happened', not 'the area is armed'."""
    label, memory = _parse_sai2_area_value("00010001")

    assert label == "Disinserito"
    assert memory is True


def test_alarm_memory_is_reported_alongside_an_armed_mode():
    label, memory = _parse_sai2_area_value("00011001")

    assert label == "Inserito PAR"
    assert memory is True


def test_no_memory_flag_on_a_clean_area():
    assert _parse_sai2_area_value("00001001")[1] is False


@pytest.mark.parametrize("raw", ["", "not-binary", "22222222"])
def test_unreadable_area_value_degrades_to_disarmed(raw):
    """Never invent an armed state out of a value we cannot parse."""
    assert _parse_sai2_area_value(raw) == ("Disinserito", False)


def test_unknown_armed_bitmask_falls_back_to_armed_not_disarmed():
    """Bit 0 set with no known mode: fail safe towards 'armed'."""
    assert _parse_sai2_area_value("00000001")[0] == "Inserito ON"


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


def test_zone_open_bit():
    """Confirmed on hardware: an open garage door reads '00000001'."""
    assert _parse_sai2_zone_value("00000001")["open"] is True


def test_zone_closed():
    flags = _parse_sai2_zone_value("00000000")

    assert flags == {
        "open": False,
        "memory": False,
        "alarm": False,
        "tamper": False,
        "masked": False,
    }


@pytest.mark.parametrize(
    ("raw", "flag"),
    [
        ("00000100", "memory"),
        ("00001000", "alarm"),
        ("00010000", "tamper"),
        ("00100000", "masked"),
    ],
)
def test_zone_individual_flags(raw, flag):
    flags = _parse_sai2_zone_value(raw)

    assert flags[flag] is True
    assert flags["open"] is False


def test_zone_flags_combine():
    flags = _parse_sai2_zone_value("00011001")

    assert flags["open"] is True
    assert flags["alarm"] is True
    assert flags["tamper"] is True
    assert flags["masked"] is False


@pytest.mark.parametrize("raw", ["", "junk"])
def test_unreadable_zone_value_reads_as_all_false(raw):
    assert _parse_sai2_zone_value(raw)["open"] is False


# ---------------------------------------------------------------------------
# Zone naming heuristic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone_name", "expected"),
    [
        ("Basculante Garage", "garage_door"),
        ("Porta Ingresso", "door"),
        ("Finestra Cucina", "window"),
        ("Volumetrico Salone", "motion"),
        ("PIR Corridoio", "motion"),
        ("Sirena Esterna", "tamper"),
        ("Manomissione Centrale", "tamper"),
    ],
)
def test_device_class_is_inferred_from_the_zone_name(zone_name, expected):
    device_class = _guess_device_class(zone_name)

    assert device_class is not None
    assert device_class.value == expected


def test_unknown_zone_name_gets_no_device_class():
    assert _guess_device_class("Zona 7") is None


def test_device_class_matching_is_case_insensitive():
    assert _guess_device_class("PORTA GARAGE") == _guess_device_class("porta garage")
