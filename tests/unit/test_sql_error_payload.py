"""What the web server answers when a SELECT fails, and what is made of it.

ONE refused SELECT has been captured on the plant so far. On 2026-10-05, at
15:59:03 and 15:59:11, the web server refused a valid poll query (`SELECT ID
AS status_id, CURRENT_VALUE AS status_value FROM DPADD_OBJECT WHERE ID IN
(...)`) with the envelope kept below as REFUSED_2026_10_05, byte for byte. The
error code is in <result> (DBMG-308); the payload is the literal
"Unknown-Payload" and has no "Response:" line at all.

_request_vimar_sql() only looked at <result> for LGMG-* codes, so this envelope
went on to the row parser, which failed on "Unknown-Payload". The query did
end up as failed (None) - but by accident, and the log said so twice over:

    WARNING sql_parser: Transient SQL parse error: Missing :-character in
            response line: Unknown-Payload at line 39 - payload: Unknown-Payload
    WARNING vimarlink:  Received invalid data from SQL: <ns0:Envelope ...

neither line naming the one thing that matters, that the web server refused
the request with DBMG-308.

Read on the plant on 2026-11-10 (beta 2026.11.1b0, debug on): a SELECT on a
table that does not exist and a valid SELECT that matches no row get the same
answer, byte for byte - <result>DPCM-0000</result> and the payload kept below
as NOTHING_FOUND_2026_11_10. This is a LIMIT OF THE WEB SERVER: nothing in the
answer tells a failed SELECT from an empty one, so both are `[]` and no
warning is logged. It is not a defect that can be fixed on this side, and the
text of the statement is not to be used to guess.

The same capture shows "Response: DBMG-000" and <result>DPCM-0000</result> as
what a SELECT that went through carries. Both were inferred until then. They
are known for one plant and one firmware, so a "Response:" line or a result
code that differs is still reported - a WARNING with the value as received,
once - and still does not fail the poll when the rows can be read. A code
that differs fails the query only when the payload cannot be read either,
which is the case of the refusal above.
"""

import logging
import os
import sys
from xml.etree import ElementTree

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink import sql_parser  # noqa: E402
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
# Home Assistant log, 2026-11-10 22:15-22:26, "SQL answer without rows, as
# received": the payload of BOTH statements below, with <result>DPCM-0000.
NOTHING_FOUND_2026_11_10 = "Response: DBMG-000\nNextRows: 0\n"
MISSING_TABLE = "SELECT ID FROM TABELLA_CHE_NON_ESISTE;"
NO_MATCHING_ROW = "SELECT ID FROM DPADD_OBJECT WHERE ID = 999999999;"
# A shape nobody has seen: stands for "a Response line that is not the known one".
UNKNOWN_RESPONSE_LINE = "Response: DBMG-123"
UNKNOWN_SHAPE = f"{UNKNOWN_RESPONSE_LINE}\nNextRows: 0\n"


def _reply(payload: str, result: str = "DPCM-0000") -> ElementTree.Element:
    root = ElementTree.Element("r")
    ElementTree.SubElement(root, "result").text = result
    ElementTree.SubElement(root, "payload").text = payload
    return root


@pytest.fixture(autouse=True)
def _nothing_reported_yet():
    """An unexpected "Response:" line is logged once per process: start clean."""
    sql_parser._reported_responses.clear()
    yield
    sql_parser._reported_responses.clear()


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


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


def test_the_refused_select_is_recognised_by_its_result_code(link, caplog):
    """<result> first: the payload of a refused request is not rows to parse."""
    _refuse(link)

    with caplog.at_level(logging.DEBUG):
        link.execute_sql("SELECT 1")

    assert "Transient SQL parse error" not in caplog.text
    assert "Received invalid data from SQL" not in caplog.text
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "DBMG-308" in warnings[0]


def test_an_unknown_result_code_with_readable_rows_is_not_a_failure(link, caplog):
    """DPCM-0000 is confirmed on one firmware: another code must not fail the poll."""
    link._request_vimar_soap = lambda post: _reply(
        NO_ROWS + "Row000002: '1672','00000001'\n", result="DPCM-0001"
    )

    with caplog.at_level(logging.WARNING):
        first = link.execute_sql("SELECT 1")
        second = link.execute_sql("SELECT 1")

    assert first == second == [{"gid": "1672", "current_value": "00000001"}]
    warnings = _warnings(caplog)
    assert len(warnings) == 1, "reported at every request instead of once per code"
    assert "DPCM-0001" in warnings[0]


def test_a_diagnostic_never_carries_the_statement_or_the_session(link, caplog):
    link._request_vimar_soap = lambda post: _reply("x" * 5000, result="DBMG-999")
    statement = "SELECT ID FROM DPADD_OBJECT WHERE ID IN (" + ",".join(["4242"] * 400) + ")"

    with caplog.at_level(logging.DEBUG):
        assert link.execute_sql(statement) is None

    assert "4242,4242" not in caplog.text
    assert "session" not in caplog.text
    assert all(len(message) < 600 for message in _warnings(caplog))


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


# --- a limit of the web server: a failed SELECT looks like an empty one -----


@pytest.mark.parametrize("statement", [MISSING_TABLE, NO_MATCHING_ROW])
def test_a_missing_table_and_no_matching_row_are_the_same_answer(link, caplog, statement):
    """Captured on the plant: one answer for both, so one outcome for both.

    Not a defect to fix here. If this ever has to tell the two apart, it is
    the web server's answer that must have changed - not the statement that
    must be inspected.
    """
    link._request_vimar_soap = lambda post: _reply(NOTHING_FOUND_2026_11_10)

    with caplog.at_level(logging.WARNING):
        assert link.execute_sql(statement) == []

    assert caplog.records == [], "the answer of a SELECT that went through is not reported"


def test_an_answer_without_rows_is_logged_as_received_at_debug_level(link, caplog):
    """How the shape was read on the plant; it stays available for other firmwares."""
    link._request_vimar_soap = lambda post: _reply(NOTHING_FOUND_2026_11_10)

    with caplog.at_level(logging.DEBUG):
        link.execute_sql(NO_MATCHING_ROW)

    assert f"SQL answer without rows, as received: {NOTHING_FOUND_2026_11_10!r}" in caplog.text


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
    """DBMG-000 is confirmed on one firmware: another line must not fail the poll."""
    link._request_vimar_soap = lambda post: _reply(UNKNOWN_SHAPE)

    assert link.execute_sql("SELECT ID FROM NO_SUCH_TABLE") == []


def test_an_unknown_response_line_is_reported_as_received(link, caplog):
    link._request_vimar_soap = lambda post: _reply(UNKNOWN_SHAPE)

    with caplog.at_level(logging.WARNING):
        link.execute_sql("SELECT ID FROM NO_SUCH_TABLE")

    assert any(UNKNOWN_RESPONSE_LINE in message for message in _warnings(caplog))


def test_an_unknown_response_line_is_reported_once(link, caplog):
    """Every 8 s otherwise, if the known line were wrong for some firmware."""
    link._request_vimar_soap = lambda post: _reply(UNKNOWN_SHAPE)

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            link.execute_sql("SELECT ID FROM NO_SUCH_TABLE")

    assert len(_warnings(caplog)) == 1


# --- rows -------------------------------------------------------------------


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
