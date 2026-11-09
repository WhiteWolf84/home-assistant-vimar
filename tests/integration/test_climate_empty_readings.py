"""A temperature nobody read is unknown, not 0 degrees (Home Assistant required).

The rule set for the operating mode on 2026-10-27 - the state shown in Home
Assistant comes from a valid reading or is unknown - does not hold yet for the
numbers of the same thermostat: the measured temperature, the setpoint and the
humidity are read with `float(value or 0)`.

* An empty CURRENT_VALUE - which the web server does return, the SAI2 areas
  read '' and '0' after its restart on 2026-10-05 - is published as 0.0: a
  plausible reading of a freezing room, recorded in the long-term statistics
  and able to trigger any automation watching the temperature.
* A value that is not a number raises ValueError inside the state write.

The companion temperature sensor (sensor.VimarClimateTempSensor) already
returns None in both cases.

Also pinned here: set_temperature() from auto/reduction/protection writes the
current `unita` back to the thermostat. The value does not change, and `unita`
is flagged SYNCDB, so it costs a GETVALUE and a SETVALUE in the middle of the
one sequence whose order matters (mode first, setpoint last).
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from custom_components.vimar.climate import VimarClimate  # noqa: E402

pytestmark = pytest.mark.integration  # Home Assistant required

# Type II thermostat (uses 'regolazione'), see const.py.
FUNZ_AUTO = "0"
FUNZ_MANUAL = "1"

PENDING = "review 2026-11 #3.5: an empty reading is published as 0.0"


def _climate(**values) -> VimarClimate:
    status = {
        "funzionamento": {"status_id": "F", "status_value": FUNZ_MANUAL},
        "regolazione": {"status_id": "R", "status_value": "1"},
        "setpoint": {"status_id": "S", "status_value": "26.0"},
        "temperatura_misurata": {"status_id": "T", "status_value": "26.4"},
        "umidita": {"status_id": "H", "status_value": "55"},
        "unita": {"status_id": "U", "status_value": "0"},
    }
    for name, value in values.items():
        status[name]["status_value"] = value

    climate = VimarClimate.__new__(VimarClimate)
    climate._device = {"object_type": "CH_HVAC_NoZonaNeutra", "status": status}
    climate._device_id = "100"
    climate.entity_id = "climate.cameretta"

    connection = MagicMock()
    connection.get_optionals_param.return_value = "NO-OPTIONALS"
    climate._vimarconnection = connection

    coordinator = MagicMock()
    coordinator._changed_device_ids = set()
    climate._coordinator = coordinator
    climate.coordinator = coordinator

    climate.hass = MagicMock()
    climate._logger = MagicMock()
    climate.async_write_ha_state = MagicMock()
    return climate


READINGS = {
    "measured temperature": ("temperatura_misurata", "native_current_temperature"),
    "setpoint": ("setpoint", "native_target_temperature"),
    "humidity": ("umidita", "current_humidity"),
}


@pytest.mark.parametrize(("status", "attribute"), READINGS.values(), ids=READINGS.keys())
def test_a_valid_reading_is_published(status, attribute):
    climate = _climate(**{status: "21.5"})

    assert getattr(climate, attribute) == 21.5


@pytest.mark.xfail(strict=True, reason=PENDING)
@pytest.mark.parametrize(("status", "attribute"), READINGS.values(), ids=READINGS.keys())
def test_an_empty_reading_is_unknown(status, attribute):
    climate = _climate(**{status: ""})

    assert getattr(climate, attribute) is None


@pytest.mark.xfail(
    strict=True, reason="review 2026-11 #3.5: a non-numeric reading raises in the state write"
)
@pytest.mark.parametrize(("status", "attribute"), READINGS.values(), ids=READINGS.keys())
def test_a_reading_that_is_not_a_number_is_unknown(status, attribute):
    climate = _climate(**{status: "n/a"})

    assert getattr(climate, attribute) is None


def _written_status_ids(climate: VimarClimate) -> list[str]:
    enqueue = climate.coordinator.enqueue_device_writes
    assert enqueue.call_count == 1
    (writes,) = enqueue.call_args.args
    return [status_id for status_id, _value, _optionals in writes]


async def test_the_manual_override_sends_the_mode_first_and_the_setpoint_last():
    """Holds today and must keep holding when `unita` leaves the batch."""
    climate = _climate(funzionamento=FUNZ_AUTO)

    await climate.async_set_temperature(temperature=24.0)

    written = _written_status_ids(climate)
    assert written[0] == "F"
    assert written[-1] == "S"


@pytest.mark.xfail(
    strict=True, reason="review 2026-11 #3.6: the manual override rewrites the unchanged 'unita'"
)
async def test_the_manual_override_does_not_rewrite_the_unit():
    climate = _climate(funzionamento=FUNZ_AUTO)

    await climate.async_set_temperature(temperature=24.0)

    assert "U" not in _written_status_ids(climate)
