"""Regression tests for the time-based cover restart-recovery (v2).

If Home Assistant restarts while a time-based cover is moving, the pending STOP
is lost and the shutter overruns to a mechanical end-stop. Recovery v2:
  - persists the in-flight direction/target/timestamp ONLY while moving;
  - on restart, if the flag is fresh, drives the cover to the end-stop in the
    direction it was already going (a guaranteed known reference), then resumes
    to the interrupted target;
  - resumes ONLY if the recovery drive finished undisturbed (idle and exactly on
    the end-stop), so an external command/button wins;
  - ignores stale/invalid/missing-timestamp flags.
"""

import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.cover import (  # noqa: E402
    ATTR_RECOVERY_DIRECTION,
    ATTR_RECOVERY_TARGET,
    ATTR_RECOVERY_TS,
    RECOVERY_MAX_AGE_SECONDS,
    VimarCover,
)

pytestmark = pytest.mark.integration  # Home Assistant required


def _make_cover(travel_up=36, travel_down=35, position=50):
    cover = VimarCover.__new__(VimarCover)
    cover._travel_time_up = travel_up
    cover._travel_time_down = travel_down
    cover._tb_position = position
    cover._tb_target = None
    cover._tb_operation = None
    cover._tb_start_time = None
    cover._recovery_pending = False
    cover._recovery_direction = None
    cover._recovery_target = None
    cover._device = None  # -> VimarEntity.extra_state_attributes returns {}
    cover.name = "Test Cover"
    cover.hass = MagicMock()
    cover._use_time_based_tracking = MagicMock(return_value=True)
    cover._get_position_mode = MagicMock(return_value="auto")
    return cover


def _old_state(direction="opening", target=50, age_s=30, with_ts=True):
    attrs = {"current_position": 30}
    if direction is not None:
        attrs[ATTR_RECOVERY_DIRECTION] = direction
    if target is not None:
        attrs[ATTR_RECOVERY_TARGET] = target
    if with_ts:
        attrs[ATTR_RECOVERY_TS] = (datetime.now() - timedelta(seconds=age_s)).isoformat()
    return SimpleNamespace(attributes=attrs)


# --- detection -------------------------------------------------------------

def test_detect_fresh_flag_arms_recovery():
    cover = _make_cover()
    cover._detect_pending_recovery(_old_state("opening", 45, age_s=20))
    assert cover._recovery_pending is True
    assert cover._recovery_direction == "opening"
    assert cover._recovery_target == 45


def test_detect_stale_flag_ignored():
    cover = _make_cover()
    cover._detect_pending_recovery(_old_state(age_s=RECOVERY_MAX_AGE_SECONDS + 60))
    assert cover._recovery_pending is False


def test_detect_future_timestamp_ignored():
    cover = _make_cover()
    cover._detect_pending_recovery(_old_state(age_s=-120))  # ts in the future
    assert cover._recovery_pending is False


def test_detect_missing_timestamp_ignored():
    cover = _make_cover()
    cover._detect_pending_recovery(_old_state(with_ts=False))
    assert cover._recovery_pending is False


def test_detect_invalid_direction_ignored():
    cover = _make_cover()
    cover._detect_pending_recovery(_old_state(direction="sideways"))
    assert cover._recovery_pending is False


# --- persistence in attributes --------------------------------------------

def test_attributes_persist_only_while_moving():
    cover = _make_cover()
    # idle -> no recovery attrs
    cover._tb_operation = None
    assert ATTR_RECOVERY_DIRECTION not in cover.extra_state_attributes
    # moving -> recovery attrs present
    cover._tb_operation = "closing"
    cover._tb_target = 20
    cover._tb_start_time = datetime.now()
    attrs = cover.extra_state_attributes
    assert attrs[ATTR_RECOVERY_DIRECTION] == "closing"
    assert attrs[ATTR_RECOVERY_TARGET] == 20
    assert ATTR_RECOVERY_TS in attrs


# --- one-shot start --------------------------------------------------------

def test_maybe_start_recovery_one_shot_when_available():
    cover = _make_cover()
    cover._recovery_pending = True
    cover._recovery_direction = "opening"
    cover._recovery_target = 40
    with patch.object(VimarCover, "available", new_callable=PropertyMock, return_value=True):
        cover._maybe_start_recovery()
        cover._maybe_start_recovery()  # second call must be a no-op
    assert cover._recovery_pending is False
    assert cover.hass.async_create_task.call_count == 1


def test_maybe_start_recovery_waits_until_available():
    cover = _make_cover()
    cover._recovery_pending = True
    with patch.object(VimarCover, "available", new_callable=PropertyMock, return_value=False):
        cover._maybe_start_recovery()
    assert cover._recovery_pending is True
    cover.hass.async_create_task.assert_not_called()


# --- recovery sequence -----------------------------------------------------

def _arm_recover_mocks(cover, reach_position):
    """Mock the drive so it lands on `reach_position`, and idle waits succeed."""
    async def _drive(**_):
        cover._tb_position = reach_position
        cover._tb_operation = None
    cover.async_open_cover = AsyncMock(side_effect=_drive)
    cover.async_close_cover = AsyncMock(side_effect=_drive)
    cover._tb_wait_idle = AsyncMock(return_value=True)
    cover.async_set_cover_position = AsyncMock()


async def test_recover_opening_drives_to_100_then_resumes():
    cover = _make_cover()
    _arm_recover_mocks(cover, reach_position=100)
    await cover._async_recover("opening", 50)
    cover.async_open_cover.assert_awaited_once()
    cover.async_close_cover.assert_not_awaited()
    cover.async_set_cover_position.assert_awaited_once()
    assert cover.async_set_cover_position.await_args.kwargs == {"position": 50}


async def test_recover_closing_drives_to_0_then_resumes():
    cover = _make_cover()
    _arm_recover_mocks(cover, reach_position=0)
    await cover._async_recover("closing", 30)
    cover.async_close_cover.assert_awaited_once()
    cover.async_set_cover_position.assert_awaited_once()
    assert cover.async_set_cover_position.await_args.kwargs == {"position": 30}


async def test_recover_endstop_target_no_resume():
    cover = _make_cover()
    _arm_recover_mocks(cover, reach_position=100)
    await cover._async_recover("opening", 100)  # target IS the end-stop
    cover.async_open_cover.assert_awaited_once()
    cover.async_set_cover_position.assert_not_awaited()


async def test_recover_interference_skips_resume():
    cover = _make_cover()
    _arm_recover_mocks(cover, reach_position=100)
    # Simulate something stopping the drive away from the end-stop.
    cover._tb_wait_idle = AsyncMock(side_effect=lambda timeout: setattr(cover, "_tb_position", 72) or True)
    await cover._async_recover("opening", 50)
    cover.async_set_cover_position.assert_not_awaited()


async def test_recover_bails_if_already_moving():
    cover = _make_cover()
    _arm_recover_mocks(cover, reach_position=100)
    cover._tb_operation = "closing"  # a move is already in progress
    await cover._async_recover("opening", 50)
    cover.async_open_cover.assert_not_awaited()
    cover.async_set_cover_position.assert_not_awaited()


async def test_recover_timeout_skips_resume():
    cover = _make_cover()
    _arm_recover_mocks(cover, reach_position=100)
    cover._tb_wait_idle = AsyncMock(return_value=False)  # never reached end-stop
    await cover._async_recover("opening", 50)
    cover.async_set_cover_position.assert_not_awaited()
