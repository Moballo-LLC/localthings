"""Tests for the ARTIK051_TVTL_18K air-purifier profile (issue #56)."""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities import air_purifier
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import FanDesc, SensorDesc, SwitchDesc
from tests.conftest import _load_device


def _purifier():
    resources = _load_device("air_purifier")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def _state():
    reg, resources = _purifier()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_model_resolves_to_air_purifier_registry():
    reg, _ = _purifier()
    assert reg is not None
    assert reg.name == "air_purifier"


def test_no_unbound_hrefs():
    """Every resource in the issue #56 dump binds or is ignored -- clears the
    coverage-gap repair."""
    reg, resources = _purifier()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_entities_present():
    state = _state()
    for key in (
        "power_switch",
        "alarm_code",
        "dust",
        "fine_dust",
        "super_fine_dust",
        "odor",
        "clean_level",
        "filter_progress",
        "device_active",
        "diagnosis_status",
        "airflow_fan",
        "fan_direction",
        "display_light",
        "operating_mode",
    ):
        assert key in state, key


def test_air_quality_sensor_values():
    """Dust/FineDust/SuperFineDust/Odor/CleanLevel read index 0 of each
    items[] entry's value list (the raw measurement, per common.sensor_item_value)."""
    state = _state()
    assert state["dust"] == 11
    assert state["fine_dust"] == 9
    assert state["super_fine_dust"] == 5
    assert state["odor"] == 0
    assert state["clean_level"] == 0


def test_filter_progress_reads_named_consumable_item():
    """FilterProgress is confirmed (issue #56) to count up as the filter
    wears -- 100 means fully used and needs replacing, not "brand new"."""
    assert _state()["filter_progress"] == 100


def test_diagnosis_reuses_dishwasher_capability():
    """/diagnosis/vs/0 has the identical field/write contract as
    dishwasher.DIAGNOSIS, so the by_type registry reuses it directly."""
    from custom_components.localthings.registry.capabilities import dishwasher

    reg, _ = _purifier()
    assert dishwasher.DIAGNOSIS in reg.capabilities["/diagnosis/vs/0"]


def test_light_switch_write_contract():
    """The display-light switch writes only the changed 'Light_*' token
    (via laundry.option_write) -- confirmed on real hardware (issue #54)
    that the device merges by prefix itself, so no read-modify-write of the
    whole packed /mode/vs/0 options list is needed."""
    desc = next(
        e
        for e in air_purifier.MODE.entities
        if e.key == "display_light" and isinstance(e, SwitchDesc)
    )
    rep = {
        "x.com.samsung.da.options": [
            "Comode_Off",
            "Blooming_0",
            "Light_On",
            "OptionCode_60282",
        ]
    }
    assert desc.rep_fn is not None
    assert desc.rep_fn(rep) is True
    assert desc.write_fn is not None
    assert desc.write_fn("Off", rep) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["Light_Off"]},
    )


def test_operating_mode_is_a_read_only_diagnostic():
    """Comode_* surfaces as a raw diagnostic sensor rather than a select/
    control -- issue #56's five running-state dumps confirmed it reads 'Off'
    regardless of the device's actual fan setting, ruling out the original
    guess that it was the fan-speed selector; its real purpose is still
    unconfirmed (see the air_purifier.py module docstring)."""
    operating_mode = next(
        e
        for e in air_purifier.MODE.entities
        if e.key == "operating_mode" and isinstance(e, SensorDesc)
    )
    rep = {"x.com.samsung.da.options": ["Comode_Off"]}
    assert operating_mode.rep_fn is not None
    assert operating_mode.rep_fn(rep) == "Off"
    assert not hasattr(operating_mode, "write_fn")


def test_blooming_not_modeled():
    """Confirmed (issue #56) to have no corresponding SmartThings app
    setting -- dropped entirely rather than kept as an unexplained
    diagnostic."""
    assert not any(e.key == "blooming_level" for e in air_purifier.MODE.entities)


def test_airflow_vs_fallback_only_binds_without_generic():
    """/airflow/vs/0 is a match_fn fallback -- it must not bind when the
    OCF-standard /airflow/0 is also present (both are on every dump seen)."""
    match_fn = air_purifier.AIRFLOW_VS_FALLBACK.match_fn
    assert match_fn is not None
    assert (
        match_fn(
            {},
            {"/airflow/0": {"speed": 0, "direction": "Off"}},
        )
        is False
    )
    assert match_fn({}, {}) is True


def test_airflow_fan_write_contract():
    """Confirmed via issue #56's second, properly-spaced diagnostics round
    (two independent units, 60-90s apart per setting): /airflow/0's `speed`
    is a clean, monotonic 0-4 code, so the write is a plain int passthrough
    -- no named-preset table needed (see fan.py's LocalThingsAirflowFan)."""
    fan_desc = next(
        e
        for e in air_purifier.AIRFLOW_GENERIC.entities
        if e.key == "airflow_fan" and isinstance(e, FanDesc)
    )
    assert fan_desc.write_fn is not None
    assert fan_desc.write_fn(("speed", 3), {}) == (["airflow", "0"], {"speed": 3})
    assert fan_desc.write_fn(("power", True, "/power/vs/0"), {}) == (
        ["power", "vs", "0"],
        {"x.com.samsung.da.power": "On"},
    )
    assert fan_desc.write_fn(("power", False, "/power/0"), {}) == (
        ["power", "0"],
        {"value": False},
    )


def test_airflow_speed_reads_from_dump():
    """The fixture's /airflow/0 (power-off snapshot) reads speed=0, and
    fan_direction stays a plain diagnostic (every dump seen reads 'Off'
    regardless of fan setting)."""
    state = _state()
    assert state["airflow_fan"] == 0
    assert state["fan_direction"] == "Off"
