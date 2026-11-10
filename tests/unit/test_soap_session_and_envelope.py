"""Commands need a live session too, and what goes into an envelope is escaped.

Only the SQL path used to look after its session: _request_vimar_sql drops it
on an LGMG-* reply and logs in again when there is none (see
test_sql_relogin.py). The commands - SETVALUE, GETVALUE, the SAI2 set and
authenticate - did neither:

* after an invalidation they were sent with the literal session id "None";
* an LGMG-* reply to a SETVALUE was not recognised, the stale session was kept
  and the write was lost without a useful log line: Home Assistant showed the
  optimistic value for the 15 s of the write-guard, then went back.

Every command now logs in first when there is no session, and drops the
session on an LGMG-* reply. SETVALUE and GETVALUE are then sent ONCE more
after a new login; the SAI2 services never are - a command to the control unit
is not repeated on a guess. None of this was observed on the plant: no write
measured there (2026-11-10) ever got an LGMG-* reply.

The envelopes are built by string formatting and used to have no escaping: a
`<` or `&` in a statement broke the XML, and a PIN - which reaches VimarLink
from a service call's `code` - could close its own element and add others.
"""

import logging
import os
import sys
from xml.etree import ElementTree

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink import device_queries  # noqa: E402
from vimarlink.exceptions import VimarConnectionError  # noqa: E402
from vimarlink.vimarlink import VimarLink  # noqa: E402

pytestmark = pytest.mark.no_ha  # No HA required

SESSION = "SESSION-ABC-123"


def _reply(result: str = "DPCM-0000", payload: str = "NO-PAYLOAD") -> ElementTree.Element:
    root = ElementTree.Element("r")
    ElementTree.SubElement(root, "result").text = result
    ElementTree.SubElement(root, "payload").text = payload
    return root


@pytest.fixture
def link(monkeypatch):
    link = VimarLink("https", "192.168.1.1", 443, "user", "pass")
    conn = link._connection

    def fake_login():
        conn._session_id = "fresh"

    monkeypatch.setattr(conn, "login", fake_login)
    return link


def _capture(link, replies=None):
    """Record every envelope sent; answer with `replies`, then with an OK."""
    sent: list[str] = []
    queue = list(replies or [])

    def fake_soap(post):
        sent.append(post)
        return queue.pop(0) if queue else _reply()

    link._request_vimar_soap = fake_soap
    return sent


def _elements(envelope: str, tag: str) -> list[ElementTree.Element]:
    root = ElementTree.fromstring(envelope)
    return [el for el in root.iter() if el.tag.rsplit("}", 1)[-1] == tag]


COMMANDS = {
    "setvalue": lambda link: link.set_device_status("8416", "26.0"),
    "getvalue": lambda link: link.request_value_refresh([8416]),
    "sai2_set": lambda link: link.set_sai2_status(1, 1, "0000"),
    "sai2_authenticate": lambda link: link.authenticate_sai2_pin("0000"),
}


# --- session ----------------------------------------------------------------


@pytest.mark.parametrize("command", COMMANDS.values(), ids=COMMANDS.keys())
def test_a_command_without_a_session_logs_in_first(link, command):
    link._connection._session_id = None
    sent = _capture(link)

    command(link)

    assert sent, "nothing was sent"
    assert all("<sessionid>None</sessionid>" not in post for post in sent)
    assert "<sessionid>fresh</sessionid>" in sent[0]


def test_a_write_rejected_for_an_expired_session_drops_that_session(link):
    link._connection._session_id = "stale"
    _capture(link, [_reply("LGMG-3019", "Unknown-Payload")])

    link.set_device_status("8416", "26.0")

    assert link._connection.session_id != "stale"


def test_an_accepted_write_keeps_its_session(link):
    """The other side: nothing is dropped, nothing is sent twice."""
    link._connection._session_id = SESSION
    sent = _capture(link)

    link.set_device_status("8416", "26.0")

    assert link._connection.session_id == SESSION
    assert len(sent) == 1


# --- sent again, once -------------------------------------------------------

EXPIRED = ("LGMG-3019", "Unknown-Payload")
RESENT = {
    "setvalue": lambda link: link.set_device_status("8416", "26.0"),
    "getvalue": lambda link: link.request_value_refresh([8416]),
}


@pytest.mark.parametrize("command", RESENT.values(), ids=RESENT.keys())
def test_a_write_refused_for_an_expired_session_is_sent_again_after_a_login(link, command):
    link._connection._session_id = "stale"
    sent = _capture(link, [_reply(*EXPIRED)])

    command(link)

    assert len(sent) == 2
    assert "<sessionid>stale</sessionid>" in sent[0]
    assert "<sessionid>fresh</sessionid>" in sent[1]
    assert sent[1] == sent[0].replace("stale", "fresh")


def test_the_outcome_of_a_resent_write_says_so(link):
    link._connection._session_id = "stale"
    _capture(link, [_reply(*EXPIRED)])

    outcome = link.set_device_status("8416", "26.0")

    assert outcome.accepted and outcome.resent and not outcome.rejected
    assert outcome.sent_at is not None


def test_a_write_is_sent_again_only_once(link, caplog):
    """Refused again after the new login: given up, and said so."""
    link._connection._session_id = "stale"
    sent = _capture(link, [_reply(*EXPIRED), _reply(*EXPIRED), _reply()])

    with caplog.at_level(logging.WARNING):
        outcome = link.set_device_status("8416", "26.0")

    assert len(sent) == 2
    assert outcome.rejected and not outcome.accepted
    assert not link.is_logged()
    assert "given up" in caplog.text


def test_a_write_without_an_answer_is_neither_accepted_nor_rejected(link):
    """It may have been executed: nothing is sent again, nothing is assumed."""
    link._connection._session_id = SESSION
    sent = []

    def no_answer(post):
        sent.append(post)
        return False

    link._request_vimar_soap = no_answer

    outcome = link.set_device_status("8416", "26.0")

    assert len(sent) == 1
    assert not outcome.accepted and not outcome.rejected


def test_a_write_that_cannot_log_in_is_not_sent(link, monkeypatch):
    link._connection._session_id = None

    def no_login():
        raise VimarConnectionError("Error during login: timeout")

    monkeypatch.setattr(link._connection, "login", no_login)
    sent = _capture(link)

    outcome = link.set_device_status("8416", "26.0")

    assert sent == []
    assert outcome.not_sent and outcome.rejected


NEVER_RESENT = {
    "sai2_set": lambda link: link.set_sai2_status(1, 1, "0000"),
    "sai2_authenticate": lambda link: link._authenticate_sai2_once("0000"),
}


@pytest.mark.parametrize("command", NEVER_RESENT.values(), ids=NEVER_RESENT.keys())
def test_a_sai2_request_is_never_sent_twice(link, command):
    """The session is dropped for the next request; this one is not repeated."""
    link._connection._session_id = "stale"
    sent = _capture(link, [_reply(*EXPIRED)])

    assert command(link) == "LGMG-3019"

    assert len(sent) == 1
    assert not link.is_logged()


# --- envelopes --------------------------------------------------------------


def test_a_plain_statement_makes_a_well_formed_envelope(link):
    link._connection._session_id = SESSION
    sent = _capture(link)

    link.execute_sql("SELECT ID FROM DPADD_OBJECT WHERE NAME = 'x'")

    (statement,) = _elements(sent[0], "statement")
    assert statement.text == "SELECT ID FROM DPADD_OBJECT WHERE NAME = 'x'"


def test_a_statement_with_xml_characters_makes_a_well_formed_envelope(link):
    link._connection._session_id = SESSION
    sent = _capture(link)
    select = "SELECT ID FROM DPADD_OBJECT WHERE ID < 100 AND NAME != 'a&b'"

    link.execute_sql(select)

    (statement,) = _elements(sent[0], "statement")
    assert statement.text == select


def test_escaping_leaves_every_query_of_the_integration_as_it_was(link):
    """None of them has <, > or &: what is sent must not change by one byte."""
    link._connection._session_id = SESSION
    sent = _capture(link)
    queries = [
        device_queries.get_room_ids_query(),
        device_queries.get_room_devices_query("330,461", 0, 300),
        device_queries.get_remote_devices_query(0, 300),
        device_queries.get_device_status_query("342"),
        device_queries.get_status_only_query([345, 347]),
        device_queries.get_sai2_groups_query(),
        device_queries.get_sai2_zones_query(),
        device_queries.get_sai2_area_values_query(["1672"]),
        device_queries.get_sai2_zone_to_group_query(),
    ]

    for query in queries:
        link.execute_sql(query)

    for query, post in zip(queries, sent, strict=True):
        as_before = (
            query.replace("\r\n", " ")
            .replace("\n", " ")
            .replace('"', "&apos;")
            .replace("'", "&apos;")
        )
        assert f"<statement>{as_before}</statement>" in post
        assert f"<statement-len>{len(as_before)}</statement-len>" in post


@pytest.mark.parametrize(
    "send",
    [
        lambda link, pin: link.authenticate_sai2_pin(pin),
        lambda link, pin: link.set_sai2_status(1, 1, pin),
    ],
    ids=["authenticate", "set"],
)
def test_a_pin_cannot_add_elements_to_the_envelope(link, send):
    """Either the PIN is refused before anything is sent, or it stays a PIN."""
    link._connection._session_id = SESSION
    sent = _capture(link)
    pin = "0000</pin><command>0</command><pin>0"

    send(link, pin)

    for post in sent:
        assert len(_elements(post, "pin")) == 1
        assert len(_elements(post, "command")) <= 1


# --- log --------------------------------------------------------------------

# The refusal logged on the plant on 2026-10-05 15:59:03 (see
# test_sql_error_payload.py): the request that got it was dumped, whole, in the
# "Received invalid data from SQL" warning.
REFUSED_2026_10_05 = (
    '<ns0:Envelope xmlns:ns0="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:ns1="urn:xmethods-dpadws"><ns0:Body '
    'ns0:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    "<ns1:service-databasesocketoperationResponse>"
    "<result-len>Unknown-Result_Length</result-len><result>DBMG-308</result>"
    "<payload-len>Unknown-Payload_Length</payload-len><payload>Unknown-Payload</payload>"
    "</ns1:service-databasesocketoperationResponse></ns0:Body></ns0:Envelope>"
)


def test_the_session_id_is_not_logged_with_a_refused_request(link, caplog):
    """The session id is a bearer credential, and logs get attached to issues."""
    link._connection._session_id = SESSION
    _capture(link, [ElementTree.fromstring(REFUSED_2026_10_05)])

    with caplog.at_level(logging.DEBUG):
        assert link.execute_sql("SELECT 1") is None

    assert SESSION not in caplog.text
