"""Tests for dryer Drum Clean+ maintenance tracking (issue #258).

Same DrumCleanProposal_/WashingTimes_/DrumCleanLog_ options[] tokens as
washer.py's issue #9 feature, shared via laundry.drum_clean_cycles_remaining/
drum_clean_last_cleaned -- except DrumCleanLog_ on a dryer is a '|'-joined
history of every past clean rather than one bare timestamp.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities.laundry import (
    drum_clean_cycles_remaining,
    drum_clean_last_cleaned,
)
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device


def _dryer(fixture_name):
    resources = _load_device(fixture_name)
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def _state(fixture_name):
    reg, resources = _dryer(fixture_name)
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_no_unbound_hrefs():
    reg, resources = _dryer("dryer_tp1_21_drum_clean")
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_confirmed_dump_reports_cycles_remaining_and_last_cleaned():
    """DA_WM_TP1_21_COMMON/DV9400B reports DrumCleanProposal_25/
    WashingTimes_25 (0 cycles remaining -- due now) and a ten-entry
    '|'-joined DrumCleanLog_, most recent 2026-05-24T21:38:08."""
    state = _state("dryer_tp1_21_drum_clean")
    assert state["drum_clean_cycles_remaining"] == 0
    assert str(state["drum_clean_last_cleaned"]) == "2026-05-24 21:38:08+00:00"


def test_dump_with_no_drum_clean_data_reports_neither_entity():
    """The default dryer fixture only has 'DrumCleanLog_Empty', no
    DrumCleanProposal_/WashingTimes_ tokens at all -- both entities stay
    gated off rather than reporting a fabricated 0 or an unparseable date."""
    state = _state("dryer")
    assert "drum_clean_cycles_remaining" not in state
    assert "drum_clean_last_cleaned" not in state


def test_multi_entry_drum_clean_log_takes_the_last_timestamp():
    rep = {
        "x.com.samsung.da.options": [
            "DrumCleanLog_2023-12-03T22:07:18|2024-02-09T15:18:59|2026-05-24T21:38:08",
        ]
    }
    assert str(drum_clean_last_cleaned(rep)) == "2026-05-24 21:38:08+00:00"


def test_single_entry_drum_clean_log_matches_washer_shape():
    """A bare, no-'|' value (the washer's own shape) parses identically."""
    rep = {"x.com.samsung.da.options": ["DrumCleanLog_2024-02-09T15:18:59"]}
    assert str(drum_clean_last_cleaned(rep)) == "2024-02-09 15:18:59+00:00"


def test_empty_drum_clean_log_reports_none():
    rep = {"x.com.samsung.da.options": ["DrumCleanLog_Empty"]}
    assert drum_clean_last_cleaned(rep) is None


def test_cycles_remaining_never_negative():
    rep = {
        "x.com.samsung.da.options": [
            "DrumCleanProposal_25",
            "WashingTimes_40",
        ]
    }
    assert drum_clean_cycles_remaining(rep) == 0
