"""Config flow for the Vimar Security System component."""

import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult as FlowResult
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TIMEOUT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import slugify

from .const import (
    _LOGGER,
    CONF_AUTOMATION_PIN,
    CONF_CERTIFICATE,
    CONF_COVER_POSITION_MODE,
    CONF_DELETE_AND_RELOAD_ALL_ENTITIES,
    CONF_DEVICES_BINARY_SENSOR_RE,
    CONF_DEVICES_LIGHTS_RE,
    CONF_ENERGY_REFRESH_INTERVAL,
    CONF_FRIENDLY_NAME_ROOM_NAME_AT_BEGIN,
    CONF_GLOBAL_CHANNEL_ID,
    CONF_IGNORE_PLATFORM,
    CONF_OVERRIDE,
    CONF_OVERRIDE_IMPORTED,
    CONF_ROOM_LABELS,
    CONF_SCHEMA,
    CONF_SECURE,
    CONF_TITLE,
    CONF_USE_VIMAR_NAMING,
    CONF_USER_PINS,
    COVER_POSITION_MODES,
    DEFAULT_CERTIFICATE,
    DEFAULT_COVER_POSITION_MODE,
    DEFAULT_ENERGY_REFRESH_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_ROOM_LABELS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SECURE,
    DEFAULT_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
)
from .override_editor import (
    FILTER_FIELDS,
    FORM_DEVICE_CLASS,
    FORM_DEVICE_TYPE,
    FORM_FILTER_FIELD,
    FORM_ICON_OFF,
    FORM_ICON_ON,
    FORM_MATCH_MODE,
    FORM_MATCH_VALUE,
    FORM_USE_VIMAR_NAME,
    MATCH_MODES,
    UNSET,
    describe_rule,
    form_to_rule,
    rule_to_form,
    validate_form,
)
from .vimar_coordinator import VimarDataUpdateCoordinator
from .vimarlink.device_types import DEVICE_TYPE_FANS, DEVICE_TYPE_OTHERS
from .vimarlink.exceptions import VimarConfigError, VimarConnectionError

#: Sentinel for "add a new rule" in the override picker. Not a valid list
#: index, so it can never collide with one.
OVERRIDE_NEW = "new"


@asynccontextmanager
async def validation_coordinator(
    hass: HomeAssistant,
    vimarconfig: dict,
    entry: config_entries.ConfigEntry | None = None,
) -> AsyncGenerator[VimarDataUpdateCoordinator]:
    """Yield a throwaway coordinator and always release its HTTP session.

    Validating credentials logs into the web server, which opens a keep-alive
    HTTPS session (one per executor thread, see VimarConnection.close). These
    coordinators live only for the duration of a flow step and are then
    dropped, and nothing else will ever close them: every submitted form -
    including every failed attempt, every reauth and twice per options save -
    used to leave a socket open against a small embedded web server.
    """
    coordinator = VimarDataUpdateCoordinator(hass, entry=entry, vimarconfig=vimarconfig)
    try:
        yield coordinator
    finally:
        await coordinator.async_close_connection()


class VimarFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Vimar."""

    VERSION = 1

    def __init__(self):
        """Initialize."""
        self.reauth_entry: config_entries.ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get options flow for this handler."""
        return OptionsFlowHandler()

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        schema = get_schema_config_user(user_input)
        errors: dict[str, str] = {}

        if user_input is not None:
            title = (
                user_input.pop(CONF_TITLE, "")
                or user_input.get(CONF_HOST)
                or user_input.get(CONF_USERNAME)
            )
            unique_id = slugify(title)
            try:
                async with validation_coordinator(self.hass, user_input) as coordinator:
                    await coordinator.validate_vimar_credentials()
            except Exception as ex:
                set_errors_from_ex(ex, errors)

            if not errors:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema), errors=errors)

    async def async_step_reauth(self, entry_data: dict) -> FlowResult:
        """Handle re-authentication when credentials become invalid.

        This flow is triggered automatically when:
        - Login fails with invalid credentials
        - Session expires and cannot be renewed
        - Certificate validation fails
        """
        entry_id = self.context.get("entry_id")
        if not entry_id:
            return self.async_abort(reason="reauth_failed")
        self.reauth_entry = self.hass.config_entries.async_get_entry(entry_id)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> FlowResult:
        """Confirm re-authentication and update credentials."""
        errors: dict[str, str] = {}

        if self.reauth_entry is None:
            return self.async_abort(reason="reauth_failed")

        if user_input is not None:
            # Merge existing config with new credentials
            new_config = {**self.reauth_entry.data, **user_input}

            try:
                async with validation_coordinator(self.hass, new_config) as coordinator:
                    await coordinator.validate_vimar_credentials()
            except Exception as ex:
                set_errors_from_ex(ex, errors)

            if not errors:
                self.hass.config_entries.async_update_entry(
                    self.reauth_entry,
                    data=new_config,
                )
                await self.hass.config_entries.async_reload(self.reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        # Show form with only credentials that might have changed
        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=self.reauth_entry.data.get(CONF_USERNAME)): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "host": self.reauth_entry.data.get(CONF_HOST, "unknown"),
            },
        )

    async def async_step_import(self, import_config):
        """Import a config entry from configuration.yaml."""
        _LOGGER.info("VIMAR async_step_import")
        if self._async_current_entries():
            _LOGGER.warning("Only one configuration of Vimar is allowed.")
            return self.async_abort(reason="single_instance_allowed")
        user_input = import_config.copy()
        title = (
            user_input.pop(CONF_TITLE, "")
            or user_input.get(CONF_HOST)
            or user_input.get(CONF_USERNAME)
        )
        # device_override used to be dropped here ("non gestito da config_flow"),
        # which was true and self-fulfilling: the entry could not hold overrides,
        # so YAML had to keep them, so the flow had no reason to import them. The
        # entry owns them now, so they come across with everything else - and the
        # entry is marked as migrated, since this IS the migration for a config
        # imported from YAML.
        user_input[CONF_OVERRIDE] = user_input.get(CONF_OVERRIDE) or []
        user_input[CONF_OVERRIDE_IMPORTED] = True
        schema = user_input.pop(CONF_SCHEMA, "https")
        user_input[CONF_SECURE] = schema == "https"
        if schema == "https" and user_input.get(CONF_CERTIFICATE, "") != "":
            user_input[CONF_VERIFY_SSL] = True
        unique_id = slugify(title)
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=title, data=user_input)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for the Home Assistant remote integration."""

    def __init__(self):
        """Initialize remote_homeassistant options flow."""
        self.options: dict = {}
        self.schema: dict = {}
        self.schema_vol: vol.Schema | None = None
        self.errors: dict[str, str] = {}
        self.user_input: dict = {}
        self.options_with_user_input: dict = {}
        #: Which device override the edit step is working on: its position in
        #: the list, or None for one being added. Order is meaningful - a later
        #: rule overwrites what an earlier one set - so rules are edited in
        #: place and appended, never re-sorted.
        self._override_index: int | None = None

    def _ensure_options_initialized(self):
        """Initialize options from config_entry if not already done."""
        if not self.options:
            self.options.update(self.config_entry.data or {})
            self.options.update(self.config_entry.options or {})

    def _init_schema(self, user_input, schema):
        self.schema = schema
        self.schema_vol = vol.Schema(schema)
        self.errors: dict[str, str] = {}
        self.user_input = user_input
        self.options_with_user_input = self.options.copy()
        if user_input:
            self._dict_update(self.options_with_user_input)

    def _option_changed(self, key):
        original = self.config_entry.data.get(key, self.config_entry.options.get(key, ""))
        new = self.options.get(key, "")
        return original != new

    def _validate_regex(self, key):
        search_regex = self.options_with_user_input.get(key, "")
        if search_regex:
            try:
                re.search(search_regex, "x", re.IGNORECASE)
            except Exception as err:
                _LOGGER.error(
                    "Error occurred in validate_regex. Key: '%s', Regex: '%s' - %s",
                    key,
                    search_regex,
                    str(err),
                )
                self.errors[key] = "regex_not_valid"

    def _options_update(self):
        self._dict_update(self.options)

    def _dict_update(self, options):
        for key in self.schema.keys():  # set all values in form, if not present remove it
            value = self.user_input.get(str(key))
            options[str(key)] = value
            if value is None:  # remove None value, problem to save in json!
                options.pop(str(key))

    def _async_show_form_step(self, step):
        return self.async_show_form(step_id=step, data_schema=self.schema_vol, errors=self.errors)

    def _async_save_options(self):
        self._options_update()
        self.hass.config_entries.async_update_entry(self.config_entry, data=self.options)
        return self.async_create_entry(title="", data={})

    async def async_step_init(self, user_input=None):
        """Manage basic options."""
        self._ensure_options_initialized()
        self._init_schema(user_input, get_schema_options_init(user_input or self.options))
        if user_input is not None:
            try:
                async with validation_coordinator(
                    self.hass, self.options_with_user_input, entry=self.config_entry
                ) as coordinator:
                    await coordinator.validate_vimar_credentials()
            except Exception as ex:
                set_errors_from_ex(ex, self.errors)

        if user_input is not None and not self.errors:
            self._options_update()
            return await self.async_step_two()

        return self._async_show_form_step("init")

    async def async_step_two(self, user_input=None):
        """Manage domain and entity filters."""
        self._init_schema(user_input, get_schema_options_two(user_input or self.options))

        if user_input is not None:
            self._validate_regex(CONF_DEVICES_LIGHTS_RE)
            self._validate_regex(CONF_DEVICES_BINARY_SENSOR_RE)
        if user_input is not None and not self.errors:
            try:
                async with validation_coordinator(
                    self.hass, self.options_with_user_input, entry=self.config_entry
                ) as coordinator:
                    await coordinator.validate_vimar_credentials()
                    await self.hass.async_add_executor_job(coordinator.vimarproject.update, True)
            except Exception as ex:
                set_errors_from_ex(ex, self.errors)

        if user_input is not None and not self.errors:
            self._options_update()
            for key in [
                CONF_USE_VIMAR_NAMING,
                CONF_FRIENDLY_NAME_ROOM_NAME_AT_BEGIN,
                CONF_DEVICES_LIGHTS_RE,
                CONF_DEVICES_BINARY_SENSOR_RE,
            ]:
                if self._option_changed(key):
                    self.options[CONF_DELETE_AND_RELOAD_ALL_ENTITIES] = True
                    break
            return await self.async_step_three()

        return self._async_show_form_step("two")

    async def async_step_three(self, user_input=None):
        """Manage domain and entity filters."""
        self._init_schema(user_input, get_schema_options_three(user_input or self.options))

        if user_input is not None and not self.errors:
            self._options_update()
            return await self.async_step_overrides()

        return self._async_show_form_step("three")

    # ------------------------------------------------------------------
    # Device overrides
    #
    # Two steps rather than one: a form cannot re-render itself with the
    # values of a rule the user has just picked, so choosing and editing have
    # to be separate submissions. The list step loops - edit, delete or add,
    # and you land back on it - until the picker is left empty, which is what
    # moves the flow on.
    #
    # These steps bypass the _init_schema/_options_update machinery the other
    # steps share: that copies every field of the form straight into the saved
    # options, and none of these fields is a setting. What gets saved is the
    # rule list they build, under CONF_OVERRIDE.
    # ------------------------------------------------------------------

    def _overrides(self) -> list[dict]:
        """The rules as they stand in this flow, as a list we may modify."""
        return list(self.options.get(CONF_OVERRIDE) or [])

    async def async_step_overrides(self, user_input=None):
        """Pick a device override to edit, add one, or move on."""
        self._ensure_options_initialized()
        rules = self._overrides()

        if user_input is not None:
            selected = user_input.get("rule")
            if not selected:
                return await self.async_step_pins()
            self._override_index = None if selected == OVERRIDE_NEW else int(selected)
            return await self.async_step_override_edit()

        options = [
            SelectOptionDict(value=str(index), label=f"{index + 1}. {describe_rule(rule)}")
            for index, rule in enumerate(rules)
        ]
        options.append(SelectOptionDict(value=OVERRIDE_NEW, label="+ …"))

        return self.async_show_form(
            step_id="overrides",
            data_schema=vol.Schema(
                {vol.Optional("rule"): SelectSelector(SelectSelectorConfig(options=options))}
            ),
            description_placeholders={
                "count": str(len(rules)),
                "rules": "\n".join(option["label"] for option in options[:-1]) or "—",
            },
        )

    async def async_step_override_edit(self, user_input=None):
        """Add, change or delete one device override."""
        rules = self._overrides()

        # Which rule is being edited, if any. An index that no longer resolves
        # is treated as "adding", not as an error: that is what a stale flow
        # looks like after the list changed underneath it, and appending a rule
        # is recoverable where overwriting an unrelated one is not.
        index = self._override_index
        if index is None or index >= len(rules):
            index = None
            original = None
        else:
            original = rules[index]

        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("delete"):
                if index is not None:
                    rules.pop(index)
                    self.options[CONF_OVERRIDE] = rules
                return await self.async_step_overrides()

            errors = validate_form(user_input)
            if not errors:
                rule = form_to_rule(user_input, original)
                if index is None:
                    rules.append(rule)
                else:
                    rules[index] = rule
                self.options[CONF_OVERRIDE] = rules
                return await self.async_step_overrides()

        # Re-showing after an error must keep what the user typed, not reset to
        # the stored rule; on first entry there is no input and the stored rule
        # is exactly what should appear.
        values = user_input if user_input is not None else rule_to_form(original)

        # A rule written by hand can filter on a field this form has no entry
        # for. Offering it alongside the usual ones keeps that rule editable
        # instead of silently rewriting what it matches on.
        filter_fields = list(FILTER_FIELDS)
        current_field = values.get(FORM_FILTER_FIELD)
        if current_field and current_field not in filter_fields:
            filter_fields.append(current_field)

        schema = vol.Schema(
            {
                vol.Required(FORM_FILTER_FIELD, default=values.get(FORM_FILTER_FIELD)): vol.In(
                    filter_fields
                ),
                vol.Required(FORM_MATCH_MODE, default=values.get(FORM_MATCH_MODE)): vol.In(
                    MATCH_MODES
                ),
                vol.Optional(
                    FORM_MATCH_VALUE,
                    description={"suggested_value": values.get(FORM_MATCH_VALUE)},
                ): str,
                vol.Optional(
                    FORM_DEVICE_TYPE, default=values.get(FORM_DEVICE_TYPE) or UNSET
                ): vol.In([UNSET, *PLATFORMS, DEVICE_TYPE_FANS, DEVICE_TYPE_OTHERS]),
                vol.Optional(
                    FORM_DEVICE_CLASS,
                    description={"suggested_value": values.get(FORM_DEVICE_CLASS)},
                ): str,
                vol.Optional(
                    FORM_ICON_ON,
                    description={"suggested_value": values.get(FORM_ICON_ON)},
                ): str,
                vol.Optional(
                    FORM_ICON_OFF,
                    description={"suggested_value": values.get(FORM_ICON_OFF)},
                ): str,
                vol.Optional(
                    FORM_USE_VIMAR_NAME, default=bool(values.get(FORM_USE_VIMAR_NAME))
                ): bool,
                vol.Optional("delete", default=False): bool,
            }
        )

        return self.async_show_form(
            step_id="override_edit",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "position": "—" if index is None else str(index + 1),
                "current": describe_rule(original) if original else "—",
            },
        )

    async def async_step_pins(self, user_input=None):
        """Associate an HA user with their SAI2 alarm PIN.

        The mapping {user_id: pin} lets a logged-in HA user arm/disarm with
        their own PIN without typing it. PINs are stored in plain text (same
        protection as the VIMAR admin password). Submit empty to skip.
        """
        self._ensure_options_initialized()
        user_pins: dict = dict(self.options.get(CONF_USER_PINS, {}))
        automation_pin: str = self.options.get(CONF_AUTOMATION_PIN, "")

        # Build the selectable HA users (active, human accounts).
        ha_users = await self.hass.auth.async_get_users()
        selectable = [u for u in ha_users if u.is_active and not u.system_generated and u.name]
        user_options = [
            SelectOptionDict(
                value=u.id,
                label=(u.name or "") + (" — PIN impostato" if u.id in user_pins else ""),
            )
            for u in selectable
        ]

        if user_input is not None:
            selected = user_input.get("user")
            pin = (user_input.get("pin") or "").strip()
            if selected:
                if user_input.get("remove"):
                    user_pins.pop(selected, None)
                elif pin:
                    user_pins[selected] = pin
            if user_pins:
                self.options[CONF_USER_PINS] = user_pins
            else:
                self.options.pop(CONF_USER_PINS, None)

            auto_pin = (user_input.get(CONF_AUTOMATION_PIN) or "").strip()
            if auto_pin:
                self.options[CONF_AUTOMATION_PIN] = auto_pin
            else:
                self.options.pop(CONF_AUTOMATION_PIN, None)
            return self._async_save_options()

        schema = vol.Schema(
            {
                vol.Optional("user"): SelectSelector(SelectSelectorConfig(options=user_options)),
                vol.Optional("pin"): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional("remove", default=False): bool,
                vol.Optional(
                    CONF_AUTOMATION_PIN,
                    description={"suggested_value": automation_pin},
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            }
        )
        return self.async_show_form(
            step_id="pins",
            data_schema=schema,
            description_placeholders={
                "count": str(len(user_pins)),
                "users": ", ".join(u.name or "" for u in selectable if u.id in user_pins) or "—",
            },
        )


def set_errors_from_ex(ex: Exception, errors: dict[str, str]):
    """Map an exception raised during validation to a form error key.

    Classify by exception TYPE first: the VIMAR server rejecting the login
    (wrong user/password) raises VimarConfigError, a network/parse failure
    raises VimarConnectionError. This is robust against wording changes in the
    server-returned messages, unlike the previous pure string matching.

    Two cases the types can't yet distinguish are handled by message first:
    - SSL/cert errors surface as a VimarConnectionError whose message mentions
      "SSLError", so they must be caught before the generic connection mapping.
    - A failed certificate save is a plain VimarApiError.

    String matching is kept only as a fallback for untyped/legacy exceptions.
    """
    exstr = str(ex)

    # Message-first cases (the type hierarchy can't disambiguate these yet).
    if "SSLError" in exstr:
        errors["base"] = "invalid_cert"
        return
    if "Saving certificate failed" in exstr:
        errors["base"] = "save_cert_failed"
        return

    # Type-based classification. VimarConfigError and VimarConnectionError are
    # both subclasses of VimarApiError, so check the specific types first.
    if isinstance(ex, VimarConfigError):
        errors["base"] = "invalid_auth"
        return
    if isinstance(ex, VimarConnectionError):
        errors["base"] = "cannot_connect"
        return

    # Fallback string matching for untyped/legacy exceptions.
    if "Log In Fallito" in exstr:  # message returned from vimar
        errors["base"] = "invalid_auth"
    elif (
        "HTTP error occurred" in exstr
        or "Client Error:" in exstr
        or "ConnectTimeoutError" in exstr
        or "NewConnectionError" in exstr
        or "HTTP timeout occurred" in exstr
    ):
        errors["base"] = "cannot_connect"
    else:
        _LOGGER.error("Unexpected error during validation: %s", exstr)
        errors["base"] = "unknown"


def get_vol_default(config: dict | None, key, default=None):
    """Return the stored value for key, else the default, else vol.UNDEFINED.

    A stored falsy value (0, False, "") is an intentional choice and must
    round-trip back into the form. The previous `config.get(key) or UNDEFINED`
    dropped every falsy value, so e.g. an energy refresh interval of 0 or a
    Secure=False toggle silently lost its pre-fill when the form was reopened.
    Only a genuinely missing key (None) falls through to the default.
    """
    if config is not None:
        value = config.get(key)
        if value is not None:
            return value
    return default if default is not None else vol.UNDEFINED


def get_vol_descr(config: dict | None, key, default=None):
    def_value = get_vol_default(config, key, default)
    if def_value is vol.UNDEFINED:
        return {}
    return {"suggested_value": def_value}


def get_schema_config_user(config: dict | None = None) -> dict:
    """Return the schema for the initial connection/credentials step."""
    config = config if config and CONF_HOST in config else None
    schema = {
        vol.Required(CONF_TITLE, description=get_vol_descr(config, CONF_TITLE)): str,
        vol.Required(CONF_HOST, description=get_vol_descr(config, CONF_HOST)): str,
        vol.Required(
            CONF_PORT, description=get_vol_descr(config, CONF_PORT, DEFAULT_PORT)
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        vol.Required(
            CONF_SECURE, description=get_vol_descr(config, CONF_SECURE, DEFAULT_SECURE)
        ): bool,
        vol.Required(
            CONF_VERIFY_SSL, description=get_vol_descr(config, CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        ): bool,
        vol.Required(CONF_USERNAME, description=get_vol_descr(config, CONF_USERNAME)): str,
        vol.Required(CONF_PASSWORD, description=get_vol_descr(config, CONF_PASSWORD)): str,
        vol.Optional(
            CONF_CERTIFICATE,
            description=get_vol_descr(config, CONF_CERTIFICATE, DEFAULT_CERTIFICATE),
        ): str,
    }
    return schema


def get_schema_options_init(config: dict | None = None) -> dict:
    """Return the options schema for the connection/credentials step."""
    schema = get_schema_config_user(config=config)
    schema.pop(CONF_TITLE, "")
    return schema


def get_schema_options_two(config: dict | None = None) -> dict:
    """Return the options schema for timing, platform and naming settings."""
    # Treat an incomplete config (no connection settings yet) as absent so the
    # fields fall back to their defaults. A missing CONF_SCAN_INTERVAL is then
    # handled by the default passed to get_vol_descr below — no need to mutate
    # the caller's dict (which could be the live self.options) to inject it.
    config = config if config and CONF_TIMEOUT in config else None
    domains = sorted(PLATFORMS)
    schema = {
        vol.Required(
            CONF_TIMEOUT, description=get_vol_descr(config, CONF_TIMEOUT, DEFAULT_TIMEOUT)
        ): vol.All(vol.Coerce(int), vol.Range(min=2, max=60)),
        vol.Required(
            CONF_SCAN_INTERVAL,
            description=get_vol_descr(config, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
        vol.Required(
            CONF_ENERGY_REFRESH_INTERVAL,
            description=get_vol_descr(
                config, CONF_ENERGY_REFRESH_INTERVAL, DEFAULT_ENERGY_REFRESH_INTERVAL
            ),
        ): NumberSelector(
            # Seconds between energy-meter GETVALUE refreshes. 0 disables the
            # refresh (meters then freeze on a stale value), so the floor is 0
            # but never negative. BOX mode keeps a plain numeric input; the
            # range guard prevents the silent foot-gun of a junk/negative value.
            NumberSelectorConfig(
                min=0,
                max=3600,
                step=1,
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_GLOBAL_CHANNEL_ID, description=get_vol_descr(config, CONF_GLOBAL_CHANNEL_ID)
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=99999)),
        vol.Optional(
            CONF_IGNORE_PLATFORM, description=get_vol_descr(config, CONF_IGNORE_PLATFORM)
        ): cv.multi_select(domains),
        vol.Optional(
            CONF_COVER_POSITION_MODE,
            description=get_vol_descr(
                config, CONF_COVER_POSITION_MODE, DEFAULT_COVER_POSITION_MODE
            ),
        ): vol.In(COVER_POSITION_MODES),
        vol.Optional(
            CONF_USE_VIMAR_NAMING, description=get_vol_descr(config, CONF_USE_VIMAR_NAMING)
        ): bool,
        vol.Optional(
            CONF_FRIENDLY_NAME_ROOM_NAME_AT_BEGIN,
            description=get_vol_descr(config, CONF_FRIENDLY_NAME_ROOM_NAME_AT_BEGIN),
        ): bool,
        # Deliberately NOT in the list that forces a delete-and-reload of every
        # entity (see async_step_two): labels are applied to devices that
        # already exist, so turning this on or off needs no re-creation. Off
        # leaves any label already applied in place - removing labels the user
        # may since have taken ownership of would be worse than leaving them.
        vol.Optional(
            CONF_ROOM_LABELS,
            description=get_vol_descr(config, CONF_ROOM_LABELS, DEFAULT_ROOM_LABELS),
        ): bool,
        vol.Optional(
            CONF_DEVICES_LIGHTS_RE, description=get_vol_descr(config, CONF_DEVICES_LIGHTS_RE)
        ): str,
        vol.Optional(
            CONF_DEVICES_BINARY_SENSOR_RE,
            description=get_vol_descr(config, CONF_DEVICES_BINARY_SENSOR_RE),
        ): str,
    }
    return schema


def get_schema_options_three(config: dict | None = None) -> dict:
    """Return the options schema for the delete-and-reload-entities toggle."""
    schema = {
        vol.Optional(
            CONF_DELETE_AND_RELOAD_ALL_ENTITIES,
            description=get_vol_descr(config, CONF_DELETE_AND_RELOAD_ALL_ENTITIES),
        ): bool,
    }
    return schema
