"""Decoding the usage history at /file/transfer/vs/0 (issue #301).

Backed by two real captures in fixtures/usage_blobs.json: a dishwasher's
single record and a TP1X_REF_21K fridge's full 181-record file, both read
live and both reconciled against their own appliance's
/energy/consumption/vs/0 at the time -- see that file's notes.
"""

from __future__ import annotations

import itertools
import json
import struct
from pathlib import Path

import pytest

from custom_components.localthings.registry import usagedb
from custom_components.localthings.registry.encode import from_json_safe

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _capture(name: str):
    data = json.loads((FIXTURES / "usage_blobs.json").read_text())[name]
    return from_json_safe(data["blob"]), data


def _rep(blob: bytes) -> dict:
    return {
        usagedb.ITEMS_FIELD: [{"x.com.samsung.name": "/mnt/usage.db", usagedb.BLOB_FIELD: blob}]
    }


def _synthetic(records, start=1_780_000_000, step=86400) -> bytes:
    """Build a blob from (value, third) pairs on a daily cadence."""
    return b"".join(
        struct.pack("<III", start + i * step, value, third)
        for i, (value, third) in enumerate(records)
    )


# ---------------------------------------------------------------------------
# The real captures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["dishwasher", "refrigerator_tp1x_ref_21k"])
def test_a_real_capture_decodes_to_its_recorded_totals(name):
    blob, expected = _capture(name)
    rep = _rep(blob)

    assert len(usagedb.records(rep)) == expected["records"]
    assert usagedb.cumulative_energy_kwh(rep) == expected["cumulative_kwh"]


def test_the_fridge_file_is_one_ordered_record_per_day():
    blob, _ = _capture("refrigerator_tp1x_ref_21k")
    parsed = usagedb.records(_rep(blob))

    timestamps = [r[0] for r in parsed]
    values = [r[1] for r in parsed]
    assert timestamps == sorted(timestamps)
    assert values == sorted(values)
    # ~181 consecutive days, so every gap is about one day.
    gaps = {round((b - a) / 86400) for a, b in itertools.pairwise(timestamps)}
    assert gaps == {1}


def test_the_third_field_is_not_interpreted():
    """It is the monthly bucket on this fridge, zero on a washer, and runtime
    hours on an ARTIK051_PRAC_20K. records() hands it back untouched and
    nothing downstream reads it yet."""
    blob, _ = _capture("refrigerator_tp1x_ref_21k")
    parsed = usagedb.records(_rep(blob))
    assert {r[2] for r in parsed} == {3, 4, 5, 6, 7, 8}


# ---------------------------------------------------------------------------
# Refusing what it cannot read
# ---------------------------------------------------------------------------


def test_a_uint64_leading_record_is_refused():
    """The ARTIK051_KRAC_18K shape (#301): <uint64 date><uint32 value>, where
    the value is cumulative *runtime hours*, not energy. Reading it as this
    module's layout would put hours in a kWh sensor.

    Keying on the leading field alone does not catch it, which is the trap
    worth pinning: when the date is a plain Unix timestamp the low half is a
    perfectly plausible leading uint32 and the high half reads as a value of
    zero. It is the zero that gives it away."""
    blob = struct.pack("<QI", 1_690_156_800, 14545)  # 2023-07-24, 1454.5 h
    assert len(blob) == usagedb.RECORD_SIZE
    assert struct.unpack("<III", blob) == (1_690_156_800, 0, 14545)

    assert usagedb.records(_rep(blob)) is None
    assert usagedb.cumulative_energy_kwh(_rep(blob)) is None


def test_a_multi_record_uint64_leading_file_is_refused():
    """The same trap across a whole file: every high half is zero, so the
    counter reads as flat rather than merely small."""
    blob = b"".join(struct.pack("<QI", 1_690_156_800 + i * 86400, 14545 + i * 5) for i in range(10))
    assert usagedb.records(_rep(blob)) is None


def test_a_counter_that_never_moves_is_refused():
    """Nothing to publish, and indistinguishable from the misread above."""
    assert usagedb.records(_rep(_synthetic([(500, 0), (500, 0), (500, 0)]))) is None


def test_a_zero_counter_is_refused():
    assert usagedb.records(_rep(_synthetic([(0, 0)]))) is None


def test_sqlite_is_refused():
    """#301 reports genuine SQLite on some washers behind the same href."""
    assert usagedb.records(_rep(b"SQLite format 3\x00" + b"\x00" * 100)) is None


@pytest.mark.parametrize(
    "blob",
    [
        b"",
        b"\x00" * 11,  # shorter than one record
        b"\x00" * 18,  # not a whole number of records
    ],
    ids=["empty", "too-short", "ragged"],
)
def test_a_payload_that_is_not_whole_records_is_refused(blob):
    assert usagedb.records(_rep(blob)) is None


def test_a_backwards_counter_is_refused():
    """Cumulative means cumulative. A decrease is a misread field, and would
    make a total_increasing sensor report a phantom meter reset."""
    assert usagedb.records(_rep(_synthetic([(100, 0), (90, 0)]))) is None


def test_records_out_of_time_order_are_refused():
    blob = struct.pack("<III", 1_780_086_400, 10, 0) + struct.pack("<III", 1_780_000_000, 20, 0)
    assert usagedb.records(_rep(blob)) is None


def test_an_implausible_gap_between_records_is_refused():
    """A daily file does not skip two years. A jump that large means the
    field being read as a timestamp is not one."""
    blob = struct.pack("<III", 1_780_000_000, 10, 0) + struct.pack("<III", 1_850_000_000, 20, 0)
    assert usagedb.records(_rep(blob)) is None


@pytest.mark.parametrize(
    "rep",
    [
        {},
        {usagedb.ITEMS_FIELD: []},
        {usagedb.ITEMS_FIELD: "not-a-list"},
        {usagedb.ITEMS_FIELD: [{"x.com.samsung.name": "/mnt/usage.db"}]},
        {usagedb.ITEMS_FIELD: [{usagedb.BLOB_FIELD: "not-bytes"}]},
    ],
    ids=["empty", "no-items", "items-not-a-list", "no-blob", "blob-not-bytes"],
)
def test_a_rep_without_a_usable_blob_is_refused(rep):
    assert usagedb.records(rep) is None
    assert usagedb.cumulative_energy_kwh(rep) is None


def test_a_repeated_reading_is_accepted():
    """22 of the washer's 281 records repeat the previous cumulative value
    (#301). Flat is not backwards."""
    parsed = usagedb.records(_rep(_synthetic([(100, 0), (100, 0), (110, 0)])))
    assert [r[1] for r in parsed] == [100, 100, 110]
