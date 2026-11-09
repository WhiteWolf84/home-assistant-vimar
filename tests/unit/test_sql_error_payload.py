"""What the web server answers when a SELECT fails, and what is made of it.

ONE refused SELECT has been captured on the plant so far. On 2026-10-05, at
15:59:03 and 15:59:11, the web server refused a valid poll query (`SELECT ID
AS status_id, CURRENT_VALUE AS status_value FROM DPADD_OBJECT WHERE ID IN
(...)`) with the envelope kept below as REFUSED_2026_10_05, byte for byte. The
error code is in <result> (DBMG-308); the payload is the literal
"Unknown-Payload" and has no "Response:" line at all.

_request_vimar_sql() only looks at <result> for LGMG-* codes, so this envelope
goes on to the row parser, which fails on "Unknown-Payload". The query does
end up as failed (None) - but by accident, and the log says so twice over:

    WARNING sql_parser: Transient SQL parse error: Missing :-character in
            response line: Unknown-Payload at line 39 - payload: Unknown-Payload
    WARNING vimarlink:  Received invalid data from SQL: <ns0:Envelope ...

neither line naming the one thing that matters, that the web server refused
the request with DBMG-308.

What is NOT known (2026-11-10): the shape of the answer to a SELECT on a
table that does not exist. Through vimar.exec_vimar_sql it comes back as `[]`
with no warning, exactly like a valid SELECT that finds nothing, so its
payload has only lines the parser skips ("Response:", "NextRows:") - with
which values, nobody has read yet. "Response: DBMG-000" as the line of a
successful answer comes from the example in parse_sql_payload's docstring
(inherited from the upstream project) and from the fixtures copied from it; it
appears in none of the plant's saved logs. Until it is read on the plant, a
"Response:" line that is not the known one must be reported - a WARNING with
the line as received - and must not fail the poll.

The tests marked xfail describe the behaviour to reach; they are strict, so
they fail - and must lose the marker - as soon as the fix lands.
"""

import logging
import os
import sys
from xml.etree import ElementTree

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink.exceptions import VimarApiError  # noqa: E402
from vimarlink.sql_parser import parse_sql_payload  # noqa: E402
from vimarlink.vimarlink import MAX_ROWS_PER_REQUEST, VimarLink  # noqa: E402

pytestmark = pytest.mark.no_ha  # No HA required

# Home Assistant log, 2026-10-05 15:59:03 - as logged, not retyped.
REFUSED_2026_10_05 = (
    '<ns0:Envelope xmlns:ns0="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:ns1="urn:xmethods-dpadws"><ns0:Body '
    'ns0:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    "<ns1:service-databasesocketoperationResponse>"
    "<result-len>Unknown-Result_Length</result-len><result>DBMG-308</result>"
    "<payload-len>Unknown-Payload_Length</payload-len><payload>Unknown-Payload</payload>"
    "</ns1:service-databasesocketoperationResponse></ns0:Body></ns0:Envelope>"
)

NO_ROWS = "Response: DBMG-000\nNextRows: 1\nRow000001: 'gid','current_value'\n"
# A shape nobody has seen: stands for "a Response line that is not the known one".
UNKNOWN_RESPONSE_LINE = "Response: DBMG-123"
UNKNOWN_SHAPE = f"{UNKNOWN_RESPONSE_LINE}\nNextRows: 0\n"

RESULT_FIRST = "review 2026-11 #1.2: <result> is only checked for LGMG, DBMG-* goes to the parser"
UNKNOWN_LINE = "review 2026-11 #1.2: the 'Response:' line of the payload is never looked at"


def _reply(payload: str) -> ElementTree.Element:
    root = ElementTree.Element("r")
    ElementTree.SubElement(root, "result").text = "DPCM-0000"
    ElementTree.SubElement(root, "payload").text = payload
    return root


@pytest.fixture
def link():
    link = VimarLink("https", "192.168.1.1", 443, "user", "pass")
    link._connection._session_id = "session"
    return link


def _refuse(link):
    link._request_vimar_soap = lambda post: ElementTree.fromstring(REFUSED_2026_10_05)


def _discovery_page(first_object_id: int, rows: int) -> str:
    header = (
        "Row000001: 'room_ids','object_id','object_name','object_type',"
        "'status_id','status_name','status_range','status_value'\n"
    )
    body = "".join(
        f"Row{n + 2:06d}: '','{first_object_id + n}','LUCE {n}','CH_Main_Automation',"
        f"'{90000 + first_object_id + n}','on/off','min=0|max=1','0'\n"
        for n in range(rows)
    )
    return f"Response: DBMG-000\nNextRows: {rows + 1}\n{header}{body}"


# --- the refusal captured on the plant --------------------------------------


def test_the_refused_select_is_a_failed_query(link):
    """Holds today (by way of the parser failing) and must keep holding."""
    _refuse(link)

    assert link.execute_sql("SELECT ID FROM DPADD_OBJECT WHERE ID IN (1)") is None


def test_the_refused_select_is_not_a_session_problem(link):
    """DBMG-308 is not LGMG-*: the session is kept, nobody logs in again."""
    _refuse(link)

    link.execute_sql("SELECT 1")

    assert link._connection.session_id == "session"


@pytest.mark.xfail(strict=True, reason=RESULT_FIRST)
def test_the_refused_select_is_recognised_by_its_result_code(link, caplog):
    """<result> first: the payload of a refused request is not rows to parse."""
    _refuse(link)

    with caplog.at_level(logging.DEBUG):
        link.execute_sql("SELECT 1")

    assert "Transient SQL parse error" not in caplog.text
    assert "Received invalid data from SQL" not in caplog.text
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "DBMG-308" in warnings[0]


def test_sai2_areas_see_the_refused_select_as_a_failed_query(link):
    """None = "the query failed" (values kept, 60 s tolerance), not `{}`."""
    _refuse(link)

    assert link.get_sai2_area_raw_values(["1672", "1727"]) is None


def test_discovery_does_not_end_quietly_on_a_refused_page(link):
    """Page 1 is full, page 2 is refused: that is not "the last page"."""
    replies = iter(
        [
            _reply(_discovery_page(1000, MAX_ROWS_PER_REQUEST)),
            ElementTree.fromstring(REFUSED_2026_10_05),
        ]
    )
    link._request_vimar_soap = lambda post: next(replies)

    with pytest.raises(VimarApiError):
        link.get_paged_results(link.get_remote_devices, {})


# --- a shape nobody has read yet --------------------------------------------


def test_a_valid_query_without_rows_is_an_empty_list():
    """Not under discussion: this is what "found nothing" looks like."""
    assert parse_sql_payload(NO_ROWS) == []


def test_the_known_response_line_is_not_reported(link, caplog):
    link._request_vimar_soap = lambda post: _reply(NO_ROWS)

    with caplog.at_level(logging.WARNING):
        assert link.execute_sql("SELECT 1") == []

    assert caplog.records == []


def test_an_unknown_response_line_does_not_fail_the_query(link):
    """Until the real shape is read on the plant, the poll must not fail on it."""
    link._request_vimar_soap = lambda post: _reply(UNKNOWN_SHAPE)

    assert link.execute_sql("SELECT ID FROM NO_SUCH_TABLE") == []


@pytest.mark.xfail(strict=True, reason=UNKNOWN_LINE)
def test_an_unknown_response_line_is_reported_as_received(link, caplog):
    link._request_vimar_soap = lambda post: _reply(UNKNOWN_SHAPE)

    with caplog.at_level(logging.WARNING):
        link.execute_sql("SELECT ID FROM NO_SUCH_TABLE")

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(UNKNOWN_RESPONSE_LINE in message for message in warnings)


# --- rows -------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True, reason="review 2026-11 #1.2: one malformed row discards the whole payload"
)
def test_one_malformed_row_does_not_discard_the_others():
    """A row with more values than columns: an apostrophe-comma inside a name."""
    payload = (
        "Response: DBMG-000\nNextRows: 4\n"
        "Row000001: 'ID','NAME'\n"
        "Row000002: '1','good'\n"
        "Row000003: '2','bro','ken'\n"
        "Row000004: '3','also good'\n"
    )

    rows = parse_sql_payload(payload)

    assert rows is not None
    assert {"ID": "1", "NAME": "good"} in rows
    assert {"ID": "3", "NAME": "also good"} in rows
