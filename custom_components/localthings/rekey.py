"""Move an entry's registry entries from one device key to another.

The key a device's registry entries are minted from (see
registry.identity.resolve_device_key) appears in three permanent places:
the config entry's unique_id, the device registry's identifiers, and every
entity's unique_id. Changing it therefore can't be a matter of writing a
new value and restarting -- everything already in the registries would be
orphaned, and the user would find a duplicate device whose entities all
carry a `_2` suffix, with their history, area and automations attached to
the dead copy.

So the key change is performed *on* the registries instead. Rewriting
beats deleting: an entity keeps its entity_id, and with it its name, area,
long-term statistics and every automation and dashboard that references
it.

Lives in its own module rather than in __init__.py because both callers
need it and they sit on opposite sides of an import edge: the v1 -> v2
migration in __init__.py, and the coordinator's first-poll adoption of the
OCF device UUID (issue #381), which can only happen once the device has
actually been reached.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


@callback
def rekey_entry(hass: HomeAssistant, entry: ConfigEntry, old_key: str, new_key: str) -> None:
    """Rewrite everything this entry registered under `old_key` to `new_key`.

    Covers all three places the key is permanent: the entity registry
    (unique_ids are f"{DOMAIN}_{key}_{state_key}"), the device registry
    (identifiers are (DOMAIN, key) for the device itself and
    (DOMAIN, f"{key}_{subdevice}") for each subdevice of a composite
    appliance -- see coordinator.device_info_for), and the config entry's
    own unique_id.

    Leaving the entry's unique_id behind would half-migrate it: the config
    flow's duplicate check (_abort_if_unique_id_configured) would still be
    comparing new devices against the key this entry no longer uses, so
    re-adding this very appliance would be waved through as a second entry.

    Idempotent: a second call finds nothing left under `old_key` and does
    nothing, which is what makes it safe to attempt on every poll rather
    than having to track whether it has already run.

    Where both keys somehow already exist, the `old_key` copy is the dead
    one -- unavailable since whichever restart created the split -- so it
    is removed rather than rewritten over the live entry.

    Must run on the event loop; the registry helpers require it.
    """
    if old_key == new_key:
        return

    new_entry_unique_id = f"{DOMAIN}_{new_key}"
    if entry.unique_id != new_entry_unique_id:
        hass.config_entries.async_update_entry(entry, unique_id=new_entry_unique_id)

    ent_reg = er.async_get(hass)
    stale_prefix = f"{DOMAIN}_{old_key}_"
    for entity in list(er.async_entries_for_config_entry(ent_reg, entry.entry_id)):
        if not entity.unique_id.startswith(stale_prefix):
            continue
        new_unique_id = f"{DOMAIN}_{new_key}_{entity.unique_id[len(stale_prefix) :]}"
        if ent_reg.async_get_entity_id(entity.domain, DOMAIN, new_unique_id):
            _LOGGER.debug("removing orphaned entity %s", entity.entity_id)
            ent_reg.async_remove(entity.entity_id)
        else:
            _LOGGER.debug("re-keying entity %s to %s", entity.entity_id, new_unique_id)
            ent_reg.async_update_entity(entity.entity_id, new_unique_id=new_unique_id)

    dev_reg = dr.async_get(hass)
    for device in list(dr.async_entries_for_config_entry(dev_reg, entry.entry_id)):
        stale = {
            ident
            for ident in device.identifiers
            if ident[0] == DOMAIN and (ident[1] == old_key or ident[1].startswith(f"{old_key}_"))
        }
        if not stale:
            continue
        fresh = {(DOMAIN, f"{new_key}{ident[1][len(old_key) :]}") for ident in stale}
        existing = dev_reg.async_get_device(identifiers=fresh)
        if existing is not None and existing.id != device.id:
            # Removing a device takes its entities with it. Anything still
            # attached here was re-keyed rather than removed above -- the
            # surviving copy, not a duplicate -- so move it onto the device
            # it now belongs to before the removal destroys it too.
            for entity in er.async_entries_for_device(
                ent_reg, device.id, include_disabled_entities=True
            ):
                ent_reg.async_update_entity(entity.entity_id, device_id=existing.id)
            _LOGGER.debug("removing orphaned device %s", device.id)
            dev_reg.async_remove_device(device.id)
        else:
            _LOGGER.debug("re-keying device %s to %s", device.id, fresh)
            dev_reg.async_update_device(
                device.id, new_identifiers=(device.identifiers - stale) | fresh
            )
