"""Tests for the TP1X_DA_AC_DHM_01001_0000 dehumidifier revision
(issues #271/#231, model AY70H18100GTD).

Both issues submitted the identical dump: same board, same two previously
unbound hrefs (/display/vs/0, /watertank/lighting/vs/0). Confirms both the
new capabilities and the /oic/d device-type routing added alongside them.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import (
    for_device_by_model,
    for_device_by_oic_type,
)
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc, SwitchDesc
from tests.conftest import _load_device


def _dehumidifier():
    resources = _load_device("dehumidifier_tp1x_dhm01001")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def _bound():
    reg, resources = _dehumidifier()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state():
    bound, resources = _bound()
    return flatten(bound, resources)


def _desc(key):
    bound, _ = _bound()
    return next(b.desc for b in bound if b.desc.key == key)


def test_model_resolves_to_dehumidifier_registry():
    reg, _ = _dehumidifier()
    assert reg is not None and reg.name == "dehumidifier"


def test_oic_device_type_resolves_to_dehumidifier_registry():
    reg = for_device_by_oic_type(("oic.wk.d", "x.com.st.d.dehumidifier"))
    assert reg is not None and reg.name == "dehumidifier"


def test_no_unbound_hrefs():
    reg, resources = _dehumidifier()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_display_switch_present_and_reads_on():
    state = _state()
    assert state["display"] is True


def test_watertank_light_switch_write_contract():
    desc = _desc("watertank_light")
    assert isinstance(desc, SwitchDesc)
    path, body = desc.write_fn("On", {})
    assert path == ["watertank", "lighting", "vs", "0"]
    assert body == {"status": "On"}


def test_watertank_light_color_options_come_from_live_supported_list():
    desc = _desc("watertank_light_color")
    assert isinstance(desc, SelectDesc)
    assert desc.options_field == "colorSupportedList"
    path, body = desc.write_fn("Green", {})
    assert path == ["watertank", "lighting", "vs", "0"]
    assert body == {"colorOption": "Green"}


def test_watertank_light_brightness_options_come_from_live_supported_list():
    desc = _desc("watertank_light_brightness")
    assert isinstance(desc, SelectDesc)
    assert desc.options_field == "modeSupportedList"
    path, body = desc.write_fn("Low", {})
    assert path == ["watertank", "lighting", "vs", "0"]
    assert body == {"mode": "Low"}


def test_watertank_full_alarm_status_exposed_read_only():
    state = _state()
    assert state["watertank_full_alarm_status"] == "On"
