"""Credential handling in the VIMAR connection layer (NO Home Assistant required).

Three regressions are covered here.

1. Credentials used to be spliced raw into the login URL with an f-string:

       ...user_login.php?sessionid=&username={user}&password={password}&...

   A password containing '&', '#', '+', '%' or a space corrupted the query
   string, so the web server saw a truncated password and the user was told
   their (perfectly valid) credentials were wrong. They are encoded now.

2. Even encoded, they were still IN the URL - and a URL is what every layer
   of the stack echoes when something goes wrong. Measured on real hardware:
   one read timeout during login and urllib3's own retry warning wrote the
   plaintext password into home-assistant.log, a file users attach to issues.
   That warning comes from urllib3's logger, before the exception reaches our
   code, so no amount of redaction on our side could have caught it. login()
   is a POST now and the URL carries nothing at all.

3. redact() remains the second line of defence, because every request after
   the login still carries the session id - a bearer credential for as long
   as it is valid - in its query string. requests and urllib3 embed the URL
   in most of their exception messages, which are logged at ERROR level and
   re-raised into the config flow, so everything that can carry a URL goes
   through it.
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


def _body_of(request):
    """The form-encoded body, parsed the way user_login.php reads it."""
    return parse_qs(request.text or "", keep_blank_values=True)


# ---------------------------------------------------------------------------
# 1. Encoding and where the credentials travel
# ---------------------------------------------------------------------------


def test_login_password_survives_special_characters():
    """The server must receive the password byte-for-byte, however odd it is.

    This is the regression test for the f-string URL: with the old code the
    query broke apart at the first '&' and the password arrived truncated to
    'p@ss w'. requests encodes a form body for the same reason it encoded the
    query string, so moving to POST does not reopen it.
    """
    conn = _connection(password=NASTY_PASSWORD)

    with requests_mock.Mocker() as mock:
        mock.post(requests_mock.ANY, text=LOGIN_OK_XML)
        conn.login()

        body = _body_of(mock.last_request)

    assert body["password"] == [NASTY_PASSWORD]
    assert body["username"] == ["admin"]
    assert body["op"] == ["login"]
    # The session id from the response is what login() is there for.
    assert conn.session_id == "SESS-42"


def test_login_puts_nothing_at_all_in_the_url():
    """THE regression: the URL is what leaks, and not only through our code.

    urllib3 logs its own retry warning - with the full URL - before the
    exception reaches this module, so redact() never sees it. On real hardware
    one read timeout during login was enough to write the plaintext password
    into home-assistant.log. Credentials that are not in the URL cannot leak
    that way, whoever does the logging.
    """
    conn = _connection(password=NASTY_PASSWORD)

    with requests_mock.Mocker() as mock:
        mock.post(requests_mock.ANY, text=LOGIN_OK_XML)
        conn.login()
        url = mock.last_request.url

    assert _query_of(url) == {}
    assert "password" not in url
    assert NASTY_PASSWORD not in url
    assert "p@ss w&rd" not in url
    assert "admin" not in url


def test_login_sends_every_expected_parameter():
    """Moving to the body must not have dropped or renamed any parameter."""
    conn = _connection()

    with requests_mock.Mocker() as mock:
        mock.post(requests_mock.ANY, text=LOGIN_OK_XML)
        conn.login()
        body = _body_of(mock.last_request)

    assert set(body) == {"sessionid", "username", "password", "remember", "op"}
    assert body["sessionid"] == [""]
    assert body["remember"] == ["0"]


def test_login_is_form_encoded():
    """The web server reads them through PHP's $_REQUEST, which merges GET and
    POST - but only for a form-encoded body, not for JSON."""
    conn = _connection()

    with requests_mock.Mocker() as mock:
        mock.post(requests_mock.ANY, text=LOGIN_OK_XML)
        conn.login()
        content_type = mock.last_request.headers["Content-Type"]

    assert content_type.startswith("application/x-www-form-urlencoded")


# ---------------------------------------------------------------------------
# 2. Redaction
# ---------------------------------------------------------------------------


def test_connection_error_message_hides_the_password():
    """The exception raised to the config flow must not carry the password.

    Second line of defence now that login() is a POST: the login URL no longer
    holds credentials, but every other request still carries the session id -
    a bearer credential for as long as it is valid - in its query string.
    """
    conn = _connection(password="hunter2")
    boom = requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='192.168.0.13', port=443): Max retries exceeded "
        "with url: /vimarbyweb/modules/system/user_login.php?sessionid=&username=admin"
        "&password=hunter2&remember=0&op=login"
    )

    with requests_mock.Mocker() as mock:
        mock.post(requests_mock.ANY, exc=boom)
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
        mock.post(requests_mock.ANY, exc=boom)
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
