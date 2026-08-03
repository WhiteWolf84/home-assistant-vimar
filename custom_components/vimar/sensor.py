"""Platform for sensor integration."""

# import copy
import logging
from typing import NamedTuple

from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor.const import (
    DEVICE_CLASS_STATE_CLASSES,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.util.enum import try_parse_enum

from .const import DEVICE_TYPE_CLIMATES, ENERGY_METER_OBJECT_TYPES
from .const import DEVICE_TYPE_SENSORS as CURR_PLATFORM
from .vimar_entity import VimarEntity, vimar_setup_entry
from .vimarlink.device_types import (
    KNX_TEMPERATURE_OBJECT_TYPES,
    KNX_WINDSPEED_OBJECT_TYPES,
)

# SCAN_INTERVAL = timedelta(seconds=20)
# MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=5)
# PARALLEL_UPDATES = 2
# see: https://developers.home-assistant.io/docs/core/entity/sensor/

_LOGGER = logging.getLogger(__name__)


class SensorSpec(NamedTuple):
    """The unit and device class a single Vimar measurement maps to.

    Kept index-addressable (it is a tuple) because the previous version of
    class_and_units() returned a plain ``[unit, device_class]`` list.
    """

    unit: str | None
    device_class: SensorDeviceClass | None


#: Name parts marking a field that is a flag, a counter reset or a category -
#: not a reading of anything. The webserver names these after the quantity they
#: relate to (`temperature_alarm`, `wind_speed_reset`, `temperature_request_minmax`),
#: so any rule that asks "what does this measure?" claims them first and dresses
#: a 0/1 flag up as a measurement: `temperature_alarm` was published as 0 °C,
#: indistinguishable from a real freezing reading.
NOT_A_READING = frozenset({"alarm", "reset", "request", "minmax", "history", "mode", "modo"})

#: Whole field names carrying a category or an operating mode rather than a
#: quantity. `fase` is the installation's phase type - "monofase" or "trifase" -
#: and matched the rule written for per-phase currents, so it was published in
#: ampere; `forzatura` and `funzionamento` are load-control modes that were
#: published as 0 kW.
NOT_A_READING_FIELDS = frozenset({"fase", "forzatura", "funzionamento"})

#: Instantaneous power flows on a load-control device, in kW. Confirmed on real
#: hardware: each one has a cumulative `energia_totale_*` counterpart in kWh
#: (`consumo_totale` 1.490 kW next to `energia_totale_consumo` 66698.4 kWh), and
#: the flows add up - consumo = autoconsumo + prelievo, produzione = autoconsumo
#: + immissione - which only holds for instantaneous power.
#: Listed by full name on purpose. `produzione_presente` also names a flow but
#: is a flag: 2978 samples of `produzione_totale` over a week spanned -3.94 to
#: 5.05 kW, while `produzione_presente` never moved off 1.
POWER_FLOW_FIELDS = frozenset(
    {
        "autoconsumo_totale",
        "consumo_totale",
        "immissione_totale",
        "prelievo_totale",
        "produzione_totale",
        "scambio_totale",
    }
)


def _is_boolean_range(status_range: str | None) -> bool:
    """Return True for a webserver range that only permits 0 or 1.

    The webserver publishes `min=0|max=1` for its flags (`dynamic_mode`,
    `reset_history`) and a full int32 span for real readings. A field that
    cannot hold anything but 0 or 1 is not a measurement, whatever its name
    suggests - and unlike the name, this is stated by the device itself.

    Trade-off: a genuine 0..1 reading (a power factor, say) would be caught
    here and reported without a unit. That is the deliberate direction of
    error - visibly incomplete beats convincingly wrong - and such a field
    lands in the debug log where a proper rule can be added for it.
    """
    if not status_range:
        return False
    parts = dict(piece.split("=", 1) for piece in status_range.split("|") if "=" in piece)
    return parts.get("min") == "0" and parts.get("max") == "1"


def not_a_reading(name: str, status_range: str | None = None) -> bool:
    """Return True when this field holds a flag or a category, not a value.

    The name is matched on whole underscore-separated parts, never as a
    substring: the substring matching this guard exists to contain is exactly
    what turned `fase` into a current and `temperature_alarm` into a
    temperature.
    """
    if name in NOT_A_READING_FIELDS:
        return True
    if set(name.split("_")) & NOT_A_READING:
        return True
    return _is_boolean_range(status_range)


def state_class_for(device_class: SensorDeviceClass | None) -> SensorStateClass | None:
    """Pick the state class Home Assistant allows for a device class.

    Derived from HA's own DEVICE_CLASS_STATE_CLASSES table rather than from a
    hand-written if/elif chain, so a state class that HA considers impossible
    for the device class cannot be produced. Getting that pair wrong is not
    cosmetic: HA logs a warning and long-term statistics are not recorded.

    Energy meters are cumulative counters, hence TOTAL_INCREASING; everything
    else that HA accepts as a measurement gets MEASUREMENT, which is what makes
    it eligible for statistics at all. Temperature, illuminance and wind speed
    used to fall through with no state class and therefore recorded no history
    beyond the recorder's purge window.
    """
    if device_class is None:
        return None
    allowed = DEVICE_CLASS_STATE_CLASSES.get(device_class)
    if not allowed:
        return None
    if SensorStateClass.TOTAL_INCREASING in allowed:
        return SensorStateClass.TOTAL_INCREASING
    if SensorStateClass.MEASUREMENT in allowed:
        return SensorStateClass.MEASUREMENT
    return None


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the Vimar Sensor platform."""
    vimar_setup_entry(VimarSensorContainer, CURR_PLATFORM, hass, entry, async_add_devices)

    # Create companion temperature sensors from climate devices so that
    # the measured temperature appears in HA area views (which require a
    # dedicated sensor entity with device_class=temperature).
    from .const import DOMAIN
    from .vimar_coordinator import VimarDataUpdateCoordinator

    coordinator: VimarDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    vimarproject = coordinator.vimarproject
    climate_devices = vimarproject.get_by_device_type(DEVICE_TYPE_CLIMATES)
    temp_sensors = []
    for device_id, device in climate_devices.items():
        if device.get("ignored", False):
            continue
        status = device.get("status", {})
        if "temperatura_misurata" in status:
            temp_sensors.append(
                VimarClimateTempSensor(coordinator, int(device_id), "temperatura_misurata")
            )
        elif "temperatura" in status:
            temp_sensors.append(VimarClimateTempSensor(coordinator, int(device_id), "temperatura"))
    if temp_sensors:
        _LOGGER.info(
            "Adding %d companion temperature sensors from climate devices", len(temp_sensors)
        )
        async_add_devices(temp_sensors)
        # Register companion sensors so async_remove_old_devices does not
        # purge them (it only keeps entities listed in devices_for_platform).
        if CURR_PLATFORM in coordinator.devices_for_platform:
            coordinator.devices_for_platform[CURR_PLATFORM].extend(temp_sensors)
        else:
            coordinator.devices_for_platform[CURR_PLATFORM] = list(temp_sensors)


class VimarSensor(VimarEntity, SensorEntity):
    """Provide a Vimar Sensors."""

    # set entity_id, object_id manually due to possible duplicates
    # entity_id = "sensor." + "unset"

    _measurement_name = None
    _measurement_display_name = None
    _class_and_units = None
    # _parent = None
    # _state_value = None

    def __init__(self, coordinator, device_id: int, measurement_name):
        """Initialize the sensor."""
        # copy device - otherwise we will have duplicate keys
        # device_c = copy.copy(device)
        # device_c['object_name'] += " " + measurement_name

        self._measurement_name = measurement_name
        self._measurement_display_name = self._measurement_name.title().strip().replace("_", " ")
        VimarEntity.__init__(self, coordinator, device_id)
        self._class_and_units = self.class_and_units()
        # this will override the name for all
        # self._device['object_name_' + self._measurement_name] = self._device['object_name'] + " " + measurement_name
        # self.entity_id = self._platform + "." + self.name.lower() + "-" + measurement_name + "_" + self._device_id
        # self.entity_id = "sensor." + self._name.lower() + "-" + measurement_name + "-" + self._device_id
        # self._name = format_name(self._device['object_name'] + " " + measurement_name)
        # _LOGGER.debug("Creating new sensor for %s", self.entity_id)
        # self._parent = parent

    @property
    def entity_platform(self):
        """Return the platform of this entity."""
        return CURR_PLATFORM

    @property
    def name(self):
        """Return the name of the device."""
        return super().name + " " + self._measurement_display_name

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Return the class of this device, from component DEVICE_CLASSES."""
        return self.class_and_units().device_class

    @property
    def state_class(self) -> SensorStateClass | None:
        """Return the state class of this entity."""
        return state_class_for(self.class_and_units().device_class)

    def class_and_units(self) -> SensorSpec:
        """Return the unit and device class for this measurement.

        Every unit here has to be one Home Assistant accepts for the paired
        device class (see DEVICE_CLASS_UNITS): an invalid pair is not ignored,
        it logs a warning on every start and disables unit conversion in the
        UI.

        The rules below match on parts of the field name, which is the only
        thing the webserver gives us to go on. That is why the "is this a
        reading at all?" question is asked FIRST: see NOT_A_READING.
        """
        if self._class_and_units is not None:
            return self._class_and_units

        name = self._measurement_name
        object_type = self._device["object_type"]
        status_range = (self._device["status"].get(name) or {}).get("status_range")

        if not_a_reading(name, status_range):
            return SensorSpec(None, None)

        if object_type in ENERGY_METER_OBJECT_TYPES:
            if "energia" in name:
                return SensorSpec(UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY)
            if "potenza_attiva" in name:
                return SensorSpec(UnitOfPower.KILO_WATT, SensorDeviceClass.POWER)
            if "potenza_reattiva" in name:
                # Reactive power is measured in kvar, not kW. Reported as POWER
                # in kW it was silently wrong: HA would happily convert it to
                # watts and add it to the energy dashboard as if it were real
                # power.
                return SensorSpec(
                    UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
                    SensorDeviceClass.REACTIVE_POWER,
                )
            if name in POWER_FLOW_FIELDS:
                return SensorSpec(UnitOfPower.KILO_WATT, SensorDeviceClass.POWER)
            if "fase" in name:
                return SensorSpec(UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT)
            if any(x in name for x in ("_date", "_time", "_datetime")):
                # Was SensorDeviceClass.TIMESTAMP, which HA only accepts as a
                # timezone-aware datetime object; the webserver gives us a
                # string, so HA raised ValueError when rendering the state.
                # No device class: the raw value is shown as-is.
                return SensorSpec(None, None)
            # Anything else on a meter used to fall through to "power in kW",
            # which is a guess, and a guess that cannot be spotted: an
            # unrecognised field would appear as a plausible power reading.
            # An unlabelled value is visibly incomplete instead, and the log
            # line makes the field discoverable so a real rule can be added.
            _LOGGER.debug(
                "Meter field '%s' on %s has no unit rule; reporting it without "
                "a unit or device class. Add one to class_and_units() if it is "
                "a real measurement",
                name,
                object_type,
            )
            return SensorSpec(None, None)

        if object_type in KNX_TEMPERATURE_OBJECT_TYPES or "temperature" in name:
            # see: https://github.com/h4de5/home-assistant-vimar/issues/20
            return SensorSpec(UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE)

        if object_type in KNX_WINDSPEED_OBJECT_TYPES or "wind_speed" in name:
            # see: https://github.com/h4de5/home-assistant-vimar/issues/20
            # Was self._device["device_class"], which parse_device_type set to
            # "pressure" for anemometers: m/s on a pressure sensor.
            return SensorSpec(UnitOfSpeed.METERS_PER_SECOND, SensorDeviceClass.WIND_SPEED)

        if "brightness" in name:
            # see: https://github.com/h4de5/home-assistant-vimar/issues/20
            # Was "lm" (lumen), which HA rejects for illuminance: lux only.
            return SensorSpec(LIGHT_LUX, SensorDeviceClass.ILLUMINANCE)

        # Fall back to whatever parse_device_type guessed for the whole device,
        # but only if it is a device class HA knows: an unrecognised string
        # would make HA refuse to add the entity.
        return SensorSpec(None, try_parse_enum(SensorDeviceClass, self._device["device_class"]))

        # ‘its_night’: {‘status_id’: ‘3369’, ‘status_value’: ‘1’, ‘status_range’: ‘’},
        # ‘its_raining’: {‘status_id’: ‘3371’, ‘status_value’: ‘0’, ‘status_range’: ‘’},
        # ‘temperature’: {‘status_id’: ‘3373’, ‘status_value’: ‘11.00’, ‘status_range’: ‘’},
        # ‘temperature_min’: {‘status_id’: ‘3375’, ‘status_value’: ‘5.30’, ‘status_range’: ‘’},
        # ‘temperature_max’: {‘status_id’: ‘3377’, ‘status_value’: ‘8.10’, ‘status_range’: ‘’},
        # ‘temperature_request_minmax’: {‘status_id’: ‘3379’, ‘status_value’: ‘0’, ‘status_range’: ‘’},
        # ‘temperature_reset’: {‘status_id’: ‘3381’, ‘status_value’: ‘1’, ‘status_range’: ‘’},
        # ‘temperature_alarm’: {‘status_id’: ‘3383’, ‘status_value’: ‘0’, ‘status_range’: ‘’},
        # ‘wind_speed’: {‘status_id’: ‘3409’, ‘status_value’: ‘3.24’, ‘status_range’: ‘’},
        # ‘wind_speed_max’: {‘status_id’: ‘3411’, ‘status_value’: ‘0.00’, ‘status_range’: ‘’},
        # ‘wind_speed_request_minmax’: {‘status_id’: ‘3413’, ‘status_value’: ‘0’, ‘status_range’: ‘’},
        # ‘wind_speed_reset’: {‘status_id’: ‘3415’, ‘status_value’: ‘1’, ‘status_range’: ‘’},
        # ‘wind_speed_alarm’: {‘status_id’: ‘3417’, ‘status_value’: ‘0’, ‘status_range’: ‘’},
        # ‘brightness’: {‘status_id’: ‘3437’, ‘status_value’: ‘0.00’, ‘status_range’: ‘’},

        # 'potenza_attiva','-1','0.01'

        # 'contatore_assoluto': {'status_id': '102467', 'status_value': '104','status_range': 'min=0|max=4294967295'},
        # 'contatore_parziale':{'status_id': '102469', 'status_value': '15', 'status_range': 'min=0|max=4294967295'},
        # 'reset_to_value': {'status_id': '102472', 'status_value': '0', 'status_range': 'min=0|max=4294967295'},
        # 'reset_history': {'status_id': '102474', 'status_value': '0', 'status_range': 'min=0|max=1'},
        # 'frequenza_impulsi': {'status_id': '102476', 'status_value':'0', 'status_range':'min=-2147483648|max=2147483648'},
        # 'divisore': {'status_id': '103644', 'status_value': '1', 'status_range': ''},
        # 'moltiplicatore': {'status_id': '103646', 'status_value': '100', 'status_range': ''}}

        # contatore assoluto = absolute counter. total pulses received
        # contatore parziale = partial counter. pulses since the last reset
        # reset_to_value = initial value to count from
        # reset_history = reset history
        # frequenza_impulsi = pulse frequency
        # divisore = divisor: value by which to divide the partial counter
        # moltiplicatore = multiplier: value by which to multiply the partial counter

    @property
    def unique_id(self):
        """Return the ID of this device and its state."""
        # _LOGGER.debug("Unique Id: " + DOMAIN + '_' + self._platform + '_' + self._device_id + '-' +
        # self._device['status'][self._measurement_name]['status_id'] + " - " + self.name)
        return super().unique_id + "-" + self._device["status"][self._measurement_name]["status_id"]
        # return str(VimarEntity.unique_id) + '-' + self._device['status'][self._measurement_name]['status_id']

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        base_attr = super().extra_state_attributes
        attr = self._device["status"][self._measurement_name]
        for key in attr:
            base_attr[key] = attr[key]
        return base_attr

    # def _reset_status(self):
    #     """Read data from device and convert it into hass states."""
    #     if 'status' in self._device and self._device['status']:
    #         if self._measurement_name in self._device['status']:
    #             self._state_value = float(self._device['status'][self._measurement_name]['status_value'])

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the native unit_of_measurement of this sensor."""
        return self.class_and_units().unit

    @property
    def native_value(self) -> float | str | None:
        """Return the native value of this sensor.

        The webserver always hands us strings. Anything with a device class is
        converted to a number here rather than passed through: HA raises
        ValueError when a numeric sensor reports a non-numeric state, so an
        unparseable reading would take out the whole entity instead of showing
        as unknown for that one poll.
        """
        value = self.get_state(self._measurement_name)
        if value is None:
            return None
        spec = self.class_and_units()
        if spec.device_class is None and spec.unit is None:
            # No unit, no device class: a plain text/diagnostic reading.
            return value
        try:
            return float(value)
        except (TypeError, ValueError):
            _LOGGER.debug(
                "%s: non-numeric value %r for %s, reporting unknown",
                self.name,
                value,
                self._measurement_name,
            )
            return None


class VimarSensorContainer(VimarEntity):
    """Defines a Vimar Sensor device."""

    def __init__(self, coordinator, device_id: int):
        """Initialize the sensor."""
        VimarEntity.__init__(self, coordinator, device_id)

    @property
    def entity_platform(self):
        """Return the platform of the entity."""
        return CURR_PLATFORM

    def get_entity_list(self):
        """Return a List of VimarSensors."""
        # if len(self._sensor_list) == 0:
        sensor_list = []
        if "status" in self._device and self._device["status"]:
            for status in self._device["status"]:
                # if status.find('_setpoint') != -1 or status.find('_output') != -1:
                if any(x in status for x in ["_setpoint", "_output"]):
                    continue
                # _LOGGER.debug("Adding sensor for %s", status)
                # _LOGGER.debug("Adding sensor %s from id %s", status, self._device_id)
                sensor_list.append(VimarSensor(self._coordinator, self._device_id, status))

        return sensor_list


class VimarClimateTempSensor(VimarEntity, SensorEntity):
    """Companion temperature sensor for a Vimar climate device.

    Exposes the thermostat's measured temperature as a dedicated sensor
    entity with device_class=temperature so that HA area views can display
    the room temperature.
    """

    _status_key: str

    def __init__(self, coordinator, device_id: int, status_key: str):
        """Initialize the companion temperature sensor."""
        self._status_key = status_key
        VimarEntity.__init__(self, coordinator, device_id)

    @property
    def entity_platform(self):
        """Return the platform of this entity."""
        return CURR_PLATFORM

    @property
    def name(self):
        """Return the name of the sensor."""
        return super().name + " Temperatura"

    @property
    def unique_id(self):
        """Return a unique ID distinct from the climate entity."""
        return super().unique_id + "-temp"

    @property
    def device_class(self):
        """Return temperature device class."""
        return SensorDeviceClass.TEMPERATURE

    @property
    def state_class(self) -> SensorStateClass:
        """Return measurement state class."""
        return SensorStateClass.MEASUREMENT

    @property
    def native_unit_of_measurement(self) -> str:
        """Return Celsius."""
        return UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float | None:
        """Return the measured temperature."""
        val = self.get_state(self._status_key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                return None
        return None
