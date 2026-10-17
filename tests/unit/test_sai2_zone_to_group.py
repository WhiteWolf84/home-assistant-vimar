"""Which area a SAI2 zone is named after (get_sai2_zone_to_group).

Rows shaped after the web server dump of 2019-05-19: the siren tamper zone
belongs to every area, and "Buona Notte" combines zones of the other three.
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink.vimarlink import VimarLink  # noqa: E402

pytestmark = pytest.mark.no_ha  # No HA required

GIORNO, NOTTE, GARAGE, BUONA_NOTTE = "1276", "1331", "1379", "1406"
SIRENA, SALA, MATRIMON, GARAGE_VOL = "1289", "1324", "1351", "1392"

GROUPS = {
    GIORNO: {"name": "Reparto Giorno", "index": 1},
    NOTTE: {"name": "Reparto Notte", "index": 2},
    GARAGE: {"name": "Esterno/Garage", "index": 3},
    BUONA_NOTTE: {"name": "Buona Notte", "index": 4},
}

MEMBERS = {
    GIORNO: [SIRENA, SALA],
    NOTTE: [SIRENA, MATRIMON],
    GARAGE: [SIRENA, GARAGE_VOL],
    BUONA_NOTTE: [SIRENA, SALA, MATRIMON, GARAGE_VOL],
}


def _link(monkeypatch, members):
    rows = [
        {"GID": gid, "GNAME": "x", "ZID": zid, "ZNAME": "z"}
        for gid, zids in members.items()
        for zid in zids
    ]
    link = VimarLink("https", "192.168.1.1", 443, "user", "pass")
    monkeypatch.setattr(link, "_request_vimar_sql", lambda select: rows)
    return link


def test_a_new_combined_area_does_not_take_the_zones(monkeypatch):
    mapping = _link(monkeypatch, MEMBERS).get_sai2_zone_to_group(GROUPS)
    assert mapping == {SALA: GIORNO, MATRIMON: NOTTE, GARAGE_VOL: GARAGE}


def test_a_zone_in_every_area_belongs_to_the_control_unit(monkeypatch):
    three = {gid: GROUPS[gid] for gid in (GIORNO, NOTTE, GARAGE)}
    members = {gid: MEMBERS[gid] for gid in three}
    mapping = _link(monkeypatch, members).get_sai2_zone_to_group(three)
    assert SIRENA not in mapping


def test_a_single_area_keeps_its_zones(monkeypatch):
    one = {GIORNO: GROUPS[GIORNO]}
    mapping = _link(monkeypatch, {GIORNO: MEMBERS[GIORNO]}).get_sai2_zone_to_group(one)
    assert mapping == {SIRENA: GIORNO, SALA: GIORNO}


def test_unnamed_areas_are_ignored(monkeypatch):
    members = {**MEMBERS, "1500": [SALA]}  # unnamed area 5, not in GROUPS
    mapping = _link(monkeypatch, members).get_sai2_zone_to_group(GROUPS)
    assert mapping[SALA] == GIORNO
    assert SIRENA not in mapping
