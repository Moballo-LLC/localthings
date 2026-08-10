"""Tests for the NE6516A-class range (issue #183).

Same no-/information/vs/0, no-burner-status shape as issues #74/#138. Its
/mode/vs/0 options[] carries EnergySaving_On and BurnerOnAlert_Off -- both
previously unbound entirely -- but no fastpreheat_*/NaturalSteam_* tokens at
all, which is what exposed the pre-existing fast_preheat/natural_steam
phantom-switch bug (both were shipped with no exists_fn, so they bound
unconditionally, always read off, and any write went to a token this
firmware never recognized).
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_resources
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device


def _range():
    resources = _load_device("range_ne6516a")
    reg = for_device_by_resources(resources)
    return reg, resources


def _state():
    reg, resources = _range()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_resolves_to_range_registry():
    reg, _ = _range()
    assert reg is not None and reg.name == "range"


def test_no_unbound_hrefs():
    reg, resources = _range()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_fast_preheat_and_natural_steam_absent_without_their_tokens():
    """Neither token is in this dump's options[] -- both switches must stay
    unbound rather than silently reading as an always-off phantom control."""
    state = _state()
    assert "fast_preheat" not in state
    assert "natural_steam" not in state


def test_energy_saving_and_cooktop_alert_present():
    state = _state()
    assert state["energy_saving"] is True
    assert state["cooktop_on_alert"] is False


def test_child_lock_already_reads_correctly():
    """issue #183 also reported child lock as unreadable -- but this dump's
    /kidslock/vs/0 ('Run') and /doors/vs/0's door lock field ('Lock') already
    agree the door is locked, and common.KIDS_LOCK_VS_FALLBACK already reads
    that correctly with no further code change needed. Now a read-only
    binary_sensor (issues #181/#183 -- the write side was never a confirmed
    contract), whose 'lock' device class is inverted from a switch's plain
    on/off: False means locked/closed, matching 'Run' here."""
    state = _state()
    assert state["child_lock"] is False
