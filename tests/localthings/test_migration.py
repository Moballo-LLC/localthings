"""Config-entry migration, the placeholder-identity repair (issue #236), and
the move onto the OCF device UUID (issue #381)."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_DEVICE_KEY,
    CONF_HOST,
    CONF_SERIAL,
    DOMAIN,
)

from .conftest import LEGACY_ENTRY_DATA, MOCK_HOST, MOCK_PORT, MOCK_SERIAL


def _legacy_entry(hass: HomeAssistant, unique_id: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=LEGACY_ENTRY_DATA,
        unique_id=unique_id,
        version=1,
    )
    entry.add_to_hass(hass)
    return entry


async def test_migration_recovers_serial_from_unique_id(
    hass: HomeAssistant, mock_coordinator_session
) -> None:
    """A v1 entry's identity is recoverable without reaching the device: the
    config flow has always keyed the entry's unique_id on the serial its probe
    read."""
    entry = _legacy_entry(hass, f"{DOMAIN}_{MOCK_SERIAL}")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Straight through to the current version: v2 -> v3 is a statistics
    # relabel that no-ops for a family without particulate sensors, and
    # v3 -> v4 only records the legacy key for the coordinator to re-key
    # from once a poll produces an OCF device id.
    assert entry.version == 4
    assert entry.data[CONF_SERIAL] == MOCK_SERIAL


async def test_migration_collapses_the_host_port_unique_id(hass: HomeAssistant) -> None:
    """A board with no usable serial (issues #83/#189) used to be keyed two
    different ways at once: `host:port` on the config entry, `host` in the
    device and entity registries. Migration collapses the entry onto the
    registry's form, so the two finally name the same thing."""
    from custom_components.localthings import async_migrate_entry

    entry = _legacy_entry(hass, f"{DOMAIN}_{MOCK_HOST}:{MOCK_PORT}")

    assert await async_migrate_entry(hass, entry) is True

    assert entry.data[CONF_SERIAL] == MOCK_HOST
    assert entry.unique_id == f"{DOMAIN}_{MOCK_HOST}"


async def test_migration_resolves_a_placeholder_serial_unique_id(hass: HomeAssistant) -> None:
    """An entry created before the placeholder rules landed was keyed on the
    placeholder itself (issues #83/#189), while the coordinator has been
    resolving those boards to the host ever since. The unique_id records what
    the flow believed then, not what the registry holds -- taking it at face
    value would re-key working devices back onto a string every unit of the
    family reports, which is the collision those issues are about."""
    from custom_components.localthings import async_migrate_entry

    entry = _legacy_entry(hass, f"{DOMAIN}_Nothing(SVC)")
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_HOST)},
    )

    assert await async_migrate_entry(hass, entry) is True

    assert entry.data[CONF_SERIAL] == MOCK_HOST
    assert entry.unique_id == f"{DOMAIN}_{MOCK_HOST}"
    unchanged = dev_reg.async_get(device.id)
    assert unchanged is not None
    assert unchanged.identifiers == {(DOMAIN, MOCK_HOST)}


async def test_migration_resolves_an_all_hex_placeholder_unique_id(hass: HomeAssistant) -> None:
    """The issue #189 flash-unset sentinel, same reasoning."""
    from custom_components.localthings import async_migrate_entry

    entry = _legacy_entry(hass, f"{DOMAIN}_FFFFFFFFFFFFFFF")

    assert await async_migrate_entry(hass, entry) is True

    assert entry.data[CONF_SERIAL] == MOCK_HOST


async def test_migration_keeps_a_survivor_off_a_removed_duplicate_device(
    hass: HomeAssistant,
) -> None:
    """Removing a device takes its entities with it (entity_registry's
    async_device_modified), so an entity that came through the pass above
    re-keyed rather than removed has to move to the surviving device first --
    otherwise the rewrite that exists to preserve an entity_id, name and area
    destroys all three a few lines later.

    Migration is called directly here: what the repair leaves behind is the
    contract, and going through async_setup would let the platform re-adding
    its entities hide a row that had in fact been deleted."""
    from custom_components.localthings import async_migrate_entry

    entry = _legacy_entry(hass, f"{DOMAIN}_{MOCK_SERIAL}")
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    real_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_SERIAL)},
    )
    orphan_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_HOST)},
    )
    # The serial-keyed device exists, but this entity's serial-keyed *key* is
    # free -- e.g. the user deleted the visible duplicate by hand -- so the
    # entity pass rewrites it instead of removing it.
    survivor = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MOCK_HOST}_connection_mode",
        config_entry=entry,
        device_id=orphan_device.id,
        suggested_object_id="kitchen_fridge_connection",
    )

    assert await async_migrate_entry(hass, entry) is True
    await hass.async_block_till_done()

    assert dev_reg.async_get(orphan_device.id) is None
    kept = ent_reg.async_get(survivor.entity_id)
    assert kept is not None
    assert kept.entity_id == "sensor.kitchen_fridge_connection"
    assert kept.unique_id == f"{DOMAIN}_{MOCK_SERIAL}_connection_mode"
    assert kept.device_id == real_device.id


async def test_migration_rekeys_an_ip_keyed_device_and_entity(
    hass: HomeAssistant, mock_coordinator_session
) -> None:
    """The registry entries the old placeholder identity minted are rewritten
    in place, so an orphan keeps its entity_id, name, area and every
    automation that referenced it -- rather than being replaced by a
    serial-keyed duplicate with a `_2` suffix while it sits permanently
    unavailable (issue #236)."""
    entry = _legacy_entry(hass, f"{DOMAIN}_{MOCK_SERIAL}")
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    orphan_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_HOST)},
        name=f"Samsung Appliance ({MOCK_HOST})",
    )
    orphan_entity = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MOCK_HOST}_connection_mode",
        config_entry=entry,
        device_id=orphan_device.id,
    )

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Same registry rows, now keyed on the real identity.
    rekeyed_device = dev_reg.async_get(orphan_device.id)
    assert rekeyed_device is not None
    assert rekeyed_device.identifiers == {(DOMAIN, MOCK_SERIAL)}
    rekeyed = ent_reg.async_get(orphan_entity.entity_id)
    assert rekeyed is not None
    assert rekeyed.unique_id == f"{DOMAIN}_{MOCK_SERIAL}_connection_mode"
    # And nothing is left keyed on the IP.
    assert dev_reg.async_get_device(identifiers={(DOMAIN, MOCK_HOST)}) is None


async def test_migration_removes_an_orphan_that_is_already_duplicated(
    hass: HomeAssistant, mock_coordinator_session
) -> None:
    """Where the serial-keyed entry already exists, the IP-keyed one is the
    dead duplicate the race left behind -- it has been unavailable since the
    restart that created it and nothing will ever update it, so it goes
    rather than being rewritten onto a key that is taken."""
    entry = _legacy_entry(hass, f"{DOMAIN}_{MOCK_SERIAL}")
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    real_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_SERIAL)},
    )
    real_entity = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MOCK_SERIAL}_connection_mode",
        config_entry=entry,
        device_id=real_device.id,
    )
    orphan_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_HOST)},
    )
    orphan_entity = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MOCK_HOST}_connection_mode",
        config_entry=entry,
        device_id=orphan_device.id,
    )
    assert orphan_entity.entity_id != real_entity.entity_id

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert ent_reg.async_get(orphan_entity.entity_id) is None
    assert dev_reg.async_get(orphan_device.id) is None
    # The working pair is untouched.
    assert ent_reg.async_get(real_entity.entity_id) is not None
    assert dev_reg.async_get(real_device.id) is not None


async def test_migration_leaves_a_host_identity_device_alone(hass: HomeAssistant) -> None:
    """A board whose serial resolves *to* the host was never keyed on a
    placeholder -- its host-keyed device is the real one, and re-keying or
    removing it would orphan a working device to fix a problem it doesn't
    have."""
    from custom_components.localthings import async_migrate_entry

    entry = _legacy_entry(hass, f"{DOMAIN}_{MOCK_HOST}")
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_HOST)},
    )

    assert await async_migrate_entry(hass, entry) is True

    assert entry.data[CONF_SERIAL] == MOCK_HOST
    unchanged = dev_reg.async_get(device.id)
    assert unchanged is not None
    assert unchanged.identifiers == {(DOMAIN, MOCK_HOST)}


async def test_migration_rejects_a_future_entry_version(hass: HomeAssistant) -> None:
    """A downgrade must fail the entry rather than silently mangling data
    written by a newer release."""
    from custom_components.localthings import async_migrate_entry

    entry = MockConfigEntry(domain=DOMAIN, data=LEGACY_ENTRY_DATA, version=5)
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is False


async def test_migration_without_a_unique_id_falls_back_to_host(hass: HomeAssistant) -> None:
    """Nothing to recover the identity from means the host, which is exactly
    what the coordinator used to seed -- so the registry keys such an entry
    already holds stay valid."""
    from custom_components.localthings import async_migrate_entry

    entry = MockConfigEntry(domain=DOMAIN, data=LEGACY_ENTRY_DATA, version=1)
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.data[CONF_SERIAL] == entry.data[CONF_HOST]
    assert entry.unique_id == f"{DOMAIN}_{MOCK_HOST}"


# ---------------------------------------------------------------------------
# v3 -> v4: onto the OCF device UUID (issue #381)
# ---------------------------------------------------------------------------

# The two purifiers from issue #381: one serialNum, two device UUIDs.
SHARED_SERIAL = "BS7SP9AW400114A"
UUID_A = "ccfd73b3-aeb4-792a-1100-68f06f5d603b"
UUID_B = "3771f8bf-c184-3a2d-d885-e4c9818736d2"


def _identity_reporting(device_id: str | None):
    """A patched _connect_session that hands the coordinator the identity a
    real DTLS connect would have read off /oic/p and /oic/d."""
    from custom_components.localthings.registry.identity import DeviceIdentity

    def _connect(self):
        self._identity = (
            None
            if device_id is None
            else DeviceIdentity(
                manufacturer="Samsung Electronics",
                model="AVT-WW-TP1-23-AXX500",
                name="Samsung AirPurifier",
                serial=None,
                device_id=device_id,
            )
        )

    return patch(
        "custom_components.localthings.coordinator.LocalThingsCoordinator._connect_session",
        _connect,
    )


def _v3_entry(hass: HomeAssistant, key: str, *, serial: str | None = None) -> MockConfigEntry:
    """An entry as it sits on disk before this release: keyed on CONF_SERIAL,
    with no CONF_DEVICE_KEY."""
    data = {**LEGACY_ENTRY_DATA, CONF_SERIAL: serial if serial is not None else key}
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=f"{DOMAIN}_{key}", version=3)
    entry.add_to_hass(hass)
    return entry


async def test_v3_entry_moves_onto_the_device_uuid_keeping_its_entity_ids(
    hass: HomeAssistant, fridge_resources
) -> None:
    """The whole migration promise for an existing user, asserted end to end.

    The re-key happens on the first live poll rather than in
    async_migrate_entry, because the UUID is only readable from the device
    and an entry can load entirely from its snapshot while the appliance is
    off (issue #295). What the user must keep across it: the entity_id (and
    with it the entity's name, area, long-term statistics, and every
    automation and dashboard that references it), and the device row.
    """
    entry = _v3_entry(hass, MOCK_SERIAL)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_SERIAL)},
    )
    existing = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MOCK_SERIAL}_connection_mode",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="kitchen_purifier_connection",
    )

    with (
        _identity_reporting(UUID_A),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once",
            return_value=fridge_resources,
        ),
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._close_session"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.version == 4
    assert entry.data[CONF_DEVICE_KEY] == UUID_A
    # The serial is kept alongside the key, not replaced by it: it is what
    # corroborates a later change of UUID.
    assert entry.data[CONF_SERIAL] == MOCK_SERIAL
    # All three permanent places moved together.
    assert entry.unique_id == f"{DOMAIN}_{UUID_A}"
    rekeyed_device = dev_reg.async_get(device.id)
    assert rekeyed_device is not None
    assert rekeyed_device.identifiers == {(DOMAIN, UUID_A)}
    kept = ent_reg.async_get(existing.entity_id)
    assert kept is not None
    assert kept.entity_id == "sensor.kitchen_purifier_connection"
    assert kept.unique_id == f"{DOMAIN}_{UUID_A}_connection_mode"
    # And nothing is left behind on the old key.
    assert dev_reg.async_get_device(identifiers={(DOMAIN, MOCK_SERIAL)}) is None


async def test_a_host_keyed_entry_adopts_a_real_identity(
    hass: HomeAssistant, fridge_resources
) -> None:
    """A placeholder-serial board (issues #83/#189) was keyed on its IP, which
    is an address rather than an identity -- a new DHCP lease silently makes
    it someone else's. Such an entry never made an identity claim to defend,
    so a real UUID is adopted without needing the serial to corroborate it;
    requiring corroboration would strand exactly these boards, since their
    serial resolves to the host and can never match."""
    entry = _v3_entry(hass, MOCK_HOST)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_HOST)},
    )

    with (
        _identity_reporting(UUID_B),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once",
            return_value=fridge_resources,
        ),
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._close_session"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.data[CONF_DEVICE_KEY] == UUID_B
    rekeyed = dev_reg.async_get(device.id)
    assert rekeyed is not None
    assert rekeyed.identifiers == {(DOMAIN, UUID_B)}


async def test_a_poll_that_reads_no_uuid_does_not_demote_a_keyed_entry(
    hass: HomeAssistant, fridge_resources
) -> None:
    """The device saying nothing is not the device saying something different.

    A reconnect that can't read /oic/d (a timeout, a firmware hiccup) must
    leave the key alone -- demoting back onto the serial would re-key every
    entity the user has for the duration of an outage, and re-key them all
    back afterwards."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**LEGACY_ENTRY_DATA, CONF_SERIAL: MOCK_SERIAL, CONF_DEVICE_KEY: UUID_A},
        unique_id=f"{DOMAIN}_{UUID_A}",
        version=4,
    )
    entry.add_to_hass(hass)

    with (
        _identity_reporting(None),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once",
            return_value=fridge_resources,
        ),
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._close_session"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.device_key == UUID_A
    assert entry.data[CONF_DEVICE_KEY] == UUID_A


async def test_a_rotated_uuid_on_the_same_serial_is_followed(
    hass: HomeAssistant, fridge_resources
) -> None:
    """OCF permits a hard factory reset to regenerate `di`. The serialNum is
    what tells that apart from a different appliance moving onto the address,
    and following it keeps the user's history rather than stranding it on a
    UUID the device will never report again."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**LEGACY_ENTRY_DATA, CONF_SERIAL: MOCK_SERIAL, CONF_DEVICE_KEY: UUID_A},
        unique_id=f"{DOMAIN}_{UUID_A}",
        version=4,
    )
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, UUID_A)},
    )

    # fridge_resources reports MOCK_SERIAL, matching what the entry stored.
    with (
        _identity_reporting(UUID_B),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once",
            return_value=fridge_resources,
        ),
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._close_session"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.data[CONF_DEVICE_KEY] == UUID_B
    rekeyed = dev_reg.async_get(device.id)
    assert rekeyed is not None
    assert rekeyed.identifiers == {(DOMAIN, UUID_B)}


async def test_a_different_appliance_on_the_same_address_keeps_the_registered_identity(
    hass: HomeAssistant, fridge_resources
) -> None:
    """Neither the UUID nor the serial matches what this entry was registered
    with, so this is a different appliance answering at this address -- not a
    reset of the registered one. Re-keying here would hand one appliance's
    entities, history and automations to another; re-adding is the user's
    call."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            **LEGACY_ENTRY_DATA,
            CONF_SERIAL: "SOME-OTHER-APPLIANCE",
            CONF_DEVICE_KEY: UUID_A,
        },
        unique_id=f"{DOMAIN}_{UUID_A}",
        version=4,
    )
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, UUID_A)},
    )

    with (
        _identity_reporting(UUID_B),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once",
            return_value=fridge_resources,
        ),
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._close_session"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.data[CONF_DEVICE_KEY] == UUID_A
    unchanged = dev_reg.async_get(device.id)
    assert unchanged is not None
    assert unchanged.identifiers == {(DOMAIN, UUID_A)}
    # The rejected appliance's serial is not written either. The serial is
    # what corroborates a later change of key, so adopting it here would
    # hand the intruder exactly the corroboration it needs to win the *next*
    # poll -- defending the identity once and then surrendering it on the
    # following cycle.
    assert entry.data[CONF_SERIAL] == "SOME-OTHER-APPLIANCE"

    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator._run_discovery(fridge_resources)

    assert coordinator.device_key == UUID_A
    assert entry.data[CONF_DEVICE_KEY] == UUID_A
    assert entry.data[CONF_SERIAL] == "SOME-OTHER-APPLIANCE"


async def test_rekey_moves_subdevice_identifiers_too(hass: HomeAssistant) -> None:
    """A composite appliance (issue #177) registers one device per logical
    subdevice, keyed f"{key}_{subdevice}" and linked via_device to the
    master's bare key. Rewriting only the exact-match identifier would strand
    every sibling, so the prefix form moves with it.

    Calls rekey_entry directly: what it leaves behind is the contract, and
    going through a setup would let the platforms re-adding their entities
    hide a row that had in fact been orphaned.
    """
    from custom_components.localthings.rekey import rekey_entry

    entry = _v3_entry(hass, MOCK_SERIAL)
    dev_reg = dr.async_get(hass)
    master = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_SERIAL)},
    )
    sub = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MOCK_SERIAL}_subdevice_1")},
    )

    rekey_entry(hass, entry, MOCK_SERIAL, UUID_A)

    assert dev_reg.async_get(master.id).identifiers == {(DOMAIN, UUID_A)}
    assert dev_reg.async_get(sub.id).identifiers == {(DOMAIN, f"{UUID_A}_subdevice_1")}


async def test_rekey_is_idempotent(hass: HomeAssistant) -> None:
    """Safe to attempt on every poll rather than having to track whether it
    has already run -- a second call finds nothing under the old key."""
    from custom_components.localthings.rekey import rekey_entry

    entry = _v3_entry(hass, MOCK_SERIAL)
    ent_reg = er.async_get(hass)
    existing = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{MOCK_SERIAL}_connection_mode", config_entry=entry
    )

    rekey_entry(hass, entry, MOCK_SERIAL, UUID_A)
    rekey_entry(hass, entry, MOCK_SERIAL, UUID_A)

    kept = ent_reg.async_get(existing.entity_id)
    assert kept is not None
    assert kept.unique_id == f"{DOMAIN}_{UUID_A}_connection_mode"
    assert entry.unique_id == f"{DOMAIN}_{UUID_A}"
