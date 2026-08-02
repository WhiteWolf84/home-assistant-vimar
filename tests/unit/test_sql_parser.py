"""The SQL payload parser (NO Home Assistant required).

parse_sql_payload is the single entry point for EVERY piece of data coming
from the VIMAR web server: device discovery, slim polling, SAI2 alarm state.
Anything it mis-parses becomes a wrong entity state, and anything it raises
surfaces as a generic "Error communicating with API" that hides the real
cause. It had no test coverage at all.

Payload shape (the web server answers a SELECT with):

    Response: DBMG-000
    NextRows: 2
    Row000001: 'ID','NAME'          <- header row, defines the dict keys
    Row000002: '768','on/off'       <- data rows
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink.sql_parser import parse_sql_payload  # noqa: E402

pytestmark = pytest.mark.no_ha  # No HA required


def _payload(*lines):
    return "\n".join(("Response: DBMG-000", "NextRows: %d" % len(lines), *lines)) + "\n"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_parses_rows_into_dicts_keyed_by_the_header():
    result = parse_sql_payload(
        _payload(
            "Row000001: 'status_id','status_value'",
            "Row000002: '769','1'",
            "Row000003: '770','75'",
        )
    )

    assert result == [
        {"status_id": "769", "status_value": "1"},
        {"status_id": "770", "status_value": "75"},
    ]


def test_header_only_payload_is_an_empty_result_not_an_error():
    """A SELECT matching no row is normal (e.g. an installation with no SAI2)."""
    assert parse_sql_payload(_payload("Row000001: 'ID','NAME'")) == []


def test_values_keep_their_spaces_and_punctuation():
    """Device names carry spaces, slashes and colons; none may be mangled."""
    result = parse_sql_payload(
        _payload(
            "Row000001: 'name','value'",
            "Row000002: 'LUCE 11 CUCINA','stop up/stop down: 1'",
        )
    )

    assert result == [{"name": "LUCE 11 CUCINA", "value": "stop up/stop down: 1"}]


def test_empty_values_are_preserved_as_empty_strings():
    """An empty CURRENT_VALUE must stay '' and not vanish from the dict."""
    result = parse_sql_payload(_payload("Row000001: 'ID','NAME'", "Row000002: '1',''"))

    assert result == [{"ID": "1", "NAME": ""}]


def test_response_and_nextrows_lines_are_metadata_not_data():
    result = parse_sql_payload(_payload("Row000001: 'ID'", "Row000002: '1'"))

    assert result == [{"ID": "1"}]


def test_blank_lines_are_skipped():
    result = parse_sql_payload("Response: DBMG-000\n\nRow000001: 'ID'\n\nRow000002: '1'\n\n")

    assert result == [{"ID": "1"}]


# ---------------------------------------------------------------------------
# Failure modes: must return None, never raise
# ---------------------------------------------------------------------------


def test_none_payload_returns_none():
    """An empty <payload> tag yields None; calling .split() on it used to crash."""
    assert parse_sql_payload(None) is None


def test_empty_payload_returns_none():
    assert parse_sql_payload("") is None


def test_unparseable_line_returns_none_without_raising():
    """A truncated/garbled response must degrade to None, not explode upstream."""
    assert parse_sql_payload("Unknown-Payload") is None


def test_row_with_more_values_than_header_returns_none():
    """Malformed row: better no data than data assigned to the wrong keys."""
    assert parse_sql_payload("Row000001: 'ID'\nRow000002: '1','extra'") is None


def test_row_with_fewer_values_than_header_is_partial_by_design():
    """Documents current behaviour: a short row yields a partial dict.

    The missing keys simply do not appear; consumers must use .get() rather
    than indexing. Pinned here so a future parser change is a conscious one.
    """
    assert parse_sql_payload("Row000001: 'ID','NAME'\nRow000002: '1'") == [{"ID": "1"}]


@pytest.mark.parametrize(
    "payload",
    [
        "Response: DBMG-000",
        "NextRows: 5",
        "Response: DBMG-000\nNextRows: 0",
    ],
)
def test_metadata_only_payloads_yield_no_rows(payload):
    assert parse_sql_payload(payload) == []
