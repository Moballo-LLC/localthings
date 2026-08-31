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
- maps each href through `subdevice.to_actual()` like every other read, so
  the UUID-prefixed per-head copies the `FAC_BORA` fixtures advertise are
  reachable by the same code.

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
`/energy/consumption/vs/0` already publishes. Creating a second
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

It should still not happen here. External statistic ids do not attach to the
entity, so the imported history lives beside the sensor rather than in it;
the AC formats carry no per-day energy to import in the first place; and it
buys a one-time cosmetic win against permanent complexity. Publish the
current cumulative value, let HA accumulate from today, leave the past in
the appliance.

## What has to be answered before any of this is written

In rough order of how much they can invalidate:

1. **The interface query.** Both reporters read the resource as
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

2. **Whether a bare GET reads the primary file, or a selection write is
   required first.** `/file/list/vs/0` hands out an `x.com.samsung.id` per
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

1. ~~Land the `bytes` rendering in `read_resource`.~~ Done, and generalized —
   see question 4.
2. Read `/file/transfer/vs/0` on a board whose `/file/list/vs/0` is already
   known — the dishwasher above is the obvious candidate, since its primary
   file is named `energy.db` outright. One read settles questions 1 and 2
   together: whether a plain GET answers, and which file it hands back when
   the appliance has two. The blob now survives the trip, so the answer
   arrives in a form that can go straight into a fixture.
3. Land the probe tier alone, with `/file/list/vs/0` and
   `/file/transfer/vs/0` as coverage-only capabilities that bind no
   entities. Diagnostics then start carrying the file list and the blob from
   every board a user owns, which is what turns four reported formats into a
   real census.
4. Land the parser and the runtime-hours sensor against the two AC formats.
5. Decide the energy question in §4 on the evidence step 3 produces.
