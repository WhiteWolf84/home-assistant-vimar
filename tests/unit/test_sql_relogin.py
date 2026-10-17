"""_request_vimar_sql logs in again after an LGMG-* reply dropped the session.

Callers outside the coordinator poll (the SAI2 confirmation loop) never call
check_login() themselves: before the fix every request after the first
LGMG-3019 went out with sessionid "None" and was rejected again.
"""

import os
import sys
from xml.etree import ElementTree

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink.exceptions import VimarConnectionError  # noqa: E402
from vimarlink.vimarlink import VimarLink  # noqa: E402

pytestmark = pytest.mark.no_ha  # No HA required

LGMG = ElementTree.fromstring("<r><result>LGMG-3019</result><payload>Unknown-Payload</payload></r>")
OK = ElementTree.fromstring(
    "<r><result>DPCM-0000</result>"
    "<payload>Response: DBMG-000\nNextRows: 1\nRow000001: 'gid','current_value'\n"
    "Row000002: '1672','00000001'\n</payload></r>"
)


@pytest.fixture
def link():
    return VimarLink("https", "192.168.1.1", 443, "user", "pass")


def test_lgmg_drops_session_and_next_request_logs_in(link, monkeypatch):
    conn = link._connection
    conn._session_id = "old"
    logins = []

    def fake_login():
        logins.append(1)
        conn._session_id = "new"

    monkeypatch.setattr(conn, "login", fake_login)
    sent = []
    replies = iter([LGMG, OK])

    def fake_soap(post):
        sent.append(post)
        return next(replies)

    monkeypatch.setattr(link, "_request_vimar_soap", fake_soap)

    assert link._request_vimar_sql("SELECT 1") is None
    assert conn._session_id is None
    assert link._request_vimar_sql("SELECT 1") is not None
    assert logins == [1]
    assert "<sessionid>new</sessionid>" in sent[1]
    assert "<sessionid>None</sessionid>" not in "".join(sent)


def test_failed_relogin_returns_none_without_request(link, monkeypatch):
    conn = link._connection
    conn._session_id = None

    def fail_login():
        raise VimarConnectionError("down")

    monkeypatch.setattr(conn, "login", fail_login)
    monkeypatch.setattr(
        link, "_request_vimar_soap", lambda post: pytest.fail("request sent without session")
    )
    assert link._request_vimar_sql("SELECT 1") is None
