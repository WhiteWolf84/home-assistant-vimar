"""The room pass of the discovery must not strip what the first pass read.

A full discovery runs two queries over the same devices: get_remote_devices,
which reads every status with its range (OPTIONALP), then get_room_devices,
which only exists to attach the rooms. The second one used to select every
status again - without the range - and _generate_device_list replaced the
whole status dict with what it got, so every device sitting in a room lost its
`status_range`; a device in no room kept it. Seen on the plant on 2026-11-10:
none of the 13 sensors with a room had the attribute, all 24 without did.

The range is not decoration: sensor.not_a_reading() uses "min=0|max=1" to tell
a flag from a measurement, so on a meter placed in a room that check never
fired.

The room query now returns one row per device - its ID and its rooms - and the
room pass sets the rooms and nothing else.
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink.device_queries import (  # noqa: E402
    get_remote_devices_query,
    get_room_devices_query,
)
from vimarlink.vimarlink import VimarLink, VimarProject  # noqa: E402

pytestmark = pytest.mark.no_ha  # No HA required

IN_A_ROOM = "10"
IN_NO_ROOM = "20"
RANGE = "min=0|max=1"


def _remote_row(object_id: str, status_id: str) -> dict:
    return {
        "room_ids": "",
        "object_id": object_id,
        "object_name": "MISURATORE CUCINA",
        "object_type": "CH_Misuratore",
        "status_id": status_id,
        "status_name": "dynamic_mode",
        "status_range": RANGE,
        "status_value": "0",
    }


def _project() -> VimarProject:
    link = VimarLink("https", "192.168.1.1", 443, "user", "pass")

    def fake_sql(select: str):
        if "_DPAD_DBCONSTANT_GROUP_MAIN" in select:
            return [{"id": "5", "name": "CUCINA"}]
        if "GROUP_CONCAT" in select:
            # What the web server returns for the room query: the columns the
            # query selects, whatever they are when the query changes.
            row = {"room_ids": "5", "object_id": IN_A_ROOM}
            if "status_name" in select:
                row.update(
                    object_name="MISURATORE CUCINA",
                    object_type="CH_Misuratore",
                    status_id="11",
                    status_name="dynamic_mode",
                    status_value="0",
                )
            return [row]
        if "DPAD_WEB_PHPCLASS" in select:
            return [_remote_row(IN_A_ROOM, "11"), _remote_row(IN_NO_ROOM, "21")]
        return None  # the SAI2 queries: no alarm on this web server

    link._request_vimar_sql = fake_sql
    return VimarProject(link)


def test_the_room_query_asks_for_rooms_only():
    """One row per device: nothing about statuses to write back over them."""
    query = get_room_devices_query("5,6", 0, 300)

    assert "status" not in query.lower()
    assert "GROUP BY o2.ID\n" in query


def test_a_device_in_two_rooms_gets_both():
    link = VimarLink("https", "192.168.1.1", 443, "user", "pass")
    link._rooms = {"5": {"id": "5", "name": "CUCINA"}, "6": {"id": "6", "name": "PIANO TERRA"}}
    link._room_ids = "5,6"
    link._request_vimar_sql = lambda select: [
        {"object_id": IN_A_ROOM, "room_ids": "5,6"},
        {"object_id": "999", "room_ids": "5"},  # not a device of ours: ignored
    ]
    devices = {IN_A_ROOM: {"room_ids": [], "room_names": [], "room_name": "", "status": {}}}

    result = link.get_room_devices(devices, 0, 300)

    assert result == (devices, 2)
    assert devices[IN_A_ROOM]["room_names"] == ["CUCINA", "PIANO TERRA"]
    assert devices[IN_A_ROOM]["room_name"] == "CUCINA"
    assert list(devices) == [IN_A_ROOM]


def test_a_failed_room_query_is_a_failed_page():
    link = VimarLink("https", "192.168.1.1", 443, "user", "pass")
    link._room_ids = "5"
    link._request_vimar_sql = lambda select: None

    assert link.get_room_devices({}, 0, 300) is None


def test_the_room_is_attached():
    devices = _project().update(forced=True)

    assert devices[IN_A_ROOM]["room_names"] == ["CUCINA"]
    assert devices[IN_NO_ROOM]["room_names"] == []


def test_a_device_in_no_room_keeps_its_status_range():
    devices = _project().update(forced=True)

    assert devices[IN_NO_ROOM]["status"]["dynamic_mode"]["status_range"] == RANGE


def test_a_device_in_a_room_keeps_its_status_range():
    devices = _project().update(forced=True)

    assert devices[IN_A_ROOM]["status"]["dynamic_mode"].get("status_range") == RANGE


@pytest.mark.parametrize(
    "query",
    [get_remote_devices_query(0, 300), get_room_devices_query("5,6", 0, 300)],
    ids=["remote_devices", "room_devices"],
)
def test_a_paged_query_has_a_defined_order(query):
    """Without ORDER BY, two pages may repeat or skip rows."""
    assert "ORDER BY" in query.upper()
