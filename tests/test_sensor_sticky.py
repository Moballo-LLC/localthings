"""Unit tests for LocalThingsSensor's sticky gate (issue #345).

Modeled on test_sensor_hysteresis.py: a _FakeCoordinator with just enough
surface for LocalThingsEntity/LocalThingsSensor, driven directly rather
than through a full coordinator/HA setup. Unlike that file, `data` here
is derived from the real `flatten()` over the same `resources` dict
`resource()` serves -- a single source of truth, so a test can't hide a
production desync bug by hand-computing the two independently.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import cast

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.sensor import LocalThingsSensor

_PROGRESS_DESC = next(e for e in OPERATIONAL_STATE.entities if e.key == "progress")
_PROGRESS_PERCENTAGE_DESC = next(
    e for e in OPERATIONAL_STATE.entities if e.key == "progress_percentage"
)
_MACHINE_STATE_DESC = next(e for e in OPERATIONAL_STATE.entities if e.key == "machine_state")

_HREF = "/operational/state/vs/0"
_ALL_BOUND = [
    BoundEntity(href=_HREF, capability=OPERATIONAL_STATE, desc=desc)
    for desc in OPERATIONAL_STATE.entities
]


class _FakeConfigEntry:
    def __init__(self):
        self.options: dict = {}


class _FakeCoordinator:
    """Just enough surface for LocalThingsEntity/LocalThingsSensor.

    `resources` is the one source of truth (standing in for the real
    coordinator's live cache); `data` and `resource()` are both derived
    from it exactly as the real coordinator derives `.data` from
    `flatten(self.bound, self._cache.snapshot())` and `.resource()` from
    the same snapshot -- so a test can't drift them apart in a way
    production couldn't.
    """

    def __init__(self):
        self.device_serial = "TEST-SERIAL"
        self.config_entry = _FakeConfigEntry()
        self.resources: dict[str, dict] = {}

    def resource(self, href: str) -> dict:
        return self.resources.get(href) or {}

    @property
    def data(self) -> dict:
        return flatten(_ALL_BOUND, self.resources)


def _sensor(desc):
    coordinator = _FakeCoordinator()
    bound = BoundEntity(href=_HREF, capability=OPERATIONAL_STATE, desc=desc)
    sensor = LocalThingsSensor(cast(LocalThingsCoordinator, coordinator), bound)
    return sensor, coordinator


def _apply(coordinator, **fields):
    """Merge `fields` onto the href's existing rep, the same shallow
    {**cached, **rep} merge ObserveManager.apply() does in production
    (issue #27) -- so a field this call doesn't mention (e.g. a stale
    `progress` the device didn't repeat) stays exactly as a real partial
    update would leave it, rather than being silently wiped."""
    href_fields = {f"x.com.samsung.da.{k}": v for k, v in fields.items()}
    coordinator.resources[_HREF] = {**coordinator.resources.get(_HREF, {}), **href_fields}


def _replace(coordinator, **fields):
    """A full rep replacement -- for a device reporting fresh from
    scratch (e.g. a full poll GET), unlike _apply's partial-update merge."""
    coordinator.resources[_HREF] = {f"x.com.samsung.da.{k}": v for k, v in fields.items()}


def test_holds_finish_after_state_leaves_active():
    """The #345 regression this guards: progress used to fall straight to
    'Idle' the instant machine_state left active, even right after
    reporting Finish."""
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "Finish"

    _replace(coordinator, state="Ready")  # device has moved on
    assert sensor.native_value == "Finish"


def test_holds_finish_even_when_state_already_idle_at_first_observation():
    """issue #345's actual reported failure mode: state can already read
    idle by the time `progress: Finish` is ever observed (the report's
    washer skips a Run+Finish moment the same-family dryer still shows) --
    the hold must still arm from that single rep."""
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Ready", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "Finish"

    # Still held on a later poll, even once the device stops repeating it.
    _replace(coordinator, state="Ready")
    assert sensor.native_value == "Finish"


def test_progress_percentage_holds_100_regardless_of_the_raw_field_at_finish():
    """The hold pins 100 explicitly (sticky_value_fn), not whatever the
    raw field happened to hold at the finish moment -- a device that
    never populates progressPercentage at all, or (issue #9's failure
    mode) leaves a stale non-100 value there, must still show 100 while
    held, not None/unknown or the stale figure."""
    sensor, coordinator = _sensor(_PROGRESS_PERCENTAGE_DESC)

    _replace(coordinator, state="Run", progress="Finish")  # no progressPercentage at all
    assert sensor.native_value == 100

    _replace(coordinator, state="Ready")
    assert sensor.native_value == 100


def test_real_data_flows_through_unheld_while_active():
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Run", progress="Spin")
    assert sensor.native_value == "Spin"

    _replace(coordinator, state="Run", progress="Rinse")
    assert sensor.native_value == "Rinse"


def test_never_finished_stays_idle():
    """A device that never actually reached Finish (e.g. Stop pressed
    mid-cycle) must not be held at some prior mid-cycle value."""
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Run", progress="Spin")
    assert sensor.native_value == "Spin"

    _replace(coordinator, state="Ready")
    assert sensor.native_value == "Idle"


def test_a_new_cycle_starting_overrides_the_hold():
    """Starting a second cycle must show its own real progress
    immediately, not the held Finish from the previous one."""
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "Finish"

    _replace(coordinator, state="Ready")
    assert sensor.native_value == "Finish"  # still held

    _replace(coordinator, state="Run", progress="Wash")
    assert sensor.native_value == "Wash"


def test_a_paused_new_cycle_also_overrides_the_hold():
    """Not just an actively-running new cycle: adding a sock and pausing
    mid-cycle must also show the real, current progress rather than a
    stale hold from the previous cycle -- machine_state isn't 'active'
    while paused, so a bypass keyed on that alone would miss this."""
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "Finish"
    _replace(coordinator, state="Ready")
    assert sensor.native_value == "Finish"  # still held

    _replace(coordinator, state="Pause", progress="Wash")
    assert sensor.native_value == "Wash"


def test_hold_expires_after_sticky_seconds():
    # A fresh desc (frozen dataclass -- replace(), not mutation, so the
    # module-level _PROGRESS_DESC other tests share stays untouched) with
    # a short real window -- consistent with this codebase's own
    # settle-guard tests (test_observe.py's mark_write_pending(...,
    # settle_s=0.05)) -- rather than a zero window, which exercises "held
    # disabled" rather than "an armed hold actually expires".
    desc = replace(_PROGRESS_DESC, sticky_seconds=0.05)
    sensor, coordinator = _sensor(desc)

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "Finish"

    _replace(coordinator, state="Ready")
    assert sensor.native_value == "Finish"  # still within the window

    time.sleep(0.1)
    assert sensor.native_value == "Idle"


def test_a_progress_stuck_at_finish_does_not_hold_open_the_window_forever():
    """Edge-triggering: sticky_fn keeps matching on every poll if the
    device's own `progress` field never resets on its own (the same class
    of quirk _completion_minutes' remainingTime-freeze workaround already
    documents) -- the hold must still expire on schedule from its first
    sighting, not have its deadline pushed out by every subsequent poll
    that still (correctly, from the device's perspective) reports Finish
    while machine_state has already gone idle."""
    desc = replace(_PROGRESS_DESC, sticky_seconds=0.05)
    sensor, coordinator = _sensor(desc)

    _replace(coordinator, state="Ready", progress="Finish")
    assert sensor.native_value == "Finish"

    time.sleep(0.03)
    # Device still (incorrectly) reports Finish on every subsequent poll --
    # must not restart the window.
    _replace(coordinator, state="Ready", progress="Finish")
    assert sensor.native_value == "Finish"

    time.sleep(0.03)  # 0.06s total since the first sighting -- past 0.05s
    _replace(coordinator, state="Ready", progress="Finish")
    assert sensor.native_value == "Idle"


def test_non_sticky_sensor_is_unaffected():
    """machine_state has no sticky_fn -- reads straight through, unchanged."""
    sensor, coordinator = _sensor(_MACHINE_STATE_DESC)
    _replace(coordinator, state="Run")
    assert sensor.native_value == "active"
    _replace(coordinator, state="Ready")
    assert sensor.native_value == "idle"


def test_cycle_active_and_machine_state_are_never_held():
    """The whole point of scoping the hold to progress/progress_percentage
    only: cycle_active (Running) and machine_state must keep reflecting
    the device's real-time state throughout, never claiming the appliance
    is still running once it isn't."""
    progress_sensor, coordinator = _sensor(_PROGRESS_DESC)
    machine_state_bound = BoundEntity(
        href=_HREF, capability=OPERATIONAL_STATE, desc=_MACHINE_STATE_DESC
    )
    machine_state_sensor = LocalThingsSensor(
        cast(LocalThingsCoordinator, coordinator), machine_state_bound
    )

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert progress_sensor.native_value == "Finish"
    assert machine_state_sensor.native_value == "active"

    _replace(coordinator, state="Ready")
    assert progress_sensor.native_value == "Finish"  # held
    assert machine_state_sensor.native_value == "idle"  # real-time, unaffected


def test_a_partial_update_that_omits_progress_does_not_erase_the_hold():
    """A device's partial poll/notify that doesn't repeat `progress` at
    all (issue #27's documented shape) merges onto the cache rather than
    replacing it -- confirms the hold reads the merged rep, still showing
    'Finish' from the earlier full rep, not a wiped/absent field."""
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "Finish"

    _apply(coordinator, state="Ready")  # partial merge, doesn't restate progress
    assert coordinator.resources[_HREF]["x.com.samsung.da.progress"] == "Finish"
    assert sensor.native_value == "Finish"
