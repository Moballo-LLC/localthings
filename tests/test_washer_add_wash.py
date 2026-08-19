"""Tests for the AddWash entities on /course/vs/0.

Three independent tokens: AddWashSet (the alarm's 3-bit mask, the only
writable one), AddWashAvailable (what the loaded course permits) and
AddWashIndicator (the live panel lamp). See washer.py for where the bit
meanings and the write contract come from.
"""

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import washer
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import BinarySensorDesc, SwitchDesc
from tests.conftest import _load_device

COURSE = ["course", "vs", "0"]
BITS = {
    "add_wash_alarm_rinse": 0,
    "add_wash_alarm_final_rinse": 1,
    "add_wash_alarm_spin": 2,
}
ENTITY_TOKENS = {
    "add_wash_alarm": "AddWashSet",
    "add_wash_alarm_rinse": "AddWashSet",
    "add_wash_alarm_final_rinse": "AddWashSet",
    "add_wash_alarm_spin": "AddWashSet",
    "add_wash_available": "AddWashAvailable",
    "add_wash_indicator": "AddWashIndicator",
}
# A value each token really carries, so presence gating is exercised against
# what a washer reports rather than a synthetic one.
SAMPLE = {"AddWashSet": "0", "AddWashAvailable": "7", "AddWashIndicator": "Off"}


def _desc(key, kind):
    return next(e for e in washer.WASHER_COURSE.entities if e.key == key and isinstance(e, kind))


def _rep(*tokens):
    return {"x.com.samsung.da.options": list(tokens)}


def _write(desc, payload, rep):
    return desc.write_fn(payload, rep)


def _options(result):
    """The tokens a write_fn result carries, asserting it targets /course/vs/0."""
    path, body = result
    assert path == COURSE
    return body["x.com.samsung.da.options"]


def _flatten(fixture):
    resources = _load_device(fixture)
    reg = resolve(resources)
    return flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)


class TestAlarmMasterSwitch:
    def test_zero_reads_off_and_seven_reads_on(self):
        desc = _desc("add_wash_alarm", SwitchDesc)
        assert desc.rep_fn(_rep("AddWashSet_0")) is False
        assert desc.rep_fn(_rep("AddWashSet_7")) is True

    @pytest.mark.parametrize("mask", range(8))
    def test_any_non_zero_mask_reads_on(self, mask):
        desc = _desc("add_wash_alarm", SwitchDesc)
        assert desc.rep_fn(_rep(f"AddWashSet_{mask}")) is (mask != 0)

    def test_turning_on_enables_every_moment(self):
        desc = _desc("add_wash_alarm", SwitchDesc)
        assert _options(_write(desc, "On", _rep("AddWashSet_0"))) == ["AddWashSet_7"]

    def test_turning_off_clears_the_mask(self):
        desc = _desc("add_wash_alarm", SwitchDesc)
        assert _options(_write(desc, "Off", _rep("AddWashSet_5"))) == ["AddWashSet_0"]

    def test_rejects_a_payload_that_is_not_on_or_off(self):
        desc = _desc("add_wash_alarm", SwitchDesc)
        assert _write(desc, "7", _rep("AddWashSet_0")) is None

    def test_rejects_a_write_against_an_empty_options_array(self):
        desc = _desc("add_wash_alarm", SwitchDesc)
        assert _write(desc, "On", {}) is None

    @pytest.mark.parametrize("raw", ["15", "On", "-1"])
    def test_refuses_to_write_over_a_mask_it_cannot_read(self, raw):
        """A device reporting a wider mask must not have it truncated to 7.
        The master is gated exactly like the per-moment writes, so an
        unrecognized mask leaves every AddWash switch read-only."""
        desc = _desc("add_wash_alarm", SwitchDesc)
        rep = _rep(f"AddWashSet_{raw}")
        assert desc.rep_fn(rep) is None
        assert _write(desc, "On", rep) is None
        assert _write(desc, "Off", rep) is None


class TestAlarmMomentSwitches:
    @pytest.mark.parametrize("key,bit", BITS.items())
    @pytest.mark.parametrize("mask", range(8))
    def test_every_mask_decodes_to_the_right_bits(self, key, bit, mask):
        desc = _desc(key, SwitchDesc)
        assert desc.rep_fn(_rep(f"AddWashSet_{mask}")) is bool(mask >> bit & 1)

    def test_setting_one_moment_leaves_the_others_alone(self):
        desc = _desc("add_wash_alarm_final_rinse", SwitchDesc)
        # 5 is rinse + spin; adding the final rinse must reach 7, not 2.
        assert _options(_write(desc, "On", _rep("AddWashSet_5"))) == ["AddWashSet_7"]

    def test_clearing_one_moment_leaves_the_others_alone(self):
        desc = _desc("add_wash_alarm_rinse", SwitchDesc)
        assert _options(_write(desc, "Off", _rep("AddWashSet_7"))) == ["AddWashSet_6"]

    def test_clearing_the_last_moment_yields_zero(self):
        desc = _desc("add_wash_alarm_spin", SwitchDesc)
        assert _options(_write(desc, "Off", _rep("AddWashSet_4"))) == ["AddWashSet_0"]

    def test_enabling_a_moment_from_zero_turns_the_alarm_on(self):
        """The mask is the only state, so this is the intended outcome --
        there is no remembered combination to restore."""
        moment = _desc("add_wash_alarm_spin", SwitchDesc)
        written = _options(_write(moment, "On", _rep("AddWashSet_0")))
        assert written == ["AddWashSet_4"]
        assert _desc("add_wash_alarm", SwitchDesc).rep_fn(_rep(*written)) is True

    @pytest.mark.parametrize("rep", [{}, _rep("AddWashSet_x"), _rep("Course_5C")])
    def test_refuses_to_write_when_the_mask_is_unreadable(self, rep):
        desc = _desc("add_wash_alarm_rinse", SwitchDesc)
        assert _write(desc, "On", rep) is None


class TestMaskParsing:
    def test_a_missing_token_is_unavailable_not_zero(self):
        assert washer._add_wash_mask(_rep("Course_5C"), "AddWashSet") is None
        assert _desc("add_wash_alarm", SwitchDesc).rep_fn(_rep("Course_5C")) is None

    def test_a_malformed_token_is_unavailable(self):
        assert washer._add_wash_mask(_rep("AddWashSet_On"), "AddWashSet") is None

    def test_zero_is_a_real_value(self):
        assert washer._add_wash_mask(_rep("AddWashSet_0"), "AddWashSet") == 0

    @pytest.mark.parametrize("raw", ["8", "255", "-1"])
    def test_a_mask_outside_three_bits_is_unavailable(self, raw):
        """This models exactly three moments, so a wider value means the
        model is wrong -- refuse it rather than read-modify-write it back."""
        assert washer._add_wash_mask(_rep(f"AddWashSet_{raw}"), "AddWashSet") is None
        assert _desc("add_wash_alarm_rinse", SwitchDesc).rep_fn(_rep(f"AddWashSet_{raw}")) is None
        assert (
            _write(_desc("add_wash_alarm_spin", SwitchDesc), "On", _rep(f"AddWashSet_{raw}"))
            is None
        )


class TestReadOnlySensors:
    @pytest.mark.parametrize("raw,expected", [("0", False), ("6", True), ("7", True)])
    def test_available_is_true_for_any_permitted_moment(self, raw, expected):
        desc = _desc("add_wash_available", BinarySensorDesc)
        assert desc.rep_fn(_rep(f"AddWashAvailable_{raw}")) is expected

    @pytest.mark.parametrize("raw,expected", [("On", True), ("Off", False)])
    def test_indicator_maps_on_off(self, raw, expected):
        desc = _desc("add_wash_indicator", BinarySensorDesc)
        assert desc.rep_fn(_rep(f"AddWashIndicator_{raw}")) is expected

    def test_indicator_ships_enabled_and_uncategorised(self):
        desc = _desc("add_wash_indicator", BinarySensorDesc)
        assert desc.enabled_default is True
        assert desc.entity_category is None

    def test_missing_tokens_are_unavailable(self):
        assert _desc("add_wash_available", BinarySensorDesc).rep_fn(_rep()) is None
        assert _desc("add_wash_indicator", BinarySensorDesc).rep_fn(_rep()) is None


class TestCapabilityDetection:
    """Each entity self-gates on its own token, so a washer advertising only
    a subset gets only that subset."""

    @pytest.mark.parametrize("key,token", ENTITY_TOKENS.items())
    def test_absent_on_a_washer_that_never_reports_the_token(self, key, token):
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == key)
        assert desc.exists_fn(_rep("Course_5C"), {}) is False

    @pytest.mark.parametrize("key,token", ENTITY_TOKENS.items())
    def test_present_once_the_token_appears(self, key, token):
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == key)
        assert desc.exists_fn(_rep(f"{token}_{SAMPLE[token]}"), {}) is True

    def test_a_washer_with_only_the_indicator_gets_only_that_entity(self):
        present = {
            e.key
            for e in washer.WASHER_COURSE.entities
            if e.key in ENTITY_TOKENS and e.exists_fn(_rep("AddWashIndicator_On"), {})
        }
        assert present == {"add_wash_indicator"}

    def test_pre_add_wash_washers_gain_nothing(self):
        """washer_device is a DA_WM_TP1_21 dump with no AddWash tokens."""
        state = _flatten("washer")
        assert not [key for key in state if key.startswith("add_wash")]


class TestAgainstTheWW6500Dump:
    """DA_WM_A51_20_COMMON_WW6500, captured with the alarm off, the course
    permitting all three moments and the lamp dark."""

    def test_types_only_by_the_description_consumer_prefix(self):
        """A51 is not a board token, so the WW prefix in the description is
        the only thing routing this device -- see the golden test's docstring.
        Pinned here so the claim can't quietly stop being true."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        info = _load_device("washer_ww6500")["/information/vs/0"]
        model = info["x.com.samsung.da.modelNum"]
        assert for_device_by_model(model, info["x.com.samsung.da.description"]).name == "washer"
        assert for_device_by_model(model, "") is None

    def test_no_unbound_hrefs(self):
        resources = _load_device("washer_ww6500")
        reg = resolve(resources)
        unbound = []
        discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
        assert unbound == []

    def test_reports_every_add_wash_entity(self):
        state = _flatten("washer_ww6500")
        assert {key for key in state if key.startswith("add_wash")} == {
            "add_wash_alarm",
            "add_wash_alarm_rinse",
            "add_wash_alarm_final_rinse",
            "add_wash_alarm_spin",
            "add_wash_available",
            "add_wash_indicator",
        }

    def test_alarm_off_course_permits_lamp_dark(self):
        state = _flatten("washer_ww6500")
        assert state["add_wash_alarm"] is False
        assert all(state[f"add_wash_alarm_{m}"] is False for m in ("rinse", "final_rinse", "spin"))
        assert state["add_wash_available"] is True
        assert state["add_wash_indicator"] is False
