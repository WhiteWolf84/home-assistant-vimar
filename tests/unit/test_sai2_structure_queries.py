"""SAI2 structure queries against the web server's column naming (NO HA).

2026.10.0b4 did not start on a real installation: "Error communicating with
API: 'GID'" - get_sai2_devices() read row["GID"] and the row had no such key.
The b4 query selected table-qualified columns without an alias
(`SELECT G.GID, G.GNAME, ...`); every other JOIN query of the integration,
all working on the same web server, aliases every column. Checked on the real
web server: a plain `SELECT ID, NAME, MSP, CURRENT_VALUE FROM DPADD_OBJECT
WHERE TYPE='SAI2_GROUP'` answers with the keys ID / NAME / MSP / CURRENT_VALUE.

The fake web server below names each column the way that evidence implies: an
alias when there is one, otherwise the expression as written ("G.GID"). The
rows are the real installation's areas (2026-10-05). And whatever the names,
a SAI2 failure must leave the rest of the integration running.
"""

import os
import re
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink.device_queries import (  # noqa: E402
    get_sai2_groups_query,
    get_sai2_zone_to_group_query,
    get_sai2_zones_query,
)
from vimarlink.sql_parser import parse_sql_payload  # noqa: E402
from vimarlink.vimarlink import VimarLink, VimarProject, sai2_row  # noqa: E402

pytestmark = pytest.mark.no_ha  # No HA required

# The b4 query, as shipped.
B4_GROUPS_QUERY = """SELECT G.GID, G.GNAME, G.CID, G.CNAME, G.CURRENT_VALUE, O.MSP AS GINDEX
FROM DPAD_SAI2GATEWAY_SAI2GROUPCHILDREN AS G JOIN DPADD_OBJECT AS O ON O.ID = G.GID
ORDER BY G.GID, G.CID;"""

# Real installation, 2026-10-05: (GID, GNAME, MSP). Area 4 has no name.
AREAS = [
    ("11919", "Reparto Giorno", "1"),
    ("11974", "Reparto Notte", "2"),
    ("12022", "Esterno/Garage", "3"),
    ("12070", "", "4"),
]
STATES = ["Disinserito", "Inserito INT", "Inserito ON", "Inserito PAR"]


def _column_names(select: str) -> list[str]:
    """How the web server names each column: alias, else the expression."""
    columns = re.search(r"SELECT\s+(?:DISTINCT\s+)?(.*?)\s+FROM\s", select, re.S | re.I).group(1)
    names = []
    for item in columns.split(","):
        item = " ".join(item.split())
        alias = re.search(r"\s+AS\s+(\w+)$", item, re.I)
        names.append(alias.group(1) if alias else item)
    return names


def _payload(header: list[str], rows: list[list[str]]) -> str:
    lines = ["Response: DBMG-000", f"NextRows: {len(rows) + 1}"]
    for n, values in enumerate([header, *rows], start=1):
        lines.append(f"Row{n:06d}: '" + "','".join(values) + "'")
    return "\n".join(lines) + "\n"


def _group_rows() -> list[list[str]]:
    rows = []
    for gid, name, msp in AREAS:
        for k, state in enumerate(STATES, start=1):
            rows.append([gid, name, str(int(gid) + k), f"{name} ({state})", "0", msp])
    return rows


def _link_answering(rows: list[list[str]]) -> VimarLink:
    link = VimarLink("https", "192.168.1.1", 443, "user", "pass")
    link._request_vimar_sql = lambda select: parse_sql_payload(  # type: ignore[method-assign]
        _payload(_column_names(select), rows)
    )
    return link


def test_the_plain_query_checked_on_the_web_server_keeps_its_names():
    assert _column_names(
        "SELECT ID, NAME, MSP, CURRENT_VALUE FROM DPADD_OBJECT WHERE TYPE='SAI2_GROUP'"
    ) == ["ID", "NAME", "MSP", "CURRENT_VALUE"]


def test_the_b4_query_has_no_gid_column():
    """Why b4 failed: the key was "G.GID"."""
    assert _column_names(B4_GROUPS_QUERY)[:2] == ["G.GID", "G.GNAME"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (get_sai2_groups_query(), ["GID", "GNAME", "CID", "CNAME", "CURRENT_VALUE", "GINDEX"]),
        (get_sai2_zones_query(), ["ZID", "GNAME", "CID", "CNAME", "CURRENT_VALUE", "ZINDEX"]),
        (get_sai2_zone_to_group_query(), ["GID", "GNAME", "ZID", "ZNAME"]),
    ],
)
def test_every_sai2_structure_column_has_a_bare_name(query, expected):
    assert _column_names(query) == expected


def test_areas_load_with_the_web_server_column_names():
    groups = _link_answering(_group_rows()).get_sai2_devices()

    assert groups is not None
    assert {gid: (g["name"], g["index"]) for gid, g in groups.items()} == {
        "11919": ("Reparto Giorno", 1),
        "11974": ("Reparto Notte", 2),
        "12022": ("Esterno/Garage", 3),
    }  # the unnamed area 4 is ignored
    assert set(groups["11919"]["children"]) == set(STATES)


def test_qualified_column_names_are_still_read():
    """Defence in depth: even the b4 query's keys now parse."""
    link = VimarLink("https", "192.168.1.1", 443, "user", "pass")
    link._request_vimar_sql = lambda select: parse_sql_payload(  # type: ignore[method-assign]
        _payload(_column_names(B4_GROUPS_QUERY), _group_rows())
    )

    groups = link.get_sai2_devices()

    assert groups is not None and groups["11974"]["index"] == 2


def test_sai2_row_strips_the_qualifier():
    assert sai2_row({"G.GID": "1", "o.msp": "2", "GNAME": "x"}) == {
        "GID": "1",
        "MSP": "2",
        "GNAME": "x",
    }


# ---------------------------------------------------------------------------
# A SAI2 failure does not take the integration down
# ---------------------------------------------------------------------------


def _project(sai2_failure: Exception | None) -> VimarProject:
    link = MagicMock()
    devices = {"768": {"object_id": "768", "object_name": "LUCE SALA"}}
    link.get_paged_results.return_value = (devices, 1)
    if sai2_failure is not None:
        link.get_sai2_devices.side_effect = sai2_failure
    else:
        link.get_sai2_devices.return_value = {"11919": {"name": "G", "index": 1, "children": {}}}
        link.get_sai2_zones.return_value = None
        link.get_sai2_zone_to_group.return_value = None
        link.get_sai2_area_checked_values.return_value = {"11919": "00000000"}
    project = VimarProject(link)
    project.check_devices = MagicMock()  # type: ignore[method-assign]
    return project


def test_a_sai2_error_leaves_the_other_devices_loaded():
    project = _project(KeyError("GID"))

    devices = project.update(forced=True)

    assert devices and "768" in devices
    project.check_devices.assert_called_once()
    assert project.sai2_groups is None
    assert project.sai2_error == "KeyError('GID')"


def test_a_successful_load_clears_the_error():
    project = _project(None)
    project.sai2_error = "KeyError('GID')"

    project.update(forced=True)

    assert project.sai2_error is None
    assert project.sai2_groups == {"11919": {"name": "G", "index": 1, "children": {}}}
    assert project.sai2_area_values == {"11919": "00000000"}
