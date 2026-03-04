"""Platform for binary_sensor integration."""

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_TYPE_BINARY_SENSOR as CURR_PLATFORM, DOMAIN
from .vimar_coordinator import VimarDataUpdateCoordinator
from .vimar_entity import VimarEntity, vimar_setup_entry

_LOGGER = logging.getLogger(__name__)

# Keywords in zone names used to infer BinarySensorDeviceClass.
# Checked case-insensitively, first match wins.
_DEVICE_CLASS_HINTS: list[tuple[list[str], BinarySensorDeviceClass]] = [
    (["basculante", "garage", "garag"], BinarySensorDeviceClass.GARAGE_DOOR),
    (["porta", "portone", "ingresso"], BinarySensorDeviceClass.DOOR),
    (["portafin"], BinarySensorDeviceClass.DOOR),
    (["fin", "finestra", "fines"], BinarySensorDeviceClass.WINDOW),
    (["vol", "volumetr", "pir", "motion"], BinarySensorDeviceClass.MOTION),
    (["sirena", "manomis", "tamper"], BinarySensorDeviceClass.TAMPER),
]


def _guess_device_class(zone_name: str) -> BinarySensorDeviceClass | None:
    """Infer device class from zone name keywords."""
    name_lower = zone_name.lower()
    for keywords, device_class in _DEVICE_CLASS_HINTS:
        for kw in keywords:
            if kw in name_lower:
                return device_class
    return None


def _parse_sai2_zone_value(value: str) -> dict[str, bool]:
    """Decode SAI2 zone CURRENT_VALUE bitmask to state flags.

    Returns dict with boolean flags for each known state bit.
    Bit mapping (confirmed from browser inspection):
        Bit 0: In allarme (alarm triggered on this zone)
        Bit 1: Manomissione (tamper)
        Bit 2: Esclusa (zone excluded/bypassed)
        Bit 3: Aperta (zone physically open)
    """
    if not value:
        return {"open": False, "alarm": False, "tamper": False, "excluded": False}
    try:
        bits = int(value, 2) if len(value) > 2 else int(value)
    except ValueError:
        return {"open": False, "alarm": False, "tamper": False, "excluded": False}

    return {
        "alarm": bool(bits & (1 << 0)),
        "tamper": bool(bits & (1 << 1)),
        "excluded": bool(bits & (1 << 2)),
        "open": bool(bits & (1 << 3)),
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Set up the Vimar BinarySensor platform."""
    # Standard Vimar binary sensors (monostable switches)
    vimar_setup_entry(VimarBinarySensor, CURR_PLATFORM, hass, entry, async_add_devices)

    # SAI2 zone sensors (alarm physical sensors)
    coordinator: VimarDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    vimarproject = coordinator.vimarproject

    if vimarproject is None or vimarproject.sai2_zones is None:
        _LOGGER.debug("SAI2: no alarm zones found, skipping zone binary sensors")
        return

    zone_to_group = vimarproject.sai2_zone_to_group or {}
    zone_entities: list[VimarSAI2ZoneSensor] = []
    for zone_id, zone_data in vimarproject.sai2_zones.items():
        parent_group_id = zone_to_group.get(zone_id)
        zone_entities.append(
            VimarSAI2ZoneSensor(
                coordinator, zone_id, zone_data, parent_group_id
            )
        )

    if zone_entities:
        _LOGGER.info(
            "Adding %d SAI2 zone binary_sensor entities", len(zone_entities)
        )
        async_add_devices(zone_entities)

    # Merge into devices_for_platform for cleanup tracking
    existing = coordinator.devices_for_platform.get(CURR_PLATFORM, [])
    if isinstance(existing, list):
        existing.extend(zone_entities)
    else:
        coordinator.devices_for_platform[CURR_PLATFORM] = zone_entities


class VimarBinarySensor(VimarEntity, BinarySensorEntity):
    """Provide Vimar BinarySensor."""

    def __init__(self, coordinator, device_id: int):
        """Initialize the switch."""
        VimarEntity.__init__(self, coordinator, device_id)

    @property
    def entity_platform(self):
        return CURR_PLATFORM

    @property
    def is_on(self):
        """Return True if the device is on."""
        if self.has_state("on/off"):
            return self.get_state("on/off") == "1"
        return None


class VimarSAI2ZoneSensor(
    CoordinatorEntity[VimarDataUpdateCoordinator], BinarySensorEntity
):
    """Representation of a Vimar SAI2 alarm zone as a binary sensor.

    Each zone corresponds to a physical sensor (door contact, motion
    detector, siren tamper, etc.) connected to the SAI2 alarm system.
    The entity reports is_on=True when the zone is physically "open"
    (e.g. door open, motion detected).
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: VimarDataUpdateCoordinator,
        zone_id: str,
        zone_data: dict[str, Any],
        parent_group_id: str | None = None,
    ) -> None:
        """Initialize the SAI2 zone sensor."""
        super().__init__(coordinator)
        self._zone_id = zone_id
        self._zone_data = zone_data
        self._parent_group_id = parent_group_id
        zone_name = zone_data.get("name", f"Zone {zone_id}")
        self._attr_name = zone_name
        self._attr_unique_id = f"vimar_sai2_zone_{zone_id}"
        self._attr_device_class = _guess_device_class(zone_name)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            super().available
            and self.coordinator.vimarproject is not None
            and self.coordinator.vimarproject.sai2_zones is not None
            and self._zone_id in self.coordinator.vimarproject.sai2_zones
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if zone is open/triggered.

        Each SAI2 zone has individual children (Esclusa, Aperta,
        Memoria, Allarme, Manomessa, Mascherata) each with their own
        CURRENT_VALUE (0 or 1), updated via slim poll CIDs.
        """
        project = self.coordinator.vimarproject
        if project is None or project.sai2_zones is None:
            return None

        zone = project.sai2_zones.get(self._zone_id)
        if zone is None:
            return None
        children = zone.get("children", {})

        # Child label for "open" state is "Aperta" (Italian)
        for label in ("Aperta", "Aperto", "Open"):
            child = children.get(label)
            if child is not None:
                return child.get("value") == "1"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes for diagnostics."""
        project = self.coordinator.vimarproject
        attrs: dict[str, Any] = {"zone_id": self._zone_id}

        if project and project.sai2_zones:
            zone = project.sai2_zones.get(self._zone_id, {})
            children = zone.get("children", {})
            for label, child in children.items():
                attrs[label.lower()] = child.get("value", "?")

        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info — nests this zone under its parent area device."""
        if self._parent_group_id:
            # Nest under the same device as the alarm_control_panel entity
            # for this area. The alarm_control_panel uses identifiers
            # {(DOMAIN, f"sai2_{group_id}")}.
            project = self.coordinator.vimarproject
            area_name = "SAI2"
            if project and project.sai2_groups:
                group = project.sai2_groups.get(self._parent_group_id)
                if group:
                    area_name = f"SAI {group.get('name', self._parent_group_id)}"
            info = DeviceInfo(
                identifiers={(DOMAIN, f"sai2_{self._parent_group_id}")},
                name=area_name,
                manufacturer="Vimar",
                model="SAI2 Alarm Area",
            )
        else:
            # No parent group known — standalone device
            info = DeviceInfo(
                identifiers={(DOMAIN, f"sai2_zone_{self._zone_id}")},
                name=f"SAI {self._zone_data.get('name', self._zone_id)}",
                manufacturer="Vimar",
                model="SAI2 Zone Sensor",
            )
        if self.coordinator.webserver_id:
            info["via_device"] = (DOMAIN, self.coordinator.webserver_id)
        return info
