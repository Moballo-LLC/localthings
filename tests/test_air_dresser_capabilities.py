"""Tests for the AirDresser device type (DA_DF_A51_20_COMMON, issue #162)."""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device


def _air_dresser():
    resources = _load_device("air_dresser")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )
    return reg, resources


def _state():
    reg, resources = _air_dresser()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_resolves_to_air_dresser_registry():
    reg, _ = _air_dresser()
    assert reg is not None and reg.name == "air_dresser"


def test_no_unbound_hrefs():
    reg, resources = _air_dresser()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_course_select_present_and_reads_current_selection():
    """This was the actual reported gap: /course/vs/0 was previously
    entirely unbound, so no cycle/mode select existed at all."""
    state = _state()
    assert state["cycle"] == "01"


def test_course_options_derived_from_supported_options_fallback():
    """No /wm/editcourse/vs/0 on this board at all, so the option list must
    come from laundry.cycle_options' supportedOptions decode rather than
    editCourseList -- confirmed distinct codes, current selection included."""
    from custom_components.localthings.registry.capabilities.laundry import cycle_options

    _, resources = _air_dresser()
    codes = cycle_options(resources)
    assert codes == ["01", "02", "04", "03", "05", "1A", "1B", "1C", "07", "08"]


def test_wrinkle_prevent_present_dry_level_fields_absent():
    """AIR_DRESSER_SETTINGS only binds wrinkle_prevent -- dryLevel/dryTime/
    dryerType are dryer-only fields this device never reports, so they
    should not appear as always-empty entities."""
    state = _state()
    assert state["wrinkle_prevent"] is False
    for key in ("dry_level", "dry_time", "dryer_type"):
        assert key not in state, key


def test_expected_entities_present():
    state = _state()
    for key in (
        "power_switch",
        "child_lock",
        "remote_control",
        "cycle",
        "machine_state",
        "progress",
        "progress_percentage",
        "finish_time",
        "completion_minutes",
        "delay_start_hours",
        "diagnosis_status",
        "job_beginning_status",
        "wrinkle_prevent",
        "energy_kwh",
    ):
        assert key in state, key
