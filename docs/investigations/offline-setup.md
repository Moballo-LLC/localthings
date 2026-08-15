# Loading a config entry while the appliance is offline

Issue #295 asks for sub-30s recovery when a powered-off appliance comes back,
instead of HA's `ConfigEntryNotReady` backoff (30s → … → 15 min). PR #303
tries to get there by catching the first-refresh failure in
`async_setup_entry` and loading the entry anyway.

That doesn't work here, and the reason is worth writing down: this
integration has no static entity list. Every entity comes from discovery,
and discovery only happens inside a successful poll.

## What PR #303 produces today

Measured on the PR's branch — set up with `_poll_once` raising, then advance
the clock four summary intervals:

| | |
| --- | --- |
| entry state | `LOADED` |
| `coordinator.bound` | 0 |
| entities in the state machine | 0 |
| registry entries | 1 (the disabled connection-mode sensor) |
| coordinator listeners | 0 |
| `_unsub_refresh` | `None` |
| poll attempts over the next 4 intervals | **0** |

The entry loads and then never polls again. `DataUpdateCoordinator._async_refresh`
reschedules only `if not auth_failed and self._listeners and not
self.hass.is_stopping`; with no bound entities the only unconditional entity
is `LocalThingsConnectionModeSensor`, which is
`entity_registry_enabled_default = False` and so never added and never
subscribes. Nothing reloads the entry either. The device comes back online to
an entry that is permanently empty until a manual reload — strictly worse
than the backoff it replaces, which did recover on its own within 15 minutes.

## Why entities can't just be created offline

Four independent gates, all of which need live device data:

1. `bound` is only ever assigned in `_run_discovery` (`coordinator.py:1081`),
   which runs on a poll's `resources` dict.
2. All ten platforms enumerate `coordinator.bound` exactly once, at forward
   time (`sensor.py:34` and siblings). Nothing adds entities later — the
   invariant is already documented at `coordinator.py:1283-1286`.
3. `_is_included` (`entity.py:39`) returns False whenever `last_resources`
   has no rep for the href. Even a fully reconstructed `bound` filters to
   nothing while `StateCache` is empty.
4. `LocalThingsEntity` is a bare `CoordinatorEntity` with no `available`
   override, no `RestoreEntity`, and no `Store` anywhere in the component. An
   entity that did exist offline would be `unavailable` with no state.

The issue cites ESPHome, Shelly, LIFX and WLED as precedent for setup that
never fails. Those integrations can do it because each one has a *persisted
device description* to build entities from — ESPHome keeps its entity list in
`.storage`, Shelly caches device info. The pattern is portable; the mechanism
underneath it is the part PR #303 is missing.

## What a working version needs

Three separable pieces, plus a gating rule.

### 1. A persisted discovery snapshot

Write the discovery result back onto the config entry after each successful
`_run_discovery`. There is already precedent for exactly this shape —
`_persist_identity` (`coordinator.py:973`), `CONF_LEARNED_MODES`,
`CONF_CLOUD_COURSES`.

`BoundEntity` holds live `Capability`/`SamsungEntityDescription` objects, so
it isn't directly serializable, but it is re-derivable: persist
`device_type_name`, the materialized `Subdevice` list (plain str/tuple
fields), and per entity the `(subdevice key, capability, desc.key, instance,
key_override, instance_name)` tuple. Rehydrate by re-resolving through
`resolve_registry` + `CAPABILITIES`.

Persist the *post-`_is_included`* set, so rehydration doesn't need reps, and
flag the coordinator as rehydrated so gate 3 above is skipped for that pass.

### 2. Reconcile on reconnect

The snapshot is a guess about a device we haven't talked to yet. When the
first successful poll lands, `_run_discovery` computes the real set; if it
differs from what was rehydrated, the entry has to reload, because gate 2
means platforms can't add the difference in place. `async_schedule_reload`
covers it (available well below the 2025.1.0 floor in `hacs.json`).

This is the piece that makes the whole thing safe against a firmware update,
a newly-appearing subdevice, or a different appliance at the same IP. PR #303
has no equivalent.

### 3. Keep polling with no listeners

Independent of the above, and worth doing on its own merits: hold one
refresh alive for the entry's lifetime so scheduling never depends on entity
count.

```python
entry.async_on_unload(coordinator.async_add_listener(lambda: None))
```

That alone fixes the measured "never polls again" bug, and covers the case
where every rehydrated entity happens to be registry-disabled.

### Gating rule: only load offline when there's a snapshot

An entry that has never successfully polled has nothing to restore and keeps
raising `ConfigEntryNotReady`. This is what answers the objection in the PR
thread — with a snapshot we *do* have metadata to build a device from, and
without one HA's backoff is still the right behavior. It also leaves room for
the #168-style flows that need to interact with the device during setup: a
device that never completed setup still blocks.

## What this still won't do

Entities will be present and `unavailable` — not showing their last values.
Gate 4 means last-known values require either `RestoreEntity` per platform or
persisting `StateCache`, and both mean asserting state the integration cannot
verify: a washer unplugged for a week would read "Running". HA's convention
is that unreachable means unavailable, and the recorder keeps the history
either way, so long-term statistics and history graphs are unaffected by this
choice.

Worth being explicit about, because it is the gap between what PR #303
promises in the thread ("load their previously recorded states") and what any
correct version can deliver.

## The cheaper alternative

If the goal is only issue #295's — fast recovery — pieces 1 and 2 are
unnecessary. Keep `ConfigEntryNotReady`, and add a retry that probes on a
fixed short interval and calls `async_schedule_reload` on the first success.
No persistence, no reconcile, no new failure modes. The device tile still
reads "Retrying setup" while the appliance is off, which is true, and
recovery drops from up-to-15-min to one probe interval.

The issue's other suggestion — wire zeroconf so the device's own boot
announcement triggers the retry — is a non-starter as things stand: there is
no `zeroconf` or `dhcp` key in `manifest.json` and the config flow is
user-driven only, so HA has no discovery signal for this integration to hang
a retry on. It would first need a confirmed mDNS service on the appliance.

## Recommendation

The cheap alternative solves the filed issue. The three-piece version solves
the goal stated later in the PR thread, at the cost of a new persisted
schema, a reconcile path, and a reload-on-mismatch — and it still leaves
entities unavailable, which is the part that was actually being asked for.
Do the cheap one first; treat offline entity materialization as a separate
change with its own issue.
