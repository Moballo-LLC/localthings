# Reading `/file/transfer/vs/0`: the on-appliance usage history

Design notes for issues #301 (`ARTIK051_KRAC_18K`, runtime hours), #329
(`ARTIK051_PRAC_20K` multi-split, energy + per-head runtime) and #285
(`DA_WM_TP1_21_COMMON` washer, energy that outlives `cumulativePower`).

Every appliance in those three reports keeps a rolling usage history in a
file at `/file/transfer/vs/0`, and none of them can reach it through this
integration. This file records why, what the design has to solve, and which
questions have to be answered before any of it can be written.

Nothing here is implemented. The reporters' parsers exist on their own
branches; this is the shape they would have to land in.

## What the reporters established

| board | payload | series it carries |
| --- | --- | --- |
| `ARTIK051_KRAC_18K` (#301) | 181 × 12 B flat records | cumulative runtime, tenths of an hour |
| `ARTIK051_PRAC_20K` (#329) | 181 × 12 B flat records, three `uint32` fields | shared outdoor-unit Wh **and** per-head runtime |
| `DA_WM_TP1_21_COMMON` (#285/#301) | 281 × 12 B flat records | cumulative Wh, third field always zero |
| some washers (#301) | genuine SQLite | a `power_usage` table |

Common to all of them: the file is named `/mnt/usage.db` regardless of what
it actually contains, values are cumulative rather than per-day, one record
per day the appliance actually ran (so the window is ~181 *operating* days,
not 181 calendar days), and the series does not start at zero.

Two corroborations worth keeping. On the washer, the file's last record
equals `/energy/consumption/vs/0`'s `cumulativePower` and `cumulativeDate`
exactly — the file is the same meter, persisted. On the multi-split, the
shared field equals `cumulativePower` on all three heads while the runtime
field differs per head — the file carries both the thing that is
legitimately shared and the thing that is legitimately per-unit, side by
side.

## `/file/list/vs/0` answers, and it changes what the probe has to do

Read live off a dishwasher (2.05, `oic.if.s`, no write and no selection
first):

```yaml
rep:
  x.com.samsung.items:
    - x.com.samsung.id: '0'
      x.com.samsung.name: /opt/data/energy.db
    - x.com.samsung.id: '1'
      x.com.samsung.name: /opt/data/hass.db
```

Three things follow.

**The filename is family-dependent, so nothing may hardcode it.** The ACs in
#301/#329 and the washer in #285 all serve `/mnt/usage.db`. This dishwasher
has no such path: its history is `/opt/data/energy.db`. A design that keys
on the name `usage.db` — or that assumes one file — is already wrong on the
third family anyone points it at.

**There is a selection step, and it has already been exercised.** The items
carry an `x.com.samsung.id`, and `/file/transfer/vs/0` is declared
`oic.if.a` in `/oic/res` while `/file/list/vs/0` is `oic.if.s`. That reads
as list-then-select-then-read, and `ac-filter-reset.md` confirms the second
half from a live `ARTIK051_KRAC_18K`: *"`/file/transfer/vs/0` serves only
`/mnt/usage.db`; selecting another path returns 4.05/4.00."* So a selection
write exists, the firmware validates what it is handed, and on that board it
refused everything but the primary file. What is still unknown is whether a
bare GET returns the primary file without any selection — which is what both
reporters appear to have done, on appliances whose primary file was the one
they wanted anyway.

**`hass.db` is Samsung's, not ours.** It sits beside `energy.db` here and
beside `/mnt/usage.db` on the KRAC, and it pairs with the `/hass/state/vs/0`
and `/hass/command/vs/0` hrefs that three fixtures advertise in `/oic/res`.
Those return 4.04 on every interface (`ac-filter-reset.md`), so this is
unimplemented vendor scaffolding that happens to collide with Home
Assistant's own shorthand. Worth stating once so nobody spends an afternoon
on it.

One correction to #301 while here: it writes off `/file/information/vs/0` as
carrying "no file list, no size", which is true and is no longer the point —
`/file/list/vs/0` is the enumeration resource. But that href's
`x.com.samsung.timeoffset` is the device's own UTC offset, and every format
on record stamps records at day or hour granularity in device-local terms.
It is not a dead resource; it is the thing that makes a record's date mean
something.

## The first blob read through the integration

Measured 2026-08-31 with `read_resource`, no query string and no selection
write, on the same appliance as the `/file/list/vs/0` read above:

```yaml
code: '2.05'
rep:
  rt: [x.com.samsung.file.transfer]
  if: [oic.if.baseline, oic.if.a]
  x.com.samsung.items:
    - x.com.samsung.name: /mnt/usage.db
      x.com.samsung.blob: {len: 12, sha256: 2e956b57…, base64: UEiTal8GAAAAAAAA}
```

The blob is `50 48 93 6a 5f 06 00 00 00 00 00 00`, and it decodes without
ambiguity as **one 12-byte record in the washer layout** from #285/#301:

| field | bytes | value | reading |
| --- | --- | --- | --- |
| `uint32` LE | `50 48 93 6a` | 1788037200 | 2026-08-29 21:00:00 UTC — on the hour |
| `uint32` LE | `5f 06 00 00` | 1631 | 163.1 kWh at the tenths scale |
| `uint32` LE | `00 00 00 00` | 0 | the always-zero third field |

Every detail matches that format's description: hour-granularity timestamps,
a tenths-of-a-kWh cumulative value, a third field that is zero. Read as the
KRAC's `uint64`-leading shape instead, the leading field is 7.0e12 — not a
date, so the two layouts are already separable on one record, which is the
discriminator #329 proposed working as advertised.

Three things this settles, and two it opens.

**A plain GET answers.** No `?if=oic.if.baseline`, and the rep came back
*with* `rt`/`if`, which is the baseline interface's own shape — so the query
the reporters used is not required to get the baseline view. Question 1 is
answered for the read path.

**No selection write was needed to get a payload.** Half of question 2 is
answered: the resource is not inert until written to.

**The encoder works end to end on real hardware.** The blob left the
appliance, survived CBOR decode, base64, JSON and the websocket, and the
recorded SHA-256 matches the 12 bytes it rebuilds to.

**But there is only one record.** The reporters measured 2172 B (181
records) and 3372 B (281 records). Twelve bytes is a single row. Either this
appliance's history genuinely holds one record, or a bare GET returns only
the newest one and the rest of the file needs the query, a selection, or
`/file/transfer/chunk/vs/0`. *(Resolved by the fridge below: the same call
returns a full 2172-byte file, so this appliance's history really is one
record.)*

**And the name is wrong for this appliance** — confirmed same unit, not an
inference across two devices. `/file/list/vs/0` on it lists
`/opt/data/energy.db` and `/opt/data/hass.db`. It does not list
`/mnt/usage.db` — which is what `/file/transfer/vs/0` just called what it
served. Either the name field is a firmware default rather than a statement
about what was read (it would be the same default the ARTIK051 boards serve,
where it happens to name the real file), or the two resources disagree.
Until that is resolved, `x.com.samsung.name` cannot be trusted to identify
the payload, and §3's rule — read the magic bytes, treat the name as a prior
— is load-bearing rather than cautious.

**The corroboration ran, and both fields match exactly.**
`/energy/consumption/vs/0` on the same appliance, minutes later:

```yaml
x.com.samsung.da.cumulativePower: '163100'      # 163.1 kWh — the blob's 1631/10
x.com.samsung.da.cumulativeDate: '1788037200'   # the blob's timestamp, to the second
x.com.samsung.da.cumulativeDateUTC: '1788055200'
x.com.samsung.da.instantaneousPower: '-500'
```

So the record is the live meter's current value, exactly as #301's washer
comment found for the last record of its file. The reading of the layout is
confirmed on hardware, not inferred.

Two things follow that were not obvious before the read.

**The blob's timestamps are in `cumulativeDate`'s frame, not UTC.** The
board reports both, and they differ by 18000 s — five hours — with the blob
agreeing with the bare field, not the `...UTC` one. Whatever that frame is,
a parser must not treat a record stamp as a Unix UTC instant. Note that this
appliance's `/file/information/vs/0` reports `timeoffset: "+00:00"`, which
does not account for five hours; the offset resource and the energy resource
disagree here, and neither has been shown right. Recorded, not resolved.

**On this appliance the file is pure duplication.** Its one record carries
the same two numbers `/energy/consumption/vs/0` already publishes, and
`energy_kwh` is already an entity from them. That is a genuinely useful
negative result: it measures what §4 argued from first principles, that the
file's energy series is worth an entity only where the meter resource cannot
supply one — #285's washer, where `cumulativePower` vanishes — or where the
file carries something the meter resource does not, which is the ACs'
per-unit runtime hours. On a healthy dishwasher it carries nothing new.

It also lowers the stakes of the one-record question. The reason to want the
other 180 rows was history, and §5 already rules backfilling out of scope.
If a bare GET serves the newest row, that row is all a `total_increasing`
sensor ever needed.

Both reads are banked in `tests/fixtures/dishwasher_device.json` under
`probes`, with the caveats in its `probes_note`: they were read later than
that fixture's `device0`, so the blob's record does not line up with the
energy rep captured there, and nothing should assert that it does.

(`instantaneousPower: -500` is the dead sentinel `common.ENERGY_METER`
already gates `power_watts` out on, documented for exactly this device
class in issue #6. Nothing new, but it confirms the appliance is behaving
as the registry expects.)

One caveat worth stating before a parser is written against this: a single
record with a small value cannot tell `uint32` value + zero padding apart
from a little-endian `uint64` value — both read 1631 here. #301's comment
reports the third field as zero across all 281 of its records, which is
equally consistent with either. The distinction only shows up on a device
whose cumulative value has exceeded 2^32 tenths, so the parser should not
claim to have resolved it.

## A full 181-record file, off a fridge

Measured 2026-08-31, same plain `read_resource` GET, on a **different**
appliance — a fridge. Its `/file/list/vs/0` is byte-identical to the
dishwasher's (`/opt/data/energy.db` id 0, `/opt/data/hass.db` id 1), and its
`/file/transfer/vs/0` again calls what it serves `/mnt/usage.db`. This time
the blob is **2172 bytes — 181 records, zero remainder**, the same size #301
measured on a `KRAC_18K`. The SHA-256 the service reported rebuilds from the
base64 exactly, so these are the appliance's own bytes end to end.

Same layout as the dishwasher's single record, and it holds for all 181:

```
record := <uint32 LE timestamp><uint32 LE cumulative, tenths of a kWh><uint32 LE month>
```

| | first | last |
| --- | --- | --- |
| timestamp | 2026-03-03 22:54 | 2026-08-30 21:05 |
| cumulative | 7748 = 774.8 kWh | 11410 = 1141.0 kWh |
| third field | 3 | 8 |

- **181 records, 181 distinct consecutive calendar days, no gaps.** A fridge
  runs every day, which fits #301's "one record per day the unit actually
  ran" without contradicting it.
- Timestamps are strictly increasing and cluster between 21:05 and 23:59 —
  an end-of-day rollup, not the on-the-hour stamps the washer and dishwasher
  show.
- The cumulative field is strictly increasing across every one of the 180
  deltas, mean **2.03 kWh/day**, which is what a fridge draws. The deltas are
  *not* multiples of 5 tenths, which is the check that separates this from
  the ACs' half-hour-quantised runtime counter.

### The third field tracks the calendar month

Not zero here, and not runtime. It runs 3, 4, 5, 6, 7, 8 across a file
spanning March to August, and it steps on exactly the five month boundaries.
Tested against every record: `month(timestamp + offset) == field3` holds for
**all 181** at any offset from +1 h to +12 h, and fails on exactly 5 at +0 h
— those five being the last day of each month, where the stamp is late
enough in the day that the month has already turned in whatever frame the
counter is kept in.

A full diagnostics download from the fridge settles the frame, where the
blob alone could not. It reports `/file/information/vs/0`
`timeoffset: "-05:00"` and `/timezone/vs/0` `America/Chicago`, DST on — so
the device is at UTC−5, and the record stamps read as local wall time put
every write between 21:05 and 23:59 **local**, i.e. a rollup just before
local midnight. That also explains the dishwasher's `cumulativeDate` sitting
exactly 5 h behind its `cumulativeDateUTC`: the bare field is local-as-epoch
and the `...UTC` one is real UTC, the same pair of frames the firmware keeps
everywhere.

With the offset known, the five month-boundary records say something
sharper. `month(ts + 5 h) == field3` holds for all 181 — that is, **field 3
is the calendar month in UTC while the timestamp is local**. The two
descriptions of the field, "UTC calendar month" and "the bucket label of
the next section", are the same fact: the monthly counter rolls at UTC
midnight, which lands mid-evening local, so the first record of a new bucket
is stamped on the last local day of the old month.

Worth recording that the timezone story looked unfalsifiable from the blob
alone — any offset from roughly +46 min to +21 h made the five mismatches
vanish — and only the device's own reported offset picked one out.

### The month field is the firmware's own billing bucket, and it reconciles exactly

The fridge is a `TP1X_REF_21K`, and its `/energy/consumption/vs/0` carries
the monthly pair this registry already maps to `energy_last_month_kwh` and
`energy_this_month_kwh`:

```yaml
x.com.samsung.da.cumulativePower: '1141129'          # 1141.129 kWh
x.com.samsung.da.monthlyConsumption: '68200'         # 68.200 kWh
x.com.samsung.da.thismonthlyConsumption: '59229'     # 59.229 kWh
x.com.samsung.da.instantaneousPower: '151'
```

Group the blob's records by field 3 and the two monthly figures fall out of
the file exactly:

| bucket | first record | last record | span |
| --- | --- | --- | --- |
| 7 | 2026-06-30 23:14, 1011.2 | 2026-07-30 22:57, 1079.4 | **68.2 kWh** |
| 8 | 2026-07-31 23:33, 1081.9 | 2026-08-30 21:05, 1141.0 | 59.1 kWh |

- **July, a complete month: 68.2 kWh from the blob against
  `monthlyConsumption` 68.200 kWh.** Exact.
- **August, still running: 59.1 kWh to the last record, plus the 0.129 kWh
  the meter has moved since it was written, is 59.229 — against
  `thismonthlyConsumption` 59.229 kWh.** Exact.
- Independently: `cumulativePower − thismonthlyConsumption` is 1081900 Wh,
  which is the first field-3=8 record's cumulative value **to the watt**.

So field 3 is not merely "the calendar month". It is the label of the
firmware's own monthly bucket, and the record carrying that label first *is*
the baseline the monthly counter measures from. That is why the label steps
on the last day of the previous calendar month rather than the first day of
the new one, and it is a better explanation of the +0 h mismatches above
than any timezone story.

The bucket is `cum(last record labelled M) − cum(first record labelled M)`,
which quietly drops the final day: July's bucket closes 2026-07-30 at
1079.4 and August's opens 2026-07-31 at 1081.9, so that day's 2.5 kWh lands
in neither. That is Samsung's arithmetic, not a decode error — the whole
point is that the blob reproduces it exactly, quirk included.

The appliance is a `TP1X_REF_21K` / `RF29DB9750QLAA`, and its diagnostics
download confirms it is **not** any of the six `TP1X_REF_21K` fixtures
already in the corpus — its `modelNum` third field (`00176141|0000085003…`)
matches none of them. It also reports `unbound_hrefs: []`, so it needs no
new capability work; the reason to bank it as a fixture is the usage file,
not entity coverage.

One detail from that download worth keeping: both file hrefs appear in the
dump's `resources` map even on a build with no probe tier, because
`_raw_read_blocking` applies whatever it reads to the state cache. So a
maintainer who runs `read_resource` and *then* downloads diagnostics gets
the probed resources in the dump for free — a usable stopgap for gathering
the census in §3's step 3 before the probe tier exists.

Three things this pins down at once. Field 2 is beyond doubt the same
counter as `cumulativePower` — it now agrees with the live meter, with the
completed month, and with the running month, on three independent
subtractions. The 129 Wh between the last record and the live meter is about
1.5 h of running at this fridge's own average, which is what a once-a-day
rollup should look like. And on a board that reports no monthly pair, the
file could *supply* one rather than merely corroborate it.

### What that means for a parser

**Field 2 is the portable one.** Cumulative energy in tenths of a kWh is now
confirmed on four families: the washer (#285/#301), the dishwasher and this
fridge (both measured here, the fridge three ways over), and the
`ARTIK051_PRAC_20K`'s `fieldA` (#329, which matched `cumulativePower`
exactly). That is the value worth an entity.

**Field 3 is not one thing, and must never be published on a guess.**

| family | field 3 |
| --- | --- |
| `DA_WM_TP1_21_COMMON` washer (#301) | zero across all 281 records |
| this dishwasher | zero (its only record) |
| this `TP1X_REF_21K` fridge | the firmware's monthly bucket label, 3 → 8 |
| `ARTIK051_PRAC_20K` AC (#329) | cumulative runtime, tenths of an hour |

Four families, three different meanings, one identical byte layout. §3's
discriminator separates the `uint64`-leading shape from the `uint32`-leading
one, and that much is sound — but nothing in the bytes says what the third
field *means*. A parser reads field 2 everywhere and treats field 3 as
unknown until a family rule says otherwise.

### Two open questions close

**A plain GET returns the whole file.** Same integration, same call, no
query and no selection write: 12 bytes from the dishwasher, 2172 from the
fridge. So the dishwasher genuinely holds one record — its history is nearly
empty, not truncated by the transport. `/file/transfer/chunk/vs/0` is not
needed for files this size, and question 2's remainder is answered.

**`/mnt/usage.db` is a firmware default label, not a per-device fact.** Two
different appliance families, both listing `energy.db` and `hass.db` in
`/file/list/vs/0`, both reporting `/mnt/usage.db` from the transfer
resource. A hardcoded string, which is also why it happens to name the real
file on the ARTIK051 boards. `x.com.samsung.name` identifies nothing; §3's
"read the magic bytes" is the rule.

## The blocker is real and it is not board-specific

`discovery.discover()` walks whatever `parse_device0_batch` found, i.e. the
`/device/0` batch. `/file/transfer/vs/0` is not in it.

That is not a quirk of the reporters' boards. Across the 83 `*_device.json`
fixtures in this repository that carry a `/device/0` batch:

- `/file/information/vs/0` appears in the batch on **63** of them.
- `/file/transfer/vs/0` appears in the batch on **none** of them.
- Of the six fixtures that captured an `/oic/res`, three list
  `/file/transfer/vs/0`, `/file/list/vs/0` and `/file/transfer/chunk/vs/0`
  as links: `ARTIK051_DONGLE_FAC_18K` and both `FAC_BORA` captures, the
  latter two also carrying UUID-prefixed per-subdevice copies. Of the other
  three, one captured no links at all and two list no `/file/*` href — and
  per `composite-subdevice-hrefs.md`, an href's absence from `/oic/res`
  proves nothing about whether it answers.

So the file surface is a firmware-wide convention: advertised in `/oic/res`,
withheld from the batch, and therefore invisible to every capability this
registry can currently declare. A `Capability(href="/file/transfer/vs/0")`
silently binds nothing — #301 deployed exactly that on live hardware and got
no entity, which is the registry working as designed.

There is already one precedent for the same gap. `/multidevice/vs/0` is
listed in `/oic/res` on the Pattern A board and absent from its batch, and
`enumerate_subdevices` gives it its own RETRIEVE and folds the result into
the merged resources dict before discovery runs. That is the seam this
design widens.

## Design

### 1. Reachability: a probe tier, not a general cold-tier rewrite

*Built.* `Capability(poll_tier="probe")` → `registry.PROBE_HREFS` →
`coordinator._probe_blocking`. What follows is the reasoning; two details
came out differently in the code and are marked below.

`poll_tier` today has three values and only two behaviours: `hot`/`warm`
hrefs get individual sub-poll GETs, `cold` means "refreshed by the batch and
nothing else". What #301 asks for is a fourth behaviour — *read this href
individually because the batch will never carry it*.

Proposal: a `probe` tier. A capability declares `poll_tier="probe"`; the
registry exposes the set of probe hrefs as a module-level constant derived
from `CAPABILITIES` at import, because discovery cannot tier an href it
never sees. The coordinator then:

- probes those hrefs once inside the first-discovery block, folding the
  results into `resources` **before** `_run_discovery` — same position and
  same posture as `_enumerate_subdevices_blocking`, so a probe that fails
  costs the entities on that href and nothing else;
- re-reads them on a slow cadence afterwards, driven by a cycle counter in
  `_async_update_data` rather than a new timer. A counter that ticks in
  half hours and gains one record a day does not need the 30 s summary
  interval; once every 30–60 minutes is generous.
- maps each href through `subdevice.to_actual()`, so a composite
  appliance's siblings are probed alongside MAIN. This is not optional
  polish: #329 read three heads of one multi-split and got three distinct
  blobs, each pairing the shared outdoor-unit energy counter with *that
  head's own* runtime hours. Those heads were three config entries on three
  IPs, so MAIN alone would have reached them — but a composite board (issue
  #177) puts the same several indoor units behind one IP as subdevices, and
  `/oic/res` on the `FAC_BORA` fixtures advertises
  `/<uuid>/file/transfer/vs/0` for exactly that. Probing MAIN alone there
  reads the master's file and silently misses every sibling's, losing the
  one number that is genuinely per-unit.

  Siblings are probed *after* `_run_discovery`, not beside MAIN's probe:
  before it, `self.subdevices` is still the pre-narrowing candidate list,
  and the probe applies what it reads straight to the state cache — which is
  the one thing that block's ordering exists to keep a rejected candidate's
  resources out of.

Cost when nothing is there: one GET per probe href per probe interval, which
404s. That is why the probe list must stay short and maintainer-curated.

`/file/list/vs/0` belongs on that list ahead of `/file/transfer/vs/0`, and
not only for diagnostics. Since the filename varies by family — `usage.db`
on the ACs and the washer, `energy.db` on the dishwasher — the list is how
the probe knows *which* file it is about to read, and by extension which
parser to reach for and whether a history exists on this board at all. It is
`oic.if.s`, it answers a plain GET, and its reply is a handful of short
strings, so it is cheap enough to read on every probe cycle rather than
cached from first discovery.

Whether reading the file itself is one GET or a select-then-GET is open —
see question 2 below, which is the single fact this section's cadence
depends on.

### 2. Decode in the registry, never cache the blob

*Superseded in part — the constraint that motivated it is gone.* This
section argued the blob must never reach `StateCache`, because the snapshot
store would fail to encode it and diagnostics would choke. `registry/encode.py`
(shipped in v0.25.0) removed both problems, so the probe caches the rep as
the appliance sent it and the blob reaches diagnostics intact — which is the
census this whole design turns on. `Capability.project` stays unused and
stays the right home for a decoder when there is one to write. The reasoning
below is still why a *parser* belongs in the registry rather than the
coordinator.

The rep is not a Property map of scalars. It is a wrapper —
`x.com.samsung.items` → `[{x.com.samsung.name, x.com.samsung.blob}]` — whose
payload is 2–3 KB of binary.

That blob must not reach `StateCache`. `_async_save_snapshot` writes the
first cycle's resources to a JSON `Store` (bytes would fail the encode and
cost the entry its offline rehydration), diagnostics dumps `last_resources`
into every issue report, and `_on_cache_changed` fires a full entity push on
any change. None of those want a kilobyte of binary.

So the probe applies a *projected* rep: parse the blob, cache the scalars.

`Capability.project(rep, resources) -> dict` is already declared on the
dataclass and is currently dead code — nothing reads it. This is what it was
for. Wiring it into the probe path (and only the probe path) keeps the
parser in the registry next to the capability that consumes it, keeps the
coordinator ignorant of record layouts, and leaves `SensorDesc(field=...)`
working unchanged against the projected keys. Synthetic keys follow the
existing convention, `x.localthings.*`, as `cloudcourse.FIELD` already does.

### 3. Discriminate on record shape, not on a model token

#329 is right that the parser should key on the payload. It is also what
this registry does everywhere else — `is_legacy_board` gates on which href
is present, not on a model string.

- `SQLite format 3\0` in the first 16 bytes → the SQLite variant.
- length divisible by 12 with no remainder → a flat record array; pick the
  layout by *validating* candidates across the whole file (a plausible,
  monotonic Unix timestamp in the leading `uint32`, versus the `uint64`
  leading field of the KRAC shape) rather than guessing from one record.
- anything else → parse nothing, log once, bind nothing. An unrecognized
  format is a coverage gap for a human, not a reason to publish a number.

The validator is the load-bearing part: three 12-byte layouts are already
known and a fourth is likely, so "does this decode into a monotonic series"
has to be a test the parser runs, not an assumption it makes.

Discriminating the layout is not the same as understanding it: the third
field means something different on every family measured so far (see the
fridge section above). Read field 2, and leave field 3 alone unless a
family rule claims it.

The filename is a prior, never the test. `energy.db` on a dishwasher and a
`power_usage` table on the SQLite washers both point at SQLite, and #301's
`/mnt/usage.db` that is not SQLite is the standing proof that the name
decides nothing. Read the magic bytes.

If the SQLite branch is ever taken, note that the file arrives as bytes in
memory and `sqlite3` cannot open a buffer. `sqlite3.Connection.deserialize`
(3.11+, so available on every Python HA now runs) reads one without a
temporary file; the alternative is writing the blob to disk from inside an
executor job, which is worse in every respect.

### 4. What to expose

**Runtime hours — yes, and this is the part worth doing first.**
`total_increasing`, `device_class: duration`, unit `h`. It is the honest
primitive, HA's own long-term statistics give the monthly view for free
without this integration touching the statistics API, and #329 proved it is
genuinely per-unit even on a multi-split where the energy figure can never
be. On a `KRAC` board it is also the *only* usage number the appliance has —
#302 established that its permanent `0.0` kWh is correct, because the
hardware has no meter and its own app draws an hours graph instead.

**Energy — yes, but not as a second `total_increasing` energy sensor.**
Where the file carries energy it is the same counter
`/energy/consumption/vs/0` already publishes — now measured rather than
argued, on the dishwasher above, to the second and to the tenth of a kWh. Creating a second
`state_class: total_increasing` energy entity for one physical meter is the
double-count trap #329 warns about, one layer down. Two shapes are
defensible:

- default: `entity_category: diagnostic`, `enabled_default=False`, no
  `state_class` — a corroborating figure, not an energy-dashboard source;
- fallback: full `total_increasing` energy **only** where
  `/energy/consumption/vs/0` has no `cumulativePower` at all, which is
  exactly #285's washer and exactly the case where this file is the only
  copy of the number.

The second needs no new machinery: two descriptors with complementary
`exists_fn`, the pattern `ai_energy_level`'s switch/select pair and
`ENERGY_METER_LEGACY`/`GENERIC` already use.

**`UsagesDB_reset` — no.** #301 is right. It destroys the only copy of the
history, and there is nothing for a user to weigh that against.

### 5. Backfilling the ~181-day window — out of scope

Agreed with #301, with one correction to the premise: the mechanism is not
as unreachable as the issue assumes. `recorder` is already in
`after_dependencies` and `__init__.py` already calls into
`recorder.statistics` for the v2→v3 particulate relabel.

One leg of that argument has since been measured away, and it should be
struck rather than quietly kept: this file claimed the AC formats "carry no
per-day energy to import". The fridge above carries 181 consecutive days of
strictly-increasing cumulative kWh with no gaps, which is close to the ideal
input for `async_add_external_statistics`. Whoever revisits this should know
the case got stronger, not weaker.

It should still not happen now. External statistic ids do not attach to the
entity, so the imported history lives beside the sensor rather than in it;
and it buys a one-time win against permanent complexity, on a mechanism this
integration has never used. Worth reopening once the probe tier exists and
the census says how many boards carry a full file rather than one row —
not before. Publish the
current cumulative value, let HA accumulate from today, leave the past in
the appliance.

## What has to be answered before any of this is written

In rough order of how much they can invalidate:

1. ~~**The interface query.**~~ **Answered for the read path** — see the
   measurement above: a plain segment-path GET returns the baseline rep.
   Retained below for the original reasoning, and because a *write* to this
   resource may still need one.

   Both reporters read the resource as
   `GET /file/transfer/vs/0?if=oic.if.baseline`. `DtlsCoapSession.get()`
   takes path segments and no query string, and nothing in this component
   has ever sent one. Either the default RETRIEVE returns the same payload —
   in which case this is a non-issue — or the design needs a
   `smartthings-local` change before its first line. Nothing else matters
   until this is settled.

   Note that `/file/list/vs/0` answered a plain segment-path GET from this
   integration's own `read_resource`, with no query, so the query is not
   required to reach the file surface as such. That is encouraging, not
   conclusive: `oic.if.s` is that resource's only non-baseline interface,
   while `/file/transfer/vs/0` is `oic.if.a`, and an actuator's default
   interface is the more likely one to answer differently.

2. ~~**Whether a bare GET reads the primary file.**~~ **Answered.** No
   selection write is needed, and the same call returns a full 2172-byte,
   181-record file from a fridge — so the dishwasher's single record is that
   appliance's actual history, not a truncated read. The name the resource
   reports is a firmware default and identifies nothing; see the fridge
   section. `/file/list/vs/0` hands out an `x.com.samsung.id` per
   file, and `ac-filter-reset.md` records a live board *rejecting* a
   selection of a non-primary path (4.05/4.00) — so a write path exists and
   the firmware validates it. Both reporters' one-shot GETs returned the
   file they wanted, but on appliances whose primary file was the one they
   wanted, which does not distinguish "GET returns the primary file" from
   "GET returns whatever was last selected, and nothing had selected
   anything".

   This is the question that decides whether the probe in §1 is a read or a
   read-modify-read, and a scheduled probe that has to *write* to an
   actuator on every cycle is a materially different proposition — it can
   race a user's own debug write, and it needs the settle guard
   `ObserveManager.mark_write_pending` exists for. If selection turns out to
   be required, that is an argument for probing far less often than §1
   proposes, or only once per session.

3. **The exact record layouts.** The byte-level specs in #301 and its washer
   comment are written in angle brackets and are stripped from the GitHub
   API's rendering of both. They need to be re-read from the issue as
   displayed, or restated by the reporters, before a parser is written
   against them. The *semantics* above are not in doubt; the field widths
   and order are, and the discriminator in §3 depends on them.

4. ~~**Getting bytes out of the device at all.**~~ **Done.**
   `read_resource` returned the decoded rep verbatim as a `ServiceResponse`,
   which HA serializes as JSON, and a CBOR byte string has no JSON form — so
   a blob could not leave the appliance through this integration at all. The
   snapshot store had the same problem with a worse symptom: the write fails
   and the entry silently loses its offline load.

   `registry/encode.py` now renders any value a JSON encoder would reject as
   a self-describing marker (`bytes` as length, SHA-256 and base64), and
   `from_json_safe` turns the markers back. It is applied once at each
   boundary that carries device data out — both service responses,
   diagnostics, and the snapshot store, which round-trips through it — so
   the next unrepresentable type is representable before anyone hits it.

   The fixture corpus needed less than this file first claimed: a fixture's
   optional `probes` map already exists for exactly this, holding hand-read
   resources that belong to no batch (`/multidevice/vs/0` on the
   `ARTIK051_DONGLE_FAC_18K` fixture). `tests/conftest.py` now decodes it
   through `from_json_safe`, so a `probes` entry can carry a blob as JSON
   and the parser under test is handed real `bytes`.

5. **`/file/transfer/chunk/vs/0`.** Implies a chunked download protocol for
   files too large for a single blockwise GET — most likely relevant to the
   SQLite washers, whose file has no reason to stay at 3 KB.

## Suggested sequencing

1. ~~Land the `bytes` rendering in `read_resource`.~~ Done and generalized as
   `registry/encode.py`, shipped in v0.25.0.
2. ~~Read `/file/transfer/vs/0` on a board whose `/file/list/vs/0` is known.~~
   Done, on a dishwasher and a fridge — see the two measurement sections.
3. ~~Land the probe tier, coverage-only.~~ Done. Every appliance now reads
   `/file/list/vs/0` and `/file/transfer/vs/0` once at setup and every ~30
   minutes after, and both land in diagnostics.
4. **Collect the census.** Nothing more should be built until dumps from
   boards nobody here owns say what the third field means on them, whether
   any family serves SQLite, and how many appliances hold a real history
   rather than the one row the dishwasher had. Two families measured in one
   household is not a basis for shipping an entity.
5. Then the parser and the runtime-hours sensor for the ACs — the one series
   that lives in this file and nowhere else (#301, #329).
6. Then the energy question in §4, on the evidence step 4 produces.

An appliance owner who wants to help now needs only to download diagnostics:
once the probe tier ships, the file list and the usage blob are in it
automatically. On a build without it, `read_resource` on `/file/list/vs/0`
and `/file/transfer/vs/0` followed by a diagnostics download does the same
thing, because `_raw_read_blocking` applies whatever it reads to the state
cache.
