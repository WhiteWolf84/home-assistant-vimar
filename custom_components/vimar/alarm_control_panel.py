"""Platform for Vimar SAI2 alarm control panel integration."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import secrets
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import translation
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_AUTOMATION_PIN, CONF_USER_PINS, DOMAIN
from .const import DEVICE_TYPE_ALARM as CURR_PLATFORM
from .vimar_coordinator import VimarDataUpdateCoordinator
from .vimarlink.vimarlink import VimarLink, VimarProject, is_valid_sai2_bitmask

_LOGGER = logging.getLogger(__name__)

# One SAI2 command at a time across ALL areas. A single alarm_disarm/arm call
# targeting several areas used to run them concurrently (HA gathers the entity
# calls) on the same web server session. On 2026-10-05 such a call got
# DPCM-0000 for all three areas yet only one was disarmed; concurrency is the
# prime suspect, not a proven cause. The semaphore HA builds from this constant
# covers action calls as well as updates, and with a coordinator there are no
# entity updates for it to slow down.
PARALLEL_UPDATES = 1

# Maps SAI2 child state labels to HA alarm states.
# Priority order: Allarme is checked first, then the armed/disarmed states.
SAI2_STATE_MAP = {
    "Disinserito": AlarmControlPanelState.DISARMED,
    "Inserito INT": AlarmControlPanelState.ARMED_HOME,
    "Inserito ON": AlarmControlPanelState.ARMED_AWAY,
    "Inserito PAR": AlarmControlPanelState.ARMED_NIGHT,
    "Allarme": AlarmControlPanelState.TRIGGERED,
}

# A DPCM-0000 from the set service does not mean the control unit executed the
# command, so a command - disarm or arm - is only reported as done once
# CURRENT_VALUE says so. Observed on hardware: the new value shows up from
# ~2.5 s to ~15 s after the command (and was still the old one 1-2 s after an
# arm on 2026-10-05). Meanwhile the area shows ARMING / DISARMING, and its last
# real value is guarded for the whole window plus a margin, so a stale poll
# cannot overwrite it.
_CONFIRM_TIMEOUT_SECONDS = 20.0
_CONFIRM_POLL_SECONDS = 1.0
_CONFIRM_GUARD_MARGIN_SECONDS = 5.0

# For that long after a command the live value of the area may still be the
# pre-command one, so it is not trusted to skip an arm (see _send_sai2_command).
_RECENT_COMMAND_SECONDS = _CONFIRM_TIMEOUT_SECONDS

# Only a valid bitmask (is_valid_sai2_bitmask: 8 characters of 0/1) counts as
# a reading, in the confirmation as in the poll. Anything else decodes as
# "Disinserito" in _parse_sai2_area_value and would show a disarm nobody read.
# After a command, the state shown always comes from a reading taken after it;
# without one the area is unknown, never the pre-command value.

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


class _Sai2PinCache:
    """Remember a WRONG PIN for the rest of ONE service call.

    A valid PIN is never reused: on hardware a successful check seems to
    authorise the command that follows it, not to validate the PIN for good.
    On 2026-10-05 (2026.10.0b1) one check was reused for three areas: the
    first area's command was executed, the other two were acknowledged with
    DPCM-0000 on an idle control unit and never carried out. So every command
    gets its own check (see _send_sai2_command).

    A wrong PIN (SAI2-3127) is remembered, so the other areas of the call fail
    at once and the control unit sees one wrong attempt per call instead of
    one per area. It can be trusted: the only false SAI2-3127 seen so far came
    from checks made while the control unit was still carrying out a command,
    and an area now starts only after the previous one is confirmed.

    Results are keyed by the Home Assistant context id - shared by every
    entity of one service call, and by every step of one script run - so they
    never carry over to an unrelated call. The PIN itself is never stored: the
    key holds an HMAC of it under a random per-process key. Entries expire
    after _TTL_SECONDS, longer than a call on three areas with their
    confirmations.
    """

    _TTL_SECONDS = 120.0

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)
        self._results: dict[tuple[str, bytes], tuple[str, float]] = {}

    def _digest(self, pin: str) -> bytes:
        return hmac.new(self._key, pin.encode(), hashlib.sha256).digest()

    def _prune(self) -> None:
        now = time.monotonic()
        for key in [k for k, (_, expiry) in self._results.items() if expiry <= now]:
            del self._results[key]

    def get(self, context_id: str | None, pin: str) -> str | None:
        """Return SAI2-3127 if this call already found `pin` wrong, else None."""
        if context_id is None:
            return None
        self._prune()
        entry = self._results.get((context_id, self._digest(pin)))
        return entry[0] if entry else None

    def put(self, context_id: str | None, pin: str, result: str | None) -> None:
        """Remember a wrong PIN for the rest of this call; nothing else."""
        if context_id is None or result != _SAI2_WRONG_PIN:
            return
        self._prune()
        self._results[(context_id, self._digest(pin))] = (
            result,
            time.monotonic() + self._TTL_SECONDS,
        )


@dataclass(frozen=True)
class _Sai2Mode:
    """A single SAI2 target mode: command code, bitmask and label.

    Keeping the command code, the CURRENT_VALUE bitmask of the mode and the
    child label together avoids the fragile label -> bitmask -> label
    round-trip the previous code relied on. The label maps back to the HA
    state via SAI2_STATE_MAP.
    """

    command: int  # SAI2 SOAP command: 0=OFF, 1=ON, 2=INT, 3=PAR
    bitmask: str  # DPADD_OBJECT.CURRENT_VALUE once the mode is active
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
        # Record the platform as set up with zero entities: the unload path
        # keys off coordinator.forwarded_platforms, but leaving the key absent
        # here made devices_for_platform disagree with what was loaded.
        coordinator.devices_for_platform[CURR_PLATFORM] = []
        return

    # Register the single SAI Alarm device.
    # All alarm and zone entities will be nested under this device.
    sai_device_info: dict[str, Any] = {
        "identifiers": {(DOMAIN, "sai2_alarm")},
        "name": "SAI Alarm",
        "manufacturer": "Vimar",
        "model": "SAI2",
    }
    # Nest the alarm under the web server it belongs to. This used to read
    # `coordinator.webserver_id`, an attribute nothing ever assigned, so the
    # branch never ran - and would not have worked if it had: it built a
    # two-element identifier, while the hub is registered with three.
    sai_device_info["via_device"] = coordinator.webserver_identifiers
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

    # One per config entry, shared by its areas: see _Sai2PinCache.
    pin_cache = _Sai2PinCache()

    entities: list[VimarAlarmControlPanel] = []
    for area_index, (group_id, group_data) in enumerate(vimarproject.sai2_groups.items(), start=1):
        entities.append(
            VimarAlarmControlPanel(
                coordinator,
                group_id,
                group_data,
                area_index,
                user_pins,
                automation_pin,
                pin_cache,
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
        pin_cache: _Sai2PinCache,
    ) -> None:
        """Initialize the alarm control panel."""
        super().__init__(coordinator)
        self._group_id = group_id
        self._group_data = group_data
        self._area_index = area_index
        self._user_pins = user_pins
        self._automation_pin = automation_pin
        self._pin_cache = pin_cache
        self._attr_name = group_data["name"]
        self._attr_unique_id = f"vimar_sai2_{group_id}"
        # Serialize commands on this area so an auto-disarm + arm sequence
        # cannot interleave with another in-flight command.
        self._command_lock = asyncio.Lock()
        # Set when a command could not be confirmed and no real value is known
        # to fall back on: the panel reports unknown - never the requested
        # mode - until a poll brings a real value or a new command.
        self._state_unknown = False
        # The mode a command in progress is waiting to see confirmed: the
        # panel shows ARMING / DISARMING until then (see _begin_transition).
        self._pending_mode: _Sai2Mode | None = None
        # Monotonic time of the last command sent to this area (see
        # _RECENT_COMMAND_SECONDS); None until the first one.
        self._last_command_at: float | None = None

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
        if project is None or self._state_unknown:
            return None
        if self._pending_mode is not None:
            # A command is waiting for the control unit's confirmation.
            if self._pending_mode.command == _MODE_DISARM.command:
                return AlarmControlPanelState.DISARMING
            return AlarmControlPanelState.ARMING

        # --- Primary: live bitmask from DPADD_OBJECT ---
        area_values = project.sai2_area_values
        if area_values is not None and self._group_id in area_values:
            raw = area_values[self._group_id]
            if raw is None:
                return None  # not readable: unknown, never "disarmed"
            label, _memory = _parse_sai2_area_value(raw)
            return SAI2_STATE_MAP.get(label, AlarmControlPanelState.DISARMED)

        # --- Fallback: children dict (populated at discovery / slim poll) ---
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
            # The bitmask alarm_state is decoded from (the last real reading,
            # also while a command is in flight), so the recorder keeps the
            # raw value behind every state change.
            "sai2_raw": raw,
        }
        if project and project.sai2_groups:
            group = project.sai2_groups.get(self._group_id, {})
            children = group.get("children", {})
            for label, child in children.items():
                attrs[f"sai2_{label}"] = child.get("value", "?")
        return attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Leave the unknown state as soon as a poll brings a real value."""
        if self._state_unknown and self._current_raw() is not None:
            self._state_unknown = False
        super()._handle_coordinator_update()

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

    def _begin_transition(self, mode: _Sai2Mode) -> None:
        """Show ARMING / DISARMING until the control unit confirms `mode`.

        The requested mode is never shown before it is confirmed. The cached
        live value is left alone - the last real reading, kept from polls by
        the guard - so whatever ends the command early (an error, a cancelled
        script) falls back to the real state, never to the requested one.
        An intermediate disarm (switching between armed modes) stays hidden
        behind ARMING.
        """
        self._state_unknown = False
        self._pending_mode = mode
        self.async_write_ha_state()

    def _end_transition(self) -> None:
        """Leave ARMING / DISARMING, showing the cached (real) value."""
        if self._pending_mode is not None:
            self._pending_mode = None
            self.async_write_ha_state()

    def _extend_guard(self, project: VimarProject, seconds: float) -> None:
        """Protect this area's cached value from polls for `seconds`, never less.

        Only ever moves the deadline forward, so a later call can never cut
        down a guard that is already covering a command in progress.
        """
        guard = project.sai2_optimistic_until
        guard[self._group_id] = max(guard.get(self._group_id, 0.0), time.monotonic() + seconds)

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

        The HA code is forwarded to the control unit as the user PIN, and
        checked right before EVERY command (see _check_pin). When switching
        between armed modes (e.g. PAR -> ON) the SAI2 system requires a disarm
        first; this is handled automatically, behind the ARMING state, so it
        stays invisible to the user: check, disarm, wait for the disarm to be
        confirmed, check again, arm.

        Every command is reported as done only once the area's live
        CURRENT_VALUE confirms it (see _confirm_mode): the set service answers
        DPCM-0000 even when the control unit does not execute the command.

        A disarm is always sent. An arm is skipped only when a live reading
        shows the area already in that mode AND no command went to the area in
        the last _RECENT_COMMAND_SECONDS - right after a command the live value
        can still be the old one, so it is then sent again (without the
        intermediate disarm, which is only for switching between armed modes).

        Raises:
            ServiceValidationError: if no code was provided or the PIN is wrong.
            HomeAssistantError: if no connection/area was available, the
                server rejected the command, or it was not confirmed.
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
            started = time.monotonic()
            is_disarm = mode.command == _MODE_DISARM.command
            # Taken before our own guard below: a guard still running, or a
            # command sent moments ago, means the live value may be stale.
            recent_command = project.sai2_optimistic_until.get(self._group_id, 0.0) > started or (
                self._last_command_at is not None
                and started - self._last_command_at < _RECENT_COMMAND_SECONDS
            )
            # Guard the whole command up-front - PIN check, SOAP call and
            # confirmation - so neither a slow authenticate nor a slow set call
            # can let it lapse and a stale poll flip the panel back.
            self._extend_guard(project, _CONFIRM_TIMEOUT_SECONDS + _CONFIRM_GUARD_MARGIN_SECONDS)
            try:
                # Validate the PIN up-front, before we touch any state: the set
                # service accepts any PIN with DPCM-0000, authenticate reports
                # a wrong one immediately and unambiguously.
                await self._check_pin(vimarconnection, code, group["name"])
            except Exception:
                # Nothing was sent and the cached value is still the real one:
                # just release the up-front guard.
                project.sai2_optimistic_until.pop(self._group_id, None)
                raise

            # Decide from the control unit's live state rather than the last
            # poll, which can be a scan interval old.
            live = await self._read_live_raw(vimarconnection)
            if live is not None:
                live_label = _parse_sai2_area_value(live)[0]
                if not is_disarm and live_label == mode.label:
                    if not recent_command:
                        # Already armed in the requested mode: send nothing.
                        # Re-sending used to disarm the area first, briefly.
                        _LOGGER.info(
                            "SAI2: area %d (%s) is already %s, nothing to send",
                            self._area_index,
                            group["name"],
                            live_label,
                        )
                        self._show_real_state(project, live)
                        persistent_notification.async_dismiss(
                            self.hass, f"vimar_sai2_{self._group_id}"
                        )
                        return
                    _LOGGER.info(
                        "SAI2: area %d (%s) reads %s but was commanded moments ago; "
                        "sending again, without the intermediate disarm",
                        self._area_index,
                        group["name"],
                        live_label,
                    )
                # Intermediate disarm only to switch between armed modes.
                was_armed = live_label not in (_MODE_DISARM.label, mode.label)
            else:
                # No live reading: fall back on the last poll. An area it shows
                # in the requested mode gets the command again, but never the
                # intermediate disarm meant for switching between armed modes.
                state = self.alarm_state
                was_armed = state not in (
                    AlarmControlPanelState.DISARMED,
                    None,
                    SAI2_STATE_MAP[mode.label],
                )

            # ARMING / DISARMING until the control unit confirms the mode.
            self._begin_transition(mode)
            # Once a command may have reached the control unit, the value read
            # before it no longer tells the area's state.
            sent = False

            try:
                # Auto-disarm when switching between armed modes.
                if mode.command != _MODE_DISARM.command and was_armed:
                    _LOGGER.info(
                        "SAI2: area %d (%s) is armed, disarming first",
                        self._area_index,
                        group["name"],
                    )
                    self._last_command_at = time.monotonic()
                    sent = True
                    disarm_result = await self.hass.async_add_executor_job(
                        vimarconnection.set_sai2_status,
                        _MODE_DISARM.command,
                        self._area_index,
                        code,
                    )
                    if disarm_result != _SAI2_OK:
                        await self._fail_command(disarm_result)
                    # Wait for the disarm to be carried out: a PIN check made
                    # while the control unit is still busy is slow and may be
                    # rejected, and the arm needs a check of its own. How long
                    # a check authorises is not known; the 11:27 log of
                    # 2026-10-05 (one check, then a disarm and an arm within
                    # 1.04 s, both carried out) favours a ~2 s window over
                    # "one command per check". Checking again before the arm
                    # is right under either.
                    raw = await self._wait_for_mode(
                        project, vimarconnection, group["name"], _MODE_DISARM
                    )
                    if raw is None or _parse_sai2_area_value(raw)[0] != _MODE_DISARM.label:
                        await self._not_confirmed(project, group["name"], mode, _MODE_DISARM, raw)
                    await self._check_pin(vimarconnection, code, group["name"])

                # Send the target command.
                _LOGGER.info(
                    "SAI2: sending command %d to area %d (%s)",
                    mode.command,
                    self._area_index,
                    group["name"],
                )
                sent_at = time.monotonic()
                self._last_command_at = sent_at
                sent = True
                result = await self.hass.async_add_executor_job(
                    vimarconnection.set_sai2_status,
                    mode.command,
                    self._area_index,
                    code,
                )
                _LOGGER.debug(
                    "SAI2: area %d (%s) command %d -> %s in %.2fs",
                    self._area_index,
                    group["name"],
                    mode.command,
                    result,
                    time.monotonic() - sent_at,
                )
                if result != _SAI2_OK:
                    await self._fail_command(result)

                await self._confirm_mode(project, vimarconnection, group["name"], mode)
            except Exception:
                # Show the real state - unless _not_confirmed already did - drop
                # the guard so the next poll can update it, re-raise for the UI.
                if self._pending_mode is not None:
                    if sent:
                        # A rejected or failed call may still have been carried
                        # out: only a reading taken now tells the state.
                        raw = await self._read_live_raw(vimarconnection)
                        if raw is not None:
                            self._show_real_state(project, raw)
                        else:
                            self._show_unknown_state(project)
                    else:
                        self._end_transition()
                project.sai2_optimistic_until.pop(self._group_id, None)
                await self.coordinator.async_request_refresh()
                raise
            finally:
                # Whatever ended the command - even a cancelled script - the
                # transitional state must not outlive it. Cancelled after a
                # command went out: nothing read since, so unknown.
                if self._pending_mode is not None and sent:
                    self._show_unknown_state(project)
                self._end_transition()

            # Success: clear any stale failure notification for this area.
            persistent_notification.async_dismiss(self.hass, f"vimar_sai2_{self._group_id}")

    async def _check_pin(self, vimarconnection: VimarLink, code: str, area_name: str) -> None:
        """Check the PIN right before a command, or fail.

        Every command gets its own check: a successful check seems to
        authorise the command that follows it, so a check reused for another
        command left that command acknowledged and never carried out (see
        _Sai2PinCache). Only a wrong PIN found earlier in the same call is
        reused, to fail at once without another attempt on the control unit.
        """
        started = time.monotonic()
        context_id = self._context.id if self._context else None
        auth = self._pin_cache.get(context_id, code)
        rejected_earlier = auth is not None
        if auth is None:
            auth = await self.hass.async_add_executor_job(
                vimarconnection.authenticate_sai2_pin, code
            )
            self._pin_cache.put(context_id, code, auth)
        _LOGGER.debug(
            "SAI2: area %d (%s) authenticate -> %s in %.2fs%s",
            self._area_index,
            area_name,
            auth,
            time.monotonic() - started,
            " (rejected earlier in this call)" if rejected_earlier else "",
        )
        if auth is None:
            await self._fail("sai2_no_response")
        if auth == _SAI2_WRONG_PIN:
            # Definitive rejection: the centrale really refused this PIN.
            await self._fail("sai2_wrong_pin", validation=True)
        if auth != _SAI2_OK:
            # Not OK and not the wrong-PIN code: the SAI2 service/session
            # was transiently unavailable (vimarlink already retried once
            # after a re-login). Don't blame the PIN — ask to retry.
            await self._fail("sai2_auth_unavailable", placeholders={"code": auth})

    async def _wait_for_mode(
        self,
        project: VimarProject,
        vimarconnection: VimarLink,
        area_name: str,
        mode: _Sai2Mode,
    ) -> str | None:
        """Read the area's live CURRENT_VALUE until it reads `mode`.

        Reads the area's own DPADD_OBJECT row once per second, outside the
        coordinator poll, for up to _CONFIRM_TIMEOUT_SECONDS. Only a
        well-formed bitmask counts: an empty/NULL value is a missing reading
        and is retried, never taken as a disarm. Touches neither the state
        shown nor the cached value.

        Returns the confirming reading, or the last valid one on timeout
        (None if there was none): the caller checks which.
        """
        start = time.monotonic()
        deadline = start + _CONFIRM_TIMEOUT_SECONDS
        # Re-extend from here: a slow PIN check + SOAP call may have eaten into
        # the up-front guard, which must still outlast the whole confirmation.
        self._extend_guard(project, _CONFIRM_TIMEOUT_SECONDS + _CONFIRM_GUARD_MARGIN_SECONDS)

        last_valid: str | None = None
        while time.monotonic() < deadline:
            await asyncio.sleep(_CONFIRM_POLL_SECONDS)
            raw = await self._read_live_raw(vimarconnection)
            _LOGGER.debug(
                "SAI2: area %d (%s) confirm raw=%r after %.1fs",
                self._area_index,
                area_name,
                raw,
                time.monotonic() - start,
            )
            if raw is None:
                continue  # failed / NULL / malformed: no reading, try again
            last_valid = raw
            if _parse_sai2_area_value(raw)[0] == mode.label:
                break
        return last_valid

    async def _confirm_mode(
        self,
        project: VimarProject,
        vimarconnection: VimarLink,
        area_name: str,
        mode: _Sai2Mode,
    ) -> None:
        """Wait until the area's live CURRENT_VALUE reads `mode`, or fail.

        The last real value stays guarded for the whole window and the panel
        shows ARMING / DISARMING, so it neither flips on a stale poll nor
        claims a mode the control unit never confirmed. On success the
        confirmed value is shown; on timeout see _not_confirmed.
        """
        raw = await self._wait_for_mode(project, vimarconnection, area_name, mode)
        if raw is not None and _parse_sai2_area_value(raw)[0] == mode.label:
            self._show_real_state(project, raw)
            return
        await self._not_confirmed(project, area_name, mode, mode, raw)

    async def _not_confirmed(
        self,
        project: VimarProject,
        area_name: str,
        mode: _Sai2Mode,
        step: _Sai2Mode,
        last_valid: str | None,
    ) -> NoReturn:
        """`step` of the command for `mode` was not confirmed: show and fail.

        `step` is `mode` itself, or the intermediate disarm of a switch
        between armed modes. The entity is put back on the real state BEFORE
        the error is raised - the last valid reading taken after the command,
        or unknown if there was none: never the pre-command value, which the
        command may have changed - because a caller (e.g. a script) may check
        the state right after the failed action, and the coordinator refresh
        in the caller's except block goes through a debouncer that does not
        always run it at once.
        """
        # WARNING on purpose: it lands in system_log even without debug.
        _LOGGER.warning(
            "SAI2: area %d (%s) not confirmed %s %.0fs after the command%s "
            "(last valid CURRENT_VALUE %s) - the area may NOT be in that state",
            self._area_index,
            area_name,
            step.label,
            _CONFIRM_TIMEOUT_SECONDS,
            "" if step is mode else f" (intermediate step before {mode.label})",
            last_valid,
        )
        if last_valid is not None:
            self._show_real_state(project, last_valid)
        else:
            self._show_unknown_state(project)
        await self._fail(
            "sai2_disarm_not_confirmed"
            if mode.command == _MODE_DISARM.command
            else "sai2_arm_not_confirmed",
            placeholders={
                "area": area_name,
                "seconds": f"{_CONFIRM_TIMEOUT_SECONDS:.0f}",
            },
        )

    async def _read_live_raw(self, vimarconnection: VimarLink) -> str | None:
        """Read this area's live CURRENT_VALUE; None if failed or not valid."""
        try:
            values = await self.hass.async_add_executor_job(
                vimarconnection.get_sai2_area_raw_values, [self._group_id]
            )
        except Exception as err:  # noqa: BLE001 - callers treat it as "no reading"
            _LOGGER.debug("SAI2: area %d live read failed: %s", self._area_index, err)
            return None
        raw = (values or {}).get(self._group_id)
        return raw if is_valid_sai2_bitmask(raw) else None

    def _show_real_state(self, project: VimarProject, raw: str) -> None:
        """Show the real value `raw`, end the transition, drop the guard."""
        # Created if missing, as the coordinator poll does: without it the
        # state would come from the children dict of the last discovery.
        if project.sai2_area_values is None:
            project.sai2_area_values = {}
        project.sai2_area_values[self._group_id] = raw
        project.sai2_optimistic_until.pop(self._group_id, None)
        self._state_unknown = False
        self._pending_mode = None
        self.async_write_ha_state()

    def _show_unknown_state(self, project: VimarProject) -> None:
        """No real value to show: report unknown, never the requested mode."""
        if project.sai2_area_values is not None:
            project.sai2_area_values.pop(self._group_id, None)
        project.sai2_optimistic_until.pop(self._group_id, None)
        self._state_unknown = True
        self._pending_mode = None
        self.async_write_ha_state()

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
