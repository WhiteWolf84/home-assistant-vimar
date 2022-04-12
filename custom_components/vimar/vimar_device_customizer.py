import logging
import sys
import re

from .const import DOMAIN
from .const import (
    DEVICE_TYPE_CLIMATES,
    DEVICE_TYPE_FANS,
    DEVICE_TYPE_COVERS,
    DEVICE_TYPE_LIGHTS,
    DEVICE_TYPE_MEDIA_PLAYERS,
    DEVICE_TYPE_OTHERS,
    DEVICE_TYPE_SCENES,
    DEVICE_TYPE_SENSORS,
    DEVICE_TYPE_SWITCHES,
)

DEVICE_OVERRIDE_FILTER = "filter"
DEVICE_OVERRIDE_FILTER_RE = "filter_re"
DEVICE_OVERRIDE_ACTIONS = "actions"
DEVICE_OVERRIDE_ACTION_FRIENDLY_NAME_AS_VIMAR = "friendly_name_as_vimar"
DEVICE_OVERRIDE_ACTION_FRIENDLY_NAME_ROOM_NAME_AT_BEGIN = "friendly_name_room_name_at_begin"
DEVICE_OVERRIDE_ACTION_REPLACE_RE = "replace_re"
DEVICE_OVERRIDE_ACTION_REPLACE_RE_FIELD = "field"
DEVICE_OVERRIDE_ACTION_REPLACE_RE_PATTERN = "pattern"
DEVICE_OVERRIDE_ACTION_REPLACE_RE_REPL = "repl"

_LOGGER = logging.getLogger(__name__)
_LOGGER_isDebug = _LOGGER.isEnabledFor(logging.DEBUG)

class VimarDeviceCustomizer:

    _device_overrides = []

    def __init__(self, device_overrides):
        """Create new container to hold all states."""
        self._device_overrides = device_overrides
        if (device_overrides is None):
            self._device_overrides = []
        self.init_overrides()


    def init_overrides(self):
        for device_override in self._device_overrides:
            try:
                self.device_override_check(device_override)
            except BaseException as err:
                _LOGGER.error("Error occurred parsing device_override. %s - device_override: %s", str(err), str(device_override))
                raise

    def customize_device(self, device):
        deviceold = None
        for device_override in self._device_overrides:
            try:
                match = self.device_override_match(device, device_override)
                if not match:
                    continue
                if (deviceold is None and _LOGGER_isDebug):
                    deviceold = {}
                    for key, value in device.items():
                        deviceold[key] = self.device_override_get_attr_str(device, key)
                actions = device_override[DEVICE_OVERRIDE_ACTIONS]
                if isinstance(actions, list):
                    for item in actions:
                        for key, value in item.items():
                            self.device_override_action(device, key, value, deviceold)
                else:
                    for key, value in actions.items():
                        self.device_override_action(device, key, value, deviceold)
            except BaseException as err:
                _LOGGER.error("Error occurred for device_override. %s - device_override: %s", str(err), str(device_override))
                raise

        if (deviceold is not None):
            fields_edit = []
            for key in device:
                old_value = str(None)
                if (key in deviceold):
                    old_value = str(deviceold[key])
                new_value = str(self.device_override_get_attr_str(device, key))
                if old_value != new_value:
                    fields_edit.append(str(key) + ": '" + str(old_value) + "' -> '" + str(new_value) + "'")
            if len(fields_edit) > 0:
                _LOGGER.debug(
                    "Overriding attributes per object_name: '" + deviceold["object_name"] + "': " + " - ".join(fields_edit) + "."
                )

    def match_name(self, name, search, searchRegex):
        match = False
        if (search is not None):
           match = search == "*" or name.upper() == search.upper()

        #gestione filtro con regex, come su https://gist.github.com/elbarsal/65f413b60d1c4976a8351fba4b4d94d5 (whitelist_re)
        try:
            if (searchRegex is not None):
              name_match = re.search(searchRegex, name, re.IGNORECASE) is not None
              if (name_match):
                #_LOGGER.debug("Whitelist regex matches entity or domain: %s", state.entity_id)
                match = True
        except BaseException as err:
            _LOGGER.error("Error occurred in match_name. name: '" + name + "', searchRegex: '" + searchRegex + "' - %s", str(err))

        return match

    def replace_name(self, name, pattern, repl):
        try:
            if (pattern is not None and repl is not None ):
               name = re.sub(pattern, repl, name, flags=re.I)
        except BaseException as err:
            _LOGGER.error("Error occurred in replace_name. name: '" + name + "', pattern: '" + pattern + "', repl: '" + repl + "' - %s", str(err))
        return name

    def device_override_check(self, device_override):
        if (device_override.get(DEVICE_OVERRIDE_ACTIONS) is None):
            device_override[DEVICE_OVERRIDE_ACTIONS] = []
        actions = device_override[DEVICE_OVERRIDE_ACTIONS]
        if isinstance(actions, list) is False:
            actions = [actions]
            device_override[DEVICE_OVERRIDE_ACTIONS] = actions

        #extend shorner version to list detailed version
        for key, value in device_override.copy().items():
            if (key == DEVICE_OVERRIDE_ACTIONS or key == DEVICE_OVERRIDE_FILTER or key == DEVICE_OVERRIDE_FILTER_RE):
                continue
            if (str(key).startswith('filter_')):
                field = str(key)[7:100]
                filters_key = DEVICE_OVERRIDE_FILTER
                if field.endswith("_regex"):
                    field = field[:-len('_regex')]
                    filters_key = DEVICE_OVERRIDE_FILTER_RE
                elif field.endswith("_re"):
                    field = field[:-len('_re')]
                    filters_key = DEVICE_OVERRIDE_FILTER_RE
                if (device_override.get(filters_key) is None):
                    device_override[filters_key] = {}
                device_override[filters_key][field] = value
            if (str(key).endswith('_regexsub_repl')):
                continue
            if (str(key).endswith('_regexsub_pattern')):
                field = key[:-len('_regexsub_pattern')]
                repl = {
                    DEVICE_OVERRIDE_ACTION_REPLACE_RE_FIELD : field,
                    DEVICE_OVERRIDE_ACTION_REPLACE_RE_PATTERN : value,
                    DEVICE_OVERRIDE_ACTION_REPLACE_RE_REPL : device_override.get(field + "_regexsub_repl")
                }
                actions.append({DEVICE_OVERRIDE_ACTION_REPLACE_RE: repl})
            elif key == "object_name_as_vimar" or key == "friendly_name_as_vimar":
                actions.append({DEVICE_OVERRIDE_ACTION_FRIENDLY_NAME_AS_VIMAR: True})
            elif key == "friendly_name_room_name_at_begin":
                actions.append({DEVICE_OVERRIDE_ACTION_FRIENDLY_NAME_ROOM_NAME_AT_BEGIN: True})
            else:
                actions.append({key: value})



    def device_override_get_attr_key(self, key):
        if (key == 'type' or key == 'class' or key == 'friendly_name'):
            return "device_" + key

        if key == 'vimar_object_type':
            return 'object_type'
        if key == 'vimar_object_name' or key == "vimar_name":
            return 'object_name'
        if key == 'vimar_object_id':
            return 'object_id'
        if key == 'vimar_room_name':
            return 'room_name'
        if key == 'vimar_room_names':
            return 'room_names'
        return key

    def device_override_get_attr_str(self, device, key):
        attr_name = self.device_override_get_attr_key(key)
        attr_str = device.get(attr_name)
        if attr_str is None:
            attr_str = ""
        elif isinstance(attr_str, list):
            attr_str = ",".join(attr_str)
        return attr_str

    def device_override_match(self, device, device_override):
        matchcnt = 0
        filters = device_override.get(DEVICE_OVERRIDE_FILTER)
        if isinstance(filters, str) and filters == "*":
            matchcnt += 1
        elif filters is not None:
            for key, value in filters.items():
                attr_str = self.device_override_get_attr_str(device, key)
                if self.match_name(attr_str, value, None) is False:
                    return False
                matchcnt += 1
        filters = device_override.get(DEVICE_OVERRIDE_FILTER_RE)
        if isinstance(filters, str) and filters == "*":
            matchcnt += 1
        elif filters is not None:
            for key, value in filters.items():
                attr_str = self.device_override_get_attr_str(device, key)
                if self.match_name(attr_str, None, value) is False:
                    return False
                matchcnt += 1
        match = matchcnt > 0
        return match


    def device_override_action(self, device, action, value, device_original):
        field = None
        if (action == DEVICE_OVERRIDE_ACTION_FRIENDLY_NAME_AS_VIMAR):
            device["device_friendly_name"] = device["object_name"].title().strip()
            if (device_original is not None):
                device_original["device_friendly_name"] = device["device_friendly_name"]
        elif (action == DEVICE_OVERRIDE_ACTION_FRIENDLY_NAME_ROOM_NAME_AT_BEGIN):
            room_name = ''
            if "room_name" in device and device["room_name"] is not None and device["room_name"] != '':
                room_name = device["room_name"].title().strip()
            if room_name != '':
                friendly_name = device["device_friendly_name"]
                if friendly_name.upper().endswith(room_name.upper()):
                    friendly_name = friendly_name[:-len(room_name)]
                    friendly_name = (room_name + ' ' + friendly_name).strip()
                if not friendly_name.upper().startswith(room_name.upper()):
                    friendly_name = (room_name + ' ' + friendly_name).strip()
                device["device_friendly_name"] = friendly_name
        elif (action == DEVICE_OVERRIDE_ACTION_REPLACE_RE):
            field = self.device_override_get_attr_key(value.get(DEVICE_OVERRIDE_ACTION_REPLACE_RE_FIELD))
            curr_value = self.device_override_get_attr_str(device, field)
            new_value = self.replace_name(curr_value, value.get(DEVICE_OVERRIDE_ACTION_REPLACE_RE_PATTERN), value.get(DEVICE_OVERRIDE_ACTION_REPLACE_RE_REPL))
            device[field] = new_value
        else: #on default, set specified value in dictionary :)
            field = self.device_override_get_attr_key(action)
            device[field] = value

        if (field == 'icon' and isinstance(device[field], str) and "," in device[field]):
            device[field] = device[field].split(",")

        if (field == 'device_type' and isinstance(device[field], str)):
            device[field] = self.device_type_singolarize(device[field])


    def device_type_singolarize(self, device_type):
        if device_type == 'climates': return  DEVICE_TYPE_CLIMATES
        if device_type == 'fans': return  DEVICE_TYPE_FANS
        if device_type == 'covers': return  DEVICE_TYPE_COVERS
        if device_type == 'lights': return  DEVICE_TYPE_LIGHTS
        if device_type == 'media_players': return  DEVICE_TYPE_MEDIA_PLAYERS
        if device_type == 'others': return  DEVICE_TYPE_OTHERS
        if device_type == 'scenes': return  DEVICE_TYPE_SCENES
        if device_type == 'sensors': return  DEVICE_TYPE_SENSORS
        if device_type == 'switches': return  DEVICE_TYPE_SWITCHES
        return device_type
