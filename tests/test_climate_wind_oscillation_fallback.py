"""Tests for the /wind/oscillation/vs/0 swing fallback helper in climate.py
(issue #126).

`_oscillation_swing` is a pure module-level function, like `_temps_vs_item`
in test_climate_temperature_fallback.py -- testable directly without a
coordinator/entity. This covers the fallback-selection logic that the
registry-level "no unbound hrefs" test in test_golden_regression.py doesn't
reach: newer WindFree firmware reports no /wind/direction/vs/0 at all, so
swing_mode/swing_modes would silently be None/empty without this fallback.
"""

from custom_components.localthings.climate import _oscillation_swing


def test_oscillation_swing_off_when_both_fixed():
    assert _oscillation_swing({"vertical": "Fix", "horizontal": "Fix"}) == "off"


def test_oscillation_swing_vertical_only():
    assert _oscillation_swing({"vertical": "Swing", "horizontal": "Fix"}) == "vertical"


def test_oscillation_swing_horizontal_only():
    assert _oscillation_swing({"vertical": "Fix", "horizontal": "Swing"}) == "horizontal"


def test_oscillation_swing_both():
    assert _oscillation_swing({"vertical": "Swing", "horizontal": "Swing"}) == "both"


def test_oscillation_swing_none_when_resource_absent():
    """Boards with /wind/direction/vs/0 instead don't populate this
    resource at all -- callers must fall through cleanly, not raise."""
    assert _oscillation_swing({}) is None
