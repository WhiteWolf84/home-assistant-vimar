"""Credential handling in the VIMAR connection layer (NO Home Assistant required).

Two regressions are covered here.

1. Credentials used to be spliced raw into the login URL with an f-string:

       ...user_login.php?sessionid=&username={user}&password={password}&...

   A password containing '&', '#', '+', '%' or a space corrupted the query
   string, so the web server saw a truncated password and the user was told
   their (perfectly valid) credentials were wrong. They are now passed as
   request parameters, which requests percent-encodes.

2. requests/urllib3 embed the requested URL in most of their exception
   messages ("Max retries exceeded with url: ...password=hunter2"). Those
   exceptions were logged at ERROR level and re-raised into the config flow,
   so a single network hiccup during login wrote the plaintext password into
   home-assistant.log - a file users routinely attach to GitHub issues.
   Everything that can carry a URL now goes through redact().
"""

import logging
import os
import sys
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import requests_mock

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink.connection import VimarConnection  # noqa: E402
from vimarlink.exceptions import VimarConnectionError  # noqa: E402

pytestmark = pytest.mark.no_ha  # No HA required

# A password exercising every character that breaks a hand-built query string:
# '&' (parameter separator), '=' (key/value separator), '#' (fragment),
# '+' (encoded space), '%' (escape char) and a literal space.
NASTY_PASSWORD = "p@ss w&rd=1#top+50%"

LOGIN_OK_XML = "<xml><result>0</result><message>ok</message><sessionid>SESS-42</sessionid></xml>"


def _connection(password="hunter2", certificate=None):
    return VimarConnection(
        schema="https",
        host="192.168.0.13",
        port=443,
        username="admin",
        password=password,
        certificate=certificate,
        timeout=6,
    )


def _query_of(url):
    return parse_qs(urlparse(url).query, keep_blank_values=True)


# ---------------------------------------------------------------------------
# 1. Query-string encoding
# ---------------------------------------------------------------------------


def test_login_password_survives_special_characters():
    """The server must receive the password byte-for-byte, however odd it is.

    This is the regression test for the f-string URL: with the old code the
    query broke apart at the first '&' and the password arrived truncated to
    'p@ss w'.
    """
    conn = _connection(password=NASTY_PASSWORD)

    with requests_mock.Mocker() as mock:
        mock.get(requests_mock.ANY, text=LOGIN_OK_XML)
        conn.login()

        query = _query_of(mock.last_request.url)

    assert query["password"] == [NASTY_PASSWORD]
    assert query["username"] == ["admin"]
    assert query["op"] == ["login"]
    # The session id from the response is what login() is there for.
    assert conn.session_id == "SESS-42"


def test_login_password_is_percent_encoded_on_the_wire():
    """The raw URL must not contain the unescaped separators."""
    conn = _connection(password=NASTY_PASSWORD)

    with requests_mock.Mocker() as mock:
        mock.get(requests_mock.ANY, text=LOGIN_OK_XML)
        conn.login()
        url = mock.last_request.url

    assert "p@ss w&rd" not in url
    assert "%26" in url  # the '&' inside the password, encoded


def test_login_sends_every_expected_parameter():
    """Encoding must not have dropped or renamed any parameter."""
    conn = _connection()

    with requests_mock.Mocker() as mock:
        mock.get(requests_mock.ANY, text=LOGIN_OK_XML)
        conn.login()
        query = _query_of(mock.last_request.url)

    assert set(query) == {"sessionid", "username", "password", "remember", "op"}
    assert query["sessionid"] == [""]
    assert query["remember"] == ["0"]


# ---------------------------------------------------------------------------
# 2. Redaction
# ---------------------------------------------------------------------------


def test_connection_error_message_hides_the_password():
    """The exception raised to the config flow must not carry the password."""
    conn = _connection(password="hunter2")
    boom = requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='192.168.0.13', port=443): Max retries exceeded "
        "with url: /vimarbyweb/modules/system/user_login.php?sessionid=&username=admin"
        "&password=hunter2&remember=0&op=login"
    )

    with requests_mock.Mocker() as mock:
        mock.get(requests_mock.ANY, exc=boom)
        with pytest.raises(VimarConnectionError) as excinfo:
            conn.login()

    message = str(excinfo.value)
    assert "hunter2" not in message
    assert "***" in message
    # The diagnostic value of the message must survive redaction.
    assert "Max retries exceeded" in message


def test_request_error_log_hides_the_password(caplog):
    """The ERROR log line emitted by _request must not carry the password."""
    conn = _connection(password="hunter2")
    boom = requests.exceptions.ConnectionError("failed for url: ?password=hunter2&op=login")

    with caplog.at_level(logging.ERROR), requests_mock.Mocker() as mock:
        mock.get(requests_mock.ANY, exc=boom)
        with pytest.raises(VimarConnectionError):
            conn.login()

    assert caplog.text  # the failure really was logged
    assert "hunter2" not in caplog.text


def test_redact_catches_the_percent_encoded_password():
    """The URL echoed back by requests carries the ENCODED password."""
    conn = _connection(password=NASTY_PASSWORD)
    encoded = "p%40ss%20w%26rd%3D1%23top%2B50%25"

    redacted = conn.redact(f"Max retries exceeded with url: /login.php?password={encoded}")

    assert encoded not in redacted
    assert "***" in redacted


def test_redact_masks_sensitive_query_parameters():
    """Even an unknown/rotated secret is masked by parameter name."""
    conn = _connection(password="hunter2")

    redacted = conn.redact(
        "GET /x.php?username=admin&password=other-secret&sessionid=ABC123&op=login"
    )

    assert "other-secret" not in redacted
    assert "ABC123" not in redacted
    assert "admin" not in redacted
    # Non-sensitive parameters are left alone for diagnosability.
    assert "op=login" in redacted


def test_redact_is_safe_without_a_password():
    """A connection with no password configured must not blow up or over-mask."""
    conn = _connection(password="")

    assert conn.redact("nothing to hide here") == "nothing to hide here"
    assert conn.redact("") == ""


def test_redact_does_not_break_ssl_error_classification():
    """set_errors_from_ex keys off 'SSLError' in the message; keep it intact."""
    conn = _connection(password="hunter2")
    message = conn.redact(
        "SSLError(SSLCertVerificationError(1, 'certificate verify failed')) "
        "for url https://h/login.php?password=hunter2"
    )

    assert "SSLError" in message
    assert "hunter2" not in message
