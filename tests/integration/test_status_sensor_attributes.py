"""The connection sensor must not publish credentials (Home Assistant required).

Regression: VimarStatusSensor exposed the VIMAR `Username` and the live
`SessionID` as state attributes. State attributes are readable by EVERY Home
Assistant user - non-admin accounts included - and are written to the recorder
database, where they are retained for weeks. The session id in particular is a
bearer credential for the web server for as long as it is valid.

The sensor now publishes only how the integration is connected, never who it
is connected as.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.binary_sensor import VimarStatusSensor  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

USERNAME = "admin-secret-user"
SESSION_ID = "SESSION-ABC-123"


def _sensor():
    connection = MagicMock()
    connection.host = "192.168.0.13"
    connection.port = 443
    connection.schema = "https"
    connection.base_url = "https://192.168.0.13:443"
    connection.certificate = "rootCA.VIMAR.crt"
    # Present on the object, but must never reach the state machine.
    connection.username = USERNAME
    connection.session_id = SESSION_ID

    coordinator = MagicMock()
    coordinator.vimarconnection.connection = connection
    coordinator.vimarconnection._session_id = SESSION_ID
    coordinator.vimarconfig = {"verify_ssl": True}
    coordinator.entity_unique_id_prefix = "casa"
    return VimarStatusSensor(coordinator)


def _attribute_blob(sensor):
    attrs = sensor.extra_state_attributes or {}
    return " ".join(f"{k}={v}" for k, v in attrs.items())


def test_the_session_id_is_not_published():
    """THE regression: a live credential in a world-readable attribute."""
    sensor = _sensor()

    assert "SessionID" not in (sensor.extra_state_attributes or {})
    assert SESSION_ID not in _attribute_blob(sensor)


def test_the_username_is_not_published():
    sensor = _sensor()

    assert "Username" not in (sensor.extra_state_attributes or {})
    assert USERNAME not in _attribute_blob(sensor)


def test_the_useful_diagnostics_are_kept():
    """Removing the secrets must not gut the sensor."""
    attrs = _sensor().extra_state_attributes

    assert attrs["Host"] == "192.168.0.13"
    assert attrs["Port"] == 443
    assert attrs["Secure"] is True
    assert attrs["Vimar Url"] == "https://192.168.0.13:443"
    assert attrs["Certificate"] == "rootCA.VIMAR.crt"


def test_the_name_still_identifies_the_web_server():
    assert _sensor().name == "Vimar Connection to 192.168.0.13:443"


def test_no_attribute_leaks_a_secret_looking_value():
    """Guard for anything added here later."""
    blob = _attribute_blob(_sensor()).lower()

    for forbidden in ("password", "session", "token", "pin"):
        assert forbidden not in blob
