"""Tests for the TP1X_DA-AC-CAC-01001_0000 cassette AC (issue #191).

0.16.0's device-type simplification dropped oneUiVersion detection on the
assumption every device it typed was already reachable via a modelNum board
token -- this board was the one exception (its oneUiVersion self-reports
"7.0 Air conditioner", but 'CAC' had never been added to the board-token
table), so it silently fell back to common caps and lost its climate entity.

This dump is NOT fully covered yet -- two hrefs remain unbound
(`/settings/sound/optimization/vs/0`, smart-sensing-cooling), both
genuinely new to this board generation. That's a real device-support gap,
left documented here rather than guessed at, per the 'don't guess' rule --
fixing the routing regression was the scope of #191.

/settings/sound/mode/vs/0, /settings/sound/output/vs/0 and
/settings/sound/volume/vs/0 used to be on this list too, until issue #319
(a sibling TP1X_DA-AC-FAC-class board) supplied a live dump for them --
airconditioner.SOUND_MODE and the reused air_purifier.SOUND_OUTPUT/
SOUND_VOLUME now cover all three here as well.

/edgelighting/vs/0 and /light/stateful/vs/0 used to be on this list too,
until issue #288 (six System A/C cassette units on this same board) gave
real dump evidence for both -- airconditioner.EDGE_LIGHTING and
LIGHT_STATEFUL now cover them.

/mds/absenceclean/vs/0 used to be on this list too -- its {mode,
supportedModes: [On, Off]} shape is byte-identical to issue #319's
/csi/absenceclean/vs/0, confirmed on that sibling board rather than
guessed, so airconditioner.MDS_ABSENCE_CLEAN now covers it too.

/uvled/vs/0 and /filter/airdustPM1filter/vs/0 used to be on this list too,
until issue #270 (TP1X_FAC_TIME_23K) added real capabilities for both --
this board's own live filterUsage/filterStatus data on the PM1 filter binds
through the same exists_fn-gated entities #270's dump (which has neither
field) leaves empty.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

_STILL_UNBOUND = frozenset(
    {
        "/settings/sound/optimization/vs/0",
        "/smartsensingcooling/vs/0",
    }
)


def _resources():
    return _load_device("airconditioner_cac")


def _reg(resources):
    info = resources["/information/vs/0"]
    return for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )


def test_resolves_to_airconditioner_registry():
    assert _reg(_resources()).name == "airconditioner"


def test_documented_coverage_gap_is_exactly_this_set():
    """Locks in the current, known-incomplete coverage so a future fix to
    any of these hrefs shows up as a golden-regression diff (extra keys) to
    update here, rather than silently shrinking this list unnoticed."""
    resources = _resources()
    reg = _reg(resources)
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert set(unbound) == _STILL_UNBOUND


def test_mds_absenceclean_shares_csi_absenceclean_key():
    """/mds/absenceclean/vs/0's mode=='Off' on this dump -- confirms
    MDS_ABSENCE_CLEAN actually binds (not just that the href stops
    reporting as unbound)."""
    resources = _resources()
    reg = _reg(resources)
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, resources)
    assert state["absence_clean"] is False


def test_non_legacy_board_uses_the_generic_energy_scale():
    """This board reports /wind/strength/vs/0 (not /airflow/vs/0), so
    is_legacy_board() is False and it must use the plain wh_to_kwh scale,
    not the /100000 correction added for the unrelated ARTIK051_KRAC-class
    board in issue #193."""
    resources = _resources()
    reg = _reg(resources)
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, resources)
    assert state["energy_kwh"] == round(84044 / 1000.0, 2)
