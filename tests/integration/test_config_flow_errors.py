"""Tests for config-flow error classification (Home Assistant required).

These cover set_errors_from_ex, which maps an exception raised during
credential validation to a form error key shown to the user. The key
improvement under test: classification is TYPE-based first (robust against
wording changes in the VIMAR server messages), with string matching kept
only as a fallback for untyped/legacy exceptions.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# Import the integration as a package so relative imports resolve.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.config_flow import set_errors_from_ex
from custom_components.vimar.vimar_coordinator import VimarDataUpdateCoordinator
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


def test_a_freshly_built_coordinator_can_be_validated_straight_away():
    """THE regression behind "'...Coordinator' object has no attribute 'vimarconnection'".

    The config flow builds a coordinator and calls validate_vimar_credentials()
    immediately, without going through init_vimarproject(). When
    _build_vimar_objects() was split out of init_vimarproject() but not called
    from __init__, that raised AttributeError, which set_errors_from_ex could
    only classify as "unknown" - so the user saw a generic failure on every
    single connection attempt, whatever their credentials were.
    """
    coordinator = VimarDataUpdateCoordinator(
        MagicMock(),
        entry=None,
        vimarconfig={
            "host": "192.168.0.13",
            "port": 443,
            "username": "admin",
            "password": "secret",
        },
    )

    assert coordinator.vimarconnection is not None
    assert coordinator.vimarproject is not None
