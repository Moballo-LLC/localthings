"""Unit tests for operational state capabilities."""

from custom_components.localthings.registry.capabilities.operational import (
    OPERATIONAL_STATE,
    _just_finished,
    _live_progress_code,
)
from custom_components.localthings.registry.entities import NumberDesc


def test_machine_state_maps_samsung_to_ocf():
    ms = next(e for e in OPERATIONAL_STATE.entities if e.key == "machine_state")
    assert ms.value_fn("Run") == "active"
    assert ms.value_fn("Pause") == "pause"
    assert ms.value_fn("Ready") == "idle"


class TestJustFinished:
    """`_just_finished` is progress/progress_percentage's sticky_fn arm
    condition (issue #345) -- see sensor.py's _apply_sticky."""

    def test_true_when_progress_is_finish_while_active(self):
        assert _just_finished(
            {"x.com.samsung.da.state": "Run", "x.com.samsung.da.progress": "Finish"}
        )

    def test_true_even_once_state_has_already_left_active(self):
        """The exact issue #345 scenario this exists for: a washer's
        `state` can already read idle by the time `progress` is observed
        at 'Finish' -- unlike _is_active, this must still arm."""
        assert _just_finished(
            {"x.com.samsung.da.state": "Ready", "x.com.samsung.da.progress": "Finish"}
        )

    def test_false_while_active_but_not_yet_finished(self):
        assert not _just_finished(
            {"x.com.samsung.da.state": "Run", "x.com.samsung.da.progress": "Spin"}
        )

    def test_false_when_progress_absent(self):
        assert not _just_finished({"x.com.samsung.da.state": "Run"})


class TestLiveProgressCode:
    """`_live_progress_code` is progress/progress_percentage's
    sticky_bypass_fn (issue #345) -- see sensor.py's _apply_sticky."""

    def test_true_for_a_concrete_non_finish_code(self):
        assert _live_progress_code({"x.com.samsung.da.progress": "Wash"})

    def test_true_regardless_of_state(self):
        """Not gated on machine_state -- a new cycle's own real progress
        must win over a held hold even while paused (e.g. adding a sock
        mid-hold), not just while actively running."""
        assert _live_progress_code(
            {"x.com.samsung.da.state": "Pause", "x.com.samsung.da.progress": "Wash"}
        )

    def test_false_for_finish(self):
        assert not _live_progress_code({"x.com.samsung.da.progress": "Finish"})

    def test_false_when_absent_or_none(self):
        assert not _live_progress_code({})
        assert not _live_progress_code({"x.com.samsung.da.progress": "None"})


class TestProgressPercentage:
    """issue #9: device firmware leaves progressPercentage stale (e.g. '1')
    after a cycle ends instead of resetting it, so it must be gated on
    active state the same way `progress`/`cycle_active`/`finish_time` are."""

    def test_zeroed_when_not_active(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "progress_percentage")
        assert desc.rep_fn is not None
        rep = {"x.com.samsung.da.state": "Ready", "x.com.samsung.da.progressPercentage": "1"}
        assert desc.rep_fn(rep) == 0

    def test_passes_through_when_active(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "progress_percentage")
        assert desc.rep_fn is not None
        rep = {"x.com.samsung.da.state": "Run", "x.com.samsung.da.progressPercentage": "42"}
        assert desc.rep_fn(rep) == 42


class TestCompletionMinutes:
    """Unit tests for completion_minutes entity."""

    def test_completion_minutes_parsing(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "completion_minutes")
        assert desc.rep_fn is not None

        # 1 hour 25 mins 30 secs -> 85 mins + 1 sec ceiling = 86 mins
        rep = {"x.com.samsung.da.remainingTime": "01:25:30"}
        assert desc.rep_fn(rep) == 86

        # Exact minutes: 1 hour 30 mins 00 secs -> 90 mins
        rep_exact = {"x.com.samsung.da.remainingTime": "01:30:00"}
        assert desc.rep_fn(rep_exact) == 90

    def test_completion_minutes_fallback_key(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "completion_minutes")
        assert desc.rep_fn is not None
        rep = {"remainingTime": "00:45:00"}
        assert desc.rep_fn(rep) == 45

    def test_completion_minutes_stale_finish_gated(self):
        """Firmware freezes remainingTime at '00:01:00' when progress reaches 'Finish'.
        Should return 0 to prevent stuck values."""
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "completion_minutes")
        assert desc.rep_fn is not None
        rep = {
            "x.com.samsung.da.progress": "Finish",
            "x.com.samsung.da.remainingTime": "00:01:00",
        }
        assert desc.rep_fn(rep) == 0

    def test_completion_minutes_missing_or_invalid(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "completion_minutes")
        assert desc.rep_fn is not None

        rep = {}
        assert desc.rep_fn(rep) is None


class TestFinishTime:
    """issue: remainingTime only has minute resolution, but datetime.now()
    always carries fresh seconds/microseconds -- an unrounded finish_time
    changed on nearly every poll even when remainingTime hadn't, flooding
    the recorder history/logbook with values that looked identical once
    the UI rounded them down to the minute for display."""

    def test_seconds_and_microseconds_are_zeroed(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "finish_time")
        assert desc.rep_fn is not None
        rep = {
            "x.com.samsung.da.state": "Run",
            "x.com.samsung.da.remainingTime": "00:29:00",
        }
        result = desc.rep_fn(rep)
        assert result.second == 0
        assert result.microsecond == 0

    def test_stable_across_polls_within_same_minute(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "finish_time")
        assert desc.rep_fn is not None
        rep = {
            "x.com.samsung.da.state": "Run",
            "x.com.samsung.da.remainingTime": "00:29:00",
        }
        first = desc.rep_fn(rep)
        second = desc.rep_fn(rep)
        assert first == second


class TestDelayFieldFallback:
    def test_reads_delay_end_time_when_delay_start_time_absent(self):
        from custom_components.localthings.registry.capabilities.operational import (
            OPERATIONAL_STATE,
        )

        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "delay_start_hours")
        assert desc.rep_fn is not None
        rep = {"x.com.samsung.da.delayEndTime": "02:30:00"}
        assert desc.rep_fn(rep) == 2.5

    def test_prefers_delay_start_time_when_both_present(self):
        from custom_components.localthings.registry.capabilities.operational import (
            OPERATIONAL_STATE,
        )

        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "delay_start_hours")
        assert desc.rep_fn is not None
        rep = {
            "x.com.samsung.da.delayStartTime": "01:00:00",
            "x.com.samsung.da.delayEndTime": "02:00:00",
        }
        assert desc.rep_fn(rep) == 1.0

    def test_write_targets_delay_end_time_when_that_is_what_device_reports(self):
        from custom_components.localthings.registry.capabilities.operational import (
            OPERATIONAL_STATE,
        )

        desc = next(
            e
            for e in OPERATIONAL_STATE.entities
            if e.key == "delay_start_hours" and isinstance(e, NumberDesc)
        )
        assert desc.write_fn is not None
        rep = {"x.com.samsung.da.delayEndTime": "00:00:00"}
        result = desc.write_fn(1.5, rep)
        assert result is not None
        path, body = result
        assert path == ["operational", "state", "vs", "0"]
        assert body == {"x.com.samsung.da.delayEndTime": "01:30:00"}

    def test_write_targets_delay_start_time_by_default(self):
        from custom_components.localthings.registry.capabilities.operational import (
            OPERATIONAL_STATE,
        )

        desc = next(
            e
            for e in OPERATIONAL_STATE.entities
            if e.key == "delay_start_hours" and isinstance(e, NumberDesc)
        )
        assert desc.write_fn is not None
        rep = {"x.com.samsung.da.delayStartTime": "00:00:00"}
        result = desc.write_fn(1.5, rep)
        assert result is not None
        _path, body = result
        assert body == {"x.com.samsung.da.delayStartTime": "01:30:00"}
