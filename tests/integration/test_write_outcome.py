"""What an entity does when the web server did not take its write (HA required).

change_state() shows the new value at once, before the web server has it. The
outcome of the write used to be thrown away, so a write that was refused left
the optimistic value standing for the 15 s of the write-guard and then
"jumped back" with nothing in the log to say why.

The coordinator's write worker now hands every batch's outcomes back to the
entity (review 2026-11 #3.1):

* refused - known not to have been carried out: no session even after the one
  resend, or never sent - the value goes back to what it was, the write-guard
  is dropped, a WARNING says so;
* no answer - it may have been executed: nothing is assumed, the poll tells.

For a time-based cover the outcome also decides where the tracking stands:
measured from the request the web server accepted when the first one was
refused, dropped when the cover never started, and at the end stop when the
STOP was the one refused. At 0% and 100% no STOP is ever sent, here as
everywhere else.

None of these cases has been seen on the plant: the ten writes measured there
on 2026-11-10 were all accepted at the first request.
"""

import asyncio
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import custom_components.vimar.cover as cover_module  # noqa: E402
from custom_components.vimar.cover import ATTR_POSITION, VimarCover  # noqa: E402
from custom_components.vimar.switch import VimarSwitch  # noqa: E402
from custom_components.vimar.vimar_coordinator import (  # noqa: E402
    VimarDataUpdateCoordinator,
)
from custom_components.vimar.vimarlink.vimarlink import CommandOutcome  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

ACCEPTED = CommandOutcome("DPCM-0000", sent_at=0.0)
REFUSED = CommandOutcome("LGMG-3019", resent=True, sent_at=0.0)
NOT_SENT = CommandOutcome(None, not_sent=True)
NO_ANSWER = CommandOutcome(None, sent_at=0.0)


# --- any entity -------------------------------------------------------------


def _switch(value: str = "0") -> VimarSwitch:
    switch = VimarSwitch.__new__(VimarSwitch)
    switch._device = {
        "object_id": "404",
        "device_friendly_name": "Vmc Rinnovo",
        "status": {"on/off": {"status_id": "405", "status_value": value}},
    }
    switch._device_id = "404"
    connection = MagicMock()
    connection.get_optionals_param.return_value = "NO-OPTIONALS"
    switch._vimarconnection = connection
    coordinator = MagicMock()
    coordinator._changed_device_ids = set()
    switch._coordinator = coordinator
    switch.coordinator = coordinator
    switch.hass = MagicMock()
    switch._logger = logging.getLogger("custom_components.vimar.switch")
    switch.async_write_ha_state = MagicMock()
    return switch


def _answer(entity, outcome: CommandOutcome) -> None:
    """Play the write worker: hand the queued batch its outcomes."""
    enqueue = entity.coordinator.enqueue_device_writes
    (writes,) = enqueue.call_args.args
    enqueue.call_args.kwargs["on_results"]([(write, outcome) for write in writes])


async def test_the_value_is_shown_before_the_answer():
    switch = _switch("0")

    await switch.async_turn_on()

    assert switch.is_on is True


@pytest.mark.parametrize("outcome", [REFUSED, NOT_SENT], ids=["refused", "not sent"])
async def test_a_refused_write_takes_the_optimistic_value_back(outcome, caplog):
    switch = _switch("0")
    await switch.async_turn_on()
    switch.async_write_ha_state.reset_mock()

    with caplog.at_level(logging.WARNING):
        _answer(switch, outcome)

    assert switch.is_on is False
    switch.coordinator.release_write_guard.assert_called_once_with("405")
    switch.async_write_ha_state.assert_called_once()
    assert "refused by the web server" in caplog.text


async def test_an_accepted_write_keeps_its_value():
    switch = _switch("0")
    await switch.async_turn_on()
    switch.async_write_ha_state.reset_mock()

    _answer(switch, ACCEPTED)

    assert switch.is_on is True
    switch.coordinator.release_write_guard.assert_not_called()
    switch.async_write_ha_state.assert_not_called()


async def test_a_write_without_an_answer_is_left_to_the_poll():
    """It may have been executed: the value and its write-guard stay."""
    switch = _switch("0")
    await switch.async_turn_on()

    _answer(switch, NO_ANSWER)

    assert switch.is_on is True
    switch.coordinator.release_write_guard.assert_not_called()


async def test_a_refused_write_does_not_undo_a_later_command():
    """on, then off before the first answer: the refusal of "on" changes nothing."""
    switch = _switch("0")
    await switch.async_turn_on()
    first = switch.coordinator.enqueue_device_writes.call_args
    await switch.async_turn_off()

    (writes,) = first.args
    first.kwargs["on_results"]([(write, REFUSED) for write in writes])

    assert switch.get_state("on/off") == "0"


# --- the coordinator's worker -----------------------------------------------


def test_the_worker_returns_the_outcome_of_each_write():
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)
    coordinator.vimarconnection = MagicMock()
    coordinator.vimarconnection.set_device_status.side_effect = [ACCEPTED, REFUSED]
    writes = [("405", "1", "NO-OPTIONALS"), ("406", "2", "NO-OPTIONALS")]

    results = coordinator._execute_device_writes(writes)

    assert results == [(writes[0], ACCEPTED), (writes[1], REFUSED)]


async def test_the_worker_hands_the_outcomes_back_to_who_queued_the_batch():
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)
    coordinator.vimarconnection = MagicMock()
    coordinator.vimarconnection.set_device_status.return_value = REFUSED

    async def _job(func, *args):
        return func(*args)

    coordinator.hass = MagicMock()
    coordinator.hass.async_add_executor_job = _job
    coordinator._write_queue = asyncio.Queue()
    writes = [("405", "1", "NO-OPTIONALS")]
    received: list = []
    coordinator._write_queue.put_nowait((writes, received.append))
    coordinator._write_queue.put_nowait((writes, None))  # nobody waiting: fine too

    worker = asyncio.create_task(coordinator._write_worker())
    await coordinator._write_queue.join()
    worker.cancel()

    assert received == [[(writes[0], REFUSED)]]


def test_releasing_a_write_guard_lets_the_poll_through():
    coordinator = VimarDataUpdateCoordinator.__new__(VimarDataUpdateCoordinator)
    coordinator._pending_write_guards = {"405": time.monotonic() + 15, "406": time.monotonic() + 15}
    devices = {
        "404": {
            "status": {
                "a": {"status_id": "405", "status_value": "1"},
                "b": {"status_id": "406", "status_value": "1"},
            }
        }
    }

    coordinator.release_write_guard("405")
    coordinator._apply_slim_results(
        devices,
        [{"status_id": "405", "status_value": "0"}, {"status_id": "406", "status_value": "0"}],
    )

    assert devices["404"]["status"]["a"]["status_value"] == "0"
    assert devices["404"]["status"]["b"]["status_value"] == "1"


# --- time-based covers ------------------------------------------------------

UP_DOWN = "up/down"
STOP = "stop up/stop down"


class _CoverRig:
    """A time-based cover with the real change_state and a clock of its own."""

    def __init__(self, monkeypatch, position: int = 0) -> None:
        self.now = datetime(2026, 11, 10, 0, 7, 0, tzinfo=UTC)
        self._started = self.now
        monkeypatch.setattr(cover_module.dt_util, "utcnow", lambda: self.now)
        monkeypatch.setattr(
            cover_module, "async_track_time_interval", lambda *args, **kwargs: lambda: None
        )
        self.tasks: list[str] = []

        cover = VimarCover.__new__(VimarCover)
        cover._travel_time_up = 36
        cover._travel_time_down = 35
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
        connection = MagicMock()
        connection.get_optionals_param.return_value = "NO-OPTIONALS"
        cover._vimarconnection = connection
        coordinator = MagicMock()
        coordinator.update_interval = timedelta(seconds=8)
        coordinator._changed_device_ids = set()
        cover._coordinator = coordinator
        cover.coordinator = coordinator
        hass = MagicMock()
        hass.async_create_background_task = self._schedule
        cover.hass = hass
        cover._logger = logging.getLogger("custom_components.vimar.cover")
        cover.async_write_ha_state = MagicMock()
        cover._use_time_based_tracking = MagicMock(return_value=True)
        self.cover = cover

    def _schedule(self, coro, name=None):
        self.tasks.append(name)
        coro.close()
        return MagicMock()

    def at(self, seconds: float) -> None:
        self.now = self._started + timedelta(seconds=seconds)
        self.cover._tb_update_position(self.now)

    def answer(self, call_index: int, outcome: CommandOutcome) -> None:
        call = self.cover.coordinator.enqueue_device_writes.call_args_list[call_index]
        (writes,) = call.args
        call.kwargs["on_results"]([(write, outcome) for write in writes])

    @property
    def sent(self) -> list[tuple[str, str]]:
        return [
            (status_id, value)
            for call in self.cover.coordinator.enqueue_device_writes.call_args_list
            for status_id, value, _optionals in call.args[0]
        ]


async def test_a_resent_start_is_measured_from_the_request_that_was_accepted(monkeypatch):
    """Refused at 0 s, accepted 4 s later: the motor started 4 s later."""
    rig = _CoverRig(monkeypatch)
    cover = rig.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    rig.now += timedelta(seconds=4.2)  # the answer arrives 0.2 s after the resend

    rig.answer(0, CommandOutcome("DPCM-0000", resent=True, sent_at=time.monotonic() - 0.2))

    measured_from = (cover._tb_start_time - rig._started).total_seconds()
    assert measured_from == pytest.approx(4.0, abs=0.05)
    assert cover._tb_operation == "opening"
    assert cover._tb_start_position == 0

    rig.at(4.0 + 3.0)  # 3 s after the accepted request: 7%, as on the plant

    assert cover._tb_position == 7


async def test_an_accepted_start_leaves_the_tracking_alone(monkeypatch):
    rig = _CoverRig(monkeypatch)
    cover = rig.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    started_at = cover._tb_start_time
    rig.now += timedelta(seconds=0.02)

    rig.answer(0, ACCEPTED)

    assert cover._tb_start_time == started_at


async def test_a_refused_start_drops_the_tracking_and_sends_no_stop(monkeypatch, caplog):
    """The cover never moved: it is where it was, and there is nothing to stop."""
    rig = _CoverRig(monkeypatch, position=20)
    cover = rig.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 60})
    rig.at(3.0)
    assert cover._tb_position == 27

    with caplog.at_level(logging.WARNING):
        rig.answer(0, REFUSED)

    assert cover._tb_operation is None
    assert cover._tb_position == 20
    assert cover.get_state(UP_DOWN) == "1"  # the optimistic "0" was taken back
    assert rig.sent == [("347", "0")]  # no STOP, no second command
    assert rig.tasks == []
    assert "did not move" in caplog.text

    cover._tb_check_vimar_state()  # the next poll must not read a physical button

    assert cover._tb_position == 20


async def test_the_refusal_of_a_superseded_start_is_ignored(monkeypatch):
    """Opening refused, but a closing was commanded meanwhile: that one stands."""
    rig = _CoverRig(monkeypatch, position=50)
    cover = rig.cover
    await cover.async_open_cover()
    await cover.async_close_cover()

    rig.answer(0, REFUSED)

    assert cover._tb_operation == "closing"
    assert cover._tb_target == 0


async def test_a_retarget_write_never_moves_the_start_of_the_tracking(monkeypatch):
    """Its relay command starts nothing, so its outcome is not about the movement."""
    rig = _CoverRig(monkeypatch)
    cover = rig.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    started_at = cover._tb_start_time
    rig.at(3.0)
    await cover.async_set_cover_position(**{ATTR_POSITION: 70})

    rig.answer(1, CommandOutcome("DPCM-0000", resent=True, sent_at=time.monotonic()))
    assert cover._tb_start_time == started_at

    rig.answer(1, REFUSED)
    assert cover._tb_operation == "opening"
    assert cover._tb_target == 70


async def test_a_refused_stop_leaves_the_cover_at_its_end_stop(monkeypatch, caplog):
    """The motor was not stopped: it runs on until the mechanical end stop."""
    rig = _CoverRig(monkeypatch)
    cover = rig.cover
    await cover.async_set_cover_position(**{ATTR_POSITION: 30})
    rig.at(5.0)
    await cover.async_stop_cover()
    assert cover._tb_operation is None
    assert rig.sent[-1] == ("345", "1")

    with caplog.at_level(logging.WARNING):
        rig.answer(1, REFUSED)

    assert cover._tb_position == 100
    assert "runs on to its end stop" in caplog.text


async def test_an_end_stop_run_has_no_stop_to_be_refused(monkeypatch):
    rig = _CoverRig(monkeypatch)
    await rig.cover.async_open_cover()

    rig.at(37.0)

    assert rig.sent == [("347", "0")]
    assert rig.tasks == ["vimar_cover_stop_tracking"]
