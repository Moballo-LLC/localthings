"""Tests for the ventilation-mode/Wind-Free/Wind-Sleep additions extracted
from PR #316 (Samsung "System Fresh Air Ventilator", model
ACA-KR-TP2-21-AN9000, vid DA-AC-DIFFUSER-01001).

No raw diagnostics dump for this model was available -- PR #316 never
attached one, only Korean code comments describing field shapes the
contributor said they observed. Per this project's fixture-integrity rule
(a fixture must record what hardware actually did, not a third party's
prose about it), there's no `airconditioner_*_device.json` fixture for
this model here. These tests instead exercise the gating logic directly
against hand-built reps matching those quoted shapes, clearly distinct
from this suite's fixture-backed tests, and check the new gate doesn't
false-positive against every real AC fixture already in the corpus.

If a real diagnostics dump for this model ever surfaces (tracked as a
follow-up device-support issue), replace this file with a proper
fixture + golden + capability test per the usual workflow, and drop the
disclaimer above.
"""

import glob
import json
import os

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities.airconditioner import (
    WINDFREE,
    WINDSLEEP,
    _is_ventilation_mode_device,
)
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _all_airconditioner_fixture_names():
    names = []
    for path in glob.glob(os.path.join(_FIXTURES_DIR, "airconditioner*_device.json")):
        name = os.path.basename(path)[: -len("_device.json")]
        with open(path) as f:
            info = json.load(f)
        rep = next(
            (e["rep"] for e in info.get("device0", []) if e.get("href") == "/information/vs/0"),
            None,
        )
        if rep is not None:  # for_device_by_model needs /information/vs/0
            names.append(name)
    return names


def test_ventilation_mode_gate_never_false_positives_on_real_ac_fixtures():
    """None of the real air-conditioner fixtures in this corpus use the
    Purification/Ventilation/SmartVentilation vocabulary -- confirms
    _is_ventilation_mode_device can't turn a real AC's climate card into
    this select."""
    for name in _all_airconditioner_fixture_names():
        resources = _load_device(name)
        reg = for_device_by_model(
            resources["/information/vs/0"]["x.com.samsung.da.modelNum"],
            resources["/information/vs/0"]["x.com.samsung.da.description"],
        )
        if reg is None or reg.name != "airconditioner":
            continue
        mode_rep = resources.get("/mode/vs/0")
        if not mode_rep:
            continue
        assert _is_ventilation_mode_device(mode_rep, resources) is False, name


def test_ventilation_mode_gate_matches_diffuser_shape():
    """PR #316: supportedModes exactly {Purification, Ventilation,
    SmartVentilation} -- the vocabulary that makes climate.py's hvac_mode
    collapse to one stuck value with no way to tell the three apart."""
    rep = {
        "x.com.samsung.da.modes": ["Purification"],
        "x.com.samsung.da.supportedModes": ["Purification", "Ventilation", "SmartVentilation"],
    }
    assert _is_ventilation_mode_device(rep, {}) is True


def test_ventilation_mode_gate_rejects_partial_overlap():
    """A real AC reporting an unrelated mode alongside one of these three
    words (coincidence, not this device) must not gate in -- the check is
    'subset of', not 'intersects'."""
    rep = {"x.com.samsung.da.supportedModes": ["Ventilation", "Cool", "Heat"]}
    assert _is_ventilation_mode_device(rep, {}) is False


def _bind(capability, href, rep):
    resources = {href: rep}
    bound = discover(resources, {href: [capability]}, [])
    return flatten(bound, resources)


def test_windfree_and_windsleep_read_their_own_hrefs():
    """PR #316's quoted rep shape: {'x.com.samsung.da.windfree': 'On'/'Off',
    'x.com.samsung.da.displaycondition': 'normal'} (displaycondition is a
    read-only UI-visibility flag, deliberately not modeled)."""
    windfree_state = _bind(
        WINDFREE,
        "/modeoption/windfree/vs/0",
        {"x.com.samsung.da.windfree": "On", "x.com.samsung.da.displaycondition": "normal"},
    )
    assert windfree_state["windfree"] is True

    windsleep_state = _bind(
        WINDSLEEP,
        "/modeoption/windsleep/vs/0",
        {"x.com.samsung.da.windsleep": "Off", "x.com.samsung.da.displaycondition": "normal"},
    )
    assert windsleep_state["windsleep"] is False
