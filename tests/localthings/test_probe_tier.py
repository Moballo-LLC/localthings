"""The probe tier: reading hrefs the /device/0 batch never carries.

Issue #301. `/file/transfer/vs/0` holds the appliance's own usage history
and appears in no batch on record -- 83 fixtures, none of them -- so
`discover()` can never reach it and a capability declaring it would never
bind. `Capability.poll_tier="probe"` puts an href on `registry.PROBE_HREFS`,
which the coordinator GETs directly and folds into the resources dict before
discovery runs.

These capabilities bind no entities on purpose (see common.FILE_LIST): the
point of the tier today is that the probed reps reach diagnostics, so what
the file is worth per appliance family can be settled on a census rather
than on three reports.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.localthings.const import DOMAIN, SUMMARY_INTERVAL_S
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type import resolve as resolve_registry
from custom_components.localthings.registry.registry import PROBE_HREFS

from .conftest import _load_fridge_resources as _load_fridge


async def _advance_one_cycle(hass):
    """Past one summary interval, so the coordinator polls again.

    `wait_background_tasks` is load-bearing here for the same reason as in
    test_offline_setup: the interval refresh runs as a background task that a
    plain block_till_done does not await.
    """
    from homeassistant.util import dt as dt_util

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=SUMMARY_INTERVAL_S + 1))
    await hass.async_block_till_done(wait_background_tasks=True)


FILE_LIST = "/file/list/vs/0"
FILE_TRANSFER = "/file/transfer/vs/0"

# A real capture off a dishwasher: one 12-byte record, decoding as
# <uint32 LE ts=1788037200><uint32 LE 1631 tenths of a kWh><uint32 LE 0>.
BLOB = bytes.fromhex("504893 6a") + bytes.fromhex("5f060000") + bytes.fromhex("00000000")

PROBE_REPS = {
    FILE_LIST: {
        "x.com.samsung.items": [
            {"x.com.samsung.id": "0", "x.com.samsung.name": "/opt/data/energy.db"},
            {"x.com.samsung.id": "1", "x.com.samsung.name": "/opt/data/hass.db"},
        ]
    },
    FILE_TRANSFER: {
        "x.com.samsung.items": [{"x.com.samsung.name": "/mnt/usage.db", "x.com.samsung.blob": BLOB}]
    },
}


def test_probe_hrefs_are_exactly_the_capabilities_that_asked_for_it():
    assert set(PROBE_HREFS) == {FILE_LIST, FILE_TRANSFER}


def test_probe_hrefs_are_absent_from_every_batch_fixture(all_device_fixtures):
    """The premise of the whole tier. If a batch ever starts carrying one of
    these, it no longer needs probing and this tier's cost is unjustified."""
    for name, resources in all_device_fixtures.items():
        for href in PROBE_HREFS:
            assert href not in resources, f"{name} carries {href} in its batch"


def test_a_probed_href_is_registered_so_it_is_not_a_coverage_gap():
    """Probing folds these into `resources`, where an unregistered href
    becomes an unbound-coverage-gap Repair. Every by_type registry has to
    know about them, which `common.UNIVERSAL` is what guarantees."""
    resources = _load_fridge()
    registry = resolve_registry(resources)
    assert registry is not None
    for href in PROBE_HREFS:
        assert href in registry.capabilities


def test_a_probed_capability_binds_no_entities():
    """Deliberate: the record's third field means something different on
    every family measured so far, so nothing is published yet."""
    resources = _load_fridge()
    registry = resolve_registry(resources)
    for href in PROBE_HREFS:
        for cap in registry.capabilities[href]:
            assert cap.entities == ()


class _ProbeSession:
    """Answers PROBE_HREFS and 4.04s everything else, recording each GET."""

    def __init__(self, answers=None):
        self.answers = PROBE_REPS if answers is None else answers
        self.gets: list[str] = []

    def pace(self):
        pass

    def get(self, path, timeout=None):
        import cbor2

        href = "/" + "/".join(path)
        self.gets.append(href)
        if href in self.answers:
            return 0x45, cbor2.dumps(self.answers[href])
        return 0x84, b""


@pytest.fixture
def probing_entry(hass: HomeAssistant, mock_entry):
    """An entry whose device answers the batch and the probe hrefs.

    Yields `(entry, session, polls)` where `polls` counts summary polls, so a
    cadence assertion can prove the coordinator actually ran the cycles it is
    being credited with rather than passing because nothing happened at all.
    """
    session = _ProbeSession()
    resources = _load_fridge()
    polls: list[int] = []

    def _connect(self):
        self._session = session

    def _poll(self):
        polls.append(1)
        return resources

    with (
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._connect_session",
            _connect,
        ),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once",
            _poll,
        ),
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._close_session"),
    ):
        yield mock_entry, session, polls


async def test_first_discovery_probes_and_caches_the_result(hass, probing_entry):
    entry, session, _polls = probing_entry
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert set(PROBE_HREFS) <= set(session.gets)

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    listed = coordinator.resource(FILE_LIST)["x.com.samsung.items"]
    assert [i["x.com.samsung.name"] for i in listed] == [
        "/opt/data/energy.db",
        "/opt/data/hass.db",
    ]
    # The blob reaches the cache as the bytes the appliance sent; making that
    # safe to serialize onward is registry/encode.py's job, not the probe's.
    served = coordinator.resource(FILE_TRANSFER)["x.com.samsung.items"][0]
    assert served["x.com.samsung.blob"] == BLOB


async def test_a_probed_href_does_not_raise_a_coverage_gap(hass, probing_entry):
    """The failure this would otherwise cause: probing makes two hrefs appear
    in `resources` that were never there before, and an unregistered href
    raises the incomplete-coverage Repair at the user."""
    entry, _session, _polls = probing_entry
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    assert not set(PROBE_HREFS) & set(coordinator._unbound_hrefs)


async def test_a_device_that_404s_the_probe_still_sets_up(hass, mock_entry):
    """Most appliances will answer 4.04, and that must cost nothing."""
    session = _ProbeSession(answers={})
    resources = _load_fridge()

    def _connect(self):
        self._session = session

    with (
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._connect_session",
            _connect,
        ),
        patch(
            "custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once",
            return_value=resources,
        ),
        patch("custom_components.localthings.coordinator.LocalThingsCoordinator._close_session"),
    ):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.bound
    assert coordinator.resource(FILE_TRANSFER) == {}


async def test_the_probe_does_not_run_on_every_summary_poll(hass, probing_entry):
    """A usage file gains one record a day and costs a multi-KB blockwise
    transfer. Riding the 30 s summary interval would be pure waste."""
    entry, session, polls = probing_entry
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert session.gets.count(FILE_TRANSFER) == 1

    for _ in range(3):
        await _advance_one_cycle(hass)

    # The cycles really ran -- without this the assertion below would pass on
    # a coordinator that had simply stopped polling.
    assert len(polls) >= 4
    assert session.gets.count(FILE_TRANSFER) == 1


async def test_the_probe_runs_again_once_its_cycle_count_is_reached(hass, probing_entry):
    """The other half of the throttle: a file that gains a record a day still
    has to be re-read, or the cache freezes at whatever setup happened to
    see."""
    entry, session, polls = probing_entry
    with patch.object(LocalThingsCoordinator, "_PROBE_EVERY_N_CYCLES", 2):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert session.gets.count(FILE_TRANSFER) == 1

        for _ in range(2):
            await _advance_one_cycle(hass)

    assert len(polls) >= 3
    assert session.gets.count(FILE_TRANSFER) == 2
