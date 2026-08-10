"""Tests for entity._is_included -- the one-time entity-creation gate run
per platform at async_setup_entry (see sensor.py etc.), separate from
adapter.flatten()'s per-poll state values. issue #127 review: this default
gate has to stay permissive on a genuinely empty {} rep, not just a true
is_stub_rep stub -- a resource like /alarms/vs/0 is validly empty in its
normal, no-alarm state (fridge._active_alarm_codes), so excluding the
entity there would silently drop working sensors on first-poll timing,
not just fix phantom ones.
"""

from typing import cast

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import SensorDesc


class _FakeCoordinator:
    def __init__(self, last_resources):
        self.last_resources = last_resources

    def canonical_resources(self, subdevice):
        # Every bound entity in this test file uses the default MAIN
        # subdevice (identity transform), so the canonical view is just the
        # raw snapshot -- same shape as the real
        # LocalThingsCoordinator.canonical_resources for a device with no
        # subdevices (issue #177).
        return self.last_resources


def _coord(last_resources) -> LocalThingsCoordinator:
    return cast(LocalThingsCoordinator, _FakeCoordinator(last_resources))


def _bound(desc, href):
    capability = Capability(href=href, entities=(desc,))
    return BoundEntity(href=href, capability=capability, desc=desc)


class TestDefaultFieldGate:
    """No explicit exists_fn -- the field-presence default in entity.py."""

    def _bound(self):
        return _bound(SensorDesc(key="x", field="x.com.samsung.da.value"), "/x/vs/0")

    def test_included_when_field_present(self):
        bound = self._bound()
        coord = _coord({"/x/vs/0": {"x.com.samsung.da.value": "1"}})
        assert _is_included(bound, coord) is True

    def test_excluded_when_field_absent_from_populated_rep(self):
        bound = self._bound()
        coord = _coord({"/x/vs/0": {"x.com.samsung.da.other": "1"}})
        assert _is_included(bound, coord) is False

    def test_included_on_true_stub(self):
        bound = self._bound()
        coord = _coord({"/x/vs/0": {"href": "/x/vs/0"}})
        assert _is_included(bound, coord) is True

    def test_included_on_genuinely_empty_rep(self):
        """The default gate stays permissive on a real {} -- see
        /alarms/vs/0, whose empty rep is the normal no-alarm state, not a
        signal the hardware is unsupported. Only a capability-specific
        exists_fn (e.g. common.ENERGY_METER) opts into excluding on
        confirmed-empty, after verifying that's actually safe for its field."""
        bound = self._bound()
        coord = _coord({"/x/vs/0": {}})
        assert _is_included(bound, coord) is True

    def test_excluded_when_href_missing_from_resources(self):
        bound = self._bound()
        coord = _coord({})
        assert _is_included(bound, coord) is False


class TestExplicitExistsFnGate:
    def test_explicit_exists_fn_overrides_default_field_gate(self):
        desc = SensorDesc(
            key="x",
            field="x.com.samsung.da.value",
            exists_fn=lambda rep, resources: rep.get("flag") is True,
        )
        bound = _bound(desc, "/x/vs/0")
        coord = _coord({"/x/vs/0": {"flag": True}})
        assert _is_included(bound, coord) is True
        coord = _coord({"/x/vs/0": {"flag": False, "x.com.samsung.da.value": "1"}})
        assert _is_included(bound, coord) is False


class TestNoFieldEntities:
    def test_rep_fn_entity_always_included(self):
        desc = SensorDesc(key="x", rep_fn=lambda rep: rep.get("x.com.samsung.da.value"))
        bound = _bound(desc, "/x/vs/0")
        coord = _coord({"/x/vs/0": {}})
        assert _is_included(bound, coord) is True
