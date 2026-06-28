"""Platform for Vimar SAI2 alarm control panel integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any, NoReturn

from homeassistant.components import persistent_notification
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import translation
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_AUTOMATION_PIN, CONF_USER_PINS, DOMAIN
from .const import DEVICE_TYPE_ALARM as CURR_PLATFORM
from .vimar_coordinator import VimarDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Maps SAI2 child state labels to HA alarm states.
# Priority order: Allarme is checked first, then the armed/disarmed states.
SAI2_STATE_MAP = {
    "Disinserito": AlarmControlPanelState.DISARMED,
    "Inserito INT": AlarmControlPanelState.ARMED_HOME,
    "Inserito ON": AlarmControlPanelState.ARMED_AWAY,
    "Inserito PAR": AlarmControlPanelState.ARMED_NIGHT,
    "Allarme": AlarmControlPanelState.TRIGGERED,
}

# How long an optimistic value is protected from being overwritten by a
# poll that may still report the pre-command state.
_OPTIMISTIC_GUARD_SECONDS = 5.0

# Result code meaning the web server accepted the call. The same DPCM-0000 is
# returned both by service-vimarsai2authenticate when the PIN is valid and by
# the set service when the request is accepted. NOTE: the set service returns
# DPCM-0000 even for a wrong PIN, so the PIN is validated up-front with
# authenticate_sai2_pin() rather than inferred from the set response.
_SAI2_OK = "DPCM-0000"

# The ONLY result code that means the PIN was genuinely rejected (usercode
# UnknownUserCode). Any other non-OK code from authenticate is a transient
# session / sub-service hiccup, NOT a wrong PIN — see _send_sai2_command. This
# fixes self-resolving "Wrong PIN" bursts caused by classifying every non-OK
# result as a bad PIN.
_SAI2_WRONG_PIN = "SAI2-3127"


@dataclass(frozen=True)
class _Sai2Mode:
    """A single SAI2 target mode: command code, optimistic bitmask and label.

    Keeping the command code, the optimistic CURRENT_VALUE bitmask and the
    child label together avoids the fragile label -> bitmask -> label
    round-trip the previous code relied on. The label maps back to the HA
    state via SAI2_STATE_MAP.
    """

    command: int  # SAI2 SOAP command: 0=OFF, 1=ON, 2=INT, 3=PAR
    bitmask: str  # optimistic DPADD_OBJECT.CURRENT_VALUE
    label: str  # child label used in the children dict / logs


# bit0 = armed-active flag (set whenever any mode is active)
_MODE_DISARM = _Sai2Mode(0, "00000000", "Disinserito")
_MODE_ARM_HOME = _Sai2Mode(2, "00000101", "Inserito INT")
_MODE_ARM_AWAY = _Sai2Mode(1, "00000011", "Inserito ON")
_MODE_ARM_NIGHT = _Sai2Mode(3, "00001001", "Inserito PAR")


def _parse_sai2_area_value(value: str) -> tuple[str, bool]:
    """Map SAI2 group CURRENT_VALUE bitmask from DPADD_OBJECT to state label.

    The SAI2 group object in DPADD_OBJECT stores its live state as an
    8-character binary bitmask (e.g. '00001001'). Confirmed bit mapping
    from browser inspection + live testing:

        Bit 5 (0b00100000): Allarme (active alarm in progress)
        Bit 4 (0b00010000): Alarm memory (alarm tripped in the past, not active)
        Bit 3 (0b00001000): Inserito PAR  <- confirmed '00001001'
        Bit 2 (0b00000100): Inserito INT  <- confirmed by user test
        Bit 1 (0b00000010): Inserito ON   <- confirmed by user test
        Bit 0 (0b00000001): armed-active flag (set whenever any mode is active)
        All zeros           Disinserito

    Returns:
        Tuple of (state_label, alarm_memory) where alarm_memory is True
        when bit 4 is set (alarm tripped previously but not active now).
    """
    if not value or all(c == "0" for c in value):
        return "Disinserito", False
    try:
        bits = int(value, 2)
    except ValueError:
        _LOGGER.warning("SAI2: unrecognised CURRENT_VALUE format: '%s'", value)
        return "Disinserito", False

    alarm_memory = bool(bits & (1 << 4))

    if bits & (1 << 5):
        return "Allarme", alarm_memory
    if bits & (1 << 3):
        return "Inserito PAR", alarm_memory
    if bits & (1 << 2):
        return "Inserito INT", alarm_memory
    if bits & (1 << 1):
        return "Inserito ON", alarm_memory
    if alarm_memory and not (bits & ~((1 << 4) | 1)):
        # Only bit 4 (and possibly bit 0) set — alarm memory with no active mode
        return "Disinserito", True
    # Bit 0 alone = armed but mode not decoded
    _LOGGER.warning("SAI2: armed state with unhandled bitmask '%s', assuming ARMED_AWAY", value)
    return "Inserito ON", alarm_memory


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Vimar Alarm Control Panel platform."""
    coordinator: VimarDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    vimarproject = coordinator.vimarproject

    if vimarproject is None or vimarproject.sai2_groups is None:
        _LOGGER.debug("SAI2: no alarm areas found, skipping alarm platform")
        return

    # Register the single SAI Alarm device.
    # All alarm and zone entities will be nested under this device.
    sai_device_info: dict[str, Any] = {
        "identifiers": {(DOMAIN, "sai2_alarm")},
        "name": "SAI Alarm",
        "manufacturer": "Vimar",
        "model": "SAI2",
    }
    if coordinator.webserver_id:
        sai_device_info["via_device"] = (DOMAIN, coordinator.webserver_id)
    dev_reg = dr.async_get(hass)
    dev_reg.async_get_or_create(config_entry_id=entry.entry_id, **sai_device_info)

    # {ha_user_id: pin} so a logged-in HA user's own PIN is used automatically.
    user_pins: dict[str, str] = {
        **entry.data.get(CONF_USER_PINS, {}),
        **entry.options.get(CONF_USER_PINS, {}),
    }
    # Fallback PIN for commands without a code and without a user (automations).
    automation_pin: str = (
        entry.options.get(CONF_AUTOMATION_PIN) or entry.data.get(CONF_AUTOMATION_PIN) or ""
    )

    entities: list[VimarAlarmControlPanel] = []
    for area_index, (group_id, group_data) in enumerate(vimarproject.sai2_groups.items(), start=1):
        entities.append(
            VimarAlarmControlPanel(
                coordinator, group_id, group_data, area_index, user_pins, automation_pin
            )
        )

    if entities:
        _LOGGER.info("Adding %d alarm_control_panel entities", len(entities))
        async_add_entities(entities)

    coordinator.devices_for_platform[CURR_PLATFORM] = entities


class VimarAlarmControlPanel(
    CoordinatorEntity[VimarDataUpdateCoordinator], AlarmControlPanelEntity
):
    """Representation of a Vimar SAI2 alarm area.

    Each named SAI2 group (area) is exposed as one alarm_control_panel entity.
    State is derived from the group's live DPADD_OBJECT.CURRENT_VALUE bitmask
    (or the children dict from last discovery as a fallback).

    The PIN is forwarded to the SAI2 control unit as the user code. A logged-in
    HA user with a PIN mapped in the options arms/disarms with a single tap
    (the PIN is resolved from their context.user_id); no keypad is shown. Users
    without a mapped PIN, or automations without a user context, must pass the
    code explicitly. No global PIN is stored, so each user uses their own SAI2
    PIN and the control unit logs the operation against the right user.
    """

    _attr_has_entity_name = True
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_NIGHT
    )
    # No keypad: arm AND disarm use the logged-in user's mapped PIN
    # automatically. code_format=None means the card never prompts for a code;
    # the PIN comes from the user->PIN map (or an explicit code in a service
    # call). code_arm_required is irrelevant without a code format.
    _attr_code_arm_required = False
    _attr_code_format = None

    def __init__(
        self,
        coordinator: VimarDataUpdateCoordinator,
        group_id: str,
        group_data: dict[str, Any],
        area_index: int,
        user_pins: dict[str, str],
        automation_pin: str,
    ) -> None:
        """Initialize the alarm control panel."""
        super().__init__(coordinator)
        self._group_id = group_id
        self._group_data = group_data
        self._area_index = area_index
        self._user_pins = user_pins
        self._automation_pin = automation_pin
        self._attr_name = group_data["name"]
        self._attr_unique_id = f"vimar_sai2_{group_id}"
        # Serialize commands on this area so an auto-disarm + arm sequence
        # cannot interleave with another in-flight command.
        self._command_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            super().available
            and self.coordinator.vimarproject is not None
            and self.coordinator.vimarproject.sai2_groups is not None
            and self._group_id in self.coordinator.vimarproject.sai2_groups
        )

    def _current_raw(self) -> str | None:
        """Return the live CURRENT_VALUE bitmask for this area, or None."""
        project = self.coordinator.vimarproject
        if project is None:
            return None
        area_values = project.sai2_area_values
        if area_values is not None and self._group_id in area_values:
            return area_values[self._group_id]
        return None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the current alarm state.

        Reads from sai2_area_values (live DPADD_OBJECT.CURRENT_VALUE bitmask)
        when available, falling back to the children dict from last discovery.
        """
        project = self.coordinator.vimarproject
        if project is None:
            return None

        # --- Primary: live bitmask from DPADD_OBJECT ---
        raw = self._current_raw()
        if raw is not None:
            label, _memory = _parse_sai2_area_value(raw)
            return SAI2_STATE_MAP.get(label, AlarmControlPanelState.DISARMED)

        # --- Fallback: children dict (populated at discovery / optimistic) ---
        if project.sai2_groups is None:
            return None
        group = project.sai2_groups.get(self._group_id)
        if group is None:
            return None
        children = group.get("children", {})
        alarm_child = children.get("Allarme")
        if alarm_child and alarm_child.get("value") == "1":
            return AlarmControlPanelState.TRIGGERED
        for label, ha_state in SAI2_STATE_MAP.items():
            if label == "Allarme":
                continue
            child = children.get(label)
            if child and child.get("value") == "1":
                return ha_state
        return AlarmControlPanelState.DISARMED

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes for diagnostics."""
        project = self.coordinator.vimarproject
        raw = self._current_raw()
        alarm_memory = _parse_sai2_area_value(raw)[1] if raw is not None else False
        attrs: dict[str, Any] = {
            "area_index": self._area_index,
            "area_name": self._group_data.get("name", "?"),
            "alarm_memory": alarm_memory,
        }
        if project and project.sai2_groups:
            group = project.sai2_groups.get(self._group_id, {})
            children = group.get("children", {})
            for label, child in children.items():
                attrs[f"sai2_{label}"] = child.get("value", "?")
        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info — all areas share the single SAI Alarm device."""
        return DeviceInfo(
            identifiers={(DOMAIN, "sai2_alarm")},
        )

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Send disarm command."""
        await self._send_sai2_command(_MODE_DISARM, code)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Send arm home (INT) command."""
        await self._send_sai2_command(_MODE_ARM_HOME, code)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Send arm away (ON) command."""
        await self._send_sai2_command(_MODE_ARM_AWAY, code)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        """Send arm night (PAR) command."""
        await self._send_sai2_command(_MODE_ARM_NIGHT, code)

    def _apply_optimistic(self, mode: _Sai2Mode) -> None:
        """Patch the live bitmask + children so the UI shows the target now.

        This makes the intermediate disarm (when switching armed modes)
        invisible to the user.
        """
        project = self.coordinator.vimarproject
        if project is None or project.sai2_groups is None:
            return
        if project.sai2_area_values is not None:
            project.sai2_area_values[self._group_id] = mode.bitmask
        group = project.sai2_groups.get(self._group_id)
        if group is not None:
            children = group.get("children", {})
            for label in SAI2_STATE_MAP:
                child = children.get(label)
                if child:
                    child["value"] = "1" if label == mode.label else "0"
        project.sai2_optimistic_until[self._group_id] = time.monotonic() + _OPTIMISTIC_GUARD_SECONDS
        self.async_write_ha_state()

    def _resolve_user_pin(self) -> str | None:
        """Return the SAI2 PIN to use when no explicit code was given.

        Order: the mapped PIN for the HA user issuing this command (from the
        call context set by HA), then the automation fallback PIN. Returns None
        if neither is available (e.g. a user with no mapping and no fallback
        configured). Trigger-based automations have no context user, so they
        fall through to the automation PIN.
        """
        user_id = self._context.user_id if self._context else None
        if user_id and (pin := self._user_pins.get(user_id)):
            return pin
        return self._automation_pin or None

    async def _send_sai2_command(self, mode: _Sai2Mode, code: str | None) -> None:
        """Send a command to the SAI2 area via service-vimarsai2allgroupsset.

        The HA code is forwarded to the control unit as the user PIN. When
        switching between armed modes (e.g. PAR -> ON) the SAI2 system
        requires a disarm first; this is handled automatically and shown
        optimistically so it stays invisible to the user.

        Raises:
            ServiceValidationError: if no code was provided or the PIN is wrong.
            HomeAssistantError: if no connection/area was available or the
                server rejected the command.
        """
        # Fall back to the logged-in HA user's mapped PIN when no code was
        # typed (e.g. one-tap arming, or automations running as a user).
        code = code or self._resolve_user_pin()
        if not code:
            await self._fail("sai2_no_pin_for_user", validation=True)

        project = self.coordinator.vimarproject
        if project is None or project.sai2_groups is None:
            await self._fail("sai2_not_available")

        group = project.sai2_groups.get(self._group_id)
        if group is None:
            await self._fail("sai2_not_available")

        vimarconnection = self.coordinator.vimarconnection
        if vimarconnection is None:
            await self._fail("sai2_not_available")

        async with self._command_lock:
            # Validate the PIN up-front. The set service accepts any PIN with
            # DPCM-0000, but service-vimarsai2authenticate validates it and
            # reports a wrong PIN immediately and unambiguously, before we
            # touch any state.
            auth = await self.hass.async_add_executor_job(
                vimarconnection.authenticate_sai2_pin, code
            )
            if auth is None:
                await self._fail("sai2_no_response")
            if auth == _SAI2_WRONG_PIN:
                # Definitive rejection: the centrale really refused this PIN.
                await self._fail("sai2_wrong_pin", validation=True)
            if auth != _SAI2_OK:
                # Not OK and not the wrong-PIN code: the SAI2 service/session was
                # transiently unavailable (vimarlink already retried once after a
                # re-login). Don't blame the PIN — ask the user to retry.
                await self._fail("sai2_auth_unavailable", placeholders={"code": auth})

            # Capture current state BEFORE the optimistic update.
            was_armed = self.alarm_state not in (AlarmControlPanelState.DISARMED, None)

            # Optimistic update first so the UI reflects the target state
            # immediately, hiding any intermediate disarm.
            self._apply_optimistic(mode)

            try:
                # Auto-disarm when switching between armed modes.
                if mode.command != _MODE_DISARM.command and was_armed:
                    _LOGGER.info(
                        "SAI2: area %d (%s) is armed, disarming first",
                        self._area_index,
                        group["name"],
                    )
                    disarm_result = await self.hass.async_add_executor_job(
                        vimarconnection.set_sai2_status,
                        _MODE_DISARM.command,
                        self._area_index,
                        code,
                    )
                    if disarm_result != _SAI2_OK:
                        await self._fail_command(disarm_result)
                    await asyncio.sleep(1.0)

                # Send the target command.
                _LOGGER.info(
                    "SAI2: sending command %d to area %d (%s)",
                    mode.command,
                    self._area_index,
                    group["name"],
                )
                result = await self.hass.async_add_executor_job(
                    vimarconnection.set_sai2_status,
                    mode.command,
                    self._area_index,
                    code,
                )
                if result != _SAI2_OK:
                    await self._fail_command(result)
            except Exception:
                # Drop the optimistic guard so the next poll restores the
                # real state quickly, then re-raise for the UI.
                project.sai2_optimistic_until.pop(self._group_id, None)
                await self.coordinator.async_request_refresh()
                raise

            # Success: clear any stale failure notification for this area.
            persistent_notification.async_dismiss(self.hass, f"vimar_sai2_{self._group_id}")

    async def _fail_command(self, result_code: str | None) -> NoReturn:
        """Notify + raise for a server-rejected command (PIN already validated).

        Covers transport-level failures only: no response, or a result code
        other than DPCM-0000. A wrong PIN is handled earlier by the
        authenticate_sai2_pin() pre-check.
        """
        if result_code is None:
            await self._fail("sai2_no_response")
        await self._fail("sai2_command_rejected", placeholders={"code": result_code})

    async def _fail(
        self,
        key: str,
        *,
        validation: bool = False,
        placeholders: dict[str, str] | None = None,
    ) -> NoReturn:
        """Show a persistent notification and raise the matching error.

        The toast from a raised exception is easy to miss, so we also create a
        persistent notification (localized to the user's language) that stays
        in the notification panel until dismissed. One stable notification id
        per area means a new failure replaces the previous one.
        """
        message = await self._localized_exception(key, placeholders)
        persistent_notification.async_create(
            self.hass,
            message,
            title=f"SAI Alarm — {self._attr_name}",
            notification_id=f"vimar_sai2_{self._group_id}",
        )
        exc = ServiceValidationError if validation else HomeAssistantError
        raise exc(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders=placeholders,
        )

    async def _localized_exception(self, key: str, placeholders: dict[str, str] | None) -> str:
        """Return the translated exception message for the current HA language."""
        translations = await translation.async_get_translations(
            self.hass, self.hass.config.language, "exceptions", {DOMAIN}
        )
        message = translations.get(f"component.{DOMAIN}.exceptions.{key}.message")
        if not message:
            return key
        if placeholders:
            with contextlib.suppress(KeyError, IndexError):
                message = message.format(**placeholders)
        return message
