"""SAI2 area values: the poll reads NULL as disarmed, the raw read does not.

get_sai2_area_values() has always turned an empty/NULL CURRENT_VALUE into
'00000000' (disarmed) for the general poll, and that stays as it is. Confirming
a disarm must not fall for it, so get_sai2_area_raw_values() keeps NULL as None.
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink.vimarlink import VimarLink  # noqa: E402

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


@pytest.mark.parametrize("method", ["get_sai2_area_values", "get_sai2_area_raw_values"])
def test_failed_query_and_empty_ids(link, monkeypatch, method):
    assert getattr(link, method)([]) == {}
    monkeypatch.setattr(link, "_request_vimar_sql", lambda select: None)
    assert getattr(link, method)(["7560"]) is None
