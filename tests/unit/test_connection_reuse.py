"""HTTP connection reuse in the VIMAR connection layer (NO HA required).

Regression: _request() built - and threw away - a fresh requests.Session for
every single call:

    with requests.Session() as s:
        s.mount("https://", HTTPAdapter())
        response = s.get(...)

That discards the connection pool the Session exists for, so every SOAP call
paid a full TCP connect plus a TLS handshake, on hardware that negotiates
RSA/AES256-SHA slowly. With an 8 s poll plus one GETVALUE per energy-meter and
thermostat status object, that is thousands of handshakes a day against a
small embedded web server.

Sessions are now kept per worker THREAD: Home Assistant runs our blocking
calls on an executor pool where a write job and a refresh job can overlap, and
requests.Session is not documented as thread-safe. One session per thread
gives full reuse without serialising unrelated requests.
"""

import os
import sys
import threading

import pytest
import requests
import requests_mock

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink.connection import VimarConnection  # noqa: E402

pytestmark = pytest.mark.no_ha  # No HA required

LOGIN_OK_XML = "<xml><result>0</result><message>ok</message><sessionid>SESS-1</sessionid></xml>"


def _connection():
    return VimarConnection(
        schema="https",
        host="192.168.0.13",
        port=443,
        username="admin",
        password="hunter2",
        certificate=None,
        timeout=6,
    )


# ---------------------------------------------------------------------------
# Reuse
# ---------------------------------------------------------------------------


def test_the_same_session_is_reused_across_requests():
    """THE regression: N requests on one thread must use ONE session."""
    conn = _connection()

    with requests_mock.Mocker() as mock:
        mock.get(requests_mock.ANY, text=LOGIN_OK_XML)
        mock.post(requests_mock.ANY, text="<xml><result>ok</result></xml>")
        for _ in range(5):
            conn.login()
            conn._request("https://192.168.0.13:443/cgi-bin/dpadws", post="<soap/>")

    assert len(conn._all_http_sessions) == 1


def test_get_http_session_is_idempotent():
    conn = _connection()

    assert conn._get_http_session() is conn._get_http_session()


def test_session_survives_a_failed_request():
    """One network error must not throw the pool away."""
    conn = _connection()

    with requests_mock.Mocker() as mock:
        mock.get(requests_mock.ANY, exc=requests.exceptions.ConnectionError("boom"))
        first = conn._get_http_session()
        assert conn._request("https://192.168.0.13:443/x") is False

    assert conn._get_http_session() is first
    assert len(conn._all_http_sessions) == 1


def test_the_https_adapter_supports_the_legacy_vimar_tls():
    """Reuse must not lose the custom adapter the VIMAR firmware needs."""
    conn = _connection()
    session = conn._get_http_session()

    adapter = session.get_adapter("https://192.168.0.13/")

    assert type(adapter).__module__.endswith("http_adapter")


def test_idempotent_requests_retry_once_on_a_stale_connection():
    """A keep-alive socket dropped by the web server must not fail the poll."""
    conn = _connection()
    session = conn._get_http_session()

    retries = session.get_adapter("https://192.168.0.13/").max_retries

    assert retries.total == 1
    # POST (a SETVALUE) must never be replayed automatically.
    assert "POST" not in retries.allowed_methods


# ---------------------------------------------------------------------------
# Thread isolation
# ---------------------------------------------------------------------------


def test_each_thread_gets_its_own_session():
    """requests.Session is not thread-safe; executor jobs can overlap."""
    conn = _connection()
    main_session = conn._get_http_session()
    other: list = []

    thread = threading.Thread(target=lambda: other.append(conn._get_http_session()))
    thread.start()
    thread.join()

    assert other[0] is not main_session
    assert len(conn._all_http_sessions) == 2


def test_every_thread_session_is_registered_for_cleanup():
    conn = _connection()
    threads = [threading.Thread(target=conn._get_http_session) for _ in range(3)]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(conn._all_http_sessions) == 3


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


def test_close_releases_every_session():
    """Reloading the entry must not leak sockets."""
    conn = _connection()
    conn._get_http_session()
    thread = threading.Thread(target=conn._get_http_session)
    thread.start()
    thread.join()
    assert len(conn._all_http_sessions) == 2

    conn.close()

    assert conn._all_http_sessions == []


def test_a_request_after_close_opens_a_fresh_session():
    """close() must not leave the connection unusable."""
    conn = _connection()
    first = conn._get_http_session()

    conn.close()
    second = conn._get_http_session()

    assert second is not first
    assert len(conn._all_http_sessions) == 1


def test_close_is_safe_when_nothing_was_opened():
    conn = _connection()

    conn.close()  # must not raise

    assert conn._all_http_sessions == []
