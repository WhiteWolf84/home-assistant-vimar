"""SQL payload parser for Vimar web server responses."""

from __future__ import annotations

import logging
from typing import NamedTuple

_LOGGER = logging.getLogger(__name__)

#: The "Response:" line of a SELECT the web server carried out. Captured on
#: the plant on 2026-11-10 (payload 'Response: DBMG-000\nNextRows: 0\n'); before
#: that it was only the example in the upstream project's docstring. One plant
#: and one firmware: it is still used to REPORT a line that differs, never to
#: reject the rows that came with it.
#:
#: It does NOT mean "the statement was valid". See the note below.
KNOWN_RESPONSE = "DBMG-000"

# A limit of the web server, not of this parser (plant, 2026-11-10): a SELECT
# on a table that does not exist and a valid SELECT that matches no row get
# the SAME answer - <result>DPCM-0000</result> and the payload
#
#     Response: DBMG-000
#     NextRows: 0
#
# byte for byte. Nothing in the answer tells the two apart, so both are an
# empty list here and a failed SELECT cannot be reported as such. Do not try
# to guess from the text of the statement: the web server is the only one that
# knows, and it does not say.

#: Longest piece of a web server answer quoted in a log line. The Home
#: Assistant log API cuts every line at about 16 KB, and a diagnostic is only
#: useful if the line it is on survives.
LOG_EXCERPT = 300

# "Response:" lines already reported, so that an unexpected one is logged once
# and not at every poll: if KNOWN_RESPONSE turned out to be wrong for some
# firmware, a warning every 8 seconds would bury everything else.
_reported_responses: set[str] = set()


class ParsedPayload(NamedTuple):
    """What a payload held, with nothing logged yet.

    `rows` is None when the payload could not be read at all - and `error`
    says why - or the list of rows, possibly empty.
    """

    rows: list[dict] | None
    #: The "Response:" line exactly as received, or None if there was none.
    response_line: str | None = None
    error: str | None = None
    #: Rows left out because they had more values than there are columns.
    malformed_rows: int = 0


def read_sql_payload(string: str | None) -> ParsedPayload:
    """Split a payload into rows without logging anything.

    For callers that have to decide what an unreadable payload means before
    anything is said about it (see VimarLink._request_vimar_sql, which first
    looks at the <result> code of the answer).
    """
    if not string:
        return ParsedPayload(None, error="empty payload")

    rows: list[dict] = []
    keys: list[str] = []
    response_line: str | None = None
    malformed_rows = 0

    for line in string.split("\n"):
        if not line:
            continue

        if ":" not in line:
            # Not a payload at all - e.g. the literal "Unknown-Payload" the web
            # server sends with a refused request.
            return ParsedPayload(
                None, response_line, f"Missing :-character in response line: {line}"
            )

        prefix, values = line.split(":", 1)
        prefix = prefix.strip()

        if prefix == "Response":
            response_line = line.strip()
            continue
        if prefix == "NextRows":
            continue

        fields = values.strip()[1:-1].split("','")

        if prefix == "Row000001":
            keys.extend(fields)
            continue

        if len(fields) > len(keys):
            # A value that itself contains `','` shifts every column after it.
            # That is one row lost, not a reason to throw away the others: it
            # used to raise IndexError and discard the whole payload - the
            # entire poll, for one name with an odd apostrophe in it.
            malformed_rows += 1
            continue

        row = dict(zip(keys, fields, strict=False))
        if row:
            rows.append(row)

    return ParsedPayload(rows, response_line, None, malformed_rows)


def report_response_line(response_line: str | None) -> None:
    """Log a "Response:" line that is not the known one, once per line.

    Nothing is rejected: see KNOWN_RESPONSE.
    """
    if response_line is None:
        return
    code = response_line.split(":", 1)[1].strip()
    if code == KNOWN_RESPONSE or response_line in _reported_responses:
        return
    _reported_responses.add(response_line)
    _LOGGER.warning(
        "SQL answer with an unexpected first line: %.*r (known: 'Response: %s'). "
        "Its rows are used as received; this line is logged once. Please report it",
        LOG_EXCERPT,
        response_line,
        KNOWN_RESPONSE,
    )


def parse_sql_payload(string: str | None) -> list[dict] | None:
    """Split string payload into dictionary array.

    FIX #20: aggiunto None guard in cima. Quando il webserver restituisce
    un tag payload vuoto, xml.find('.//payload').text e' None; chiamare
    None.split() sollevava AttributeError catturato dal BaseException handler
    e loggato come errore fuorviante. Ora restituisce None immediatamente.

    Example payload string:
    Response: DBMG-000
    NextRows: 2
    Row000001: 'MAIN_GROUPS'
    Row000002: '435,439,...'
    """
    if not string:
        return None

    parsed = read_sql_payload(string)

    if parsed.rows is None:
        _LOGGER.warning(
            "Transient SQL parse error: %.*s - payload: %.200s",
            LOG_EXCERPT,
            parsed.error,
            string,
        )
        return None

    report_response_line(parsed.response_line)

    if parsed.malformed_rows:
        _LOGGER.warning(
            "SQL answer: %d row(s) with more values than columns left out, "
            "%d row(s) kept - payload: %.200s",
            parsed.malformed_rows,
            len(parsed.rows),
            string,
        )

    if not parsed.rows and _LOGGER.isEnabledFor(logging.DEBUG):
        # Kept at debug level: it is how the shape above was read on the
        # plant, and how it can be read on another firmware.
        _LOGGER.debug("SQL answer without rows, as received: %.*r", LOG_EXCERPT, string)

    return parsed.rows
