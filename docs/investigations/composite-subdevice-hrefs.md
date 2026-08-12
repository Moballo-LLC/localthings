# Composite AC subdevices: where else a sibling's hrefs could live

Open question behind issue #335 (`ARTIK051_FAC_BORA_19K`, a 2-in-1 floor +
wall AC): the board reports a sibling in `/subdevices/vs/0`'s
`subdeviceIdList`, but every seed `registry/subdevices.enumerate_subdevices`
tries comes back 4.04, so `subdevices` and `subdevices_skipped` are both
empty and the wall unit never becomes an entity.

This file records what that actually rules out (less than it looks like),
why, and which hrefs are worth reading next.

## `/oic/res` does not enumerate the resource tree on modern firmware

This is the finding that reopens the question. Across every fixture that
carries a captured `/oic/res`:

| board | links | `sec:true` | lists `/device/0`? |
| --- | --- | --- | --- |
| `ARTIK051_DONGLE_FAC_18K` | 91 | 78 | yes |
| `TP2X_FAC_BORA_21K` (2-in-1) | 17 | 6 | no |
| `TP2X_FAC_BORA_21K` (#205 flat) | 17 | 6 | no |
| `TP1X_DA_KS_RANGE_0101X` | 10 | 6 | no |
| `AWM-WW-AID-26-ONEBODY` | 15 | 9 | no |
| `ARTIK051_FAC_BORA_19K` (#335) | 18 | 6 | no |

The `ARTIK051_DONGLE_FAC_18K` board — the one Pattern A was built against —
is the outlier, not the model. Everywhere else `/oic/res` lists the
onboarding surface and nothing else: `/oic/d`, `/oic/p`, the security and
EasySetup/WiFiConf/CoapCloudConf/DevConf resources, file transfer, and the
`sec/*` pair. On issue #335's board the six `sec:true` links are exactly
doxm, pstat and the four setup URIs; every other listed link is `sec:false`.
The entire secure operational tree — `/device/0` included, which
demonstrably answers, since the dump comes from it — is absent.

Two consequences, both load-bearing:

1. **Nothing is learned from an href's absence in `/oic/res`.** On the range
   board (issue #324) `/oic/res` lists ten onboarding links and no
   `/device/0`, yet `/device/1` answers a full indexed dual-cavity sibling.
   It was found only by `_SPECULATIVE_DEVICE_INDICES`, never by enumeration
   of the links.
2. **Pattern A's `/oic/res` index scan is dead weight on these boards.** It
   contributes nothing anywhere except the dongle board, so in practice
   indexed siblings are found by the speculative `/device/1`, `/device/2`
   probe alone.

## What issue #335 has actually ruled out

All 26 probes in the report returned false. Twenty-three of them are the
issue #205 flat fallback walking the master's own href list under the
sibling's UUID prefix, plus `/<uuid>/device/0`, `/device/1`, `/device/2`
and `/multidevice/vs/0`. Four more were read by hand from the issue thread
(`/<uuid>/information/vs/{1,2}`, `/<uuid>/device/{1,2}`), all 4.04.

So what is ruled out is: the UUID-prefixed namespace (Pattern B/C), and the
indexed **Collection** (`/device/<n>`). What has never been read on this
board — or on any `FAC_BORA` board — is **a bare indexed leaf**:
`/mode/vs/1`, `/temperatures/vs/1`, and friends. Every indexed href ever
probed by this project arrived via a `/device/<n>` batch; none was ever
GETed directly.

That gap matters because the "leaves exist, their Collection does not" shape
is already confirmed on this exact product family, just in the other
namespace: issue #205's `TP2X_FAC_BORA_21K` answers
`/<uuid>/information/vs/0` while `/<uuid>/device/0` comes back empty. A
board that mounts sibling leaves without mounting a sibling Collection is
the documented BORA behavior, so `/device/1`'s 4.04 is evidence about the
Collection and not about `/mode/vs/1`.

## What the OCF spec says about composite devices

The Core/Device specifications model this as a *Composite Device*: one
Platform representing the whole appliance, `/oic/d` carrying the Device
Types of every constituent Device, and — the relevant part — a **Collection
per distinct Device in the composition**, each Collection's `rt` including
the Device Type it represents.

Issue #335's `/oic/d` reports `["oic.wk.d", "oic.d.airconditioner"]`, which
is consistent with a two-indoor-unit composite (both constituents are air
conditioners, so the type appears once) and equally consistent with a single
unit. It does not discriminate.

The Collection half does suggest something untried. `x.com.samsung.devcol`
is Samsung's Collection type, carried by `/device/0` — and on the dongle
board `/oic/res` advertises a second resource with the same
`["x.com.samsung.devcol", "oic.wk.col"]` pair: **`/sec/devices`**. A
collection of devices, sitting alongside `/device/0`, never read by this
project or by any issue thread. If the composite enumeration is exposed
anywhere as a first-class resource, that is the shape it would take.

## Hrefs worth reading next, in priority order

All plain RETRIEVEs via `localthings.read_resource`; a wrong guess costs one
round trip.

**1 — indexed leaves (the untried namespace).** `/information/vs/1` first:
on the TP2X 2-in-1 the sibling's `/information/vs/0` is what identified the
wall unit by `modelNum`, so a populated `/information/vs/1` both proves the
namespace and names the unit.

    /information/vs/1
    /power/vs/1
    /mode/vs/1
    /temperatures/vs/1
    /temperature/current/1
    /airflow/vs/1
    /sensors/vs/1
    /power/1

**2 — the device Collection.** `/sec/devices`, per the clause above.

**3 — a positive control for the UUID namespace.** `/oic/res` advertises
three UUID-prefixed file resources on this board, so at least one path under
that prefix is supposed to answer:

    /c24e25e9-55dd-ba18-d567-000000000001/file/list/vs/0
    /file/list/vs/0

The master's own href is the baseline. If the prefixed one answers, the
prefix routes and the sibling's operational resources are genuinely not
mounted there — stop probing that namespace on this family. If it 4.04s
while the master's answers, the prefix is advertised but unrouted, which
says the `/oic/res` advertisement is scaffolding and is worth knowing before
trusting it for Pattern C elsewhere.

**4 — only if index 1 shows anything:** repeat tier 1 at index 2.

## Dead ends, so they aren't re-tried

- `/hass/state/vs/0`, `/hass/command/vs/0` — advertised in `/oic/res` on
  every board here, and indexed per subdevice on the dongle board
  (`/hass/state/vs/{0,1,2}`), which makes them look like a subdevice-aware
  state channel. They are not: 4.04 on every interface on
  `ARTIK051_KRAC_18K` (see `ac-filter-reset.md`). Cheap enough to retry once
  on #335's newer build, but expect nothing.
- `/multidevice/vs/0` — probed, 4.04. Absent on this board; only the dongle
  family exposes it.
- `/actions/vs/0` — GET returns `{}` on baseline and `oic.if.a`; publishes
  no schema (`ac-filter-reset.md`).

## If tier 1 comes back empty

A full 4.04 sweep is a real result, not a failed one: it would mean the
sibling is named in `subdeviceIdList` for the cloud's benefit and has no
local resource surface at all on this firmware. That closes issue #335 as a
firmware limitation rather than leaving it open against a probe strategy
that was never actually exercised.
