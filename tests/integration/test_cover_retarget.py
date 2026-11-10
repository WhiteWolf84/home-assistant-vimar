"""A new command while a time-based cover is already moving (HA required).

_tb_start_tracking() used to return at once when the requested operation was
the one in progress, before it stored the target. The relay command was sent
again (harmless: same direction), but the tracking kept the old target. A
same-direction command now replaces the target and nothing else.

Recorded on the plant on 2026-11-10, stanza multimediale,
CH_ShutterWithoutPosition_Automation, travel 36 s up / 35 s down; both stops
confirmed by eye:

    6a  00:07:00.366 cover: Tracking opening from 0% to 30%
        00:07:00.380 SETVALUE idobject=347 value=0
        00:07:03.389 SETVALUE idobject=347 value=0   (set_cover_position 70 at
                                                      7%: no "Tracking" line)
        00:07:11.213 cover: Reached target 30%, sending STOP
        00:07:11.214 SETVALUE idobject=345 value=1

    6b  00:09:11.726 cover: Tracking opening from 0% to 30%
        00:09:11.727 SETVALUE idobject=347 value=0
        00:09:14.737 SETVALUE idobject=347 value=0   (open_cover at 6-7%)
        00:09:22.573 cover: Reached target 30%, sending STOP   <- 100% was asked
        00:09:22.575 SETVALUE idobject=345 value=1

The replay tests below run those two sequences on the same clock (command at
0 s, second command at +3 s, the stop that should not happen at +10.85 s).

Two things must not change:

* the movement under way keeps being measured from the first command: start
  time and start position are not reset by a same-direction command, or the
  travel already done would be counted from the wrong moment;
* towards 0% and 100% no STOP is ever sent - the cover runs to its mechanical
  end stop, which is what resets the drift of a position estimated by time.
  The closings of the same session logged "Reached end-stop 0%, mechanical
  stop (no STOP command)", which is correct.
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import custom_components.vimar.cover as cover_module  # noqa: E402
from custom_components.vimar.cover import ATTR_POSITION, VimarCover  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

TRAVEL_UP = 36
TRAVEL_DOWN = 35
UP_DOWN = "up/down"  # 347 on the plant: "0" opens, "1" closes
STOP = "stop up/stop down"  # 345 on the plant
STOP_COMMAND = "vimar_cover_stop"  # task that sends the STOP
MECHANICAL_STOP = "vimar_cover_stop_tracking"  # task that only ends the tracking


class _Clock:
    """Stands in for dt_util.utcnow: the tests decide what time it is."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 10, 0, 7, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class _Rig:
    """A time-based cover, the commands it wrote and the stops it scheduled."""

    def __init__(self, monkeypatch, position: int) -> None:
        self.clock = _Clock()
        self._started = self.clock.now
        monkeypatch.setattr(cover_module.dt_util, "utcnow", self.clock)
        monkeypatch.setattr(
            cover_module, "async_track_time_interval", lambda *args, **kwargs: lambda: None
        )
        self.writes: list[tuple[str, str]] = []
        self.contexts: list[object] = []
        self.tasks: list[str] = []

        cover = VimarCover.__new__(VimarCover)
        cover._travel_time_up = TRAVEL_UP
        cover._travel_time_down = TRAVEL_DOWN
        cover._tb_position = position
        cover._tb_target = None
        cover._tb_operation = None
        cover._tb_start_time = None
        cover._tb_start_position = None
        cover._tb_last_reported_position = position
        cover._tb_last_updown = "1"
        cover._tb_unsub = None
        cover._tb_ha_command_active = False
        cover._tb_ha_stop_time = None
        cover._tb_planned_stop = False
        cover._recovery_pending = False
        cover._background_tasks = set()
        cover._device_id = "342"
        cover._device = {
            "object_id": "342",
            "device_friendly_name": "Tapparella",
            "status": {
                UP_DOWN: {"status_id": "347", "status_value": "1"},
                STOP: {"status_id": "345", "status_value": "0"},
            },
        }

        coordinator = MagicMock()
        coordinator.update_interval = timedelta(seconds=8)
        cover.coordinator = coordinator

        hass = MagicMock()
        hass.async_create_background_task = self._schedule
        cover.hass = hass
        cover.async_write_ha_state = MagicMock()
        cover._use_time_based_tracking = MagicMock(return_value=True)
        cover.change_state = self._change_state
        self.cover = cover

    def _schedule(self, coro, name=None):
        """Record which stop was scheduled; the coroutine itself is not run."""
        self.tasks.append(name)
        coro.close()
        return MagicMock()

    def _change_state(self, state: str, value: object, write_context: object = None) -> None:
        """What change_state() does to the local device: the optimistic value."""
        self.writes.append((state, str(value)))
        self.contexts.append(write_context)
        self.cover._device["status"][state]["status_value"] = str(value)

    def at(self, seconds: float) -> None:
        """Move the clock to `seconds` after the start and run one tracking tick."""
        self.clock.now = self._started + timedelta(seconds=seconds)
        self.cover._tb_update_position(self.clock.now)

    def poll(self) -> None:
        """A coordinator update reaching the cover ("resynchronised after a local write")."""
        self.cover._tb_check_vimar_state()

    @property
    def stop_commands(self) -> list[tuple[str, str]]:
        return [write for write in self.writes if write[0] == STOP]


@pytest.fixture
def closed(monkeypatch) -> _Rig:
    return _Rig(monkeypatch, position=0)


# --- the two sequences recorded on the plant --------------------------------


async def test_6a_set_position_30_then_70_stops_at_70(closed):
    cover = closed.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    closed.at(3.0)
    assert cover._tb_position == 7  # as logged: "at 7%"

    await cover.async_set_cover_position(**{ATTR_POSITION: 70})
    closed.at(10.85)  # 00:07:11.213 - where the STOP was sent

    assert closed.tasks == [], "stopped at the first target"
    assert cover._tb_operation == "opening"

    closed.at(26.0)

    assert closed.tasks == [STOP_COMMAND]
    assert cover._tb_position == 70


async def test_6b_set_position_30_then_open_runs_to_the_end_stop(closed):
    cover = closed.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    closed.at(3.0)

    await cover.async_open_cover()
    closed.at(10.85)  # 00:09:22.573 - where the STOP was sent

    assert closed.tasks == [], "a STOP was sent to a cover asked to open fully"

    closed.at(TRAVEL_UP + 1.0)

    assert closed.tasks == [MECHANICAL_STOP]
    assert cover._tb_position == 100
    assert closed.stop_commands == []


async def test_the_relay_command_is_sent_again_as_logged(closed):
    """Both sequences show a second SETVALUE 347=0: that part already works."""
    await closed.cover.async_set_cover_position(**{ATTR_POSITION: 30})
    closed.at(3.0)

    await closed.cover.async_open_cover()

    assert closed.writes == [(UP_DOWN, "0"), (UP_DOWN, "0")]


# --- same direction ---------------------------------------------------------


async def test_a_nearer_target_the_same_way_replaces_the_first(closed):
    """30 -> 10 while rising and still below 10: stop earlier, same direction."""
    cover = closed.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    closed.at(3.0)

    await cover.async_set_cover_position(**{ATTR_POSITION: 10})

    assert cover._tb_operation == "opening"
    assert cover._tb_target == 10

    closed.at(4.5)  # (4.5 - 0.5) / 36 = 11%

    assert closed.tasks == [STOP_COMMAND]
    assert cover._tb_position == 10


async def test_close_during_a_partial_closing_runs_to_the_end_stop(monkeypatch):
    rig = _Rig(monkeypatch, position=100)
    cover = rig.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 60})
    rig.at(3.0)

    await cover.async_close_cover()

    assert cover._tb_operation == "closing"
    assert cover._tb_target == 0

    rig.at(TRAVEL_DOWN + 1.0)

    assert rig.tasks == [MECHANICAL_STOP]
    assert cover._tb_position == 0
    assert rig.stop_commands == []


async def test_a_same_direction_command_does_not_restart_the_measurement(closed):
    """Holds today and must keep holding once the target is updated."""
    cover = closed.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    started_at = cover._tb_start_time
    closed.at(3.0)

    await cover.async_set_cover_position(**{ATTR_POSITION: 70})

    assert cover._tb_start_time == started_at
    assert cover._tb_start_position == 0


# --- a new target the cover is already at -----------------------------------


@pytest.mark.parametrize("target", [19, 20], ids=["same position", "within the deadband"])
async def test_a_target_within_reach_stops_the_moving_cover(closed, target):
    """At 19% on the way to 30%: 19 or 20 cannot be placed better than "here"."""
    cover = closed.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    closed.at(7.5)
    assert cover._tb_position == 19

    await cover.async_set_cover_position(**{ATTR_POSITION: target})

    assert closed.stop_commands == [(STOP, "1")]
    assert cover._tb_operation is None
    assert cover._tb_position == 19


@pytest.mark.parametrize(
    ("position", "target", "operation", "relay"),
    [(99, 100, "opening", "0"), (1, 0, "closing", "1")],
    ids=["opening, 100 asked at 99%", "opening, 0 asked at 1%"],
)
async def test_an_end_stop_within_reach_is_still_run_to(closed, position, target, operation, relay):
    """First exception: 0 and 100 never get a STOP, whatever the estimate says."""
    cover = closed.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})
    cover._tb_position = position  # the estimate, a tick before the request

    await cover.async_set_cover_position(**{ATTR_POSITION: target})

    assert closed.stop_commands == []
    assert closed.tasks == []
    assert cover._tb_operation == operation
    assert cover._tb_target == target
    assert closed.writes[-1] == (UP_DOWN, relay)


@pytest.mark.parametrize("target", [19, 20], ids=["same position", "within the deadband"])
async def test_a_target_within_reach_sends_nothing_to_a_cover_at_rest(monkeypatch, target):
    """Second exception: at rest, an intermediate target within reach sends nothing."""
    rig = _Rig(monkeypatch, position=19)

    await rig.cover.async_set_cover_position(**{ATTR_POSITION: target})

    assert rig.writes == []
    assert rig.tasks == []
    assert rig.cover._tb_operation is None
    assert rig.cover._tb_position == 19


@pytest.mark.parametrize(
    ("position", "target", "operation", "relay", "travel"),
    [
        (99, 100, "opening", "0", TRAVEL_UP),
        (1, 0, "closing", "1", TRAVEL_DOWN),
        (100, 100, "opening", "0", TRAVEL_UP),
        (0, 0, "closing", "1", TRAVEL_DOWN),
    ],
    ids=["99 to 100", "1 to 0", "100 to 100", "0 to 0"],
)
async def test_an_end_stop_is_run_to_from_rest_whatever_the_estimate(
    monkeypatch, position, target, operation, relay, travel
):
    """An estimate of 99% may be a cover that is not fully open.

    set_position 0 or 100 on a cover at rest sends the relay command alone,
    like open_cover and close_cover: no STOP, the limit switch stops the motor
    and the estimate is back on a known position.
    """
    rig = _Rig(monkeypatch, position=position)

    await rig.cover.async_set_cover_position(**{ATTR_POSITION: target})

    assert rig.writes == [(UP_DOWN, relay)]
    assert rig.cover._tb_operation == operation
    assert rig.cover._tb_target == target

    rig.at(travel + 1.0)

    assert rig.cover._tb_position == target
    assert rig.stop_commands == []
    assert rig.tasks == [MECHANICAL_STOP]


# --- reversal ---------------------------------------------------------------


async def test_a_target_already_passed_reverses_the_cover(closed):
    """30 -> 10 while rising and already at 19%: a closing, from where it is."""
    cover = closed.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    closed.at(7.5)  # (7.5 - 0.5) / 36 = 19%
    assert cover._tb_position == 19

    await cover.async_set_cover_position(**{ATTR_POSITION: 10})

    assert cover._tb_operation == "closing"
    assert cover._tb_target == 10
    assert cover._tb_start_position == 19
    assert cover._tb_start_time == closed.clock.now
    assert closed.writes == [(UP_DOWN, "0"), (UP_DOWN, "1")]
    assert closed.stop_commands == []


async def test_close_during_an_opening_reverses_to_the_end_stop(closed):
    cover = closed.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    closed.at(7.5)

    await cover.async_close_cover()

    assert cover._tb_operation == "closing"
    assert cover._tb_target == 0

    closed.at(7.5 + TRAVEL_DOWN)

    assert closed.tasks == [MECHANICAL_STOP]
    assert cover._tb_position == 0
    assert closed.stop_commands == []


# --- the coordinator update that follows every command ----------------------


async def test_the_poll_after_a_command_does_not_end_the_tracking(closed):
    """3-8 s after each write the coordinator hands the cover its own value back."""
    cover = closed.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    closed.at(3.0)
    await cover.async_open_cover()
    closed.at(6.0)

    closed.poll()

    assert closed.tasks == [], "the poll was taken for a physical stop"
    assert cover._tb_operation == "opening"
    assert cover._tb_start_position == 0


async def test_the_poll_after_a_retarget_leaves_the_new_target(closed):
    cover = closed.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    closed.at(3.0)
    await cover.async_set_cover_position(**{ATTR_POSITION: 70})
    closed.at(6.0)

    closed.poll()

    assert cover._tb_target == 70
    assert closed.tasks == []


# --- end stops --------------------------------------------------------------


async def test_a_full_opening_gets_no_stop_command(closed):
    await closed.cover.async_open_cover()

    closed.at(TRAVEL_UP + 1.0)

    assert closed.tasks == [MECHANICAL_STOP]
    assert closed.cover._tb_position == 100
    assert closed.stop_commands == []


async def test_a_full_closing_gets_no_stop_command(monkeypatch):
    rig = _Rig(monkeypatch, position=100)

    await rig.cover.async_close_cover()
    rig.at(TRAVEL_DOWN + 1.0)

    assert rig.tasks == [MECHANICAL_STOP]
    assert rig.cover._tb_position == 0
    assert rig.stop_commands == []
