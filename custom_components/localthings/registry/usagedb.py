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


def cumulative_energy_kwh(rep: dict) -> float | None:
    """The newest record's cumulative energy, in kWh.

    The file is a once-a-day rollup, so this steps once a day rather than
    tracking the meter continuously -- see common.FILE_TRANSFER for why that
    is still worth an entity on an appliance that reports no meter at all.
    """
    parsed = records(rep)
    if not parsed:
        return None
    return parsed[-1][1] / 10
