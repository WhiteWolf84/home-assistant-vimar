"""Platform for climate integration."""

import logging
from typing import NoReturn

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_OFF,
    FAN_ON,
    PRESET_AWAY,
    PRESET_ECO,
    PRESET_NONE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.exceptions import ServiceValidationError

from .const import (
    CLIMATE_POST_WRITE_REFRESH_DELAY,
    CLIMATE_REFRESH_STATUS_NAMES,
    DOMAIN,
    PRESET_CLIMATE_AUTO,
    PRESET_CLIMATE_PROTECTION,
    VIMAR_CLIMATE_ASSENZA,
    VIMAR_CLIMATE_ASSENZA_II,
    VIMAR_CLIMATE_AUTO,
    VIMAR_CLIMATE_AUTO_I,
    VIMAR_CLIMATE_AUTO_II,
    VIMAR_CLIMATE_COOL,
    VIMAR_CLIMATE_COOL_I,
    VIMAR_CLIMATE_COOL_II,
    VIMAR_CLIMATE_HEAT,
    VIMAR_CLIMATE_HEAT_I,
    VIMAR_CLIMATE_HEAT_II,
    VIMAR_CLIMATE_MANUAL,
    VIMAR_CLIMATE_MANUAL_I,
    VIMAR_CLIMATE_MANUAL_II,
    VIMAR_CLIMATE_OFF,
    VIMAR_CLIMATE_OFF_I,
    VIMAR_CLIMATE_OFF_II,
    VIMAR_CLIMATE_PROTEZIONE,
    VIMAR_CLIMATE_PROTEZIONE_I,
    VIMAR_CLIMATE_PROTEZIONE_II,
    VIMAR_CLIMATE_RIDUZIONE,
    VIMAR_CLIMATE_RIDUZIONE_I,
    VIMAR_CLIMATE_RIDUZIONE_II,
)
from .const import DEVICE_TYPE_CLIMATES as CURR_PLATFORM
from .vimar_entity import MISSING_DEVICE, VimarEntity, vimar_setup_entry

_LOGGER = logging.getLogger(__name__)

# Known Vimar object types that include a physical fan coil unit.
# CH_HVAC_NoZonaNeutra and similar thermostats expose 'modalita_fancoil' and
# 'velocita_fancoil' status keys with value '0' even when no fan coil is
# physically present. The only reliable discriminant is therefore object_type.
FANCOIL_OBJECT_TYPES = (
    "CH_Fancoil",
    "CH_HVAC_FanCoil",
    "CH_HVAC_FanCoilWithNeutralZone",
)

# Raw 'funzionamento' codes with a known meaning, per climate_type (see
# get_const_value). Anything else - an empty CURRENT_VALUE included - is not a
# valid reading: the mode is reported as unknown and only turning the
# thermostat off is accepted, never a default such as cool or heat.
KNOWN_FUNZIONAMENTO: dict[str, frozenset[str]] = {
    "heat_cool": frozenset(
        {
            VIMAR_CLIMATE_OFF_I,
            VIMAR_CLIMATE_MANUAL_I,
            VIMAR_CLIMATE_AUTO_I,
            VIMAR_CLIMATE_RIDUZIONE_I,
            VIMAR_CLIMATE_PROTEZIONE_I,
        }
    ),
    "heat_cool_fancoil": frozenset(
        {
            VIMAR_CLIMATE_AUTO_II,
            VIMAR_CLIMATE_MANUAL_II,
            VIMAR_CLIMATE_RIDUZIONE_II,
            VIMAR_CLIMATE_ASSENZA_II,
            VIMAR_CLIMATE_PROTEZIONE_II,
            VIMAR_CLIMATE_OFF_II,
        }
    ),
}

# Raw heat/cool direction: 'stagione' on Type I, 'regolazione' on Type II.
# The Type II neutral zone ('0', VIMAR_CLIMATE_NEUTRAL_II) has no HVACMode in
# hvac_modes and reads as unknown.
DIRECTION_MODES: dict[str, dict[str, HVACMode]] = {
    "heat_cool": {VIMAR_CLIMATE_HEAT_I: HVACMode.HEAT, VIMAR_CLIMATE_COOL_I: HVACMode.COOL},
    "heat_cool_fancoil": {
        VIMAR_CLIMATE_HEAT_II: HVACMode.HEAT,
        VIMAR_CLIMATE_COOL_II: HVACMode.COOL,
    },
}


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the Vimar Climate platform."""
    vimar_setup_entry(VimarClimate, CURR_PLATFORM, hass, entry, async_add_devices)


class VimarClimate(VimarEntity, ClimateEntity):
    """Provides a Vimar climates."""

    # {'status_id': '2129', 'status_value': '0', 'status_range': 'min=0|max=1'},
    # 'regolazione': {'status_id': '2131', 'status_value': '2', 'status_range': ''},
    # 'modalita_fancoil': {'status_id': '2135', 'status_value': '0', 'status_range': 'min=0|max=1'},
    # 'velocita_fancoil': {'status_id': '2137', 'status_value': '0', 'status_range': 'min=0|max=100'},
    # 'funzionamento': {'status_id': '2139', 'status_value': '6', 'status_range': ''},
    # 'setpoint': {'status_id': '2146', 'status_value': '21.00', 'status_range': 'min=-273|max=670760'},
    # 'temporizzazione': {'status_id': '2152', 'status_value': '1', 'status_range': 'min=0|max=65535'},
    # 'temperatura_misurata': {'status_id': '2160', 'status_value': '24.40', 'status_range': 'min=-273|max=670760'},
    # 'stato_boost on/off': {'status_id': '2163', 'status_value': '0', 'status_range': 'min=0|max=1'},
    # 'stato_principale_condizionamento on/off': {'status_id': '2164', 'status_value': '0', 'status_range': 'min=0|max=1'},
    # 'stato_principale_riscaldamento on/off': {'status_id': '2165', 'status_value': '0', 'status_range': 'min=0|max=1'},
    # 'uscita4': {'status_id': '2944', 'status_value': 'non_utilizzata', 'status_range':
    #   'principale_riscaldamento=0|boost_riscaldamento=0|principale_condizionamento=0|boost_condizionamento=0'},
    # 'uscita3': {'status_id': '2945', 'status_value': 'non_utilizzata', 'status_range':
    #   'principale_riscaldamento=0|boost_riscaldamento=0|principale_condizionamento=0|boost_condizionamento=0'},
    # 'uscita2': {'status_id': '2946', 'status_value': 'non_utilizzata', 'status_range':
    #   'principale_riscaldamento=0|boost_riscaldamento=0|principale_condizionamento=0|boost_condizionamento=0'},
    # 'uscita1': {'status_id': '2947', 'status_value': 'CH_Uscita_ValvolaOnOff', 'status_range':
    #    'principale_riscaldamento=1|boost_riscaldamento=0|principale_condizionamento=0|boost_condizionamento=0'},
    # 'forzatura off': {'status_id': '3282', 'status_value': '0', 'status_range': ''}}

    # Row000008: '951','on/off','-1','0' # idle/working
    # Row000009: '952','setpoint','-1','15.6'  #desired temperature
    # Row000010: '953','temporizzazione','-1','0'  #timer (forcing temp)
    # Row000011: '954','temperatura','-1','25.6'  #current temp
    # Row000014: '959','unita','-1','0' #unit of measurement

    # # other climate (heat_cool_fancoil)
    # Row000001: 'ID','NAME','STATUS_ID','CURRENT_VALUE'
    # Row000006: '62791','forzatura off','-1','0'  #i don't know
    # Row000007: '62793','allarme_massetto','-1','0'  #alarm temp for cooling floor
    # Row000008: '62795','regolazione','-1','1'  # heating/cooling/neutral zone
    # Row000009: '62799','modalita_fancoil','-1','0'  #fan auto or manual
    # Row000010: '62801','velocita_fancoil','-1','0'  #fan speed
    # Row000011: '62803','funzionamento','-1','6'  #mode of operation on/off
    # Row000012: '62811','setpoint','-1','25.00'  #desired temperature
    # Row000013: '62819','temporizzazione','-1','1'  #timer (forcing temp)
    # Row000014: '62830','temperatura_misurata','-1','25.70'  #current temp
    # Row000015: '62833','stato_boost on/off','-1','0'  #activates second output to reach the setpoint first
    # Row000016: '62834','stato_principale_condizionamento on/off','-1','0'  #cooling on/ff
    # Row000017: '62835','stato_principale_riscaldamento on/off','-1','0'  #heating on/ff

    # Status keys already reported as unrecognised, so each is logged once per
    # entity. Replaced, never mutated, so this class-level default is not shared.
    _unrecognised_logged: frozenset[str] = frozenset()

    def __init__(self, coordinator, device_id: int):
        """Initialize the climate."""
        VimarEntity.__init__(self, coordinator, device_id)

    # climate properties
    @property
    def translation_key(self) -> str:
        """Return translation key for entity strings and icons."""
        return "vimar_climate"

    @property
    def entity_platform(self):
        """Return the platform of the entity."""
        return CURR_PLATFORM

    @property
    def is_on(self) -> bool | None:
        """Return whether the thermostat is on, or None if the mode is unknown."""
        mode = self._operating_mode
        if mode is None:
            return None
        return mode != self.get_const_value(VIMAR_CLIMATE_OFF)

    @property
    def supported_features(self):
        """Flag supported features."""
        flags = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.PRESET_MODE
        )
        if self._has_fancoil:
            flags |= ClimateEntityFeature.FAN_MODE
        return flags

    @property
    def current_temperature(self):
        """Return current temperature."""
        if self.has_state("temperatura"):
            return float(self.get_state("temperatura") or 0)
        if self.has_state("temperatura_misurata"):
            return float(self.get_state("temperatura_misurata") or 0)

    @property
    def current_humidity(self):
        """Return current humidity."""
        if self.has_state("umidita"):
            return float(self.get_state("umidita") or 0)

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return float(self.get_state("setpoint") or 0)

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return 0.1

    @property
    def temperature_unit(self):
        """Return unit of temperature measurement."""
        if self.has_state("unita"):
            return (UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS)[
                self.get_state("unita") == "0"
            ]
        return UnitOfTemperature.CELSIUS

    @property
    def hvac_mode(self):
        """Return current heat/cool direction, or OFF.

        Auto/Manual/ECO/Away/Protection are expressed as preset_mode.
        The heat/cool direction persists across preset changes.
        None (state 'unknown') when the mode, or the direction of a thermostat
        that is on, is not a valid reading. Off needs no direction.
        """
        is_on = self.is_on
        if is_on is None:
            return None
        if not is_on:
            return HVACMode.OFF
        return self._direction

    @property
    def hvac_modes(self):
        """List of available operation modes."""
        return [HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]

    @property
    def preset_modes(self) -> list[str]:
        """Return available preset modes."""
        presets = [PRESET_NONE, PRESET_CLIMATE_AUTO, PRESET_ECO, PRESET_CLIMATE_PROTECTION]
        if self.climate_type == "heat_cool_fancoil":
            presets.append(PRESET_AWAY)
        return presets

    @property
    def preset_mode(self) -> str | None:
        """Return current preset (operating mode), independent of heat/cool direction."""
        funz = self._operating_mode
        if funz is None:
            return None
        if funz == self.get_const_value(VIMAR_CLIMATE_AUTO):
            return PRESET_CLIMATE_AUTO
        if funz == self.get_const_value(VIMAR_CLIMATE_RIDUZIONE):
            return PRESET_ECO
        # PROTEZIONE must be checked before ASSENZA: Type I thermostats have no
        # absence mode, so get_const_value(ASSENZA) falls back to the same raw
        # value as PROTEZIONE ("3"). Matching ASSENZA first would report
        # PRESET_AWAY, which is not in preset_modes for Type I.
        if funz == self.get_const_value(VIMAR_CLIMATE_PROTEZIONE):
            return PRESET_CLIMATE_PROTECTION
        if funz == self.get_const_value(VIMAR_CLIMATE_ASSENZA):
            return PRESET_AWAY
        return PRESET_NONE

    @property
    def hvac_action(self):
        """Return current HVAC action (heating, cooling, idle, off), None if unknown."""
        hvac_mode = self.hvac_mode
        if hvac_mode is None:
            return None
        if hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        if self.has_state("on/off") and self.get_state("on/off") == "0":
            return HVACAction.IDLE

        if self.climate_type == "heat_cool":
            return HVACAction.COOLING if hvac_mode == HVACMode.COOL else HVACAction.HEATING
        else:
            if (
                self.has_state("stato_principale_condizionamento on/off")
                and self.get_state("stato_principale_condizionamento on/off") == "1"
            ):
                return HVACAction.COOLING
            elif (
                self.has_state("stato_principale_riscaldamento on/off")
                and self.get_state("stato_principale_riscaldamento on/off") == "1"
            ):
                return HVACAction.HEATING
            return HVACAction.IDLE

    async def async_set_preset_mode(self, preset_mode):
        """Set operating mode (funzionamento), keeping heat/cool direction unchanged.

        The preset lives in 'funzionamento', the very status that also holds
        off: writing any preset to an off thermostat switches it on, and only
        set_hvac_mode may do that. Refused while off or unknown.
        """
        self._refuse_unless_on("set_preset_mode", "climate_preset_while_off")
        if preset_mode == PRESET_CLIMATE_AUTO:
            set_function_mode = self.get_const_value(VIMAR_CLIMATE_AUTO)
        elif preset_mode == PRESET_ECO:
            set_function_mode = self.get_const_value(VIMAR_CLIMATE_RIDUZIONE)
        elif preset_mode == PRESET_AWAY:
            set_function_mode = self.get_const_value(VIMAR_CLIMATE_ASSENZA)
        elif preset_mode == PRESET_CLIMATE_PROTECTION:
            set_function_mode = self.get_const_value(VIMAR_CLIMATE_PROTEZIONE)
        else:  # PRESET_NONE → manual
            set_function_mode = self.get_const_value(VIMAR_CLIMATE_MANUAL)

        _LOGGER.info("Vimar Climate setting preset_mode: %s", preset_mode)
        self.change_state("funzionamento", set_function_mode)
        self._schedule_post_write_refresh()

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the list of available fan modes. Requires ClimateEntityFeature.FAN_MODE."""
        if not self._has_fancoil:
            return None
        return [FAN_ON, FAN_OFF, FAN_LOW, FAN_MEDIUM, FAN_HIGH]

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode. Requires ClimateEntityFeature.FAN_MODE."""
        if not self._has_fancoil:
            return None

        if self.has_state("modalita_fancoil") and self.get_state("modalita_fancoil") == "0":
            return FAN_OFF

        if self.has_state("velocita_fancoil"):
            fancoil_speed = float(self.get_state("velocita_fancoil") or 0)
            if fancoil_speed == 0:
                return FAN_ON
            elif fancoil_speed <= 33:
                return FAN_LOW
            elif fancoil_speed <= 66:
                return FAN_MEDIUM
            else:
                return FAN_HIGH
        return None

    async def async_set_fan_mode(self, fan_mode):
        """Set new target fan mode."""
        if not self._has_fancoil:
            return
        _LOGGER.info("Vimar Climate setting fan_mode: %s", fan_mode)

        if self.has_state("velocita_fancoil") and self.has_state("modalita_fancoil"):
            if fan_mode in (FAN_ON, FAN_OFF):
                self.change_state("modalita_fancoil", ("0", "1")[fan_mode == FAN_ON])
            else:
                fancoil_speed = {FAN_LOW: "33", FAN_MEDIUM: "66", FAN_HIGH: "100"}.get(
                    fan_mode, "0"
                )
                self.change_state("modalita_fancoil", "1", "velocita_fancoil", fancoil_speed)

    async def async_set_hvac_mode(self, hvac_mode):
        """Set heat/cool direction, or turn off.

        HEAT/COOL change only the season direction (stagione/regolazione).
        If currently OFF, also switch funzionamento to MANUAL to activate.
        The current preset (auto/eco/away/protection) is preserved when ON.

        The setpoint is intentionally NOT written here. On activation the
        thermostat regulates to its own stored manual setpoint; the setpoint is
        owned solely by set_temperature(). Re-sending a cached setpoint
        alongside the mode caused the well-known race where a stale value
        overwrote an explicit set_temperature() issued in the same command
        batch (see set_temperature docstring).
        """
        if hvac_mode == HVACMode.OFF:
            # Accepted from any state, unknown included: off is the safe direction.
            _LOGGER.info("Vimar Climate setting hvac_mode to off")
            self.change_state("funzionamento", self.get_const_value(VIMAR_CLIMATE_OFF))
            self._schedule_post_write_refresh()
            return

        if hvac_mode not in (HVACMode.HEAT, HVACMode.COOL):
            return

        if self._operating_mode is None:
            # Whether this is an activation (manual) or a direction change that
            # keeps the preset cannot be told: turn it off first.
            self._refuse("set_hvac_mode", "climate_mode_unknown")

        direction = self.get_const_value(
            VIMAR_CLIMATE_COOL if hvac_mode == HVACMode.COOL else VIMAR_CLIMATE_HEAT
        )
        _LOGGER.info("Vimar Climate setting direction to %s", hvac_mode)

        season_key = "stagione" if self.climate_type == "heat_cool" else "regolazione"

        if not self.is_on:
            # Device is OFF: activate in manual mode with the chosen direction.
            # No setpoint write: the device keeps its stored manual setpoint.
            self.change_state(
                "funzionamento",
                self.get_const_value(VIMAR_CLIMATE_MANUAL),
                season_key,
                direction,
            )
            # Activation reloads the device's stored manual setpoint, which HA
            # does not know: pull it back once the device has applied the mode.
            self._schedule_post_write_refresh()
        else:
            # Device is ON: change direction only, preserve operating mode (preset)
            self.change_state(season_key, direction)

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature.

        When the thermostat is already running in manual mode, write ONLY the
        setpoint, exactly like the native VIMAR web UI (a single SETVALUE).
        Re-sending funzionamento=MANUAL or the season alongside the setpoint
        makes the firmware reload its stored manual setpoint and discard the
        value we just wrote.

        When the thermostat is in away (absence) mode, also write ONLY the
        setpoint: the firmware keeps per-mode setpoints and applies a plain
        SETVALUE to the ACTIVE mode, so this edits the absence setpoint while
        staying in absence. Verified against the native By-Web UI ("T Assenza"
        panel): it sends a single SETVALUE on the same 'setpoint' status, with
        no funzionamento write. Re-sending funzionamento here would drop the
        thermostat to manual mode instead.

        When the thermostat is on in another preset (auto/eco/protection),
        activate manual mode first and apply the setpoint last (manual
        override). change_state() sends the writes sequentially in this order,
        so the setpoint wins. The heat/cool direction is left untouched.

        An off or unknown thermostat is never switched on from here and no
        setpoint is written to it: the call is refused. With an explicit
        hvac_mode in the same call, set_hvac_mode() - the only method allowed
        to switch the thermostat on - runs first, then the setpoint. With
        hvac_mode off the whole call is refused, so nothing is half applied.
        """
        set_temperature = kwargs.get(ATTR_TEMPERATURE)
        if set_temperature is None:
            return

        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        if hvac_mode == HVACMode.OFF:
            self._refuse("set_temperature", "climate_temperature_while_off")
        if hvac_mode is not None:
            await self.async_set_hvac_mode(hvac_mode)
        self._refuse_unless_on("set_temperature", "climate_temperature_while_off")

        set_temperature = max(0, min(50, set_temperature))

        _LOGGER.info("Vimar Climate setting target temperature: %s", str(set_temperature))

        # Already in manual mode: a plain setpoint write, like the web UI.
        if self.is_on and self.preset_mode == PRESET_NONE:
            self.change_state("setpoint", str(set_temperature))
            return

        # In away (absence): plain setpoint write as well — it targets the
        # absence setpoint and the thermostat STAYS in absence mode (see
        # docstring). The post-write refresh reads back what the firmware
        # actually applied, so HA converges to the device truth regardless.
        if self.is_on and self.preset_mode == PRESET_AWAY:
            self.change_state("setpoint", str(set_temperature))
            self._schedule_post_write_refresh()
            return

        # On in another preset (auto/eco/protection): manual override, setpoint last.
        set_function_mode = self.get_const_value(VIMAR_CLIMATE_MANUAL)
        extra = ("unita", self.get_state("unita")) if self.has_state("unita") else ()
        _LOGGER.info("Vimar Climate activating manual mode: %s", set_function_mode)
        self.change_state(
            "funzionamento",
            set_function_mode,
            *extra,
            "setpoint",
            str(set_temperature),
        )
        self._schedule_post_write_refresh()

    async def async_turn_on(self) -> None:
        """Switch on in the current heat/cool direction.

        Home Assistant's default turn_on picks the first of HEAT_COOL/HEAT/COOL
        in hvac_modes, i.e. HEAT, which would flip a cooling zone to heating.
        """
        direction = self._direction
        if direction is None:
            self._refuse("turn_on", "climate_mode_unknown")
        if self.is_on:
            return  # already on, in the direction it reports
        await self.async_set_hvac_mode(direction)

    # helper

    def _valid_raw(self, key: str, known) -> str | None:
        """Return the raw value of `key` if it is a known code, else None.

        An unrecognised value (missing, empty, or a code this integration does
        not know) is logged once per entity with its raw repr.
        """
        value = self.get_state(key) if self.has_state(key) else None
        if value is not None and value in known:
            return value
        if key not in self._unrecognised_logged:
            self._unrecognised_logged = self._unrecognised_logged | {key}
            _LOGGER.warning(
                "Vimar Climate %s: unrecognised %s=%r, state reported as unknown",
                self.entity_id,
                key,
                value,
            )
        return None

    @property
    def _operating_mode(self) -> str | None:
        """Raw 'funzionamento' if it is a valid reading, else None."""
        return self._valid_raw("funzionamento", KNOWN_FUNZIONAMENTO[self.climate_type])

    @property
    def _direction(self) -> HVACMode | None:
        """Heat/cool direction from a valid reading, else None."""
        key = "stagione" if self.climate_type == "heat_cool" else "regolazione"
        directions = DIRECTION_MODES[self.climate_type]
        raw = self._valid_raw(key, directions)
        return None if raw is None else directions[raw]

    def _refuse_unless_on(self, method: str, off_reason: str) -> None:
        """Refuse a setpoint/preset call unless the thermostat is validly on."""
        hvac_mode = self.hvac_mode
        if hvac_mode is None:
            self._refuse(method, "climate_mode_unknown")
        if hvac_mode == HVACMode.OFF:
            self._refuse(method, off_reason)

    def _refuse(self, method: str, reason: str) -> NoReturn:
        """Refuse a service call before anything is queued or published.

        Raised ahead of change_state(): no SETVALUE is queued and the state in
        Home Assistant is left as it was. Logged as a warning because nothing
        reaches the thermostat, which a default `logger: warning` must show.
        """
        _LOGGER.warning("Vimar Climate %s: %s refused (%s)", self.entity_id, method, reason)
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=reason,
            translation_placeholders={"entity_id": str(self.entity_id)},
        )

    def _schedule_post_write_refresh(self):
        """Ask the coordinator to GETVALUE this thermostat after a mode write.

        The webserver DB does not follow the physical device: when a mode
        change makes the device switch to another stored setpoint (absence,
        reduction, manual), the DB - and HA - keep the old value until a
        GETVALUE is issued (the native UI popup does it as "device
        synchronization"). Refresh setpoint+funzionamento after the
        write-guard window so the next poll shows the real values.
        """
        if self._device is MISSING_DEVICE:
            return
        ids = [
            status.get("status_id")
            for name, status in self._device.get("status", {}).items()
            if name in CLIMATE_REFRESH_STATUS_NAMES
        ]
        self.coordinator.schedule_status_refresh(ids, CLIMATE_POST_WRITE_REFRESH_DELAY)

    @property
    def _has_fancoil(self):
        """Return True only if the device has a physical fan coil unit.

        CH_HVAC_NoZonaNeutra and similar thermostats expose 'modalita_fancoil'
        and 'velocita_fancoil' status keys with value '0' even though no
        physical fan coil is present. The only reliable discriminant is
        object_type matching against FANCOIL_OBJECT_TYPES.
        """
        if self._device is MISSING_DEVICE:
            return False
        return self._device.get("object_type", "") in FANCOIL_OBJECT_TYPES

    @property
    def climate_type(self):
        """Return type of climate control: 'heat_cool' or 'heat_cool_fancoil'.

        Determines which HVAC constant set and state keys to use:
        - 'heat_cool' (Type I): uses 'stagione', funzionamento 0=off/6=manual/8=auto
        - 'heat_cool_fancoil' (Type II): uses 'regolazione', funzionamento 0=auto/1=manual/6=off

        NOTE: this property is intentionally decoupled from fan coil presence.
        A Type II thermostat (e.g. CH_HVAC_NoZonaNeutra) uses 'regolazione'
        but has no physical fan coil. Fan UI is controlled by _has_fancoil.
        """
        if self.has_state("regolazione"):
            return "heat_cool_fancoil"
        return "heat_cool"

    def get_const_value(self, const):
        """Return ids depending on the climate type."""
        # thermostat I (funzionamento)
        # 0 .. off  VIMAR_CLIMATE_OFF_I
        # 6 .. manual  VIMAR_CLIMATE_MANUAL_I
        # 8 .. auto  VIMAR_CLIMATE_AUTO_I

        # thermostat II (funzionamento)
        # 0 .. automatic  VIMAR_CLIMATE_AUTO_II
        # 1 .. manual  VIMAR_CLIMATE_MANUAL_II
        # 6 .. off  VIMAR_CLIMATE_OFF_II

        if self.climate_type == "heat_cool":
            if const == VIMAR_CLIMATE_OFF:
                return VIMAR_CLIMATE_OFF_I
            elif const == VIMAR_CLIMATE_MANUAL:
                return VIMAR_CLIMATE_MANUAL_I
            elif const == VIMAR_CLIMATE_AUTO:
                return VIMAR_CLIMATE_AUTO_I
            elif const == VIMAR_CLIMATE_COOL:
                return VIMAR_CLIMATE_COOL_I
            elif const == VIMAR_CLIMATE_HEAT:
                return VIMAR_CLIMATE_HEAT_I
            elif const == VIMAR_CLIMATE_RIDUZIONE:
                return VIMAR_CLIMATE_RIDUZIONE_I
            elif const == VIMAR_CLIMATE_PROTEZIONE or const == VIMAR_CLIMATE_ASSENZA:
                return VIMAR_CLIMATE_PROTEZIONE_I
            return None
        else:
            if const == VIMAR_CLIMATE_OFF:
                return VIMAR_CLIMATE_OFF_II
            elif const == VIMAR_CLIMATE_MANUAL:
                return VIMAR_CLIMATE_MANUAL_II
            elif const == VIMAR_CLIMATE_AUTO:
                return VIMAR_CLIMATE_AUTO_II
            elif const == VIMAR_CLIMATE_COOL:
                return VIMAR_CLIMATE_COOL_II
            elif const == VIMAR_CLIMATE_HEAT:
                return VIMAR_CLIMATE_HEAT_II
            elif const == VIMAR_CLIMATE_RIDUZIONE:
                return VIMAR_CLIMATE_RIDUZIONE_II
            elif const == VIMAR_CLIMATE_PROTEZIONE:
                return VIMAR_CLIMATE_PROTEZIONE_II
            elif const == VIMAR_CLIMATE_ASSENZA:
                return VIMAR_CLIMATE_ASSENZA_II
            return None


# end class VimarClimate
