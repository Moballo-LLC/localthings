"""Tests for the AILP_DA-AC-FAC-02011_0000 air conditioner (issue #319).

This board resolves to the airconditioner registry two independent ways --
modelNum's 'FAC' board token (for_device_by_model) and /oic/d's
oic.d.airconditioner type (for_device_by_oic_type) agree -- and reports
several resources the sibling TP1X_DA-AC-CAC-01001 board (issue #191) left
as a documented gap: /display/vs/0 and the /settings/sound/* trio (now
shared with air_purifier.py's identical shapes), plus two genuinely new
hrefs (/csi/absenceclean/vs/0, /csi/energysaving/vs/0).
"""

from typing import cast

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import (
    for_device_by_model,
    for_device_by_oic_type,
    resolve,
)
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

_DEVICE_TYPES = ("oic.wk.d", "oic.d.airconditioner")


class _FakeCoordinator:
    """Minimal stand-in for entity._is_included's coordinator dependency --
    same shape as test_entity.py's own fake, kept local rather than shared
    across test modules."""

    def __init__(self, last_resources):
        self.last_resources = last_resources

    def canonical_resources(self, subdevice):
        return self.last_resources

    # _is_included judges existence against the discovery view, which is the
    # live cache for everything but an offline load (issue #295).
    @property
    def discovery_resources(self):
        return self.last_resources

    def discovery_canonical(self, subdevice):
        return self.canonical_resources(subdevice)


def _resources():
    return _load_device("airconditioner_ailp_fac")


def _bound():
    resources = _resources()
    reg = resolve(resources, device_types=_DEVICE_TYPES)
    assert reg is not None
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def test_oic_device_type_resolves_to_airconditioner_registry():
    reg = for_device_by_oic_type(_DEVICE_TYPES)
    assert reg is not None and reg.name == "airconditioner"


def test_board_token_resolves_to_airconditioner_registry():
    """'FAC' is a real _BOARD_TOKEN_TO_KEY entry -- for_device_by_model
    alone (no device_types) already resolves this board correctly, same as
    for_device_by_oic_type above; resolve()'s device_types-agreement isn't
    covering an otherwise-unreachable path."""
    resources = _resources()
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )
    assert reg is not None and reg.name == "airconditioner"


def test_no_unbound_hrefs():
    resources = _resources()
    reg = resolve(resources, device_types=_DEVICE_TYPES)
    assert reg is not None
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_sound_mode_registers_despite_no_live_mode_value():
    """This dump has no current `mode` value yet, only supportedModes
    (mute/tone/voice). entity.py's default field-presence gate would
    otherwise keep the select from ever being created in HA even though
    adapter.flatten() (checked below) has no such gate and would look
    bound regardless -- exercise the real _is_included gate, not just
    flatten(), so a regression here can't hide behind that gap again."""
    bound, resources = _bound()
    entity = next(b for b in bound if b.desc.key == "sound_mode")
    assert entity.desc.options_field == "supportedModes"
    coord = cast(LocalThingsCoordinator, _FakeCoordinator(resources))
    assert _is_included(entity, coord) is True
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
