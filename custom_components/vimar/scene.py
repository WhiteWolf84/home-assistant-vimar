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

    async def async_added_to_hass(self) -> None:
        """Restore last activation timestamp from HA storage on startup.

        Scene inherits RestoreEntity, so async_get_last_state() reads the
        last persisted state from .storage/core.restore_state. The state
        string is the ISO 8601 timestamp we set in async_activate().
        """
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (STATE_UNKNOWN, "unknown", None):
            try:
                self._last_activated = dt_util.parse_datetime(last_state.state)
                _LOGGER.debug(
                    "Scene %s: restored last activation: %s",
                    self.name,
                    self._last_activated,
                )
            except (ValueError, TypeError):
                _LOGGER.debug(
                    "Scene %s: could not parse restored state '%s'",
                    self.name,
                    last_state.state,
                )

    @property
    def entity_platform(self):
        return CURR_PLATFORM

    # scene properties

    @property
    def is_default_state(self) -> bool:
        """Return True of in default state - resulting in default icon."""
        return True

    # `state` is deliberately NOT overridden. BaseScene declares it @final and
    # already does exactly what this class used to reimplement: Scene's @final
    # _async_activate() - the method the scene.turn_on service actually calls -
    # records the activation timestamp, writes the state and only then invokes
    # our async_activate(). It also restores that timestamp from the recorder
    # on startup. The override reproduced all of it against a second, parallel
    # timestamp, so the two could disagree; _last_activated is kept only to
    # publish the value as an attribute.

    @property
    def extra_state_attributes(self):
        """Return scene-specific state attributes."""
        attrs = super().extra_state_attributes
        if self._last_activated is not None:
            attrs["last_activated"] = self._last_activated.isoformat()
        return attrs

    # async getter and setter

    async def async_activate(self, **kwargs) -> None:
        """Activate scene. Try to get entities into requested state.

        Called by Scene._async_activate(), which has already recorded the
        activation timestamp and written the state. _last_activated is our own
        copy, kept only for the `last_activated` attribute, and is set before
        change_state() because that triggers another state write.
        """
        self._last_activated = dt_util.utcnow()

        if self.has_state("on/off"):
            self.change_state("on/off", "1")
        elif self.has_state("comando"):
            self.change_state("comando", "0")
        else:
            # No Vimar state to write, but still update HA state machine
            self.async_write_ha_state()


# end class VimarScene
