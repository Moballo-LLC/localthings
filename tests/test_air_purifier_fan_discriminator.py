"""Tests for the /mode/vs/0 match_fn discriminator in capabilities/
air_purifier.py (issue #130).

`_has_top_level_modes` decides whether a given air-purifier dump's shared
/mode/vs/0 href binds to the older ARTIK051_TVTL family's MODE capability
(packed options[] scheme) or the newer TP1X_DA-AC-AIR family's FAN
capability (modes/supportedModes reported directly). Pure function, no
coordinator/entity dependency needed to test it directly -- same rationale
as test_climate_wind_oscillation_fallback.py's `_oscillation_swing`.
"""

from custom_components.localthings.registry.capabilities.air_purifier import (
    _has_top_level_modes,
)


def test_new_family_with_supported_modes_list_matches():
    rep = {
        "x.com.samsung.da.modes": ["Smart"],
        "x.com.samsung.da.supportedModes": ["Smart", "Max", "Mid", "WindFree", "Sleep"],
    }
    assert _has_top_level_modes(rep, {}) is True


def test_old_family_with_no_supported_modes_field_does_not_match():
    """The ARTIK051_TVTL family's /mode/vs/0 packs everything into
    options[] (Light_On, Comode_Off, ...) and has no top-level
    supportedModes at all."""
    rep = {
        "x.com.samsung.da.options": ["Light_On", "Comode_Off", "OptionCode_60282"],
    }
    assert _has_top_level_modes(rep, {}) is False


def test_non_list_supported_modes_does_not_match():
    assert _has_top_level_modes({"x.com.samsung.da.supportedModes": "Smart"}, {}) is False


def test_empty_rep_does_not_match():
    assert _has_top_level_modes({}, {}) is False
