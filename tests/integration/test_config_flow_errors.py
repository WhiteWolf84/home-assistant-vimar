"""Tests for config-flow error classification (Home Assistant required).

These cover set_errors_from_ex, which maps an exception raised during
credential validation to a form error key shown to the user. The key
improvement under test: classification is TYPE-based first (robust against
wording changes in the VIMAR server messages), with string matching kept
only as a fallback for untyped/legacy exceptions.
"""

import os
import sys

import pytest

# Import the integration as a package so relative imports resolve.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.config_flow import set_errors_from_ex
from custom_components.vimar.vimarlink.exceptions import (
    VimarApiError,
    VimarConfigError,
    VimarConnectionError,
)

pytestmark = pytest.mark.integration  # Home Assistant required


def _classify(ex: Exception) -> str:
    errors: dict[str, str] = {}
    set_errors_from_ex(ex, errors)
    return errors["base"]


def test_config_error_is_invalid_auth_by_type():
    """A login rejected by the server is invalid_auth purely from the type.

    The message intentionally does NOT contain 'Log In Fallito', proving the
    classification no longer depends on the server wording.
    """
    assert _classify(VimarConfigError("server said no, code=1")) == "invalid_auth"


def test_connection_error_is_cannot_connect_by_type():
    """A network/parse failure maps to cannot_connect from the type."""
    assert _classify(VimarConnectionError("Error during login: timeout")) == "cannot_connect"


def test_ssl_message_wins_over_connection_type():
    """SSL surfaces as a VimarConnectionError but must map to invalid_cert.

    The message-first check has to run before the generic connection mapping.
    """
    assert _classify(VimarConnectionError("request failed: SSLError bad cert")) == "invalid_cert"


def test_saving_certificate_failed_is_save_cert_failed():
    """A failed certificate save is its own error key."""
    assert _classify(VimarApiError("Saving certificate failed: disk full")) == "save_cert_failed"


def test_legacy_login_string_fallback():
    """A plain Exception carrying the VIMAR message still maps via fallback."""
    assert _classify(Exception("Log In Fallito")) == "invalid_auth"


def test_legacy_timeout_string_fallback():
    """A plain Exception with a known connection phrase maps via fallback."""
    assert _classify(Exception("HTTP timeout occurred")) == "cannot_connect"


def test_unknown_exception_is_unknown():
    """An unrecognized exception falls through to 'unknown'."""
    assert _classify(ValueError("something unexpected")) == "unknown"
