"""Tests for the DA_DF_TP2_20_COMMON AirDresser (model DF9500A, issue #157).

A different board generation than issue #162's DA_DF_A51_20_COMMON, routed
into the same air_dresser registry (both carry the '_DF_' modelNum token).
Exercises the two things this board does differently: a populated
/wm/editcourse/vs/0 (so cycle_options() never needs the supportedOptions
fallback #162 relies on) and the new /airdresseroption/sanitize/vs/0
capability #162's dump doesn't report at all.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import air_dresser, for_device_by_model
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SwitchDesc
from tests.conftest import _load_device


def _air_dresser():
    resources = _load_device("air_dresser_tp2_20")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )
    return reg, resources


def _state():
    reg, resources = _air_dresser()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_resolves_to_air_dresser_registry():
    reg, _ = _air_dresser()
    assert reg is not None and reg.name == "air_dresser"


def test_no_unbound_hrefs():
    """Confirms /st/airdressercourse/vs/0 and /airdresseroption/sanitize/vs/0
    -- the two hrefs this board reports that #162's dump doesn't -- are
    covered (ignored.py and AIR_DRESSER_SANITIZE respectively)."""
    reg, resources = _air_dresser()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_course_options_come_from_edit_course_list_not_the_fallback():
    """Unlike issue #162's board, this one populates /wm/editcourse/vs/0
    directly, so cycle_options() should never reach the
    supportedOptions-decode fallback."""
    from custom_components.localthings.registry.capabilities.laundry import cycle_options

    _, resources = _air_dresser()
    codes = cycle_options(resources)
    assert codes[:3] == ["22", "23", "0C"]
    assert codes == cycle_options({"/wm/editcourse/vs/0": resources["/wm/editcourse/vs/0"]})


def test_course_select_reads_current_selection():
    state = _state()
    assert state["cycle"] == "22"


def test_course_translation_key_falls_back_to_cycle_for_unidentified_table():
    """Table_00's course codes aren't identified yet, so the select's
    translation_key resolves to the generic 'cycle' catalog entry rather
    than a table-specific one that doesn't exist."""
    _, resources = _air_dresser()
    desc = air_dresser.REGISTRY.capabilities["/course/vs/0"][0].entities[0]
    assert desc.translation_key(resources) == "cycle"


def test_sanitize_present_and_toggles():
    state = _state()
    assert state["sanitize"] is False

    desc = next(
        e
        for e in air_dresser.REGISTRY.capabilities["/airdresseroption/sanitize/vs/0"][0].entities
        if e.key == "sanitize" and isinstance(e, SwitchDesc)
    )
    assert desc.write_fn is not None
    result = desc.write_fn("On", {})
    assert result is not None
    path, body = result
    assert path == ["airdresseroption", "sanitize", "vs", "0"]
    assert body == {"x.com.samsung.da.sanitize": "On"}
    assert desc.write_fn("Sparkle", {}) is None
