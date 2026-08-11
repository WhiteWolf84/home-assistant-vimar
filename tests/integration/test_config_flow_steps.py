"""The config-flow steps actually run end to end (Home Assistant required).

Only `set_errors_from_ex` was under test before, so every flow step - the very
code a user hits before anything else works - was exercised for the first time
in production. That is how a coordinator built without its VimarLink shipped:
the step raised AttributeError, `set_errors_from_ex` could only file it under
"unknown", and every connection attempt failed with a generic message whatever
the credentials were.

These tests drive the steps themselves, and also pin the resource contract:
a validation coordinator is thrown away when the step returns, so the HTTPS
session it opened by logging in has to be closed before then - including when
validation fails.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.config_flow import (  # noqa: E402
    OptionsFlowHandler,
    VimarFlowHandler,
)
from custom_components.vimar.vimarlink.exceptions import VimarConfigError  # noqa: E402
from custom_components.vimar.vimarlink.vimarlink import VimarLink, VimarProject  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

USER_INPUT = {
    "title": "Casa",
    "host": "192.168.0.13",
    "port": 443,
    "username": "admin",
    "password": "secret",
    "timeout": 5,
}


def _hass():
    """A hass double whose executor jobs run inline, on this thread."""
    hass = MagicMock()

    async def _job(func, *args):
        return func(*args)

    hass.async_add_executor_job = _job
    return hass


def _user_flow():
    flow = VimarFlowHandler()
    flow.hass = _hass()
    # Supplied by the flow manager in production.
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_show_form = MagicMock(side_effect=lambda **kw: {"type": "form", **kw})
    return flow


async def test_the_user_step_completes_with_valid_credentials():
    """THE regression: this step raised AttributeError before reaching the login."""
    flow = _user_flow()

    with (
        patch.object(VimarLink, "check_login", return_value=True),
        patch.object(VimarLink, "close"),
    ):
        result = await flow.async_step_user(dict(USER_INPUT))

    assert result["type"] == "create_entry"
    assert flow.async_create_entry.call_args.kwargs["title"] == "Casa"


async def test_the_user_step_releases_the_session_it_opened():
    """The coordinator is dropped when the step returns; the socket must not be."""
    flow = _user_flow()

    with (
        patch.object(VimarLink, "check_login", return_value=True),
        patch.object(VimarLink, "close") as close,
    ):
        await flow.async_step_user(dict(USER_INPUT))

    assert close.call_count == 1


async def test_a_rejected_login_shows_invalid_auth_and_still_releases_the_session():
    """A wrong password is the case a user repeats the most, so it must not leak."""
    flow = _user_flow()

    with (
        patch.object(VimarLink, "check_login", side_effect=VimarConfigError("Log In Fallito")),
        patch.object(VimarLink, "close") as close,
    ):
        result = await flow.async_step_user(dict(USER_INPUT))

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}
    assert close.call_count == 1


async def test_the_reauth_step_completes_and_releases_the_session():
    """Reauth builds its own throwaway coordinator, on the same contract."""
    flow = VimarFlowHandler()
    flow.hass = _hass()
    entry = MagicMock()
    entry.data = dict(USER_INPUT)
    entry.entry_id = "abc"
    flow.reauth_entry = entry
    flow.async_abort = MagicMock(side_effect=lambda reason: {"type": "abort", "reason": reason})
    flow.async_show_form = MagicMock(side_effect=lambda **kw: {"type": "form", **kw})
    flow.hass.config_entries.async_reload = AsyncMock()

    with (
        patch.object(VimarLink, "check_login", return_value=True),
        patch.object(VimarLink, "close") as close,
    ):
        result = await flow.async_step_reauth_confirm({"username": "admin", "password": "new"})

    assert result["reason"] == "reauth_successful"
    assert close.call_count == 1


async def test_the_options_steps_release_every_session_they_open():
    """Saving options validates twice (init, then two): both must be released."""
    options = OptionsFlowHandler()
    options.hass = _hass()
    entry = MagicMock()
    entry.data = dict(USER_INPUT)
    entry.options = {}

    with (
        patch.object(OptionsFlowHandler, "config_entry", new_callable=PropertyMock) as cfg,
        patch.object(VimarLink, "check_login", return_value=True),
        patch.object(VimarProject, "update", return_value={}),
        patch.object(VimarLink, "close") as close,
    ):
        cfg.return_value = entry
        options.async_show_form = MagicMock(side_effect=lambda **kw: {"type": "form", **kw})
        first = await options.async_step_init(dict(USER_INPUT))
        second = await options.async_step_two(dict(USER_INPUT))

    # Each validating step moved the flow on, and each released its own
    # connection: two saves of the options form, two sessions, two closes.
    assert first["step_id"] == "two"
    assert second["step_id"] == "three"
    assert close.call_count == 2
