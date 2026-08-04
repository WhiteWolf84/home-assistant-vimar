"""Vimar connection and authentication module."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
import xml.etree.ElementTree as xmlTree
from typing import Literal
from urllib.parse import quote

import requests
import requests.adapters
import urllib3
from requests.exceptions import HTTPError

from .exceptions import VimarApiError, VimarConfigError, VimarConnectionError
from .http_adapter import HTTPAdapter

_LOGGER = logging.getLogger(__name__)

# Credentials must never reach the log or an exception message: the VIMAR login
# endpoint takes them as query parameters, and requests/urllib3 embed the full
# URL in most of their exception messages ("Max retries exceeded with url:
# ...?username=admin&password=hunter2"). Those exceptions are logged at ERROR
# level and bubble up to the config flow, so a single network hiccup during
# login used to write the plaintext password into home-assistant.log - a file
# users routinely attach to GitHub issues.
_REDACTED = "***"
_SENSITIVE_QUERY_RE = re.compile(r"((?:password|username|sessionid)=)[^&\s'\"]*", re.IGNORECASE)
# FIX #19: rimosso SSL_IGNORED module-level global. Come globale non veniva
# mai resettato tra reload della config-entry nello stesso processo, quindi
# il messaggio debug "ignoring ssl" veniva soppresso anche per nuove istanze.
# Spostato come attributo _ssl_ignore_logged per istanza.


class VimarConnection:
    """Handles HTTP connections and authentication to Vimar web server."""

    def __init__(
        self,
        schema: str,
        host: str,
        port: int,
        username: str,
        password: str,
        certificate: str | None = None,
        timeout: int = 6,
    ):
        """Initialize connection parameters."""
        self._schema = schema
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._certificate = certificate
        self._timeout = timeout
        self._session_id: str | None = None
        self.request_last_exception: Exception | None = None
        # FIX #19: per-instance flag (era SSL_IGNORED globale di modulo)
        self._ssl_ignore_logged: bool = False
        # HTTP sessions, one per worker thread (see _get_http_session).
        self._thread_sessions = threading.local()
        self._all_http_sessions: list[requests.Session] = []
        self._http_sessions_lock = threading.Lock()

    # -- read-only view of the connection settings (for diagnostics) --------

    @property
    def session_id(self) -> str | None:
        """Get current session ID."""
        return self._session_id

    @property
    def host(self) -> str:
        """Web server host."""
        return self._host

    @property
    def port(self) -> int:
        """Web server port."""
        return self._port

    @property
    def schema(self) -> str:
        """URL schema in use ('http' or 'https')."""
        return self._schema

    @property
    def base_url(self) -> str:
        """Base URL of the web server."""
        return f"{self._schema}://{self._host}:{self._port}"

    @property
    def certificate(self) -> str | None:
        """Path of the CA certificate in use, if any."""
        return self._certificate

    # -- HTTP session reuse -------------------------------------------------

    def _get_http_session(self) -> requests.Session:
        """Return this thread's HTTP session, creating it on first use.

        A fresh requests.Session used to be built - and thrown away - for
        EVERY request, which discarded the connection pool it exists for: each
        SOAP call paid a full TCP connect plus a TLS handshake, on hardware
        that negotiates RSA/AES256-SHA slowly. With an 8 s poll plus the
        per-object GETVALUE refreshes that is thousands of handshakes a day
        against a small embedded web server.

        The session is kept per THREAD rather than per instance on purpose:
        requests.Session is not documented as thread-safe, and Home Assistant
        runs our blocking calls on an executor pool where a write job and a
        refresh job can overlap. One session per worker thread gives full
        connection reuse without serialising unrelated requests or changing
        any of the timing the write-guards depend on.
        """
        session = getattr(self._thread_sessions, "session", None)
        if session is None:
            session = requests.Session()
            # max_retries=1 only covers idempotent methods (GET), so a stale
            # keep-alive connection dropped by the web server is retried on a
            # fresh socket instead of failing a whole poll. SETVALUE POSTs are
            # never replayed.
            session.mount("https://", HTTPAdapter(max_retries=1))
            session.mount("http://", requests.adapters.HTTPAdapter(max_retries=1))
            self._thread_sessions.session = session
            with self._http_sessions_lock:
                self._all_http_sessions.append(session)
        return session

    def close(self) -> None:
        """Close every HTTP session opened by this connection.

        Called when the config entry is unloaded so a reload does not leak
        sockets. Sessions belonging to other worker threads are closed too:
        closing is safe from any thread once nothing is in flight.
        """
        with self._http_sessions_lock:
            sessions, self._all_http_sessions = self._all_http_sessions, []
        for session in sessions:
            with contextlib.suppress(Exception):
                session.close()
        self._thread_sessions = threading.local()

    def install_certificate(self) -> bool:
        """Download CA certificate from web server."""
        cert_changed = False

        if not self._certificate:
            return False

        temp_certificate = self._certificate
        self._certificate = None

        download_url = (
            f"{self._schema}://{self._host}:{self._port}"
            "/vimarbyweb/modules/vimar-byme/script/rootCA.VIMAR.crt"
        )

        certificate_file = self._request(download_url)
        self._certificate = temp_certificate

        if certificate_file is None or certificate_file is False:
            raise VimarConnectionError(
                f"Certificate download failed: {self.redact(str(self.request_last_exception))}"
            )

        old_cert = None
        try:
            with open(self._certificate) as f:
                old_cert = f.read()
        except OSError:
            old_cert = None

        if old_cert != certificate_file:
            cert_changed = True
            try:
                with open(self._certificate, "w") as f:
                    f.write(certificate_file)
                _LOGGER.debug("Downloaded Vimar CA certificate to: %s", self._certificate)
            except OSError as err:
                raise VimarApiError(f"Saving certificate failed: {err}")

        return cert_changed

    def redact(self, text: str) -> str:
        """Strip credentials from a message before it is logged or re-raised.

        Two passes, because a credential can appear either verbatim (our own
        f-strings) or percent-encoded inside a URL echoed back by requests:
        first the literal secrets, then any sensitive query parameter.
        """
        if not text:
            return text
        for secret in (self._password, quote(self._password or "", safe="")):
            if secret:
                text = text.replace(secret, _REDACTED)
        return _SENSITIVE_QUERY_RE.sub(rf"\1{_REDACTED}", text)

    def login(self) -> str | None:
        """Authenticate and get session ID."""
        login_url = (
            f"{self._schema}://{self._host}:{self._port}/vimarbyweb/modules/system/user_login.php"
        )
        # Credentials go through the query-parameter encoder instead of an
        # f-string: a password containing '&', '#', '+', '%' or a space used to
        # be spliced raw into the URL, silently truncating or corrupting it, so
        # a perfectly valid password was reported back as "invalid credentials".
        login_params = {
            "sessionid": "",
            "username": self._username,
            "password": self._password,
            "remember": "0",
            "op": "login",
        }

        use_cert = bool(self._certificate)

        if self._schema == "https" and use_cert and not os.path.isfile(self._certificate):
            self.install_certificate()

        result = self._request(login_url, params=login_params)

        if result is False and use_cert:
            curr_ex_str = str(self.request_last_exception)
            if "SSLError" in curr_ex_str or "TLS CA" in curr_ex_str:
                try:
                    if self.install_certificate():
                        result = self._request(login_url, params=login_params)
                except Exception:
                    pass

        if result is None:
            _LOGGER.warning("Empty response from webserver login")
            return None

        if result is False:
            # redact(): the underlying requests exception carries the full login
            # URL, credentials included, and this message is both logged and
            # surfaced in the config-flow error path.
            raise VimarConnectionError(
                f"Error during login: {self.redact(str(self.request_last_exception))}"
            )

        try:
            xml = self._parse_xml(result)
            if xml is None:
                raise Exception("Login failed - check username, password and certificate path")
            logincode = xml.find("result")
            loginmessage = xml.find("message")
        except Exception as err:
            raise VimarConnectionError(
                f"Error parsing login response: {self.redact(str(err))} - {self.redact(result)}"
            )

        if logincode is not None and logincode.text != "0":
            msg = loginmessage.text if loginmessage is not None else logincode.text
            raise VimarConfigError(f"Error during login: {msg}")

        _LOGGER.info("Vimar login ok")
        loginsession = xml.find("sessionid")

        if loginsession is not None and loginsession.text:
            _LOGGER.debug("Got new Vimar Session id: %s", loginsession.text)
            self._session_id = loginsession.text
        else:
            _LOGGER.warning("Missing Session id in login response: %s", result)

        return result if isinstance(result, str) else None

    def invalidate_session(self) -> None:
        """Drop the cached session ID so the next check_login() re-authenticates.

        The webserver can expire a session server-side (SQL requests then
        return LGMG-3019 with an Unknown-Payload body); without dropping the
        stale ID, check_login() keeps reusing it forever.
        """
        self._session_id = None

    def is_logged(self) -> bool:
        """Check if session is available."""
        return self._session_id is not None

    def check_login(self) -> bool:
        """Ensure we have a valid session."""
        if not self._session_id:
            self.login()
        return self._session_id is not None

    def _request(
        self,
        url: str,
        post: str | None = None,
        headers: dict | None = None,
        # requests' `verify`: True/False, or a path to a CA bundle.
        check_ssl: bool | str = False,
        params: dict | None = None,
        # Never True: the body returns the response text, False on a failed
        # request, or None. Declaring plain `bool` made every caller look like
        # it had to cope with True as well.
    ) -> str | Literal[False] | None:
        """Execute HTTP request.

        `params` is passed to requests so query values are properly encoded;
        never build a query string with credentials by hand (see login()).
        """
        try:
            timeouts = (int(self._timeout / 2), self._timeout)

            if self._certificate:
                check_ssl = self._certificate
            else:
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                # FIX #19: attributo di istanza invece di globale di modulo
                if not self._ssl_ignore_logged:
                    _LOGGER.debug("Request ignores ssl certificate")
                    self._ssl_ignore_logged = True

            # Reused across requests (see _get_http_session): do NOT close it
            # here, closing is what used to throw the connection pool away.
            s = self._get_http_session()

            if post is None:
                response = s.get(
                    url, params=params, headers=headers, verify=check_ssl, timeout=timeouts
                )
            else:
                response = s.post(
                    url,
                    params=params,
                    data=post,
                    headers=headers,
                    verify=check_ssl,
                    timeout=timeouts,
                )

            response.raise_for_status()
            return response.text

        # Every message below goes through redact(): requests and urllib3
        # embed the requested URL - login credentials included - in most of
        # their exception strings.
        except HTTPError as http_err:
            self.request_last_exception = http_err
            _LOGGER.error("HTTP error occurred: %s", self.redact(str(http_err)))
            return False
        except requests.exceptions.Timeout as ex:
            self.request_last_exception = ex
            _LOGGER.error("HTTP timeout occurred")
            return False
        except Exception as err:
            self.request_last_exception = err
            _LOGGER.error("Error occurred: %s", self.redact(str(err)))
            return False

    def _parse_xml(self, xml: str) -> xmlTree.Element | None:
        """Parse XML response."""
        try:
            return xmlTree.fromstring(xml)
        except Exception as err:
            _LOGGER.error("Error parsing XML: %s", err)
            _LOGGER.debug("Problematic XML: %s", str(xml))
            return None
