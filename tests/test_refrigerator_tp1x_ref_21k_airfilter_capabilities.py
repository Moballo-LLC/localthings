"""Tests for the TP1X_REF_21K air-filter-equipped variant (issue #318).

The dump's only unbound href was /filter/airdustfilter/vs/0, this board's
internal deodorizing filter -- same filterUsage/filterStatus field pair as
common.WATER_FILTER, but filterUsage here is already a 0-100 percentage
(no filterCapacity to divide by, unlike airconditioner.AIR_FILTER).
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device


def _bound():
    resources = _load_device("refrigerator_tp1x_ref_21k_airfilter")
    reg = resolve(resources)
    assert reg is not None
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def test_no_unbound_hrefs():
    _, resources = _bound()
    unbound = []
    reg = resolve(resources)
    assert reg is not None
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_air_filter_usage_and_status():
    bound, resources = _bound()
    state = flatten(bound, resources)
    assert state["air_filter_usage"] == 100
    assert state["air_filter_status"] == "wash"
