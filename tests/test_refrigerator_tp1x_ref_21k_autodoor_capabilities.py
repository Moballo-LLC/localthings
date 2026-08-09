"""Auto Door Open (issue #328, TP1X_REF_21K single-freezer-door variant).

`/status/lock/vs/0`'s ado.devicecontrol switch was already modeled
(STATUS_LOCK.auto_door_opener); this dump adds the paired voice-feedback
toggle, the door-hold timer, and the per-variant capability declaration
href -- see fridge.py's AUTO_DOOR_TIMER/AUTO_DOOR_VARIANT comments.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities import fridge
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

FIXTURE = "refrigerator_tp1x_ref_21k_autodoor"


def _fridge():
    resources = _load_device(FIXTURE)
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )
    return reg, resources


def _state():
    reg, resources = _fridge()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_resolves_to_refrigerator_registry():
    reg, _ = _fridge()
    assert reg is not None and reg.name == "refrigerator"


def test_no_unbound_hrefs():
    reg, resources = _fridge()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_auto_door_voice_control_reads_status_lock():
    state = _state()
    assert state["auto_door_opener"] is False
    assert state["auto_door_voice_control"] is False
    # This variant's dump has no ado.soundcontrol field -- gated absent.
    assert "auto_door_sound_control" not in state


def test_auto_door_timer_reads_its_own_options():
    desc = next(e for e in fridge.AUTO_DOOR_TIMER.entities if e.key == "auto_door_timer")
    rep = {
        "x.com.samsung.da.time.desired": "1",
        "x.com.samsung.da.time.supportedOptions": ["1", "2", "3", "4", "5", "6"],
    }
    assert desc.value_fn(rep["x.com.samsung.da.time.desired"]) == "1"
    assert desc.options_field == "x.com.samsung.da.time.supportedOptions"
    assert desc.write_fn is not None
    assert desc.write_fn("3", rep) == (
        ["autodoor", "timer", "vs", "0"],
        {"x.com.samsung.da.time.desired": "3"},
    )


def test_auto_door_single_variant_href_bound_with_no_entities():
    """/autodoor/single/vs/0 only ever declares openOptions=['Single'] --
    no paired current/desired field to expose, so it's coverage-only via
    the shared AUTO_DOOR_VARIANT pattern cap, not an entry of its own."""
    assert fridge.AUTO_DOOR_VARIANT.entities == ()
    _reg, resources = _fridge()
    assert fridge.AUTO_DOOR_VARIANT.match_fn(resources["/autodoor/single/vs/0"], resources)
