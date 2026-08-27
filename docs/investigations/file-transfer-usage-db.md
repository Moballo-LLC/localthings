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
2. **The exact record layouts.** The byte-level specs in #301 and its washer
   comment are written in angle brackets and are stripped from the GitHub
   API's rendering of both. They need to be re-read from the issue as
   displayed, or restated by the reporters, before a parser is written
   against them. The *semantics* above are not in doubt; the field widths
   and order are, and the discriminator in §3 depends on them.
3. **Whether the GET is safe to repeat.** `/oic/res` advertises
   `/file/transfer/vs/0` as `oic.if.a` with `bm: 3` — an actuator, and
   observable. Both reporters' one-shot GETs worked with no prior write, so
   it very probably has no side effect and needs no session setup, but a
   resource we intend to poll on a schedule deserves better than "probably".
   The `bm: 3` is a curiosity only: a 2 KB blob is not something to OBSERVE.
4. **`/file/list/vs/0`.** Sits in the same `/oic/res` block on every board
   that carries one, is declared `oic.if.s`, and has never been read by
   anyone. It is the natural "what files are there, and how big" resource —
   `/file/information/vs/0` demonstrably is not, carrying only a timezone
   offset and a misspelled `supprtedtype`. One probe answers whether the
   guessing in §3 is necessary at all.
5. **`/file/transfer/chunk/vs/0`.** Implies a chunked download protocol for
   files too large for a single blockwise GET — most likely relevant to the
   SQLite washers, whose file has no reason to stay at 3 KB.
6. **Fixtures.** Every fixture in this repository is `/device/0` JSON. A
   blob needs a new fixture shape (base64 beside the batch), one per format,
   before the parser can have a golden test.

## Suggested sequencing

1. Answer (1) and (4) with the existing `read_resource` debug service — no
   code changes, and between them they decide the shape of everything else.
2. Land the probe tier alone, with `/file/transfer/vs/0` and
   `/file/list/vs/0` as coverage-only capabilities that bind no entities.
   Diagnostics then start carrying both payloads from every board a user
   owns, which is what turns four reported formats into a real census.
3. Land the parser and the runtime-hours sensor against the two AC formats.
4. Decide the energy question in §4 on the evidence step 2 produces.
