"""Tests for async_remove_config_entry_device (issue #214).

A subdevice's HA device outlives the discovery that created it: nothing
recreates it and nothing prunes it once discovery stops materializing that
subdevice, so a phantom created by an older release (issue #214's duplicate
air conditioner, and the duplicate the second reporter still sees on a
refrigerator whose diagnostics now report no subdevices at all) sticks
around forever. Defining this callback is what puts a working "Delete
device" button on it.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.localthings import async_remove_config_entry_device
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.registry.subdevices import Subdevice


def _device(hass, entry, identifiers) -> dr.DeviceEntry:
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=identifiers,
    )


async def test_stale_device_can_be_removed(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """The phantom case: a device entry left over from a subdevice this
    entry no longer provides."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.subdevices == []  # this fixture is not a composite device
    stale = _device(
        hass,
        mock_entry,
        {(DOMAIN, f"{coordinator.device_serial}_1")},
    )

    assert await async_remove_config_entry_device(hass, mock_entry, stale) is True


async def test_master_device_cannot_be_removed(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """Refusing here is what stops the delete from *looking* like it worked
    and then having HA recreate the device on the next entity add."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    master = _device(hass, mock_entry, set(coordinator.device_info["identifiers"]))

    assert await async_remove_config_entry_device(hass, mock_entry, master) is False


async def test_live_subdevice_device_cannot_be_removed(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """A subdevice that *did* materialize is as protected as the master --
    the identifiers this checks against come from device_info_for, the same
    call every one of that subdevice's entities reports."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    subdevice = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    coordinator.subdevices = [subdevice]
    live = _device(
        hass,
        mock_entry,
        set(coordinator.device_info_for(subdevice)["identifiers"]),
    )

    assert await async_remove_config_entry_device(hass, mock_entry, live) is False


async def test_removal_allowed_when_entry_is_not_loaded(
    hass: HomeAssistant,
    mock_entry,
) -> None:
    """No coordinator means nothing is claiming the device -- don't strand
    it behind a callback that can't answer."""
    mock_entry.add_to_hass(hass)
    orphan = _device(hass, mock_entry, {(DOMAIN, "whatever")})

    assert await async_remove_config_entry_device(hass, mock_entry, orphan) is True
