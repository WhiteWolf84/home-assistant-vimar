"""Vimar base entity implementation."""

import logging

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    _LOGGER,
    CONF_IGNORE_PLATFORM,
    DEVICE_TYPE_BINARY_SENSOR,
    DOMAIN,
    PACKAGE_NAME,
)
from .vimar_coordinator import VimarDataUpdateCoordinator
from .vimarlink.vimarlink import VimarDevice, VimarLink, VimarProject


class VimarEntity(CoordinatorEntity[VimarDataUpdateCoordinator]):
    """Vimar abstract base entity.
    
    Implements proper availability handling according to Home Assistant standards:
    - Entity is unavailable when coordinator update fails
    - Entity is unavailable when device data is missing from coordinator
    - Entity is available when device data is present and valid
    """

    _logger = _LOGGER
    _logger_is_debug = False
    _device: VimarDevice | None = None
    _device_id: str = "0"
    _vimarconnection: VimarLink | None = None
    _vimarproject: VimarProject | None = None
    _coordinator: VimarDataUpdateCoordinator | None = None
    _attributes = {}

    ICON = "mdi:checkbox-marked"

    def __init__(self, coordinator: VimarDataUpdateCoordinator, device_id: int):
        """Initialize the base entity."""
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._device_id = str(device_id)
        self._vimarconnection = coordinator.vimarconnection
        self._vimarproject = coordinator.vimarproject
        self._reset_status()

        if self._vimarproject is not None and self._device_id in self._vimarproject.devices:
            self._device = self._vimarproject.devices[self._device_id]
            self._logger = logging.getLogger(str(PACKAGE_NAME) + "." + self.entity_platform)
            self._logger_is_debug = self._logger.isEnabledFor(logging.DEBUG)
        else:
            self._logger.warning("Cannot find device #%s", self._device_id)

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self._device_id in self.coordinator._changed_device_ids:
            super()._handle_coordinator_update()
        elif self.coordinator.logger.isEnabledFor(logging.DEBUG):
            self.coordinator.logger.debug(
                "Skipping update for %s (no changes detected)", self.name
            )

    @property
    def available(self) -> bool:
        """Return True if entity is available.
        
        Entity is considered available when:
        1. Coordinator update is successful (super().available)
        2. Device data exists in coordinator
        3. Device has not been removed
        
        This ensures entities correctly show 'unavailable' state when:
        - Vimar web server is offline
        - Authentication fails
        - Network connectivity is lost
        - Device is removed from Vimar configuration
        """
        if not super().available:
            return False
        
        # Check if device still exists in coordinator data
        if self.coordinator.data is None:
            return False
        
        if self._device_id not in self.coordinator.data:
            return False
        
        return True

    @property
    def device_name(self):
        """Return the name of the device."""
        if self._device is None:
            return f"Unknown Device {self._device_id}"
        name = self._device.get("device_friendly_name")
        if name is None:
            name = self._device.get("object_name", f"Device {self._device_id}")
        return name

    @property
    def name(self):
        """Return the name of the device."""
        return self.device_name

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        if self._device is None:
            return self._attributes
        
        # mostro gli attributi importati da vimar
        for key in self._device:
            value = self._device[key]
            if self._logger_is_debug is False and (
                key == "status"
                or key == "device_class"
                or key == "device_friendly_name"
                or key == "vimar_icon"
            ):
                continue
            self._attributes["vimar_" + key] = value
        return self._attributes

    def request_statemachine_update(self):
        """Update the hass status."""
        # with polling, we need to schedule another poll request
        self.async_schedule_update_ha_state()

    def change_state(self, *args, **kwargs):
        """Change state on bus system and the local device state."""
        if self._device is None or "status" not in self._device:
            self._logger.warning(
                "Cannot change state for device %s - device data not available",
                self._device_id
            )
            return
        
        state_changed = False
        if self._device["status"]:
            if args and len(args) > 0:
                iter_args = iter(args)
                for state, value in zip(iter_args, iter_args, strict=False):
                    if state in self._device["status"]:
                        state_changed = True
                        optionals = self._vimarconnection.get_optionals_param(state)
                        self.hass.async_add_executor_job(
                            self._vimarconnection.set_device_status,
                            self._device["status"][state]["status_id"],
                            str(value),
                            optionals,
                        )
                        self._device["status"][state]["status_value"] = str(value)
                    else:
                        self._logger.warning(
                            "Could not find state %s in device %s - %s - could not change value to: %s",
                            state,
                            self.name,
                            self._device_id,
                            value,
                        )

            if kwargs and len(kwargs) > 0:
                for state, value in kwargs.items():
                    if state in self._device["status"]:
                        state_changed = True
                        optionals = self._vimarconnection.get_optionals_param(state)
                        self.hass.async_add_executor_job(
                            self._vimarconnection.set_device_status,
                            self._device["status"][state]["status_id"],
                            str(value),
                            optionals,
                        )
                        self._device["status"][state]["status_value"] = str(value)
                    else:
                        self._logger.warning(
                            "Could not find state %s in device %s - %s - could not change value to: %s",
                            state,
                            self.name,
                            self._device_id,
                            value,
                        )

            if state_changed:
                self.request_statemachine_update()

    def get_state(self, state):
        """Get state of the local device state."""
        if self.has_state(state):
            return self._device["status"][state]["status_value"]
        else:
            self._logger.warning(
                "Could not find state %s in device %s - %s - could not get value",
                state,
                self.name,
                self._device_id,
            )
        return None

    def has_state(self, state):
        """Return true if local device has a given state."""
        if self._device is None:
            return False
        if "status" in self._device and self._device["status"] and state in self._device["status"]:
            return True
        return False

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        if self._device is None:
            return self.ICON
        
        device_icon = self._device.get("icon")
        if isinstance(device_icon, str):
            return device_icon
        elif isinstance(device_icon, list):
            return (device_icon[1], device_icon[0])[self.is_default_state]

        return self.ICON

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        if self._device is None:
            return None
        return self._device.get("device_class")

    @property
    def unique_id(self):
        """Return the ID of this device."""
        prefix = self._coordinator.entity_unique_id_prefix or ""
        if len(prefix) > 0:
            prefix += "_"
        return DOMAIN + "_" + prefix + self.entity_platform + "_" + self._device_id

    def _reset_status(self):
        """Set status from _device to class variables."""

    @property
    def is_default_state(self):
        """Return True of in default state - resulting in default icon."""
        return False

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device information for device registry."""
        if self._device is None:
            return None
        
        room_name = None
        if self._device.get("room_friendly_name") and self._device["room_friendly_name"] != "":
            room_name = self._device["room_friendly_name"]

        # Keep original 3-element tuple format for backward compatibility
        device: DeviceInfo = {
            "identifiers": {
                (
                    DOMAIN,
                    self._coordinator.entity_unique_id_prefix or "",
                    self._device_id,
                )
            },  # type: ignore[arg-type]
            "name": self.device_name,
            "model": self._device.get("object_type"),
            "manufacturer": "Vimar",
            "suggested_area": room_name,
        }
        return device

    @property
    def entity_platform(self):
        """Return device_type (platform overrrided in sensor class)"""
        if self._device is None:
            return "unknown"
        return self._device.get("device_type", "unknown")

    def get_entity_list(self) -> list:
        """return entity as list for async_add_devices, method to override if has multiple attribute, as sensor"""
        return [self]


class VimarStatusSensor(BinarySensorEntity):
    """Representation of Vimar connection status sensor."""

    _coordinator: VimarDataUpdateCoordinator
    _attr_should_poll = True

    def __init__(self, coordinator: VimarDataUpdateCoordinator):
        """Initialize the sensor."""
        self._coordinator = coordinator
        vimarconfig = coordinator.vimarconfig
        # Access connection attributes through _connection after refactoring
        conn = coordinator.vimarconnection._connection
        self._attr_name = (
            "Vimar Connection to "
            + str(conn._host)
            + ":"
            + str(conn._port)
        )
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_extra_state_attributes = {
            "Host": conn._host,
            "Port": conn._port,
            "Secure": conn._schema == "https",
            "Verify SSL": conn._schema == "https" and vimarconfig.get(CONF_VERIFY_SSL),
            "Vimar Url": f"{conn._schema}://{conn._host}:{conn._port}",
            "Certificate": conn._certificate,
            "Username": conn._username,
            "SessionID": coordinator.vimarconnection._session_id,
        }
        self._attr_is_on = False

    @property
    def unique_id(self):
        """Return the ID of this device."""
        prefix = self._coordinator.entity_unique_id_prefix or ""
        if len(prefix) > 0:
            prefix += "_"
        return DOMAIN + "_" + prefix + "status"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        # Keep original 3-element tuple format for backward compatibility
        return DeviceInfo(
            identifiers={(DOMAIN, self._coordinator.entity_unique_id_prefix or "", "status")},  # type: ignore[arg-type]
            name="Vimar WebServer",
            model="Vimar WebServer",
            manufacturer="Vimar",
        )

    def update(self):
        """Fetch new state data for the sensor."""
        logged = self._coordinator.vimarconnection.is_logged()
        self._attr_is_on = logged


def vimar_setup_entry(
    vimar_entity_class: type[VimarEntity],
    platform: str,
    hass: HomeAssistant,
    entry,
    async_add_devices,
):
    """Generic method for add entities of specified platform to HASS."""
    logger = logging.getLogger(str(PACKAGE_NAME) + "." + platform)
    coordinator: VimarDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    ignored_platforms = coordinator.vimarconfig.get(CONF_IGNORE_PLATFORM) or []
    platform_ignored = platform in ignored_platforms
    vimarproject = coordinator.vimarproject

    entities = []
    entities_to_add = []

    if platform == DEVICE_TYPE_BINARY_SENSOR:
        status_sensor = VimarStatusSensor(coordinator)
        async_add_devices([status_sensor], True)
        entities += [status_sensor]

    if not platform_ignored:
        logger.debug("Vimar %s started!", platform)
        devices = vimarproject.get_by_device_type(platform)
        if len(devices) != 0:
            for device_id, device in devices.items():
                if device.get("ignored", False):
                    continue
                entity: VimarEntity = vimar_entity_class(coordinator, device_id)
                entity_list = entity.get_entity_list()
                entities_to_add += entity_list

    if len(entities_to_add) != 0:
        logger.info("Adding %d %s", len(entities_to_add), platform)
    # need to call async_add_devices everytime for each registered platform (even if it's empty)!
    async_add_devices(entities_to_add)
    entities += entities_to_add

    coordinator.devices_for_platform[platform] = entities

    if not platform_ignored:
        logger.debug("Vimar %s complete!", platform)
