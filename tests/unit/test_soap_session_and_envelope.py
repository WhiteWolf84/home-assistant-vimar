"""Commands need a live session too, and what goes into an envelope is escaped.

Only the SQL path looks after its session: _request_vimar_sql drops it on an
LGMG-* reply and logs in again when there is none (see test_sql_relogin.py).
The commands - SETVALUE, GETVALUE, the SAI2 set and authenticate - do neither:

* after an invalidation they are sent with the literal session id "None";
* an LGMG-* reply to a SETVALUE is not recognised, the stale session is kept
  and the write is lost without a useful log line: Home Assistant shows the
  optimistic value for the 15 s of the write-guard, then goes back.

The envelopes are built by string formatting with no escaping: a `<` or `&` in
a statement breaks the XML, and a PIN - which reaches VimarLink from a service
call's `code` - can close its own element and add others.
"""

import logging
import os
import sys
from xml.etree import ElementTree

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

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
    "sai2_set": lambda link: link.set_sai2_status(1, 1, "1234"),
    "sai2_authenticate": lambda link: link.authenticate_sai2_pin("1234"),
}


# --- session ----------------------------------------------------------------


@pytest.mark.xfail(
    strict=True, reason="review 2026-11 #3.1: commands are sent with sessionid 'None'"
)
@pytest.mark.parametrize("command", COMMANDS.values(), ids=COMMANDS.keys())
def test_a_command_without_a_session_logs_in_first(link, command):
    link._connection._session_id = None
    sent = _capture(link)

    command(link)

    assert sent, "nothing was sent"
    assert all("<sessionid>None</sessionid>" not in post for post in sent)
    assert "<sessionid>fresh</sessionid>" in sent[0]


@pytest.mark.xfail(
    strict=True, reason="review 2026-11 #3.1: an LGMG reply to a SETVALUE keeps the stale session"
)
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


# --- envelopes --------------------------------------------------------------


def test_a_plain_statement_makes_a_well_formed_envelope(link):
    link._connection._session_id = SESSION
    sent = _capture(link)

    link.execute_sql("SELECT ID FROM DPADD_OBJECT WHERE NAME = 'x'")

    (statement,) = _elements(sent[0], "statement")
    assert statement.text == "SELECT ID FROM DPADD_OBJECT WHERE NAME = 'x'"


@pytest.mark.xfail(
    strict=True, reason="review 2026-11 #2.5: SQL statements are not XML-escaped (< > &)"
)
def test_a_statement_with_xml_characters_makes_a_well_formed_envelope(link):
    link._connection._session_id = SESSION
    sent = _capture(link)
    select = "SELECT ID FROM DPADD_OBJECT WHERE ID < 100 AND NAME != 'a&b'"

    link.execute_sql(select)

    (statement,) = _elements(sent[0], "statement")
    assert statement.text == select


@pytest.mark.xfail(strict=True, reason="review 2026-11 #2.5: the SAI2 PIN is not escaped")
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
    pin = "1234</pin><command>0</command><pin>1"

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


@pytest.mark.xfail(
    strict=True,
    reason="review 2026-11 #3.9: the session id is logged with the failed SQL request",
)
def test_the_session_id_is_not_logged_with_a_refused_request(link, caplog):
    """The session id is a bearer credential, and logs get attached to issues."""
    link._connection._session_id = SESSION
    _capture(link, [ElementTree.fromstring(REFUSED_2026_10_05)])

    with caplog.at_level(logging.DEBUG):
        assert link.execute_sql("SELECT 1") is None

    assert SESSION not in caplog.text
