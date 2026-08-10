"""Tests for registry.batch — the /device/0 sweep parser and its stub marker.

issue #127: a device whose /energy/consumption/vs/0 is permanently
unsupported reports a genuinely empty {} rep for it. The previous parser
collapsed /device/0's own {"href": "..."} "not fetched yet" marker to that
same {} shape, so downstream exists_fn checks couldn't tell "confirmed
empty" apart from "haven't polled it yet" and created phantom always-
"unknown" entities either way. is_stub_rep/parse_device0_batch now keep the
two shapes distinct.
"""

from custom_components.localthings.registry.batch import is_stub_rep, parse_device0_batch


class TestIsStubRep:
    def test_true_for_bare_href_marker(self):
        assert is_stub_rep({"href": "/energy/consumption/vs/0"}) is True

    def test_false_for_genuinely_empty_rep(self):
        assert is_stub_rep({}) is False

    def test_false_for_populated_rep(self):
        assert is_stub_rep({"x.com.samsung.da.cumulativePower": "58900"}) is False

    def test_false_for_href_plus_data(self):
        """A real, populated rep may legitimately echo 'href' alongside
        actual fields -- only a rep with *no other keys* is the stub."""
        assert is_stub_rep({"href": "/x/0", "value": True}) is False


class TestParseDevice0Batch:
    def test_keeps_first_resource_when_collection_rep_is_absent(self):
        """Some firmware starts directly with resource entries instead of a
        device-level collection representation."""
        device0 = [
            {"href": "/connectionconfig/vs/0", "rep": {"autoReconnection": "true"}},
            {"href": "/power/vs/0", "rep": {"power": "Off"}},
        ]

        assert parse_device0_batch(device0) == {
            "/connectionconfig/vs/0": {"autoReconnection": "true"},
            "/power/vs/0": {"power": "Off"},
        }

    def test_stub_rep_kept_distinct_from_genuine_empty(self):
        device0 = [
            {},
            {"href": "/energy/consumption/vs/0", "rep": {"href": "/energy/consumption/vs/0"}},
            {"href": "/sabbath/vs/0", "rep": {}},
        ]
        resources = parse_device0_batch(device0)
        assert is_stub_rep(resources["/energy/consumption/vs/0"]) is True
        assert is_stub_rep(resources["/sabbath/vs/0"]) is False
        assert resources["/sabbath/vs/0"] == {}

    def test_populated_rep_passes_through_unchanged(self):
        device0 = [
            {},
            {"href": "/door/cooler/0", "rep": {"openState": "Close"}},
        ]
        resources = parse_device0_batch(device0)
        assert resources["/door/cooler/0"] == {"openState": "Close"}

    def test_skips_entries_without_href(self):
        device0 = [{}, {"rep": {"a": 1}}]
        assert parse_device0_batch(device0) == {}

    def test_skips_non_dict_rep(self):
        device0 = [{}, {"href": "/x/0", "rep": "not-a-dict"}]
        assert parse_device0_batch(device0) == {}
