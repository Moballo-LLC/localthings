"""Local Things — Samsung appliance local control integration."""

from __future__ import annotations

import inspect
import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER as PARTICULATE_UNIT,
)
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import CONF_DEVICE_TYPE, CONF_HOST, CONF_PORT, CONF_SERIAL, DOMAIN, PLATFORMS
from .coordinator import LocalThingsCoordinator, snapshot_store
from .registry.identity import resolve_serial
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Services are process-global, registered once here rather than per
    # config entry (issue #300) -- see services.async_setup_services.
    async_setup_services(hass)
    return True


def _serial_from_unique_id(entry: ConfigEntry) -> str:
    """The device identity a pre-v2 entry was created with.

    The config flow has always keyed the entry's unique_id on the serial the
    probe read (`localthings_<serial>`), so that string is the identity the
    entry's registry entries were minted from -- no need to reach the device
    to recover it. Anything unrecoverable resolves to the host, matching
    what the coordinator seeded such an entry with anyway.

    The recovered string goes back through resolve_serial rather than being
    taken at face value: entries created before the placeholder rules
    (issues #83/#189) were keyed on the placeholder itself, while the
    coordinator has since resolved those same boards to the host.
    Re-keying onto the placeholder would reintroduce the collision those
    issues are about -- two units of a family sharing the same placeholder
    would share entity unique_ids again. A later wrinkle, same root cause:
    for a stretch the flow wrote `host:port` while the coordinator wrote
    `host`; collapsed here to the coordinator's form too.
    """
    host = entry.data[CONF_HOST]
    prefix = f"{DOMAIN}_"
    unique_id = entry.unique_id or ""
    if not unique_id.startswith(prefix):
        return host
    serial = unique_id[len(prefix) :]
    if serial == f"{host}:{entry.data.get(CONF_PORT)}":
        return host
    return resolve_serial(serial, host)


@callback
def _repair_placeholder_keys(hass: HomeAssistant, entry: ConfigEntry, serial: str) -> None:
    """Re-key registry entries this entry minted from the placeholder identity.

    Before the identity moved onto the config entry, the coordinator seeded
    `device_serial` with the host and only replaced it after the first poll.
    Anything that registered in between -- the connection-mode sensor
    especially, added unconditionally rather than from `bound` -- was
    written into the registry keyed on the IP permanently, orphaned the
    moment the serial-keyed identity appeared (issue #236). Deleting the
    orphans by hand didn't help: the next restart that lost the same race
    recreated them.

    Rewriting beats deleting where possible -- an entity keeps its
    entity_id, name, area and automations. Only possible when the
    serial-keyed key is still free; where both exist the placeholder-keyed
    one is the dead duplicate (unavailable since the restart that created
    it), so it goes.
    """
    host = entry.data[CONF_HOST]
    if serial == host:
        # A board with no usable serial resolves to the host, so its keys
        # were never placeholders.
        return

    ent_reg = er.async_get(hass)
    stale_prefix = f"{DOMAIN}_{host}_"
    for entity in list(er.async_entries_for_config_entry(ent_reg, entry.entry_id)):
        if not entity.unique_id.startswith(stale_prefix):
            continue
        new_unique_id = f"{DOMAIN}_{serial}_{entity.unique_id[len(stale_prefix) :]}"
        if ent_reg.async_get_entity_id(entity.domain, DOMAIN, new_unique_id):
            _LOGGER.debug("removing orphaned entity %s", entity.entity_id)
            ent_reg.async_remove(entity.entity_id)
        else:
            _LOGGER.debug("re-keying entity %s to %s", entity.entity_id, new_unique_id)
            ent_reg.async_update_entity(entity.entity_id, new_unique_id=new_unique_id)

    dev_reg = dr.async_get(hass)
    for device in list(dr.async_entries_for_config_entry(dev_reg, entry.entry_id)):
        # `host` for the master, `host_<key>` for a subdevice (device_info_for).
        stale = {
            ident
            for ident in device.identifiers
            if ident[0] == DOMAIN and (ident[1] == host or ident[1].startswith(f"{host}_"))
        }
        if not stale:
            continue
        fresh = {(DOMAIN, f"{serial}{ident[1][len(host) :]}") for ident in stale}
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


# Registries whose Dust/FineDust/SuperFineDust sensors gained pm10/pm25/pm1
# and a unit in the release that introduced entry version 3. Deliberately
# not every family reading /sensors/vs/0: range_hood and airconditioner
# still declare no unit for their identically-named sensors, and relabelling
# their statistics to µg/m³ would assert a unit those entities don't report
# -- creating the very mismatch this migration exists to prevent.
_PARTICULATE_TYPED_IN_V3 = frozenset({"air_purifier", "air_monitor"})

# unique_id is f"{DOMAIN}_{serial}_{state_key}"; state_key is the descriptor
# key, optionally carrying a subdevice prefix and a trailing `_<n>` instance
# (registry/adapter._key, discovery.instance_suffix). Matching the tail rather
# than rebuilding the whole id keeps this working for a renamed entity, whose
# entity_id -- and so its statistic_id -- no longer follows from the key.
_PARTICULATE_KEY_RE = re.compile(r"_(?:super_fine_dust|fine_dust|dust)(?:_\d+)?$")


@callback
def _relabel_particulate_statistics(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Point existing particulate statistics at the unit they always were.

    These sensors recorded long-term statistics with no unit, and the
    release carrying this migration gives them µg/m³. Home Assistant treats
    that as a unit change it can't convert and *suppresses statistics
    generation entirely* for the entity until someone resolves the repair
    (sensor.recorder._update_issues -> UNITS_CHANGED_ISSUE, and the matching
    `continue` in its compile path). Silently freezing the history we just
    finished labelling is the worst of both outcomes, so the metadata is
    corrected up front instead.

    Only the metadata row is rewritten, never the recorded values. The
    readings were always µg/m³ concentrations (issue #325); what was missing
    was the label, so there is nothing to convert and no way for this to
    distort history. That is also why it uses
    `async_update_statistics_metadata` and not `change_statistics_unit`,
    which would scale every stored value.

    A no-op when this device family isn't one that gained the unit, or when
    the device never recorded any statistics -- the underlying UPDATE simply
    matches no rows.

    Returns False only when the recorder wasn't loaded, meaning the caller
    should leave the entry on its old version and try again next start.
    """
    if entry.data.get(CONF_DEVICE_TYPE) not in _PARTICULATE_TYPED_IN_V3:
        return True
    if "recorder" not in hass.config.components:
        # after_dependencies orders the recorder ahead of us when it's
        # configured, so this is either an install without it (nothing to
        # relabel, and the retry costs one set lookup per start) or a boot
        # where it failed to come up. Not distinguishable here, and burning
        # the one-shot migration on the second case would leave the
        # statistics suppressed for good.
        _LOGGER.debug("recorder not loaded, deferring statistics relabel")
        return False

    from homeassistant.components.recorder.statistics import (
        STATISTIC_UNIT_TO_UNIT_CONVERTER,
        async_update_statistics_metadata,
    )

    kwargs: dict[str, Any] = {"new_unit_of_measurement": PARTICULATE_UNIT}
    # `new_unit_class` only exists from HA 2025.11; hacs.json still supports
    # 2025.1, where passing it is a TypeError -- which would propagate out of
    # async_migrate_entry and fail the whole entry. Where it is supported it
    # must be named, since omitting it is deprecated from HA 2026.11. µg/m³
    # has a converter, so the value is 'concentration' rather than None.
    if "new_unit_class" in inspect.signature(async_update_statistics_metadata).parameters:
        converter = STATISTIC_UNIT_TO_UNIT_CONVERTER.get(PARTICULATE_UNIT)
        kwargs["new_unit_class"] = converter.UNIT_CLASS if converter is not None else None

    ent_reg = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if registry_entry.domain != "sensor":
            continue
        if not _PARTICULATE_KEY_RE.search(registry_entry.unique_id):
            continue
        _LOGGER.debug("relabelling statistics unit for %s", registry_entry.entity_id)
        try:
            async_update_statistics_metadata(hass, registry_entry.entity_id, **kwargs)
        except Exception:
            # Relabelling is a convenience: without it the user gets Home
            # Assistant's own units_changed repair, which is where they were
            # before this migration existed. Never worth failing setup over,
            # so no recorder-side surprise can cost them the integration.
            _LOGGER.warning(
                "Could not relabel statistics unit for %s; Home Assistant will "
                "offer a units-changed repair for it instead",
                registry_entry.entity_id,
                exc_info=True,
            )
            return True
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an entry to the current version.

    v1 -> v2 stores the device's identity on the entry so the coordinator
    can key its registry entries before the first poll (issue #236), and
    repairs whatever the old placeholder-keyed registration already
    orphaned.

    v2 -> v3 relabels the recorded statistics for the particulate sensors,
    which gained a device_class/unit in the same release (issue #325).
    """
    if entry.version > 3:
        return False  # downgrade: this release doesn't know the newer shape

    if entry.version == 1:
        serial = entry.data.get(CONF_SERIAL) or _serial_from_unique_id(entry)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_SERIAL: serial},
            unique_id=f"{DOMAIN}_{serial}",
            version=2,
        )
        _repair_placeholder_keys(hass, entry, serial)
        _LOGGER.debug("migrated entry %s to version 2 (serial=%s)", entry.entry_id, serial)

    if entry.version == 2 and _relabel_particulate_statistics(hass, entry):
        hass.config_entries.async_update_entry(entry, version=3)
        _LOGGER.debug("migrated entry %s to version 3", entry.entry_id)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    coordinator = LocalThingsCoordinator(hass, entry)

    # Before the first refresh, so the coordinator keeps rescheduling even
    # when that refresh fails and leaves nothing subscribed: the base class
    # only re-arms its timer while it has listeners, and an offline load can
    # legitimately have zero live entities (every one of them disabled, say).
    # Without this the entry loads once and never polls again (issue #295).
    entry.async_on_unload(coordinator.async_add_listener(lambda: None))

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        # An entry that has polled successfully before comes up on its last
        # known entity set and keeps retrying on the normal poll interval,
        # rather than sitting in setup-retry with a device that reads as
        # broken and entities that exist only as registry rows (issue #295).
        #
        # An entry that has never reached the device has no snapshot, so
        # there is nothing to show and no device metadata to name it with --
        # that case still fails, which is also what keeps the door open for
        # setup flows that need to interact with the device (issue #168).
        if not await coordinator.async_rehydrate():
            await coordinator.async_close()
            raise

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Send the DTLS close_notify on Core shutdown, not just on unload (issue
    # #254): HA doesn't unload entries on a plain Core restart, so a restart
    # left the previous run's association orphaned, making the next
    # handshake time out. Complements the fixed source port, which covers
    # the unclean-exit case this can't.
    #
    # A coroutine listener, not one that spawns its own task: the event bus
    # runs it as a hass-tracked job, awaited by `async_block_till_done()`
    # inside `hass.async_stop`. A detached task would likely be cancelled
    # mid-shutdown -- the exact case this exists to prevent.
    async def _async_close_on_stop(_event: Event) -> None:
        await coordinator.async_close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_close_on_stop)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: dr.DeviceEntry,
) -> bool:
    """Allow deleting a device this entry no longer provides (issue #214).

    Defining this at all is what makes HA offer the "Delete device" action;
    without it, a device belonging to a loaded config entry can never be
    removed from the UI. That matters because a subdevice's HA device
    outlives the discovery that created it: a candidate materialized under
    an older release (issue #214's phantom second air conditioner, born
    from an unused slot reporting the appliance's energy counter -- see
    registry/subdevices.py's liveness gate) leaves a device entry nothing
    recreates or cleans up once the gate stops materializing it. Same for a
    sibling a firmware update stops exposing.

    Removal is refused for devices this entry does currently provide -- HA
    would recreate them on the next entity add. Deliberately no automatic
    pruning at discovery time: a sibling can fail to answer for a single
    poll (issue #205), so auto-removal would throw away a real subdevice's
    name/area/automations on a transient miss. The user gets the button;
    the integration doesn't guess.
    """
    coordinator: LocalThingsCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return True  # entry not loaded -- nothing claims this device
    live = set(coordinator.device_info.get("identifiers") or set())
    for subdevice in coordinator.subdevices:
        live |= set(coordinator.device_info_for(subdevice).get("identifiers") or set())
    return not (device.identifiers & live)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the discovery snapshot this entry accumulated (issue #295).

    Nothing else would: the store is keyed on entry_id, so re-adding the same
    appliance mints a new one and the old file would linger in .storage
    forever.
    """
    await snapshot_store(hass, entry).async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: LocalThingsCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_close()
    return unloaded
