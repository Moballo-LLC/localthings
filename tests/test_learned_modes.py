"""Tests for issue #327: remembering a mode the device reports itself in
but never advertises in supportedModes.

The reporters' unit (ARTIK051_PRAC_20K) isn't in the corpus, so the
scenario runs on an existing fixture whose /mode/convenient/vs/0 genuinely
lacks Quiet -- airconditioner_tp1x_fac_time_23k, supportedModes [Off,
Sleep, Nano, NanoSleep] -- by applying the rep their firmware sends: a
current mode of 'Quiet' with that same supportedModes list unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.climate import LocalThingsClimate
from custom_components.localthings.const import (
    CONF_LEARN_MODES,
    CONF_LEARNED_MODES,
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.learned import (
    LEARNABLE,
    MODES_FIELD,
    SUPPORTED_FIELD,
    LearnedModes,
)
from custom_components.localthings.registry.entities import ClimateDesc
from tests.test_subdevice_discovery import ENTRY_DATA, _discover

FIXTURE = "airconditioner_tp1x_fac_time_23k"
CONVENIENT = "/mode/convenient/vs/0"
ADVERTISED = ["Off", "Sleep", "Nano", "NanoSleep"]

# What the reporters' firmware sends while the unit is in Quiet: the mode
# named as current, and a supported list that still omits it. `modes` is a
# bare string here, matching the dump in the issue.
QUIET_REP = {MODES_FIELD: "Quiet", SUPPORTED_FIELD: ADVERTISED}


# ---------------------------------------------------------------------------
# The store itself
# ---------------------------------------------------------------------------


def test_learns_a_current_mode_missing_from_the_supported_list():
    learned = LearnedModes()
    assert learned.observe(CONVENIENT, CONVENIENT, QUIET_REP) is True
    assert learned.codes(CONVENIENT) == ["Quiet"]


def test_relearning_the_same_mode_is_not_a_change():
    """The device reports the same rep on every poll while it sits in the
    mode, so only the first one may report back as something to persist."""
    learned = LearnedModes()
    assert learned.observe(CONVENIENT, CONVENIENT, QUIET_REP) is True
    assert learned.observe(CONVENIENT, CONVENIENT, QUIET_REP) is False
    assert learned.codes(CONVENIENT) == ["Quiet"]


def test_an_advertised_mode_is_never_learned():
    learned = LearnedModes()
    rep = {MODES_FIELD: "Sleep", SUPPORTED_FIELD: ADVERTISED}
    assert learned.observe(CONVENIENT, CONVENIENT, rep) is False
    assert learned.codes(CONVENIENT) == []


def test_a_rep_with_no_supported_list_teaches_nothing():
    """ "Missing from the list" is only meaningful against a list that
    exists -- a board publishing none would otherwise get an option list
    invented out of whatever it happened to be doing."""
    learned = LearnedModes()
    assert learned.observe(CONVENIENT, CONVENIENT, {MODES_FIELD: "Quiet"}) is False
    assert learned.codes(CONVENIENT) == []


def test_an_href_outside_the_allowlist_learns_nothing():
    """The guard that keeps this feature off resources whose current value
    isn't a selectable option: an oven idling in 'NoOperation' reports
    exactly this shape on /mode/vs/0, and remembering it would put a
    permanent junk option in that unit's cook-mode select."""
    learned = LearnedModes()
    rep = {MODES_FIELD: "NoOperation", SUPPORTED_FIELD: ["Bake", "Broil"]}
    assert learned.observe("/mode/vs/0", "/mode/vs/0", rep) is False
    assert learned.snapshot() == {}


def test_two_subdevices_learn_separately():
    """Keyed by the actual on-the-wire href (issue #177), so a composite
    appliance's second indoor unit doesn't inherit the first's gap."""
    learned = LearnedModes()
    learned.observe(CONVENIENT, "/mode/convenient/vs/1", QUIET_REP)
    assert learned.codes("/mode/convenient/vs/1") == ["Quiet"]
    assert learned.codes(CONVENIENT) == []


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "not-a-dict",
        {"/mode/convenient/vs/0": "not-a-dict"},
        {"/mode/convenient/vs/0": {SUPPORTED_FIELD: [""]}},
        {"/mode/convenient/vs/0": {SUPPORTED_FIELD: [1, 2]}},
    ],
)
def test_malformed_stored_data_restores_as_empty(stored):
    """It round-trips through the config entry as plain JSON, so a
    hand-edited .storage file must not be able to break setup."""
    assert LearnedModes(stored).snapshot() == {}


def test_well_formed_stored_data_restores():
    learned = LearnedModes({CONVENIENT: {SUPPORTED_FIELD: ["Quiet"]}})
    assert learned.codes(CONVENIENT) == ["Quiet"]


def test_clear_reports_whether_there_was_anything_to_forget():
    learned = LearnedModes({CONVENIENT: {SUPPORTED_FIELD: ["Quiet"]}})
    assert learned.clear() is True
    assert learned.snapshot() == {}
    assert learned.clear() is False


def test_every_learnable_href_is_one_climate_resolves():
    """climate._supported is the only consumer that unions learned codes
    in today, so an href added to LEARNABLE that climate never reads would
    be learned, persisted, and never offered anywhere."""
    from custom_components.localthings.climate import (
        CONVENIENT_HREF,
        MODE_HREF,
        WIND_DIRECTION_HREF,
        WIND_STRENGTH_HREF,
    )

    assert set(LEARNABLE) <= {MODE_HREF, WIND_STRENGTH_HREF, WIND_DIRECTION_HREF, CONVENIENT_HREF}


# ---------------------------------------------------------------------------
# End to end: device reports it -> HA offers it -> the entry remembers it
# ---------------------------------------------------------------------------


def _entry(hass: HomeAssistant, data: dict | None = None, options: dict | None = None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, **(data or {})},
        options=options or {},
        unique_id="localthings_LEARNED-TEST",
    )
    entry.add_to_hass(hass)
    return entry


async def _flush(hass: HomeAssistant) -> None:
    """Let a persist scheduled from the applying thread land.

    The coordinator marshals it with hass.add_job (the same way it
    marshals its state push), which goes through call_soon_threadsafe --
    async_block_till_done alone doesn't pick that up in this harness.
    """
    await asyncio.sleep(0)
    await hass.async_block_till_done()


async def _climate(hass: HomeAssistant, entry) -> tuple[LocalThingsCoordinator, Any]:
    coordinator = LocalThingsCoordinator(hass, entry)
    await _discover(coordinator, FIXTURE)
    bound = next(b for b in coordinator.bound if isinstance(b.desc, ClimateDesc))
    return coordinator, LocalThingsClimate(coordinator, bound)


async def test_the_fixture_really_does_not_advertise_quiet(hass: HomeAssistant):
    """Guard for every assertion below: they'd all pass vacuously against a
    unit that advertised Quiet in the first place."""
    _, entity = await _climate(hass, _entry(hass))
    assert "quiet" not in entity.preset_modes


async def test_a_reported_mode_becomes_a_preset(hass: HomeAssistant):
    coordinator, entity = await _climate(hass, _entry(hass))
    coordinator._observe.apply(CONVENIENT, QUIET_REP, source="poll")

    assert entity.preset_mode == "quiet"
    assert "quiet" in entity.preset_modes
    # Alongside, not instead of, what the device does advertise.
    assert "sleep" in entity.preset_modes


async def test_a_learned_preset_is_writable(hass: HomeAssistant, monkeypatch):
    """The reason the union belongs in _supported: async_set_preset_mode
    reverse-resolves the device code from the same list, so read and write
    are fixed by one change."""
    coordinator, entity = await _climate(hass, _entry(hass))
    coordinator._observe.apply(CONVENIENT, QUIET_REP, source="poll")

    sent: list = []

    async def _record(bound, payload):
        sent.append(payload)

    monkeypatch.setattr(coordinator, "async_send_command", _record)
    await entity.async_set_preset_mode("quiet")
    assert sent == [("preset", "Quiet")]


async def test_learning_persists_onto_the_config_entry(hass: HomeAssistant):
    entry = _entry(hass)
    coordinator, _ = await _climate(hass, entry)
    coordinator._observe.apply(CONVENIENT, QUIET_REP, source="poll")
    await _flush(hass)

    assert entry.data[CONF_LEARNED_MODES] == {CONVENIENT: {SUPPORTED_FIELD: ["Quiet"]}}


async def test_a_learned_preset_survives_a_restart(hass: HomeAssistant):
    """The point of persisting: the unit only names Quiet while it is in
    Quiet, so a restart in any other mode would otherwise lose it until
    someone reached for the physical remote again."""
    entry = _entry(hass, data={CONF_LEARNED_MODES: {CONVENIENT: {SUPPORTED_FIELD: ["Quiet"]}}})
    _, entity = await _climate(hass, entry)

    # Nothing applied this run -- the fixture's own rep says Off, and its
    # supportedModes still omits Quiet.
    assert entity.preset_mode == "none"
    assert "quiet" in entity.preset_modes


async def test_an_optimistic_write_teaches_nothing(hass: HomeAssistant):
    """An optimistic cache entry is the value this integration just wrote,
    not something the device reported."""
    coordinator, _ = await _climate(hass, _entry(hass))
    coordinator._observe.apply(CONVENIENT, QUIET_REP, source="optimistic")

    assert coordinator.learned_snapshot() == {}


async def test_a_partial_notify_still_learns(hass: HomeAssistant):
    """An OBSERVE notify can carry `modes` alone (issue #27). Learning sees
    the merged rep, so the supported list from the last full poll is still
    there to judge it against."""
    coordinator, entity = await _climate(hass, _entry(hass))
    coordinator._observe.apply(CONVENIENT, {MODES_FIELD: "Quiet"}, source="observe")

    assert "quiet" in entity.preset_modes


async def test_the_option_turns_off_both_halves(hass: HomeAssistant):
    """Off means the stock list, immediately -- nothing new is learned and
    nothing already learned is offered."""
    entry = _entry(
        hass,
        data={CONF_LEARNED_MODES: {CONVENIENT: {SUPPORTED_FIELD: ["Smart"]}}},
        options={CONF_LEARN_MODES: False},
    )
    coordinator, entity = await _climate(hass, entry)
    coordinator._observe.apply(CONVENIENT, QUIET_REP, source="poll")
    await _flush(hass)

    assert "smart" not in entity.preset_modes
    assert "quiet" not in entity.preset_modes
    # Kept, not discarded -- turning the option back on restores it, and
    # nothing new was written while it was off.
    assert coordinator.learned_snapshot() == {CONVENIENT: {SUPPORTED_FIELD: ["Smart"]}}
    assert entry.data[CONF_LEARNED_MODES] == {CONVENIENT: {SUPPORTED_FIELD: ["Smart"]}}


async def test_forgetting_clears_the_store_and_the_entry(hass: HomeAssistant):
    entry = _entry(hass, data={CONF_LEARNED_MODES: {CONVENIENT: {SUPPORTED_FIELD: ["Quiet"]}}})
    coordinator, entity = await _climate(hass, entry)

    coordinator.forget_learned_modes()
    await _flush(hass)

    assert "quiet" not in entity.preset_modes
    assert entry.data[CONF_LEARNED_MODES] == {}


async def test_diagnostics_report_what_was_learned(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """Kept out of the `resources` dump on purpose -- that stays exactly
    what the device said, so a triager can still see the gap."""
    from custom_components.localthings.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = _entry(hass)
    coordinator, _ = await _climate(hass, entry)
    coordinator._observe.apply(CONVENIENT, QUIET_REP, source="poll")
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    diag = await async_get_config_entry_diagnostics(hass, cast(Any, entry))

    assert diag["learned_modes"] == {
        "enabled": True,
        "codes": {CONVENIENT: {SUPPORTED_FIELD: ["Quiet"]}},
    }
    assert diag["resources"][CONVENIENT][SUPPORTED_FIELD] == ADVERTISED
