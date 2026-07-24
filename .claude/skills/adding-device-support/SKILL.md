---
name: adding-device-support
description: >-
  Add or extend support for a Samsung OCF appliance in localthings from a
  /device/0 diagnostics dump. Use when a device-support issue lands, a device
  raises the "incomplete capability coverage" repair, a diagnostics JSON needs
  triaging, or you're mapping OCF resources to HA entities. Covers reading dumps,
  OCF-standard vs vendor hrefs, the diagnostic/config/normal entity taxonomy,
  ensuring every href is bound or ignored, and locking it in with a fixture +
  golden + test.
---

# Adding device support

localthings maps a Samsung appliance's OCF resources (`/device/0` dump) to Home
Assistant entities. Each resource `href` is handled by a `Capability` that
declares the entities it produces. This skill is the workflow for turning a new
dump into coverage.

## 1. Get the dump and see the gaps

A user's diagnostics download (`config_entry-localthings-*.json`) has, under
`data`:
- `resources`: `{href: rep}` — the parsed `/device/0` snapshot. **This is the
  source of truth**, not code comments.
- `unbound_hrefs`: resources that bound to no capability. The
  "incomplete capability coverage" repair fires whenever this is **non-empty or
  the device type is unrecognized** (`coordinator._update_coverage_gap_issue`).

Goal: make `unbound_hrefs` empty by **binding** the useful resources and
**ignoring** the noise — and surface every genuinely useful sensor/select/switch
along the way.

## 2. Compute coverage without Home Assistant

The `registry/` package is HA-free, so you can drive discovery directly (HA
isn't importable standalone because `localthings/__init__.py` pulls it in — stub
the package to skip that):

```python
import sys, types, json, importlib
cc = types.ModuleType('custom_components'); cc.__path__=['custom_components']; sys.modules['custom_components']=cc
lt = types.ModuleType('custom_components.localthings'); lt.__path__=['custom_components/localthings']; sys.modules['custom_components.localthings']=lt
by_type   = importlib.import_module('custom_components.localthings.registry.by_type')
discovery = importlib.import_module('custom_components.localthings.registry.discovery')
adapter   = importlib.import_module('custom_components.localthings.registry.adapter')

resources = json.load(open('dump.json'))['data']['resources']
info = resources['/information/vs/0']
reg = by_type.for_device_by_model(info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'])
# or: by_type.for_device(one_ui_version)  when /otninformation has swVersionInfo.oneUiVersion
unbound = []
bound = discovery.discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
state = adapter.flatten(bound, resources)   # {entity_key: value}
print('registry:', reg.name, 'unbound:', sorted(unbound))
print('state_keys:', sorted(state))
```

`discover()` binds caps (applies `rt_filter`/`match_fn`); `flatten()` applies
`exists_fn` and produces the final entity values. Use the same routine to
regenerate a golden.

## 3. OCF-standard vs vendor hrefs (`/x/0` vs `/x/vs/0`)

Samsung appliances run RT-OCF and often expose the **same state twice**:
- `/x/vs/0` — **vendor** resource, `x.com.samsung.da.*` fields.
- `/x/0` — **standard OCF** resource type (`oic.r.*`) with OCF's fixed field
  names. Confirmable against the OCF spec: `/power/0` `{value: bool}` is
  `oic.r.switch.binary`; `/operational/state/0` is `oic.r.operational.state`.

Newer firmware advertises both as Samsung migrates onto the OCF standard. **There
is no single "always prefer vs / always prefer non-vs" rule** — decide per
resource from the populated dump:
- **Both populated, same state** (power, kids-lock, remote): prefer the
  OCF-standard `/x/0`; fall back to `/x/vs/0` when `/x/0` is absent. Encode with
  a `match_fn` presence check — see `common.POWER_GENERIC` / `POWER_VS_FALLBACK`.
- **Only vendor populated** (`/energy/consumption/0` is often empty `{}`): use
  `/x/vs/0`.
- **Vendor is a superset** (`/operational/state/vs/0` adds fields the OCF one
  lacks): build on the vendor resource, ignore the OCF subset.

Course/cycle is **not** an OCF question — there's no standard course resource, so
`/course/vs/0` (and the `/st/*course/vs/0` re-encoding) are both vendor.

## 4. Entity taxonomy — the judgement call

For each field worth exposing, decide the entity kind and category
(`entity_category` on the descriptor):
- **Normal / primary** (no `entity_category`): the things a user acts on or
  watches — power switch, machine state, the cycle select, energy sensors.
- **`config`**: user-tunable settings — sound mode, door LED, wash temperature,
  buzzer. Shown under the device's Configuration section.
- **`diagnostic`**: read-only status/troubleshooting — alarms, diagnosis, job
  beginning status, last-operation source.

Also set `poll_tier` (`hot`/`warm`/`cold`) on the capability for how often it's
sub-polled between summary polls. Pick descriptor types from `entities.py`
(`SensorDesc`, `SelectDesc`, `SwitchDesc`, `NumberDesc`, `BinarySensorDesc`,
`TimeDesc`, `ButtonDesc`) — the class selects the HA platform.

**Don't guess.** If a field's meaning or write contract is unclear from the dump
(opaque encoded blobs, no supported-values list), leave it unbound so it surfaces
as a gap for a human, or ignore it with a documented reason — never invent an
entity on a hunch (`ignored.py`'s rule).

## 5. Parse units out of the value — don't ship them embedded in a string

Samsung reps sometimes encode a numeral and its unit as one string
(`x.com.samsung.da.powerLevel: "700W"`; a `desired`/`current` temperature whose
unit lives in a sibling field instead). The lazy fix — a plain `SensorDesc`
with no `device_class`/`unit` that passes the raw string straight through —
binds the href and looks done, but HA then sees text, not a measurement: no
unit conversion, no long-term statistics, no graphing. This shipped once
already (microwave `power_level` went out as the literal string `"700W"`
before being caught in review) — treat that as the standard failure mode to
check for, not a one-off.

Before wiring up a numeric-looking field:
- **Check where the unit actually lives.** Embedded in the same string
  (`powerLevel`)? A fixed, undocumented assumption (most wattages/energy
  fields)? Or reported live in a sibling field (`/temperatures/vs/0`'s
  per-item `unit`, `'Celsius'`/`'Fahrenheit'`)? Don't guess C vs F or W vs kW
  without checking the dump.
- **Parse the numeral in `value_fn`** (regex or strip the unit suffix) so the
  entity's state is a number, not `"700W"`.
- **Set `unit`** (static) **or `unit_fn`** (reads a live sibling field — see
  `common.normalize_temp_unit`, used by both `fridge.py` and `oven.py` for a
  per-device C/F reading) **plus the matching `device_class`/`state_class`**
  so HA treats it as a real measurement.
- A plain string `SensorDesc` (no unit/device_class) is still correct for
  genuinely non-numeric state (mode names, enum-like text) — reserve it for
  that, not as a shortcut past parsing a numeral.

## 6. Enum selects need translation support

Any select whose options are raw device codes (course/cycle, and code-valued
settings) must render through translations, not Python:
- Set `translation_key='<family>_cycle'` (or similar) on the `SelectDesc`;
  `options`/`options_field` supply the **raw** codes.
- Add the labels to **both** `strings.json` and `translations/en.json` under
  `entity.select.<translation_key>.state.<code>`, with the code **lowercased**
  (e.g. `"16": "Cotton"`). Codes with no entry render as the raw code — that's
  the cue to identify and name them.
  
## 7. Names and enum labels live in translations, never in Python

Descriptors have **no `name` field**. Every entity is named from the shipped
catalog, keyed by `translation_key` — which defaults to the descriptor's own
`key`. So adding `SensorDesc(key='filter_status', ...)` obliges you to add:

```json
"entity": { "sensor": { "filter_status": { "name": "Filter status" } } }
```

to `translations/en.json`. Skip it and the entity ships nameless;
`tests/test_translations.py` fails the build instead.

- **Sentence case** ("Filter status", not "Filter Status"), per HA's style
  guide — capitalize only proper nouns and Samsung feature names ("AI Energy
  Mode", "Storm Wash+").
- Set `translation_key` explicitly only to **share** one catalog entry across
  descriptors, or to point at a differently-named one. Two descriptors on the
  same platform with the same `key` already share an entry — intended for
  `common.py`'s OCF/vendor fallback pairs, a silent mislabel otherwise.
- Prefer HA's own vocabulary where it fits: a `device_class` gives you
  translated states for free (`binary_sensor` door/running, `sensor`
  timestamp/enum), so don't restate them.

Selects whose options are raw device codes (course/cycle, code-valued
settings) additionally need those codes labelled:
- `options`/`options_field` supply the **raw** codes; the catalog maps them.
- Add labels under `entity.select.<translation_key>.state.<code>`, code
  **lowercased** (e.g. `"16": "Cotton"`). `select.py` derives which values it
  normalizes from the catalog itself, so there is no Python list to keep in
  sync — a code with no entry simply renders as the raw code, which is the cue
  to identify and name it.

`translations/en.json` is the only place any of this lives: there is no
`strings.json` (Home Assistant doesn't read one from a custom integration) and
no `[%key:...%]` resolution (that's Core build tooling). Every other language
must mirror `en.json` key for key — also enforced by
`tests/test_translations.py`.

## 8. Coverage discipline: bound or ignored

Every href in the dump must resolve, or the repair fires. If a resource isn't
worth an entity, add it to `capabilities/ignored.py` (a no-entity `Capability`)
with a one-line reason. Add there only when it's **irrelevant plumbing**
(network/OTA/account housekeeping) or a **duplicate of state exposed via a
friendlier href**.

- **Global vs per-registry ignore:** `ignored.IGNORED` is folded into every
  registry. A global ignore **collides** (via `_build`) with any real capability
  that binds the same href in some family — e.g. `/course/vs/0` can't be globally
  ignored because washers bind it. When only one family should ignore an href
  that another binds, scope the ignore to that family's registry.

## 9. Reuse before writing new code

Check `common.py` (generic OCF: power, energy, alarms, water) and `laundry.py`
(shared washer/dryer/dishwasher: buzzer, job status, `cycle_select` + course
machinery) before adding a capability. Cross-family reuse is normal — the dryer
registry uses `fridge.FIRMWARE_UPDATE`; all three laundry families share
`laundry.cycle_select`. If two families hand-roll the same helper, hoist it to a
shared module rather than copying.

## 10. Lock it in

1. Add a **scrubbed** fixture `tests/fixtures/<type>_device.json`
   (`{"device0": [ {devcol rep}, {href, rep}, ... ]}`) — replace serials, MACs,
   and other PII with placeholders.
2. Generate `tests/fixtures/golden/<type>.json` (`{"state_keys": [...]}`) with
   the harness in §2.
3. Add the type to `test_golden_regression.py` and write a
   `test_<type>_capabilities.py` asserting **zero unbound hrefs** and that the
   expected entities exist (and any misleading ones are gated).
4. Run `pytest tests/ -q` — and re-run the golden tests for **other** device
   types after any change to `common.py`/`laundry.py`, since they share those.

## Key files
- `registry/discovery.py` — `discover()`, unbound reporting, pattern caps.
- `registry/capability.py`, `registry/entities.py` — the `Capability` and
  descriptor shapes (`rt_filter`, `match_fn`, `exists_fn`, `rep_fn`, `write_fn`).
- `registry/capabilities/{common,laundry,fridge,...}.py` — capability defs.
- `registry/capabilities/ignored.py` — the ignore list + its philosophy.
- `registry/by_type/*.py` — per-device-type registries (what to include).
- `registry/registry.py` — the global unknown-device fallback + collision check.
- `tests/test_golden_regression.py`, `tests/fixtures/` — regression harness.
