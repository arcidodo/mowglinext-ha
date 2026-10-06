"""One entity per item of a list the mower publishes (schedules, recorded areas).

The set of entities follows the list: an entity is added for every new item and
removed — from the entity registry, which also removes the live entity — for every
item that disappeared, including items deleted while Home Assistant was not running.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import MowglinextHub


@callback
def async_track_list_entities(
    hass: HomeAssistant,
    hub: MowglinextHub,
    async_add_entities: AddEntitiesCallback,
    *,
    platform: Platform,
    topic: str,
    unique_id_prefix: str,
    current_keys: Callable[[], Iterable[str] | None],
    create: Callable[[list[str]], list[Entity]],
) -> None:
    """Keep one `platform` entity per key of `current_keys()` in step with `topic`.

    Each entity's unique id must be `<device_id>_<unique_id_prefix><key>`.
    `current_keys` returns None while nothing usable has been received yet: then the
    registry is left alone, so a restart does not prune every entity before the
    retained list arrives. `create` builds the entities for the new keys, and may set
    their `entity_id` before they are added.
    """
    registry = er.async_get(hass)
    prefix = f"{hub.device_id}_{unique_id_prefix}"
    added: set[str] = set()

    @callback
    def _sync() -> None:
        keys = current_keys()
        if keys is None:
            return
        current = set(keys)
        for reg_entry in er.async_entries_for_config_entry(registry, hub.entry.entry_id):
            if (
                reg_entry.domain == platform
                and reg_entry.unique_id.startswith(prefix)
                and reg_entry.unique_id.removeprefix(prefix) not in current
            ):
                registry.async_remove(reg_entry.entity_id)
        added.intersection_update(current)
        new_keys = sorted(current - added)
        if not new_keys:
            return
        added.update(new_keys)
        async_add_entities(create(new_keys))

    hub.entry.async_on_unload(hub.async_add_listener(topic, _sync))
    _sync()  # a retained payload that already arrived


def mower_object_id(hass: HomeAssistant, hub: MowglinextHub) -> str:
    """The lawn_mower entity's object id ("mowgli"). Per-item entities are named after
    it: the lawn-mower-card finds a mower's companion entities by that prefix."""
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, hub.entry.entry_id):
        if reg_entry.domain == Platform.LAWN_MOWER:
            return reg_entry.entity_id.split(".", 1)[1]
    return "mowgli"


def free_entity_id(hass: HomeAssistant, base: str, taken: set[str]) -> str:
    """`base` itself, or `base_2`, `base_3`, … — the first one not in use."""
    registry = er.async_get(hass)
    n = 1
    while True:
        entity_id = base if n == 1 else f"{base}_{n}"
        if (
            entity_id not in taken
            and registry.async_get(entity_id) is None
            and hass.states.get(entity_id) is None
        ):
            return entity_id
        n += 1
