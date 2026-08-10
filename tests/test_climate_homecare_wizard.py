"""Tests for issue #235: HOMECARE_WIZARD_V2 false-positive unmapped-mode
warning, and the entity_id-is-None logging gap it surfaced.

Uses the pre-existing airconditioner_tp2x_rac_20k fixture (issue #37) --
its /mode/vs/0 already carries the exact reporter-confirmed shape:
supportedModes includes 'HOMECARE_WIZARD_V2' alongside the real modes, the
current mode ('Cool') never is it, and /configuration/vs/0's
airconOptionList lists it alongside other plain capability flags.
"""

from __future__ import annotations

import logging

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant

from custom_components.localthings.climate import _NON_HVAC_OPTION_CODES, LocalThingsClimate
from custom_components.localthings.registry.entities import ClimateDesc
from tests.test_subdevice_discovery import _coordinator, _discover


@pytest.fixture
async def climate(hass: HomeAssistant):
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_tp2x_rac_20k")
    bound = next(b for b in coordinator.bound if isinstance(b.desc, ClimateDesc))
    return LocalThingsClimate(coordinator, bound)


def test_homecare_wizard_v2_excluded_from_hvac_modes(climate):
    """supportedModes is ['Auto', 'Cool', 'Dry', 'Wind', 'Heat',
    'HOMECARE_WIZARD_V2'] -- five real modes plus the option flag. Every
    real mode maps through; the flag contributes no extra (or bogus)
    HVACMode entry."""
    modes = climate.hvac_modes
    assert set(modes) == {
        HVACMode.OFF,
        HVACMode.AUTO,
        HVACMode.COOL,
        HVACMode.DRY,
        HVACMode.HEAT,
        HVACMode.FAN_ONLY,
    }


def test_homecare_wizard_v2_reports_no_warning(climate, caplog):
    """Reading hvac_modes must not emit the issue #93 unmapped-mode warning
    for HOMECARE_WIZARD_V2 -- that's the actual bug report: every owner of
    an affected unit saw this fire on every start."""
    with caplog.at_level(logging.WARNING):
        _ = climate.hvac_modes
    assert "HOMECARE_WIZARD_V2" not in caplog.text


def test_hvac_mode_is_off_when_powered_off(climate):
    """The fixture's power is off -- hvac_mode short-circuits to OFF before
    ever reading /mode/vs/0's 'Cool', matching real behavior (mode is only
    meaningful while the unit is on)."""
    assert climate.hvac_mode == HVACMode.OFF


def test_homecare_wizard_v2_is_the_known_non_hvac_set():
    assert "HOMECARE_WIZARD_V2" in _NON_HVAC_OPTION_CODES


def test_warn_unmapped_falls_back_to_unique_id_when_entity_id_unset(climate, caplog):
    """Issue #235's secondary bug: _warn_unmapped logged 'None: device mode
    ...' because entity_id is unset until the entity is added to hass --
    exactly the state a fixture-backed entity is in here, without ever
    calling async_add_entities. unique_id is set eagerly in __init__
    (entity.py), so it must appear in the log line instead of a bare
    'None'."""
    assert climate.entity_id is None
    assert climate.unique_id is not None
    with caplog.at_level(logging.WARNING):
        climate._warn_unmapped("/mode/vs/0", "TotallyUnknownCode")
    assert "None:" not in caplog.text
    assert climate.unique_id in caplog.text
