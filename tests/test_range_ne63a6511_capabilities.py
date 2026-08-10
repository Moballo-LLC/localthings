"""Tests for the NE63A6511SS/AA range (issue #138) -- same no-/information/vs/0,
no-burner-status shape as issue #74's NE63B8411SS, confirming the existing
range_no_info detection path still resolves this model with zero unbound
hrefs, plus oven.OVEN_MODE reading this dump's ConvectionRoast/KeepWarm/
BreadProof/AirFryer/Dehydrate/SelfClean/SteamClean modes live from its own
/mode/vs/0 supportedModes rather than needing them hardcoded."""

from typing import cast

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_resources
from custom_components.localthings.registry.capabilities import oven
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc
from tests.conftest import _load_device


def _range():
    resources = _load_device("range_ne63a6511")
    reg = for_device_by_resources(resources)
    return reg, resources


def _state():
    reg, resources = _range()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_resolves_to_range_registry():
    reg, _ = _range()
    assert reg is not None and reg.name == "range"


def test_no_unbound_hrefs():
    reg, resources = _range()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_entities_present():
    state = _state()
    for key in (
        "power_switch",
        "oven_setpoint",
        "current_temp_c",
        "oven_mode",
        "machine_state",
        "door_open",
        "cloud_connected",
        "child_lock",
        "cooktop_running_state",
        "warming_center_state",
    ):
        assert key in state, key


def test_oven_mode_accepts_this_devices_supported_modes():
    """This dump's /mode/vs/0 supportedModes reports ConvectionRoast,
    KeepWarm, BreadProof, AirFryer, Dehydrate, SelfClean, and SteamClean --
    none of which were in the static oven._OVEN_MODES fallback, so a
    hardcoded write-validation list would've silently rejected them even
    though the device itself advertises them. write_fn reads supportedModes
    off the live rep (the same one it's called with in production -- see
    coordinator.py's async_send_command), so this passes without needing
    those modes added to any Python list."""
    _, resources = _range()
    live_rep = resources["/mode/vs/0"]
    desc = cast(SelectDesc, oven.OVEN_MODE.entities[0])
    assert desc.options is not None
    assert desc.write_fn is not None
    assert desc.options(resources) == live_rep["x.com.samsung.da.supportedModes"]
    for mode in (
        "ConvectionRoast",
        "KeepWarm",
        "BreadProof",
        "AirFryer",
        "Dehydrate",
        "SelfClean",
        "SteamClean",
    ):
        result = desc.write_fn(mode, live_rep)
        assert result is not None
        path, body = result
        assert path == ["mode", "vs", "0"]
        assert body["x.com.samsung.da.modes"] == [mode]
