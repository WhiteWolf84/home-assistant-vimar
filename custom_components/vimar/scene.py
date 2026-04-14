"""Platform for scene integration."""

import logging
from datetime import datetime

from homeassistant.components.scene import Scene
from homeassistant.const import STATE_UNKNOWN
from homeassistant.util import dt as dt_util

from .const import DEVICE_TYPE_SCENES as CURR_PLATFORM
from .vimar_entity import VimarEntity, vimar_setup_entry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the Vimar Scene platform."""
    vimar_setup_entry(VimarScene, CURR_PLATFORM, hass, entry, async_add_devices)


class VimarScene(VimarEntity, Scene):
    """Provide Vimar scenes."""

    _last_activated: datetime | None = None

    def __init__(self, coordinator, device_id: int):
        """Initialize the scene."""
        VimarEntity.__init__(self, coordinator, device_id)

    @property
    def entity_platform(self):
        return CURR_PLATFORM

    # scene properties

    @property
    def is_default_state(self):
        """Return True of in default state - resulting in default icon."""
        return True

    @property
    def state(self) -> str:
        """Return the state of the scene.

        Returns the ISO 8601 timestamp of the last activation, or STATE_UNKNOWN
        if the scene has never been activated since HA started.
        This mirrors the behaviour of button entities and avoids a permanent
        'unknown' state that confuses tools like hass-watchman.
        """
        if self._last_activated is None:
            return STATE_UNKNOWN
        return self._last_activated.isoformat()

    @property
    def extra_state_attributes(self):
        """Return scene-specific state attributes."""
        attrs = super().extra_state_attributes
        if self._last_activated is not None:
            attrs["last_activated"] = self._last_activated.isoformat()
        return attrs

    # async getter and setter

    async def async_activate(self, **kwargs) -> None:
        """Activate scene. Try to get entities into requested state."""
        if self.has_state("on/off"):
            self.change_state("on/off", "1")

        elif self.has_state("comando"):
            self.change_state("comando", "0")

        self._last_activated = dt_util.utcnow()
        self.async_write_ha_state()


# end class VimarScene
