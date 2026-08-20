"""Tests for dryer support and washer/dryer consistency (issue #14)."""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model, resolve
from custom_components.localthings.registry.capabilities import dryer, ignored
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc
from tests.conftest import _load_device


def _dryer():
    resources = _load_device("dryer")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def _state():
    reg, resources = _dryer()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_dryer_model_resolves_to_dryer_registry():
    reg, _ = _dryer()
    assert reg is not None and reg.name == "dryer"


def test_no_unbound_hrefs():
    """Every resource in the issue #14 dump binds or is ignored -- clears the
    coverage-gap repair."""
    reg, resources = _dryer()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_entities_present():
    state = _state()
    for key in (
        "buzzer_sound",
        "cycle",
        "power_switch",
        "child_lock",
        "remote_control",
        "dry_level",
        "wrinkle_prevent",
        "energy_kwh",
    ):
        assert key in state, key


def test_job_beginning_status_reads_current_status():
    """The dump carries x.com.samsung.da.currentStatus (not the old
    jobBeginingStatus field the dryer descriptor used to read), so the sensor
    is populated instead of blank."""
    assert _state()["job_beginning_status"] == "None"  # the dump's currentStatus value


def test_power_watts_gated_for_dead_sentinel():
    """instantaneousPower is the dead '-500' sentinel on this dryer, so the
    misleading 0 W power sensor is suppressed; cumulative energy still shows."""
    state = _state()
    assert "power_watts" not in state
    assert "energy_kwh" in state


def test_course_bound_to_shared_course_vs_0():
    """Dryer course uses the shared /course/vs/0 cycle select, with the
    translation key built from the device's own course table (see
    laundry.cycle_select) -- confirmed dryers report Table_03, matching
    the shipped dryer_cycle_table_03 translations, consistent with
    washer/dishwasher."""
    assert dryer.DRYER_COURSE.href == "/course/vs/0"
    desc = next(
        e for e in dryer.DRYER_COURSE.entities if e.key == "cycle" and isinstance(e, SelectDesc)
    )
    assert callable(desc.translation_key)
    table_03 = {"/st/dryercourse/vs/0": {"x.com.samsung.da.st.courseTable": "Table_03"}}
    assert desc.translation_key(table_03) == "dryer_cycle_table_03"
    assert desc.translation_key({}) == "cycle"
    live = {"/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": "EditCourseList_1620"}}
    assert desc.options(live) == ["16", "20"]
    rep = {"x.com.samsung.da.options": ["Course_16", "GMT_02"]}
    assert desc.rep_fn is not None
    assert desc.rep_fn(rep) == "16"


def test_reported_table_00_course_codes_are_translated():
    """The DVE45R6300W/A3 reporter confirmed these codes by selecting each
    cycle and reading back the raw course code (issue #357). A DV6800N --
    same DA_WM_A51_20_COMMON board, also Table_00 -- later confirmed 14 more
    (issue #394): a different subset of the same table, not a conflicting
    code family (its one code in common with #357, 'a5', means Bedding on
    both), so both sets share the one dryer_cycle_table_00 catalog entry."""
    from custom_components.localthings.catalog import translated_states

    desc = next(
        e for e in dryer.DRYER_COURSE.entities if e.key == "cycle" and isinstance(e, SelectDesc)
    )
    table_00 = {"/st/dryercourse/vs/0": {"x.com.samsung.da.st.courseTable": "Table_00"}}
    assert desc.translation_key(table_00) == "dryer_cycle_table_00"
    confirmed = {
        "01",
        "9c",
        "a5",
        "9e",
        "9b",
        "27",
        "a0",
        "a4",
        "a6",
        "a3",
        "a2",  # issue #357
        "9a",
        "ca",
        "db",
        "99",
        "93",
        "b5",
        "d7",
        "96",
        "97",
        "7f",
        "98",
        "eb",
        "b6",  # issue #394
    }
    assert confirmed <= translated_states("select", "dryer_cycle_table_00")


def test_st_dryercourse_is_ignored():
    """/st/dryercourse/vs/0 re-encodes the course exposed via /course/vs/0 and
    is globally ignored -- the mirror of /st/washercourse/vs/0."""
    ignored_hrefs = {c.href for c in ignored.IGNORED}
    assert "/st/dryercourse/vs/0" in ignored_hrefs
    assert "/st/washercourse/vs/0" in ignored_hrefs


def _dv6800n():
    resources = _load_device("dryer_dv6800n")
    reg = resolve(resources, device_types=("oic.wk.d", "oic.d.dryer"))
    return reg, resources


def test_dv6800n_no_unbound_hrefs():
    """Every resource in the issue #394 dump binds or is ignored."""
    reg, resources = _dv6800n()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_dv6800n_course_codes_read_from_dump():
    """A DV6800N (DA_WM_A51_20_COMMON, issue #394) reports 'Table_00' same
    as #357's DVE45R6300W/A3, so it resolves to the same catalog entry --
    its /course/vs/0 supportedOptions just advertises a different subset of
    the same table (see test_reported_table_00_course_codes_are_translated)."""
    _, resources = _dv6800n()
    desc = next(
        e for e in dryer.DRYER_COURSE.entities if e.key == "cycle" and isinstance(e, SelectDesc)
    )
    assert desc.translation_key(resources) == "dryer_cycle_table_00"
    confirmed = ["9A", "CA", "DB", "99", "93", "B5", "D7", "A5", "96", "97", "7F", "98", "EB", "B6"]
    assert desc.options(resources) == confirmed
