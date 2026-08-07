"""Tests for the AILP_DA-AC-FAC-02011_0000 air conditioner (issue #319).

This board has no `_BOARD_TOKEN_TO_KEY` entry (routes via `/oic/d`'s
`oic.d.airconditioner` type only) and reports several resources the sibling
TP1X_DA-AC-CAC-01001 board (issue #191) left as a documented gap:
/display/vs/0 and the /settings/sound/* trio (now shared with
air_purifier.py's identical shapes), plus two genuinely new hrefs
(/csi/absenceclean/vs/0, /csi/energysaving/vs/0).
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_oic_type, resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

_DEVICE_TYPES = ("oic.wk.d", "oic.d.airconditioner")


def _resources():
    return _load_device("airconditioner_ailp_fac")


def _bound():
    resources = _resources()
    reg = resolve(resources, device_types=_DEVICE_TYPES)
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def test_oic_device_type_resolves_to_airconditioner_registry():
    reg = for_device_by_oic_type(_DEVICE_TYPES)
    assert reg is not None and reg.name == "airconditioner"


def test_no_unbound_hrefs():
    resources = _resources()
    reg = resolve(resources, device_types=_DEVICE_TYPES)
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_sound_mode_bound_with_live_supported_modes():
    """This dump has no current `mode` value yet, only supportedModes
    (mute/tone/voice) -- confirms the select is bound (present in state,
    even if unknown) and its options come from the live field rather than
    a static tuple (see laundry.SOUND_MODE's docstring for why that's
    preferred whenever the resource carries one)."""
    bound, resources = _bound()
    desc = next(b.desc for b in bound if b.desc.key == "sound_mode")
    assert desc.options_field == "supportedModes"
    state = flatten(bound, resources)
    assert "sound_mode" in state
    assert state["sound_mode"] is None


def test_absence_clean_and_energy_saving_bound():
    bound, resources = _bound()
    state = flatten(bound, resources)
    assert state["absence_clean"] is True
    assert state["energy_saving_mode"] == "Off_100"
    assert state["energy_saving_state"] == "Off"


def test_sound_volume_self_gates_off_without_max_level():
    """This board's /settings/sound/volume/vs/0 reports minLevel/resolution
    but no maxLevel -- air_purifier.SOUND_VOLUME's exists_fn should keep it
    unbound-but-covered rather than shipping a min=0/max=0 slider."""
    bound, resources = _bound()
    state = flatten(bound, resources)
    assert "sound_volume" not in state
