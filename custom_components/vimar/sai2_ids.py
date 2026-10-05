"""Stable unique IDs for the SAI2 alarm entities, and the migration to them.

The SAI2 entities used to be keyed on the web server's DPADD_OBJECT IDs
(`vimar_sai2_<gid>`, `vimar_sai2_zone_<zid>`). Those IDs are not stable:
reprogramming the control unit recreates every SAI2 row under new IDs (on
2026-10-05 "Reparto Giorno" went from 7560 to 11919). Each time, Home Assistant
saw a brand-new entity - created with a "_2" entity_id - while the old one was
removed by async_remove_old_devices, taking its name, area, labels and
device_class override with it.

The new IDs use the area/zone NUMBER on the control unit (the object's MSP,
see get_sai2_groups_query), namespaced by the config entry like every other
entity of the integration:

    vimar_<entry>_sai2_area_<n>     vimar_<entry>_sai2_zone_<n>

Where that number is missing or not unique, the object keeps its legacy ID:
an ID that may change is better than two entities fighting over one.
"""

from __future__ import annotations

import copy
import logging
import re
from collections import Counter
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ALARM_DOMAIN = "alarm_control_panel"
BINARY_SENSOR_DOMAIN = "binary_sensor"

_LEGACY_AREA_RE = re.compile(r"^vimar_sai2_(\d+)$")
_LEGACY_ZONE_RE = re.compile(r"^vimar_sai2_zone_(\d+)$")


def legacy_area_unique_id(group_id: str) -> str:
    """Return the pre-2026.10 unique_id of an area (web server object ID)."""
    return f"vimar_sai2_{group_id}"


def legacy_zone_unique_id(zone_id: str) -> str:
    """Return the pre-2026.10 unique_id of a zone (web server object ID)."""
    return f"vimar_sai2_zone_{zone_id}"


def _stable_unique_ids(
    prefix: str,
    kind: str,
    items: dict[str, dict[str, Any]] | None,
    legacy: Callable[[str], str],
) -> dict[str, str]:
    """Map each object ID to its unique_id: by number, or legacy as a fallback."""
    if not items:
        return {}
    namespace = f"{DOMAIN}_{prefix}_" if prefix else f"{DOMAIN}_"
    counts = Counter(item.get("index") for item in items.values())
    unique_ids: dict[str, str] = {}
    for object_id, item in items.items():
        index = item.get("index")
        if index is not None and counts[index] == 1:
            unique_ids[object_id] = f"{namespace}sai2_{kind}_{index}"
        else:
            _LOGGER.warning(
                "SAI2 %s %s (%s) has no unique number on the control unit (%s): "
                "keeping its web server ID as unique_id, which changes if the "
                "control unit is reprogrammed",
                kind,
                object_id,
                item.get("name", "?"),
                index,
            )
            unique_ids[object_id] = legacy(object_id)
    return unique_ids


def sai2_area_unique_ids(prefix: str, groups: dict[str, dict[str, Any]] | None) -> dict[str, str]:
    """Return {group_id: unique_id} for the SAI2 areas."""
    return _stable_unique_ids(prefix, "area", groups, legacy_area_unique_id)


def sai2_zone_unique_ids(prefix: str, zones: dict[str, dict[str, Any]] | None) -> dict[str, str]:
    """Return {zone_id: unique_id} for the SAI2 zones."""
    return _stable_unique_ids(prefix, "zone", zones, legacy_zone_unique_id)


def sai2_zone_area_name(project: Any, zone_id: str) -> str | None:
    """Return the name of the area a zone belongs to, if known."""
    group_id = (project.sai2_zone_to_group or {}).get(zone_id)
    group = (project.sai2_groups or {}).get(group_id) if group_id else None
    return group.get("name") if group else None


def sai2_zone_entity_name(zone_name: str, area_name: str | None) -> str:
    """Return the entity name of a zone: prefixed with its area when known."""
    return f"{area_name} - {zone_name}" if area_name else zone_name


@callback
def async_migrate_sai2_unique_ids(
    hass: HomeAssistant, entry: ConfigEntry, prefix: str, project: Any
) -> None:
    """Move the SAI2 registry entries to the stable unique_ids.

    Runs before the platforms are set up, every start; it only touches entries
    still on a legacy ID, so once migrated it is a no-op.
    """
    if project is None:
        return
    groups = project.sai2_groups or {}
    zones = project.sai2_zones or {}
    zone_names = {
        zid: sai2_zone_entity_name(
            zone.get("name", f"Zone {zid}"), sai2_zone_area_name(project, zid)
        )
        for zid, zone in zones.items()
    }
    registry = er.async_get(hass)
    _migrate_domain(
        registry,
        entry.entry_id,
        ALARM_DOMAIN,
        _LEGACY_AREA_RE,
        sai2_area_unique_ids(prefix, groups),
        {gid: group.get("name") for gid, group in groups.items()},
    )
    _migrate_domain(
        registry,
        entry.entry_id,
        BINARY_SENSOR_DOMAIN,
        _LEGACY_ZONE_RE,
        sai2_zone_unique_ids(prefix, zones),
        zone_names,
    )


def _migrate_domain(
    registry: er.EntityRegistry,
    entry_id: str,
    domain: str,
    legacy_re: re.Pattern[str],
    new_ids: dict[str, str],
    names: dict[str, str | None],
) -> None:
    """Migrate one entity domain; see async_migrate_sai2_unique_ids."""
    if not new_ids:
        # Nothing discovered (no SAI2, or the query failed): nothing to map to.
        return

    # A name identifies an object only when no other object carries it.
    name_counts = Counter(names.values())
    by_name = {
        name: new_ids[object_id]
        for object_id, name in names.items()
        if name and name_counts[name] == 1 and object_id in new_ids
    }

    def _ours(entity: er.RegistryEntry) -> bool:
        return entity.domain == domain and entity.platform == DOMAIN

    # 1. Live entries on a legacy ID. Matched by object ID when it still
    #    exists, otherwise by name: an entry whose object was renumbered while
    #    Home Assistant was running is still live at the next start.
    candidates: dict[str, list[er.RegistryEntry]] = {}
    for entity in er.async_entries_for_config_entry(registry, entry_id):
        if not _ours(entity) or not (match := legacy_re.match(entity.unique_id)):
            continue
        target = new_ids.get(match.group(1)) or by_name.get(entity.original_name or "")
        if target is not None and target != entity.unique_id:
            candidates.setdefault(target, []).append(entity)

    for target, entities in candidates.items():
        if registry.async_get_entity_id(domain, DOMAIN, target) is not None:
            continue  # already taken by a migrated entry
        # Several entries for one area/zone: the oldest is the one the user
        # has been customising; the newer duplicates are cleaned up afterwards
        # by async_remove_old_devices like any entity no longer provided.
        winner = min(entities, key=lambda entity: entity.created_at)
        registry.async_update_entity(winner.entity_id, new_unique_id=target)
        _LOGGER.info("SAI2: %s unique_id %s -> %s", winner.entity_id, winner.unique_id, target)

    # 2. Entries already lost to a renumbering. async_remove_old_devices
    #    deleted them, but Home Assistant keeps a deleted entry of a config
    #    entry with its settings. The entity that replaced it was created
    #    while the old one still held the entity_id, so it carries the same
    #    entity_id plus Home Assistant's collision suffix ("_2"): give it the
    #    original entity_id back and the settings it has not set itself.
    current_ids = set(new_ids.values())
    live = [
        entity
        for entity in er.async_entries_for_config_entry(registry, entry_id)
        if _ours(entity) and entity.unique_id in current_ids
    ]
    for deleted in list(registry.deleted_entities.values()):
        if (
            deleted.config_entry_id != entry_id
            or deleted.platform != DOMAIN
            or deleted.domain != domain
            or not (match := legacy_re.match(deleted.unique_id))
            or match.group(1) in new_ids
        ):
            continue
        suffixed = re.compile(re.escape(deleted.entity_id) + r"_\d+")
        replacements = [entity for entity in live if suffixed.fullmatch(entity.entity_id)]
        if len(replacements) != 1 or registry.async_get(deleted.entity_id) is not None:
            continue
        _async_restore_deleted(registry, replacements[0], deleted)


def _async_restore_deleted(
    registry: er.EntityRegistry,
    entity: er.RegistryEntry,
    deleted: er.DeletedRegistryEntry,
) -> None:
    """Give `entity` the entity_id and the user settings of `deleted`.

    Only settings the live entity has not set itself are copied, so nothing
    the user changed after the renumbering is overwritten.
    """
    updates: dict[str, Any] = {"new_entity_id": deleted.entity_id}
    for field in ("name", "icon", "device_class", "area_id"):
        if getattr(entity, field) is None and getattr(deleted, field) is not None:
            updates[field] = getattr(deleted, field)
    if not entity.labels and deleted.labels:
        updates["labels"] = set(deleted.labels)
    if not entity.categories and deleted.categories:
        updates["categories"] = dict(deleted.categories)
    if not entity.aliases and deleted.aliases:
        updates["aliases"] = copy.copy(deleted.aliases)
    if entity.hidden_by is None and deleted.hidden_by is er.RegistryEntryHider.USER:
        updates["hidden_by"] = er.RegistryEntryHider.USER
    try:
        registry.async_update_entity(entity.entity_id, **updates)
    except ValueError as err:  # entity_id taken in the state machine
        _LOGGER.warning(
            "SAI2: could not give %s back its entity_id %s: %s",
            entity.entity_id,
            deleted.entity_id,
            err,
        )
        return
    _LOGGER.warning(
        "SAI2: %s was created after the control unit renumbered its objects; "
        "restored its original entity_id %s and settings (%s)",
        entity.entity_id,
        deleted.entity_id,
        ", ".join(sorted(set(updates) - {"new_entity_id"})) or "none",
    )
