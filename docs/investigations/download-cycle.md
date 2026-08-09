# Laundry cloud "Download" cycles: solved, with one dead end

`registry/capabilities/laundry.py`'s cycle select offers a washer's
downloaded ("Download" / "Downloaded") programs alongside its ordinary
courses now, driven by the store in `cloudcourse.py` (issue #342). This file
records the byte-level work behind it, including a decode that fit one
device perfectly and collapsed on the second — the reason nothing in the
shipped code interprets a program payload at all.

Two real devices carry these tokens:

| dump | model | slots advertised | blob width |
| --- | --- | --- | --- |
| `washer_ww5000c_cloud` | WW5000C, `DA_WM_TP1_21_COMMON`, Table_02 | 9 | 20 bytes |
| `washer_wa55a7700av` | WA55A7700AV, `DA_WM_TP1_21_COMMON`, Table_02 | 2 | 16 bytes |

Both are Table_02. Note that already: same course table, different
everything else.

## The three tokens

All on `/course/vs/0`'s `x.com.samsung.da.options` array, same
prefix-match/replace merge as every other token there.

- `CloudExtraCourse_<slot><slot>…` — the device's own list of downloaded
  program slots, one byte each. The cloud counterpart of `EditCourseList_`.
- `CloudCourse_<blob>` — the persisted default program.
- `OneTimeCloudCourse_<blob>` — a this-run-only override.

### `CloudExtraCourse_` is an enumeration, and byte 2 of a blob is its slot

The WW5000C reports `CloudExtraCourse_0A5C286B2D0C55301A` — nine bytes for
its nine downloaded programs. Byte 2 of each of the nine blobs its owner
captured is exactly one of those nine, no repeats, sets equal:

```
        blob                                       byte2  program
        00 21 55 04 49 28 4D 13 4A A0 4C 00 …       55    Sports
        00 20 28 04 49 00 4D 00 4A B8 4C 00 …       28    Spin only
        00 02 5C 04 49 28 4D 13 4A B0 4C 00 …       5C    Outdoor
        00 1F 6B 04 49 28 4D 13 4A A0 4C 00 …       6B    Jeans
        00 2E 2D 04 49 30 4D 12 4A A0 4C 00 …       2D    Super quiet
        00 2F 0C 04 49 58 4D 14 4A B8 4C 00 …       0C    Baby care intensive
        00 0D 30 04 49 30 4D 12 4A B8 4C 00 …       30    Cloudy heaven
        00 30 1A 04 49 28 4D 12 4A A0 4C 00 …       1A    Shirts
        00 04 0A 04 49 40 4D 13 4A B8 4C 00 …       0A    Towels

CloudExtraCourse_  0A 5C 28 6B 2D 0C 55 30 1A
```

Confirmed independently on the WA55A7700AV: `CloudExtraCourse_5958`, and its
`CloudCourse` blob `00 1C 59 05 …` has byte 2 = `59`. Its
`OneTimeCloudCourse` is `FF FF 01 …` — byte 2 = `01`, which is *not* an
advertised slot, and the `FFFF` prefix marks it as "nothing loaded" rather
than naming a program. That sentinel is why `cloudcourse.is_loaded` exists.

This is what makes "3 of 9 discovered" answerable, and it is why no catalog
of program ids is hardcoded anywhere: the appliance already knows which
programs it has.

## Writing: the two-token rule

Confirmed on hardware by the issue #342 reporter. Writing
`OneTimeCloudCourse_<blob>` alone while some other course is selected is
accepted at the protocol level (no error) and then silently ignored by the
machine. It takes effect only when the same write also switches `Course_` to
the Download course — which is what `laundry._cloud_cycle_write` does, and
the only two-token options write in the codebase:

```yaml
x.com.samsung.da.options:
  - Course_87
  - OneTimeCloudCourse_001F6B0449284D134AA04C0035F004F005F0AC00
```

`CloudCourse_` was separately confirmed writable on its own: set while on
Download it changes the running program; set from another course it becomes
what gets preselected the next time Download is chosen. The integration
doesn't write it today — a "default download cycle" control is a possible
follow-up, deliberately left out of the first pass.

### There is no single "Download" course code

The WW5000C's Download is `Course_87`; the WA55A7700AV's is `17`
("Downloaded" in `washer_cycle_table_02`). **Same course table, different
code.** Any per-table lookup of "the Download code" would have been wrong on
one of the only two devices available to check it against, which is why the
code is learned by observation and confirmed by the user in the options flow
instead of tabled.

The observation signal is "whatever `Course_` reads while a non-sentinel
`OneTimeCloudCourse_` is loaded." That is a *candidate*, never applied
directly: tokens in this array are replaced by prefix and never evicted, so
a stale program token outlives the run it belonged to and can be reported
next to an unrelated local course. Acting on that unconfirmed would start
the wrong wash cycle.

## The dead end: bytes 5/7/9 do not decode portably

With the nine WW5000C programs and their app-reported settings side by side,
three of the varying bytes fit perfectly:

| byte | meaning | formula | fit |
| --- | --- | --- | --- |
| 5 | wash temperature | `(b - 0x10) / 0.8` °C, `0x00` = n/a | 9/9 |
| 7 | rinse count | `b - 0x10`, `0x00` = off | 9/9 |
| 9 | spin level | `(b - 0x90) / 8` | 9/9 |

Nine for nine, including the internally consistent case: "Spin only" is the
only program with `0x00` in *both* byte 5 and byte 7, matching a cycle that
skips washing entirely while still reporting a spin level.

It does not survive the second device. Against the WA55A7700AV's
`CloudCourse` blob `00 1C 59 05 49 16 4D 11 4A 22 4C 20 37 F0 AC 22`:

- temperature: `(0x16 - 0x10) / 0.8` = **7.5 °C**
- spin: `(0x22 - 0x90) / 8` = **negative**
- rinse: `0x11 - 0x10` = 1 — the only plausible one

### What *does* survive is the grammar

The two boards' payloads are different lengths (20 vs 16 bytes) but not a
different format — same header, same leading fields, two fewer optional
trailing ones:

```
WW5000C  00 | 2155 | 04 | 49:28  4D:13  4A:A0  4C:00 | 35:F0  04:F0  05:F0 | AC:00
WA55     00 | 1C59 | 05 | 49:16  4D:11  4A:22  4C:20 | 37:F0               | AC:22
```

- `00`, then the 2-byte program id, then one byte (`04` vs `05`) — identical
  layout on both.
- Then a tag/value stream whose **first four tags are the same, in the same
  order, at the same offsets**: `49`, `4D`, `4A`, `4C`. These are exactly the
  four whose values vary per program.
- Then a fixed tail, terminated on both by an `AC:<value>` pair. The entire
  width difference is two trailing pairs the WA55 doesn't carry.

The tail is not program data. Across all nine WW5000C programs, byte 3 is
always `04` and bytes 12–19 are byte-identical — every trailing pair carries
value `F0` except the `AC` terminator, and `35` vs `37` looks like a board or
profile marker rather than a field. (Byte 3 is not a field count: the board
with the *higher* value has *fewer* pairs.)

So the payload is tag/value, not fixed offsets — but knowing the grammar
doesn't recover the values. The same four tags carry non-overlapping ranges
between the two boards:

| tag | WW5000C (9 programs) | WA55 |
| --- | --- | --- |
| `49` | `00, 28, 30, 40, 58` | `16` |
| `4D` | `00, 12, 13, 14` | `11` |
| `4A` | `A0, B0, B8` | `22` |
| `4C` | `00` (all nine) | `20` |

Same field, board-specific encoding. Decoding it properly needs a third
device; one device's fit is a coincidence-shaped hypothesis, not a format.

**A trap for whoever picks this up:** the WA55's `/washer/vs/0` reads
Warm / High / 1, which looks like it could confirm a decode of that unit's
`CloudCourse`. It can't — that appliance is sitting on `Course_01` (Normal),
not on its cloud course, so those values describe the local cycle it has
selected, not the saved cloud program. A cross-check like this is only
evidence when the machine is actually loaded with the program being decoded.

So the shipped code never interprets a payload: a blob is recorded whole and
replayed byte-for-byte, exactly as the device reported it, and never
decomposed or rebuilt. The read-only "loaded program's temperature/spin"
sensors this decode would have enabled were dropped for the same reason.

## What is deliberately not done

- **No hardcoded program catalog.** Blobs are cloud-assigned per
  account/region. One owner's captured payload is not evidence about anyone
  else's appliance, and a table of them would offer options that write
  another household's wash settings.
- **No invented names.** The appliance reports an opaque slot id and nothing
  else. Names come from the user in the options flow, the same rule that
  stops an unrecognized local course code from getting a made-up English
  label (PR #251 review).
- **No blob synthesis.** Even with the byte 5/7/9 decode in hand, nothing
  builds a payload from parts — the device was never tested with one, and a
  fabricated blob is an untested write to a wash cycle.

## Open questions for the next dump

1. Does `OneTimeCloudCourse_` clear itself when a cycle finishes, or when the
   course changes? Behavior suggests the appliance falls back to
   `CloudCourse_` when Download is re-entered, but the token's own lifecycle
   is unconfirmed. `laundry.cloud_current` is written to be correct either
   way.
2. What does byte 1 mean? It is distinct per program and *sometimes*
   coincides with a plausible local course code for that program (`2E` Baby
   Care for "Baby care intensive", `30` Cloudy Day for "Cloudy heaven") and
   sometimes doesn't (`21` Colors for "Sports"). Probably a base-course
   reference; not reliable enough to use.
3. A third device with downloaded programs would settle the tag/value
   reading of the payload.
