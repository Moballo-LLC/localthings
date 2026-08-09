"""Wine-cellar refrigerator variant (issue #328, x.com.st.d.winecellar
device type -- RW33C99B1TFG-class, TP1X_REF_21K board). Auto Door Open's
winecellar-specific declaration href, a deodorizing filter reported at a
different href than the regular AIR_FILTER, a multi-compartment pantry
select, and a table-revision info resource -- all new coverage this dump
surfaced.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_oic_type, resolve
from custom_components.localthings.registry.capabilities import fridge
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc
from tests.conftest import _load_device

FIXTURE = "refrigerator_winecellar"
DEVICE_TYPES = ("oic.wk.d", "x.com.st.d.winecellar")


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


def test_no_unbound_hrefs():
    reg, resources = _fridge()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_auto_door_sound_control_present_here_only():
    """This variant's dump is the only one that carries
    ado.soundcontrol -- the other TP1X_REF_21K auto-door fixtures don't."""
    state = _state()
    assert state["auto_door_sound_control"] is True
    assert state["auto_door_voice_control"] is False


def test_deodor_filter_has_its_own_keys_not_air_filters():
    """Same shape/reasoning as fridge.AIR_FILTER (percentage-not-raw-count,
    see its own comment) but its own 'deodor_'-prefixed keys, not a shared
    tuple -- AIR_FILTER's own docstring picked 'air_' specifically to avoid
    colliding with WATER_FILTER's filter_usage/filter_status, and reusing
    AIR_FILTER.entities verbatim here would silently recreate that same
    collision if a unit ever reports both hrefs."""
    assert fridge.DEODOR_FILTER.entities != fridge.AIR_FILTER.entities
    state = _state()
    assert state["deodor_filter_status"] == "normal"
    # The device's own sentinel, relayed as-is -- not a real 0-100 reading
    # on this unit, and no second dump to say what else -1 could mean.
    assert state["deodor_filter_usage"] == -1
    assert "air_filter_usage" not in state
    assert "air_filter_status" not in state


def test_winecellar_pantry_zone_mode_options_and_write():
    desc = next(
        e
        for e in fridge.WINECELLAR_PANTRY_ZONE.entities
        if e.key == "winecellar_pantry_zone_mode" and isinstance(e, SelectDesc)
    )
    assert desc.options_field == "x.com.samsung.da.supportedOptions"
    assert desc.write_fn is not None
    assert desc.write_fn("Cheese", {}) == (
        ["status", "winecellar", "pantry", "one", "vs", "0"],
        {"x.com.samsung.da.mode": "Cheese"},
    )
    state = _state()
    assert state["winecellar_pantry_zone_mode"] == "Fruit"


def test_winecellar_variant_and_info_hrefs_bound_with_no_entities():
    assert fridge.AUTO_DOOR_VARIANT.entities == ()
    assert fridge.WINECELLAR_INFO.entities == ()
    assert fridge.AUTO_DOOR_VARIANT.match_fn is not None
    _reg, resources = _fridge()
    assert fridge.AUTO_DOOR_VARIANT.match_fn(resources["/autodoor/winecellar/vs/0"], resources)
