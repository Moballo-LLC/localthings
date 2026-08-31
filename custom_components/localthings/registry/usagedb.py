"""Decode the usage history the appliance keeps at `/file/transfer/vs/0`.

Issue #301. The resource serves a file -- named `/mnt/usage.db` whatever it
actually is, a firmware default that matches neither name `/file/list/vs/0`
reports -- as a wrapper of items each carrying a raw `blob`. Despite the
name it is not SQLite on any board measured so far, but a flat array of
fixed 12-byte records.

Two record shapes are known, and telling them apart matters for correctness
rather than tidiness, because **their second field means different things**:

    uint32-leading   <uint32 LE timestamp><uint32 LE cumulative energy,
                      tenths of a kWh><uint32 LE family-specific>
    uint64-leading   the ARTIK051_KRAC_18K shape (#301), whose value field
                      is cumulative *runtime hours*, not energy

Publishing one as the other would put hours in a kWh sensor, so the
`uint32`-leading shape has to be positively identified rather than assumed:
`records` returns nothing unless the whole file reads as a plausible,
ordered, daily-ish series whose counter actually moves. #329 proposed keying
on the leading field, which is necessary but *not* sufficient -- a
`uint64`-leading record whose date is a plain Unix timestamp puts that
timestamp in the low half and zeros in the high half, so it reads as a valid
leading `uint32` followed by a value of 0. That is why a counter which is
zero, or which never moves across the file, is refused too: on the shape
this module can read those mean "nothing to report", and on the shape it
cannot they are the tell.

The third field is deliberately not interpreted here. It is zero on a
`DA_WM_TP1_21_COMMON` washer and on a dishwasher, the firmware's monthly
billing bucket on a `TP1X_REF_21K` fridge, and cumulative runtime hours on
an `ARTIK051_PRAC_20K` -- four families, one byte layout, three meanings,
and nothing in the bytes to say which. See
docs/investigations/file-transfer-usage-db.md.
"""

from __future__ import annotations

import itertools
import struct

ITEMS_FIELD = "x.com.samsung.items"
BLOB_FIELD = "x.com.samsung.blob"

RECORD_SIZE = 12
_RECORD = struct.Struct("<III")

# A timestamp outside this window is the tell that the leading field is not a
# timestamp at all. Lower bound predates every capture on record; upper bound
# is comfortably inside what a uint32 can hold (2106).
_TS_MIN = 1_420_070_400  # 2015-01-01
_TS_MAX = 4_102_444_800  # 2100-01-01

# Records are written once a day. A gap longer than this is not a slow
# appliance, it is a misread field -- the files skip days the appliance did
# not run, so the bound is generous rather than tight.
_MAX_GAP_S = 60 * 86400


def _blob(rep: dict) -> bytes | None:
    """The first item's raw blob, or None if this rep carries none."""
    items = rep.get(ITEMS_FIELD)
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if not isinstance(first, dict):
        return None
    blob = first.get(BLOB_FIELD)
    return blob if isinstance(blob, bytes) else None


def records(rep: dict) -> list[tuple[int, int, int]] | None:
    """Every record in `rep`'s blob, or None if it is not the uint32-leading
    shape this module can read.

    None means "not understood", never "empty": an unrecognized payload has
    to produce no entity at all rather than a plausible-looking wrong number.
    """
    blob = _blob(rep)
    if not blob or len(blob) < RECORD_SIZE or len(blob) % RECORD_SIZE:
        return None

    out: list[tuple[int, int, int]] = []
    for offset in range(0, len(blob), RECORD_SIZE):
        timestamp, value, third = _RECORD.unpack_from(blob, offset)
        if not _TS_MIN <= timestamp <= _TS_MAX:
            return None
        if out:
            previous_ts, previous_value, _ = out[-1]
            # Strictly increasing in time, never decreasing in a cumulative
            # counter, and no implausible jump between neighbours.
            if timestamp <= previous_ts or timestamp - previous_ts > _MAX_GAP_S:
                return None
            if value < previous_value:
                return None
        out.append((timestamp, value, third))

    # A cumulative energy counter that is zero, or that never moved across
    # the whole window, has nothing to publish either way -- and is what a
    # misread uint64-leading file looks like (see the module docstring).
    if out[-1][1] <= 0:
        return None
    if len(out) > 1 and out[-1][1] <= out[0][1]:
        return None
    return out


# A month label is 1..12 and steps by one. A runtime counter is far larger
# and moves in half hours -- "all 360 deltas across both units divisible by 5
# in tenths" (#301), "every delta on every unit is divisible by 5 tenths"
# (#329). The +1 of a month rollover is what that test rejects.
_MAX_MONTH = 12
_RUNTIME_QUANTUM = 5  # tenths of an hour


def _is_runtime_series(values: list[int]) -> bool:
    """True when the third field is a cumulative runtime counter rather than
    the firmware's monthly bucket or a constant zero.

    Deliberately strict, because the answer decides both whether runtime is
    published *and* how the energy field is scaled -- see
    `cumulative_energy_kwh`.
    """
    if values[-1] <= _MAX_MONTH:
        return False
    deltas = [b - a for a, b in itertools.pairwise(values)]
    if any(delta < 0 for delta in deltas):
        return False
    if not any(deltas):
        return False
    return all(delta % _RUNTIME_QUANTUM == 0 for delta in deltas)


def cumulative_energy_kwh(rep: dict) -> float | None:
    """The newest record's cumulative energy, in kWh.

    The file is a once-a-day rollup, so this steps once a day rather than
    tracking the meter continuously -- see common.FILE_TRANSFER for why that
    is still worth an entity on an appliance that reports no meter at all.

    Refused on a board whose third field is a runtime counter, because the
    energy field's *scale* is not the same there. Every family whose third
    field is zero or a month label stores tenths of a kWh, confirmed against
    each one's own `cumulativePower`: a washer (#301), a dishwasher and a
    fridge. The one family that pairs energy with runtime, the
    `ARTIK051_PRAC_20K` of #329, stores plain Wh -- its `fieldA` equals
    `cumulativePower` outright rather than a hundredth of it. Nothing in the
    bytes distinguishes 5118585 Wh from 5118585 tenths of a kWh, so reading
    that board with this scale would report 511858.5 kWh instead of 5118.6.

    That board's meter works, so it never reaches this fallback anyway; this
    refuses on the shape rather than relying on that.
    """
    parsed = records(rep)
    if not parsed:
        return None
    if _is_runtime_series([r[2] for r in parsed]):
        return None
    return parsed[-1][1] / 10


def cumulative_runtime_hours(rep: dict) -> float | None:
    """The newest record's cumulative running time, in hours.

    The one number on a multi-head system that is genuinely per-unit: #329
    read three heads of one multi-split and found this field differing per
    head (6270.0 h, 799.5 h, 1660.0 h) while the energy field beside it was
    the shared outdoor unit's, identical on all three.

    Only the `uint32`-leading shape is read. The `ARTIK051_KRAC_18K` of #301
    keeps runtime too, but in the `uint64`-leading layout `records` refuses,
    and no capture of one exists to write a decoder against.
    """
    parsed = records(rep)
    if not parsed:
        return None
    values = [r[2] for r in parsed]
    if not _is_runtime_series(values):
        return None
    return values[-1] / 10
