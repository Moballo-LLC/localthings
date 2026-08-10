"""Tests for the AVT-WW-TP1-23-AXX500 air-purifier profile (issue #190).

Next-gen BESPOKE Cube Air board -- same resource surface as the
A-VTWW-TP2-21-COMMON family (issue #151), but the '-WW-' delimiter now falls
one letter to the left ('A-VTWW-' -> 'AVT-WW-'), splitting into an 'AVT'/'WW'
token pair the existing 'VTWW' whole-token entry can't see. The reporter's
diagnostics showed device_type 'unknown' with empty oneUiVersion and every
resource unbound -- once routed to the existing air_purifier registry, every
resource here binds with zero gaps.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device


def _resources():
    return _load_device("air_purifier_avt_ww")


def _reg(resources):
    info = resources["/information/vs/0"]
    return for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )


def test_resolves_to_air_purifier_registry():
    assert _reg(_resources()).name == "air_purifier"


def test_no_unbound_hrefs():
    resources = _resources()
    reg = _reg(resources)
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_wind_strength_fan_preset_names():
    """This dump's /wind/strength/vs/0 reports the same numeric-code +
    modesName shape as the original VTWW fixture (SMART/MAX/WINDFREE/Sleep),
    confirming the shared WIND_STRENGTH_FAN capability applies unchanged."""
    resources = _resources()
    reg = _reg(resources)
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, resources)
    assert state["wind_strength_fan"] == "87"
    wind = resources["/wind/strength/vs/0"]
    assert wind["x.com.samsung.da.modesName"] == ["SMART", "MAX", "WINDFREE", "Sleep"]


def test_air_quality_and_filter_sensors_present():
    resources = _resources()
    reg = _reg(resources)
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, resources)
    assert state["dust"] == 10
    assert state["fine_dust"] == 9
    assert state["odor"] == 1
    assert state["hepa_filter_usage"] == 30
    assert state["hepa_filter_status"] == "normal"
