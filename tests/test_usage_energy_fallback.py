"""The usage file as a *fallback* source of cumulative energy (issue #301).

Only useful where the appliance does not report the number directly. Where
both exist the file is pure duplication -- measured on a dishwasher, whose
single record matched `cumulativePower` and `cumulativeDate` exactly -- and
two `total_increasing` energy sensors for one physical meter is the
double-count trap issue #329 warns about.

So the two sources share the key `energy_kwh` and their `exists_fn` are
exact complements, the same shape as common's POWER_GENERIC/POWER_VS_FALLBACK
pair: an appliance gets one energy entity from whichever source can supply
it, and its entity_id does not depend on which.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from custom_components.localthings.registry import usagedb
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities import common
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.encode import from_json_safe
from custom_components.localthings.registry.registry import CAPABILITIES

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ENERGY = common.HREF_ENERGY_CONSUMPTION
TRANSFER = "/file/transfer/vs/0"


def _blob(name: str = "refrigerator_tp1x_ref_21k") -> bytes:
    data = json.loads((FIXTURES / "usage_blobs.json").read_text())[name]
    return from_json_safe(data["blob"])


def _transfer_rep(blob: bytes | None = None) -> dict:
    return {
        usagedb.ITEMS_FIELD: [
            {
                "x.com.samsung.name": "/mnt/usage.db",
                usagedb.BLOB_FIELD: _blob() if blob is None else blob,
            }
        ]
    }


def _state(resources: dict) -> dict:
    return flatten(discover(resources, CAPABILITIES), resources)


def test_the_file_supplies_energy_when_the_meter_reports_none():
    """#285's washer: `cumulativePower` vanished from the rep entirely while
    the appliance kept recording to the file."""
    state = _state(
        {
            ENERGY: {"x.com.samsung.da.instantaneousPower": "-500"},
            TRANSFER: _transfer_rep(),
        }
    )
    assert state["energy_kwh"] == 1141.0


def test_the_file_supplies_energy_when_there_is_no_meter_resource_at_all():
    assert _state({TRANSFER: _transfer_rep()})["energy_kwh"] == 1141.0


def test_the_meter_wins_when_it_reports_cumulative_power():
    """The fallback must not shadow the live meter, which updates every poll
    where the file is a once-a-day rollup."""
    state = _state(
        {
            ENERGY: {"x.com.samsung.da.cumulativePower": "1141141"},
            TRANSFER: _transfer_rep(),
        }
    )
    # wh_to_kwh rounds to 2dp; the file's own resolution is 0.1 kWh.
    assert state["energy_kwh"] == 1141.14


def test_a_zero_meter_reading_still_wins():
    """A permanent, correct `0` -- the ARTIK051_KRAC_18K of issue #302, whose
    hardware genuinely has no meter. The field being *present* is what
    decides, not its value, so this stays the meter's entity and the file
    does not quietly substitute a different number for it."""
    state = _state(
        {
            ENERGY: {"x.com.samsung.da.cumulativePower": "0"},
            TRANSFER: _transfer_rep(),
        }
    )
    assert state["energy_kwh"] == 0.0


def test_a_not_yet_fetched_meter_stub_wins():
    """A stub rep means "not fetched yet", and ENERGY_METER includes its own
    entity on that basis. Treating it as absent here would put two entities
    on one key, and flatten() would silently pick one."""
    state = _state({ENERGY: {"href": ENERGY}, TRANSFER: _transfer_rep()})
    assert state.get("energy_kwh") is None


@pytest.mark.parametrize(
    "resources",
    [
        {ENERGY: {"x.com.samsung.da.cumulativePower": "1141141"}, TRANSFER: _transfer_rep()},
        {ENERGY: {"x.com.samsung.da.instantaneousPower": "-500"}, TRANSFER: _transfer_rep()},
        {TRANSFER: _transfer_rep()},
        {ENERGY: {"x.com.samsung.da.cumulativePower": "1141141"}},
    ],
    ids=["meter-and-file", "file-only", "no-meter-resource", "meter-only"],
)
def test_exactly_one_source_ever_binds_energy_kwh(resources):
    """The invariant the shared key depends on. Two BoundEntity on one key
    would collapse in flatten() rather than error, so a gating mistake would
    be silent."""
    bound = discover(resources, CAPABILITIES)
    energy = [b for b in bound if b.desc.key == "energy_kwh"]
    included = [b for b in energy if b.desc.exists_fn(resources.get(b.href) or {}, resources)]
    assert len(included) <= 1


def test_no_entity_when_the_blob_cannot_be_decoded():
    """An unreadable payload must produce nothing, not a permanently-unknown
    entity. This is the ARTIK051_KRAC_18K byte shape, whose value field is
    runtime hours rather than energy."""
    krac = struct.pack("<QI", 1_690_156_800, 14545)
    state = _state({ENERGY: {}, TRANSFER: _transfer_rep(krac)})
    assert "energy_kwh" not in state


def test_no_entity_when_the_file_is_absent():
    assert "energy_kwh" not in _state({ENERGY: {}})
