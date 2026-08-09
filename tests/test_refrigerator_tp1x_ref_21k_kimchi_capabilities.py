"""Kimchi-refrigerator Auto Door Open variant (issue #328,
oic.d.krefrigerator device type -- TP1X_REF_21K board, single kimchi
compartment). Same STATUS_LOCK/AUTO_DOOR_TIMER shape as the regular
TP1X_REF_21K, at the kimchi-specific /autodoor/kimchi/vs/0 declaration
href.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_oic_type, resolve
from custom_components.localthings.registry.capabilities import fridge
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

FIXTURE = "refrigerator_tp1x_ref_21k_kimchi"
DEVICE_TYPES = ("oic.wk.d", "oic.d.krefrigerator")


def _fridge():
    resources = _load_device(FIXTURE)
    reg = resolve(resources, device_types=DEVICE_TYPES)
    return reg, resources


def _state():
    reg, resources = _fridge()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_resolves_via_oic_type():
    reg = for_device_by_oic_type(DEVICE_TYPES)
    assert reg is not None and reg.name == "refrigerator"


def test_resolves_to_refrigerator_registry():
    reg, _ = _fridge()
    assert reg is not None and reg.name == "refrigerator"


def test_no_unbound_hrefs():
    reg, resources = _fridge()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_auto_door_opener_present():
    state = _state()
    assert "auto_door_opener" in state
    assert "auto_door_timer" in state


def test_auto_door_kimchi_variant_href_bound_with_no_entities():
    assert fridge.AUTO_DOOR_KIMCHI.entities == ()
