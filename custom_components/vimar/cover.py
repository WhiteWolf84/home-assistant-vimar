"""Platform for cover integration - CON ENTITY OPTIONS.

Configurazione travel times tramite UI di ogni singola cover!
"""

import logging
from datetime import datetime, timedelta

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
import voluptuous as vol

from .const import (
    CONF_COVER_POSITION_MODE,
    COVER_POSITION_MODE_AUTO,
    COVER_POSITION_MODE_NATIVE,
    COVER_POSITION_MODE_TIME_BASED,
    DEFAULT_COVER_POSITION_MODE,
    DEVICE_TYPE_COVERS as CURR_PLATFORM,
)
from .vimar_entity import VimarEntity, vimar_setup_entry

_LOGGER = logging.getLogger(__name__)

DEFAULT_TRAVEL_TIME_UP = 28
DEFAULT_TRAVEL_TIME_DOWN = 26
POSITION_UPDATE_INTERVAL = 0.2

# Chiavi per storage entity options
CONF_TRAVEL_TIME_UP = "travel_time_up"
CONF_TRAVEL_TIME_DOWN = "travel_time_down"


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the Vimar Cover platform."""
    vimar_setup_entry(VimarCover, CURR_PLATFORM, hass, entry, async_add_devices)

    # Registra servizio per configurare travel times
    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        "set_travel_times",
        {
            vol.Required(CONF_TRAVEL_TIME_UP): vol.All(int, vol.Range(min=1, max=300)),
            vol.Required(CONF_TRAVEL_TIME_DOWN): vol.All(int, vol.Range(min=1, max=300)),
        },
        "async_set_travel_times",
    )


class VimarCover(VimarEntity, CoverEntity, RestoreEntity):
    """Provides a Vimar cover with time-based position tracking."""

    @property
    def assumed_state(self) -> bool:
        """Return True if we're using time-based tracking."""
        if self._use_time_based_tracking():
            return True
        return False

    def __init__(self, coordinator, device_id: int):
        """Initialize the cover."""
        VimarEntity.__init__(self, coordinator, device_id)

        # Time-based tracking
        self._tb_position = None
        self._tb_target = None
        self._tb_start_time = None
        self._tb_start_position = None
        self._tb_operation = None
        self._tb_unsub = None
        self._tb_last_updown = None

        # Travel times (saranno caricati in async_added_to_hass)
        self._travel_time_up = DEFAULT_TRAVEL_TIME_UP
        self._travel_time_down = DEFAULT_TRAVEL_TIME_DOWN

    def _get_position_mode(self) -> str:
        """Get configured position mode from coordinator."""
        if hasattr(self.coordinator, "vimarconfig"):
            return self.coordinator.vimarconfig.get(
                CONF_COVER_POSITION_MODE, DEFAULT_COVER_POSITION_MODE
            )
        return DEFAULT_COVER_POSITION_MODE

    def _use_time_based_tracking(self) -> bool:
        """Determine if time-based tracking should be used."""
        mode = self._get_position_mode()

        if mode == COVER_POSITION_MODE_TIME_BASED:
            # Force time-based even if native position is available
            return True
        elif mode == COVER_POSITION_MODE_NATIVE:
            # Never use time-based, rely on native only
            return False
        else:  # COVER_POSITION_MODE_AUTO or default
            # Use time-based only if native position is not available
            return not self.has_state("position")

    async def async_set_travel_times(self, travel_time_up: int, travel_time_down: int):
        """Service to set travel times for this cover."""
        self._travel_time_up = travel_time_up
        self._travel_time_down = travel_time_down

        # Salva nelle entity options
        if hasattr(self, "registry_entry") and self.registry_entry:
            from homeassistant.helpers import entity_registry as er

            entity_reg = er.async_get(self.hass)
            entity_reg.async_update_entity_options(
                self.entity_id,
                "cover",
                {
                    CONF_TRAVEL_TIME_UP: travel_time_up,
                    CONF_TRAVEL_TIME_DOWN: travel_time_down,
                }
            )

        _LOGGER.info(
            f"{self.name}: Travel times updated - up: {travel_time_up}s, down: {travel_time_down}s"
        )

    async def async_added_to_hass(self):
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        _LOGGER.debug(f"{self.name}: === async_added_to_hass START ===")
        _LOGGER.debug(f"{self.name}: Position mode: {self._get_position_mode()}")
        _LOGGER.debug(f"{self.name}: Use time-based tracking: {self._use_time_based_tracking()}")

        # Carica travel times dalle entity options
        if hasattr(self, "registry_entry") and self.registry_entry:
            options = self.registry_entry.options.get("cover", {})

            saved_up = options.get(CONF_TRAVEL_TIME_UP)
            saved_down = options.get(CONF_TRAVEL_TIME_DOWN)

            if saved_up is not None:
                self._travel_time_up = int(saved_up)
            if saved_down is not None:
                self._travel_time_down = int(saved_down)

            if (
                self._travel_time_up != DEFAULT_TRAVEL_TIME_UP
                or self._travel_time_down != DEFAULT_TRAVEL_TIME_DOWN
            ):
                _LOGGER.info(
                    f"{self.name}: Custom travel times loaded - up: {self._travel_time_up}s, "
                    f"down: {self._travel_time_down}s"
                )

        # Ripristina posizione solo se usiamo time-based tracking
        if self._use_time_based_tracking():
            old_state = await self.async_get_last_state()

            _LOGGER.debug(f"{self.name}: old_state exists = {old_state is not None}")

            if old_state:
                _LOGGER.debug(f"{self.name}: old_state.state = '{old_state.state}'")
                position_attr = old_state.attributes.get("current_position")
                _LOGGER.debug(f"{self.name}: current_position value = {position_attr}")

            if old_state and old_state.attributes.get("current_position") is not None:
                self._tb_position = old_state.attributes["current_position"]
                _LOGGER.info(f"{self.name}: ✅ Position restored: {self._tb_position}%")
            else:
                self._tb_position = 0
                _LOGGER.info(f"{self.name}: ⚠️ New cover, default position: 0% (closed)")
        else:
            _LOGGER.debug(f"{self.name}: Using native position from webserver")

        self._tb_last_updown = self.get_state("up/down")
        _LOGGER.debug(f"{self.name}: === async_added_to_hass END ===")

    async def async_will_remove_from_hass(self):
        """Cleanup when removed."""
        if self._tb_unsub:
            self._tb_unsub()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        super()._handle_coordinator_update()
        if self._use_time_based_tracking():
            self._tb_check_vimar_state()

    def _tb_check_vimar_state(self):
        """Controlla stato Vimar e gestisci movimenti."""
        current_updown = self.get_state("up/down")

        if self._tb_operation:
            expected_updown = "0" if self._tb_operation == "opening" else "1"

            if current_updown != expected_updown:
                _LOGGER.info(
                    f"{self.name}: ⏸️ STOP detected during HA tracking! "
                    f"up/down={current_updown} (was {self._tb_operation})"
                )
                self.hass.async_create_task(self._tb_stop_tracking())
                return

        if current_updown != self._tb_last_updown and not self._tb_operation:

            if current_updown == "0":
                self._tb_position = 100
                _LOGGER.info(
                    f"{self.name}: ⬆️ Physical button OPEN → Position set to 100%"
                )
                self.async_write_ha_state()

            elif current_updown == "1":
                self._tb_position = 0
                _LOGGER.info(
                    f"{self.name}: ⬇️ Physical button CLOSE → Position set to 0%"
                )
                self.async_write_ha_state()

        self._tb_last_updown = current_updown

    async def _tb_start_tracking(self, opening: bool, target: int = None):
        """Avvia tracking temporale per comandi HA."""
        operation = "opening" if opening else "closing"

        if self._tb_operation == operation:
            return

        self._tb_operation = operation
        self._tb_start_time = datetime.now()
        self._tb_start_position = self._tb_position
        self._tb_target = target if target is not None else (100 if opening else 0)

        if self._tb_unsub:
            self._tb_unsub()

        self._tb_unsub = async_track_time_interval(
            self.hass,
            self._tb_update_position,
            timedelta(seconds=POSITION_UPDATE_INTERVAL),
        )

        _LOGGER.debug(
            f"{self.name}: ▶️ Tracking {operation} from {self._tb_position}% to {self._tb_target}%"
        )
        self.async_write_ha_state()

    async def _tb_stop_tracking(self):
        """Ferma tracking e calcola posizione finale."""
        if self._tb_unsub:
            self._tb_unsub()
            self._tb_unsub = None

        if self._tb_start_time:
            self._tb_calculate_position()

        _LOGGER.info(f"{self.name}: ⏹️ Stopped at {self._tb_position}%")

        self._tb_operation = None
        self._tb_start_time = None
        self._tb_target = None

        self.async_write_ha_state()

    @callback
    def _tb_update_position(self, now):
        """Aggiorna posizione durante tracking."""
        self._tb_calculate_position()

        should_stop = False
        send_stop_command = False

        if self._tb_position >= 100 and self._tb_operation == "opening":
            self._tb_position = 100
            should_stop = True
            send_stop_command = False

        elif self._tb_position <= 0 and self._tb_operation == "closing":
            self._tb_position = 0
            should_stop = True
            send_stop_command = False

        elif self._tb_target is not None:
            if self._tb_operation == "opening" and self._tb_position >= self._tb_target:
                self._tb_position = self._tb_target
                should_stop = True
                send_stop_command = (self._tb_target not in [0, 100])

            elif self._tb_operation == "closing" and self._tb_position <= self._tb_target:
                self._tb_position = self._tb_target
                should_stop = True
                send_stop_command = (self._tb_target not in [0, 100])

        if should_stop:
            if send_stop_command:
                _LOGGER.info(
                    f"{self.name}: 🎯 Reached target {self._tb_position}%, sending STOP"
                )
                self.hass.async_create_task(self.async_stop_cover())
            else:
                _LOGGER.info(
                    f"{self.name}: 🏁 Reached end-stop {self._tb_position}%, "
                    "mechanical stop (no STOP command)"
                )

            self.hass.async_create_task(self._tb_stop_tracking())

        self.async_write_ha_state()

    def _tb_calculate_position(self):
        """Calcola posizione attuale basata sul tempo trascorso."""
        if not self._tb_start_time:
            return

        elapsed = (datetime.now() - self._tb_start_time).total_seconds()
        travel_time = (
            self._travel_time_up
            if self._tb_operation == "opening"
            else self._travel_time_down
        )
        percentage = (elapsed / travel_time) * 100

        if self._tb_operation == "opening":
            self._tb_position = min(100, self._tb_start_position + percentage)
        else:
            self._tb_position = max(0, self._tb_start_position - percentage)

        self._tb_position = round(self._tb_position)

    @property
    def entity_platform(self):
        return CURR_PLATFORM

    @property
    def is_closed(self) -> bool | None:
        if not self._use_time_based_tracking():
            # Native mode - use traditional logic
            if self.get_state("up/down") == "1":
                return True
            elif self.get_state("up/down") == "0":
                return False
            else:
                return None
        # Time-based mode
        return self._tb_position == 0 if self._tb_position is not None else None

    @property
    def is_opening(self) -> bool:
        if self._use_time_based_tracking():
            return self._tb_operation == "opening"
        return False

    @property
    def is_closing(self) -> bool:
        if self._use_time_based_tracking():
            return self._tb_operation == "closing"
        return False

    @property
    def current_cover_position(self):
        if not self._use_time_based_tracking() and self.has_state("position"):
            # Native mode
            return 100 - int(self.get_state("position"))
        # Time-based mode
        return self._tb_position

    @property
    def current_cover_tilt_position(self):
        if self.has_state("slat_position"):
            return 100 - int(self.get_state("slat_position"))
        return None

    @property
    def is_default_state(self):
        return (self.is_closed, True)[self.is_closed is None]

    @property
    def supported_features(self) -> CoverEntityFeature:
        flags = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
        )

        # Add SET_POSITION if using time-based OR if native position is available
        if self._use_time_based_tracking() or self.has_state("position"):
            flags |= CoverEntityFeature.SET_POSITION

        if self.has_state("slat_position") and self.has_state(
            "clockwise/counterclockwise"
        ):
            flags |= (
                CoverEntityFeature.STOP_TILT
                | CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )

        return flags

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        attrs = super().extra_state_attributes or {}
        attrs["position_mode"] = self._get_position_mode()
        attrs["uses_time_based_tracking"] = self._use_time_based_tracking()
        if self._use_time_based_tracking():
            attrs["travel_time_up"] = self._travel_time_up
            attrs["travel_time_down"] = self._travel_time_down
        return attrs

    async def async_close_cover(self, **kwargs):
        if self._use_time_based_tracking():
            await self._tb_start_tracking(False, target=0)
        self.change_state("up/down", "1")

    async def async_open_cover(self, **kwargs):
        if self._use_time_based_tracking():
            await self._tb_start_tracking(True, target=100)
        self.change_state("up/down", "0")

    async def async_stop_cover(self, **kwargs):
        if self._use_time_based_tracking():
            await self._tb_stop_tracking()
        self.change_state("stop up/stop down", "1")

    async def async_set_cover_position(self, **kwargs):
        if kwargs:
            if ATTR_POSITION in kwargs:
                target = int(kwargs[ATTR_POSITION])

                if not self._use_time_based_tracking() and self.has_state("position"):
                    # Native mode
                    self.change_state("position", 100 - target)
                else:
                    # Time-based mode
                    if target > self._tb_position:
                        await self._tb_start_tracking(True, target=target)
                        self.change_state("up/down", "0")
                    elif target < self._tb_position:
                        await self._tb_start_tracking(False, target=target)
                        self.change_state("up/down", "1")

    async def async_open_cover_tilt(self, **kwargs):
        self.change_state("clockwise/counterclockwise", "0")

    async def async_close_cover_tilt(self, **kwargs):
        self.change_state("clockwise/counterclockwise", "1")

    async def async_set_cover_tilt_position(self, **kwargs):
        if kwargs:
            if ATTR_TILT_POSITION in kwargs and self.has_state("slat_position"):
                self.change_state(
                    "slat_position", 100 - int(kwargs[ATTR_TILT_POSITION])
                )

    async def async_stop_cover_tilt(self, **kwargs):
        self.change_state("stop up/stop down", "1")
