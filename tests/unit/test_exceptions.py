"""The Vimar exception hierarchy (NO Home Assistant required).

This file used to import `vimarlink.vimarlink_exceptions`, a module nothing in
the integration ever imported: it tested a dead copy while the live
`vimarlink.exceptions` - the one every raise site actually uses - had no
coverage at all. The dead copy has been removed and these tests now target the
live module.

The live module also carried a bug the dead copy did not have: `__str__` ran
the message through percent formatting, so stringifying an exception whose
message contained a '%' raised instead of returning text. That matters because
requests echoes percent-encoded URLs into its error messages, and both the
config flow and the coordinator stringify these exceptions to report a failure
- so a plain connection error crashed the error handling itself.
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "custom_components", "vimar")
)

from vimarlink.exceptions import (  # noqa: E402
    VimarApiError,
    VimarConfigError,
    VimarConnectionError,
)

pytestmark = pytest.mark.no_ha  # No HA required


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


def test_exception_hierarchy():
    """One base class so callers can catch every Vimar failure at once."""
    assert issubclass(VimarConfigError, VimarApiError)
    assert issubclass(VimarConnectionError, VimarApiError)


def test_base_exception_catches_every_subclass():
    for error in (VimarConfigError, VimarConnectionError):
        with pytest.raises(VimarApiError):
            raise error("Test")


def test_exception_can_be_raised_and_carries_its_message():
    with pytest.raises(VimarConnectionError) as exc_info:
        raise VimarConnectionError("Connection refused")

    assert "Connection refused" in str(exc_info.value)


# ---------------------------------------------------------------------------
# __str__: the message must survive, whatever it contains
# ---------------------------------------------------------------------------


def test_str_prefixes_the_class_name():
    assert str(VimarConnectionError("Error during login")) == (
        "VimarConnectionError: Error during login"
    )


def test_str_survives_a_percent_encoded_url():
    """THE regression: requests echoes percent-encoded URLs into its messages."""
    message = "Max retries exceeded with url: /login.php?password=%26abc%20def"

    assert str(VimarConnectionError(message)) == f"VimarConnectionError: {message}"


@pytest.mark.parametrize(
    "message",
    [
        "disk 50% full",
        "100%",
        "%s placeholder that has no argument",
        "%(name)s mapping placeholder",
        "trailing percent %",
    ],
)
def test_str_survives_any_percent_in_the_message(message):
    assert message in str(VimarApiError(message))


def test_str_without_arguments_does_not_raise():
    """`raise VimarApiError()` used to explode with IndexError when logged."""
    assert str(VimarApiError()) == "VimarApiError"


def test_extra_arguments_are_kept_but_not_interpolated():
    """Nothing passes format arguments; keep them accessible, never applied."""
    err = VimarApiError("first", "second")

    assert str(err) == "VimarApiError: first"
    assert err.err_args == ("first", "second")


def test_err_args_is_per_instance():
    """A class-level mutable default would leak between instances."""
    first = VimarApiError("one")
    second = VimarApiError("two")

    assert first.err_args == ("one",)
    assert second.err_args == ("two",)
