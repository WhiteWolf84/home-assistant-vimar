"""SAI2 area values: what counts as a reading.

get_sai2_area_values() turns an empty/NULL CURRENT_VALUE into '00000000'; it
is now only used for the zones. get_sai2_area_raw_values() keeps NULL as None,
and get_sai2_area_checked_values() - used for the areas - keeps only valid
8-character bitmasks (is_valid_sai2_bitmask): anything else, including '0'
and a missing row, is None, i.e. unknown.
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink.vimarlink import VimarLink, is_valid_sai2_bitmask  # noqa: E402

pytestmark = pytest.mark.no_ha  # No HA required

ROWS = [
    {"gid": "7560", "current_value": "00000101"},
    {"gid": "7615", "current_value": ""},  # what the SQL parser yields for NULL
    {"gid": "7663"},  # column missing altogether
]


@pytest.fixture
def link(monkeypatch):
    link = VimarLink("https", "192.168.1.1", 443, "user", "pass")
    monkeypatch.setattr(link, "_request_vimar_sql", lambda select: [dict(r) for r in ROWS])
    return link


def test_poll_values_are_unchanged_null_reads_disarmed(link):
    assert link.get_sai2_area_values(["7560", "7615", "7663"]) == {
        "7560": "00000101",
        "7615": "00000000",
        "7663": "00000000",
    }


def test_raw_values_keep_null_as_none(link):
    assert link.get_sai2_area_raw_values(["7560", "7615", "7663"]) == {
        "7560": "00000101",
        "7615": None,
        "7663": None,
    }


@pytest.mark.parametrize(
    "method",
    ["get_sai2_area_values", "get_sai2_area_raw_values", "get_sai2_area_checked_values"],
)
def test_failed_query_and_empty_ids(link, monkeypatch, method):
    assert getattr(link, method)([]) == {}
    monkeypatch.setattr(link, "_request_vimar_sql", lambda select: None)
    assert getattr(link, method)(["7560"]) is None


@pytest.mark.parametrize("value", ["00000000", "00000101", "00001001", "00100011", "11111111"])
def test_valid_bitmasks(value):
    assert is_valid_sai2_bitmask(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "0",
        "1",
        "0 ",
        " 00000000",
        "0000000",
        "000000000",
        "0000000000000101",
        "0000010x",
        "0b000101",
        "NULL",
    ],
)
def test_invalid_bitmasks(value):
    assert not is_valid_sai2_bitmask(value)


def test_checked_values_keep_only_valid_bitmasks_and_every_requested_area(link, monkeypatch):
    rows = [*ROWS, {"gid": "9000", "current_value": "0"}]
    monkeypatch.setattr(link, "_request_vimar_sql", lambda select: [dict(r) for r in rows])

    assert link.get_sai2_area_checked_values(["7560", "7615", "7663", "9000", "9999"]) == {
        "7560": "00000101",
        "7615": None,  # NULL
        "7663": None,  # column missing
        "9000": None,  # '0', as after the 2026-10-05 web server restart
        "9999": None,  # no row at all
    }
