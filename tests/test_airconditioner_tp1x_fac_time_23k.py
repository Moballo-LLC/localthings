"""Tests for the TP1X_FAC_TIME_23K air conditioner (issue #270).

A 2-in-1 system (x.com.samsung.da.numofsubdevice='2') whose UUID-prefixed
sibling materializes via the flat fallback (issue #205) -- unrelated to the
three capability gaps this fixture actually locks in (UV LED, ventilation
alarm, and the unmodeled PM1 filter), which live entirely on canonical
hrefs already exercised by the master's own resources.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SwitchDesc
from tests.conftest import _load_device


def _airconditioner():
    resources = _load_device("airconditioner_tp1x_fac_time_23k")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def _bound():
    reg, resources = _airconditioner()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state():
    bound, resources = _bound()
    return flatten(bound, resources)


def _desc(key):
    bound, _ = _bound()
    return next(b.desc for b in bound if b.desc.key == key)


def test_model_resolves_to_airconditioner_registry():
    reg, _ = _airconditioner()
    assert reg is not None and reg.name == "airconditioner"


def test_no_unbound_hrefs():
    reg, resources = _airconditioner()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_uv_led_switch_reads_on_and_has_write_contract():
    state = _state()
    assert state["uv_led"] is True
    desc = _desc("uv_led")
    assert isinstance(desc, SwitchDesc)
    path, body = desc.write_fn("Off", {})
    assert path == ["uvled", "vs", "0"]
    assert body == {"x.com.samsung.da.modes": "Off"}


def test_ventilation_alarm_switch_reads_off_and_has_write_contract():
    state = _state()
    assert state["ventilation_alarm"] is False
    desc = _desc("ventilation_alarm")
    assert isinstance(desc, SwitchDesc)
    path, body = desc.write_fn("On", {})
    assert path == ["ventilation", "setting", "vs", "0"]
    assert body == {"alarm": "On"}


def test_pm1_filter_has_no_live_fields_so_no_state_is_reported():
    """This dump's /filter/airdustPM1filter/vs/0 has only
    filterCapacity/filterCapacityUnit/filterResetType -- no filterUsage or
    filterStatus -- so every AIR_FILTER_PM1 entity's exists_fn gates it out.
    The href still covers (see test_no_unbound_hrefs above) without
    fabricating a reading from nothing (the 'don't guess' rule); contrast
    with test_airconditioner_cac.py, whose dump has live data on the same
    href and does get real air_filter_pm1_* state."""
    state = _state()
    assert not any(k.startswith("air_filter_pm1_") for k in state)
