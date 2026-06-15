"""Regression tests for time-based cover drift / micro-movement fixes.

Covers the 2026.6.7 cover fixes (after the 2026.6.5 revert):

FIX #1 - false "physical button" detection: the post-STOP grace period must
  span at least one polling cycle (+margin) so the first poll after an HA stop
  falls inside the grace and resyncs _tb_last_updown instead of mistaking the
  latched up/down for a physical button press (which jumped position to 0/100).

FIX #2 - drift / no chatter: the stop is issued a relay-coast margin before the
  target (overshoot compensation, so partial moves land on target instead of
  creeping open), and a deadband ignores moves smaller than that same coast
  (which are not positionable). Tying both to _overshoot_pct guarantees that any
  move passing the deadband can never stop at its first tick -> no relay chatter.
"""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

# Import the integration as a package so relative imports resolve.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.cover import (  # noqa: E402
    ATTR_POSITION,
    GRACE_SECONDS,
    RELAY_DELAY,
    VimarCover,
)

pytestmark = pytest.mark.integration  # Home Assistant required


def _make_cover(travel_up=36, travel_down=35, position=50, poll_seconds=8):
    """Build a VimarCover with mocked HA deps, bypassing CoordinatorEntity init."""
    cover = VimarCover.__new__(VimarCover)
    cover._travel_time_up = travel_up
    cover._travel_time_down = travel_down
    cover._tb_position = position
    cover._tb_target = None
    cover._tb_operation = None
    cover._tb_start_time = None
    cover._tb_start_position = None
    cover._tb_last_reported_position = position
    cover.name = "Test Cover"

    coordinator = MagicMock()
    coordinator.update_interval = timedelta(seconds=poll_seconds)
    cover.coordinator = coordinator

    cover.hass = MagicMock()
    cover.async_write_ha_state = MagicMock()
    # Time-based path + no native sensor.
    cover._use_time_based_tracking = MagicMock(return_value=True)
    cover.has_state = MagicMock(return_value=False)
    # Spy on the side effects of a move.
    cover._tb_start_tracking = AsyncMock()
    cover.change_state = MagicMock()
    return cover


def test_overshoot_pct_matches_relay_coast():
    cover = _make_cover(travel_up=36, travel_down=35)
    assert cover._overshoot_pct(True) == pytest.approx(RELAY_DELAY / 36 * 100)
    assert cover._overshoot_pct(False) == pytest.approx(RELAY_DELAY / 35 * 100)


def test_grace_seconds_spans_a_poll_cycle():
    """FIX #1: grace must cover at least one poll interval (+margin)."""
    cover = _make_cover(poll_seconds=8)
    assert cover._grace_seconds() > 8  # the bug was grace(6) < poll(8)
    # Long poll intervals must scale too.
    cover_slow = _make_cover(poll_seconds=30)
    assert cover_slow._grace_seconds() >= 30
    # Falls back to the floor when no interval is known.
    cover.coordinator.update_interval = None
    assert cover._grace_seconds() == GRACE_SECONDS


async def test_deadband_ignores_subovershoot_move():
    """FIX #2: a move <= relay coast is not positionable -> ignored (no command)."""
    cover = _make_cover(travel_up=36, position=50)
    # overshoot ~1.39% -> a 1% nudge must be ignored.
    await cover.async_set_cover_position(**{ATTR_POSITION: 51})
    cover._tb_start_tracking.assert_not_awaited()
    cover.change_state.assert_not_called()


async def test_supraovershoot_move_starts_tracking():
    """FIX #2: a real automation nudge (2%) still moves the cover."""
    cover = _make_cover(travel_up=36, position=38)
    await cover.async_set_cover_position(**{ATTR_POSITION: 40})
    cover._tb_start_tracking.assert_awaited_once()
    args, kwargs = cover._tb_start_tracking.await_args
    assert args[0] is True  # opening
    assert kwargs["target"] == 40
    cover.change_state.assert_called_once_with("up/down", "0")


async def test_set_position_equal_is_noop():
    cover = _make_cover(position=40)
    await cover.async_set_cover_position(**{ATTR_POSITION: 40})
    cover._tb_start_tracking.assert_not_awaited()
    cover.change_state.assert_not_called()


def test_no_chatter_first_tick():
    """FIX #2: a move just above the deadband does not stop at its first tick."""
    cover = _make_cover(travel_up=36, position=38)
    # Simulate a tracking open 38 -> 40 that just started (elapsed ~0).
    cover._tb_operation = "opening"
    cover._tb_target = 40
    cover._tb_start_position = 38
    cover._tb_start_time = datetime.now()
    cover._tb_position = 38

    cover._tb_update_position(now=datetime.now())

    # At the first tick the position is still the start; with the deadband
    # guaranteeing delta > stop_margin it must NOT schedule a stop.
    cover.hass.async_create_task.assert_not_called()


def test_stop_fires_with_overshoot_margin():
    """FIX #2: an intermediate target stops a coast-margin early (sends STOP)."""
    cover = _make_cover(travel_up=36, position=38)
    cover._tb_operation = "opening"
    cover._tb_target = 40
    cover._tb_start_position = 38
    cover._tb_start_time = datetime.now()
    # Freeze the calculated position right at the early-stop threshold.
    cover._tb_calculate_position = MagicMock()
    cover._tb_position = 39  # >= 40 - overshoot(~1.39) -> should stop
    cover.async_stop_cover = AsyncMock()

    cover._tb_update_position(now=datetime.now())

    assert cover._tb_position == 40  # snapped to target
    cover.hass.async_create_task.assert_called_once()  # STOP scheduled
