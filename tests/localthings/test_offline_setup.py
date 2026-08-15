"""Loading a config entry while the appliance is unreachable (issue #295).

The device's entity set only exists as the output of a live poll, so coming
up offline means replaying the last successful discovery from a stored
snapshot. These tests pin the four things that makes load-bearing: the
snapshot gets written, it produces the same entity set offline, the
coordinator keeps polling until the device answers, and a live discovery that
disagrees with the snapshot reloads the entry rather than silently keeping a
stale set.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.localthings.const import DOMAIN, SUMMARY_INTERVAL_S
from custom_components.localthings.coordinator import (
    _SNAPSHOT_SAVE_DELAY_S,
    LocalThingsCoordinator,
)
from custom_components.localthings.registry.identity import DeviceIdentity

from .conftest import _load_fridge_resources as _load_fridge

_COORD = "custom_components.localthings.coordinator.LocalThingsCoordinator"


@contextmanager
def _reachable(resources: dict, identity: DeviceIdentity | None = None):
    """A device that answers, optionally with an /oic/* identity -- which
    `_connect_session` is what normally reads, so a test that patches it out
    otherwise leaves `_identity` None."""

    def _connect(self) -> None:
        self._identity = identity

    with (
        patch(f"{_COORD}._connect_session", _connect),
        patch(f"{_COORD}._poll_once", return_value=resources),
        patch(f"{_COORD}._close_session"),
    ):
        yield


@contextmanager
def _unreachable():
    with (
        patch(f"{_COORD}._connect_session"),
        patch(f"{_COORD}._poll_once", side_effect=OSError("device offline")),
        patch(f"{_COORD}._close_session"),
    ):
        yield


def _store_key(entry) -> str:
    return f"{DOMAIN}.{entry.entry_id}.discovery"


async def _flush_snapshot(hass: HomeAssistant) -> None:
    """Run out `async_delay_save`'s timer without reaching the next poll."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=_SNAPSHOT_SAVE_DELAY_S + 1))
    await hass.async_block_till_done()


async def _tick(hass: HomeAssistant) -> None:
    """Advance past one summary interval so the coordinator polls again.

    `wait_background_tasks` is load-bearing: DataUpdateCoordinator runs its
    interval refresh as a background task, which a plain block_till_done
    doesn't await -- the poll would still be in flight at the assertion.
    """
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=SUMMARY_INTERVAL_S + 1))
    await hass.async_block_till_done(wait_background_tasks=True)


async def _setup_online_then_unload(hass: HomeAssistant, entry, resources: dict) -> set[str]:
    """Bring the entry up against a live device, bank the snapshot, and take
    it back down. Returns the entity_ids that run produced."""
    with _reachable(resources):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_ids = {s.entity_id for s in hass.states.async_all()}
        await _flush_snapshot(hass)
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    return entity_ids


# ---------------------------------------------------------------------------
# Writing the snapshot
# ---------------------------------------------------------------------------


async def test_snapshot_written_after_first_discovery(
    hass: HomeAssistant, mock_entry, mock_coordinator_session, hass_storage
) -> None:
    """A successful first cycle banks what it handed _run_discovery."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    await _flush_snapshot(hass)

    stored = hass_storage[_store_key(mock_entry)]["data"]
    assert stored["resources"]
    assert "/information/vs/0" in stored["resources"]
    assert "subdevice_candidates" in stored


async def test_snapshot_not_written_when_device_never_answers(
    hass: HomeAssistant, mock_entry, hass_storage
) -> None:
    """Nothing to bank, so nothing is -- this is what keeps the no-snapshot
    gate meaningful on a brand-new entry."""
    with _unreachable():
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        # Asserted before the flush: running the save timer out also runs out
        # HA's own setup-retry timer, which puts the entry back into
        # SETUP_IN_PROGRESS.
        assert mock_entry.state is ConfigEntryState.SETUP_RETRY
        await _flush_snapshot(hass)

    assert _store_key(mock_entry) not in hass_storage


async def test_snapshot_removed_when_entry_removed(
    hass: HomeAssistant, mock_entry, mock_coordinator_session, hass_storage
) -> None:
    """The store is keyed on entry_id, so re-adding the appliance mints a new
    one -- the old file has to go with the entry that wrote it."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    await _flush_snapshot(hass)
    assert _store_key(mock_entry) in hass_storage

    await hass.config_entries.async_remove(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert hass_storage.get(_store_key(mock_entry), {}).get("data") is None


# ---------------------------------------------------------------------------
# Loading from it
# ---------------------------------------------------------------------------


async def test_offline_load_restores_the_same_entity_set(
    hass: HomeAssistant, mock_entry, hass_storage
) -> None:
    """The whole point: a restart with the appliance powered off comes up on
    the entity set the device last actually reported."""
    resources = _load_fridge()
    online_ids = await _setup_online_then_unload(hass, mock_entry, resources)
    assert online_ids  # guard: the online run must actually produce entities

    with _unreachable():
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.LOADED
    assert {s.entity_id for s in hass.states.async_all()} == online_ids


async def test_offline_entities_are_unavailable_not_stale(hass: HomeAssistant, mock_entry) -> None:
    """Restored entities must not render the snapshot's values -- the
    appliance is unreachable, so `unavailable` is the honest state and the
    live cache stays empty to enforce it."""
    resources = _load_fridge()
    await _setup_online_then_unload(hass, mock_entry, resources)

    with _unreachable():
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    states = hass.states.async_all()
    assert states
    assert all(s.state == "unavailable" for s in states)

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.rehydrated
    assert not coordinator.last_resources


async def test_offline_load_without_snapshot_still_fails(hass: HomeAssistant, mock_entry) -> None:
    """No snapshot means no device metadata to build anything from, so the
    entry stays on HA's backoff rather than loading empty."""
    with _unreachable():
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.states.async_all()


async def test_corrupt_snapshot_falls_back_to_setup_retry(
    hass: HomeAssistant, mock_entry, hass_storage
) -> None:
    """A snapshot whose resources no longer replay cleanly must not take the
    entry down with it."""
    key = _store_key(mock_entry)
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {"resources": {"/information/vs/0": "not-a-rep"}, "subdevice_candidates": []},
    }

    with _unreachable():
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_RETRY


async def test_snapshot_restores_identity(hass: HomeAssistant, mock_entry) -> None:
    """`/oic/d`'s device types route the registry, so an offline load that
    lost them could resolve a different one than the live poll did -- which
    would show up as a spurious reconcile reload every restart."""
    resources = _load_fridge()
    identity = DeviceIdentity(
        manufacturer="Samsung",
        model="TEST-MODEL",
        name="Fridge",
        serial=None,
        device_types=("oic.d.refrigerator",),
        raw={"/oic/p": {}, "/oic/d": {}, "/oic/res": []},
    )
    with _reachable(resources, identity):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        await _flush_snapshot(hass)
        await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()

    with _unreachable():
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator._identity is not None
    assert coordinator._identity.device_types == ("oic.d.refrigerator",)


# ---------------------------------------------------------------------------
# Recovery and reconciliation
# ---------------------------------------------------------------------------


async def test_offline_load_keeps_polling_and_recovers(hass: HomeAssistant, mock_entry) -> None:
    """The failure the PR this replaces actually shipped: with zero live
    listeners the base coordinator stops rescheduling, and the entry never
    polls again. Entities must go available on the next interval once the
    appliance answers."""
    resources = _load_fridge()
    await _setup_online_then_unload(hass, mock_entry, resources)

    with _unreachable():
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator._unsub_refresh is not None  # a poll is actually queued
    assert not coordinator.last_update_success

    with _reachable(resources):
        await _tick(hass)

    assert coordinator.last_update_success
    assert any(s.state != "unavailable" for s in hass.states.async_all())


async def test_reconcile_reloads_when_live_discovery_differs(
    hass: HomeAssistant, mock_entry
) -> None:
    """Platforms enumerate `bound` once, so a live set that disagrees with the
    snapshot can only be adopted by bringing the entry back up."""
    resources = _load_fridge()

    with _reachable(resources):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
        victim = coordinator.bound[0].href
        await _flush_snapshot(hass)
        await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()

    with _unreachable():
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    reduced = {href: rep for href, rep in resources.items() if href != victim}
    with (
        _reachable(reduced),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        await _tick(hass)

    reload.assert_called_once_with(mock_entry.entry_id)


async def test_reconcile_is_quiet_when_live_discovery_agrees(
    hass: HomeAssistant, mock_entry
) -> None:
    """The common case -- same appliance, same firmware -- must not reload,
    or every offline restart would cost a second setup cycle."""
    resources = _load_fridge()
    await _setup_online_then_unload(hass, mock_entry, resources)

    with _unreachable():
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    with (
        _reachable(resources),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        await _tick(hass)

    reload.assert_not_called()


def test_coverage_gap_repair_is_live_only(hass: HomeAssistant, mock_entry) -> None:
    """A coverage gap is a claim about what the device reports, so replaying
    a snapshot must not raise the Repair -- it would restate last run's
    conclusion while the diagnostics download it points at is still empty."""
    gappy = {
        "/information/vs/0": {
            "x.com.samsung.da.modelNum": "TOTALLY_UNKNOWN_BOARD",
            "x.com.samsung.da.serialNum": "TEST-SERIAL-0000",
        },
        "/nothing/maps/this/vs/0": {"someField": 1},
    }
    issue_id = f"device_gap_{mock_entry.entry_id}"
    coordinator = LocalThingsCoordinator(hass, mock_entry)

    coordinator._run_discovery(gappy, from_snapshot=True)
    assert coordinator._unbound_hrefs  # the gap is real, it just stays quiet
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    coordinator._run_discovery(gappy)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


async def test_live_load_never_reconciles(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """An entry that came up against a live device has nothing to reconcile
    against; the reload path must stay out of the normal startup entirely."""
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert not coordinator.rehydrated
    reload.assert_not_called()


@pytest.mark.parametrize("failures", [1, 3])
async def test_offline_load_survives_repeated_poll_failures(
    hass: HomeAssistant, mock_entry, failures: int
) -> None:
    """Recovery isn't one-shot: the entry keeps its entities and keeps
    retrying across however many intervals the appliance stays dark."""
    resources = _load_fridge()
    online_ids = await _setup_online_then_unload(hass, mock_entry, resources)

    with _unreachable():
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        for _ in range(failures):
            await _tick(hass)

    assert mock_entry.state is ConfigEntryState.LOADED
    assert {s.entity_id for s in hass.states.async_all()} == online_ids

    with _reachable(resources):
        await _tick(hass)

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.last_update_success
