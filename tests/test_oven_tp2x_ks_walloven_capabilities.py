"""Tests for the TP2X_DA-KS-WALLOVEN-000002 steam oven (issue #300).

The reporter's "not fully supported" notification was /diagnosis/vs/0,
now covered via dishwasher.DIAGNOSIS. Their "doesn't respond to commands"
complaint is broader: oven.py's own module docstring already documents
setpoint/cook-time/mode writes as unproven on this local-OCF firmware, and
this board's options[] carries no UpperLamp_ token at all -- confirming
`lamp` was previously a phantom, write-does-nothing switch here (same gap
issue #183 fixed for fast_preheat/natural_steam on a different board).
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_oic_type, resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

_DEVICE_TYPES = ("oic.wk.d", "oic.d.oven")


def _bound():
    resources = _load_device("oven_tp2x_ks_walloven")
    reg = resolve(resources, device_types=_DEVICE_TYPES)
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def test_oic_device_type_resolves_to_oven_registry():
    reg = for_device_by_oic_type(_DEVICE_TYPES)
    assert reg is not None and reg.name == "oven"


def test_no_unbound_hrefs():
    resources = _load_device("oven_tp2x_ks_walloven")
    reg = resolve(resources, device_types=_DEVICE_TYPES)
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_lamp_not_bound_without_an_upperlamp_token():
    """This board's options[] has no UpperLamp_ entry -- confirms the new
    exists_fn keeps `lamp` from registering a switch that would always
    read Off and never actually do anything on write."""
    bound, resources = _bound()
    state = flatten(bound, resources)
    assert "lamp" not in state


def test_sound_and_energy_saving_bound_from_real_options_tokens():
    """Unlike lamp, this board's options[] does carry Sound_On and
    EnergySaving_On -- these switches should reflect real state."""
    bound, resources = _bound()
    state = flatten(bound, resources)
    assert state["sound"] is True
    assert state["energy_saving"] is True
