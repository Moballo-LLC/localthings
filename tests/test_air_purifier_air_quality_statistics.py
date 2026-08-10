"""The particulate sensors must declare a state_class so Home Assistant keeps
long-term statistics for them; the graded readings must not.

Without a state_class a sensor only lives in the short-term recorder history
and is dropped at the next purge, so a long-range air-quality graph is not
possible -- that is the bug this guards against reappearing.
"""

from custom_components.localthings.registry.capabilities import air_purifier
from custom_components.localthings.registry.entities import SensorDesc

PARTICULATE = ("dust", "fine_dust", "super_fine_dust")
GRADED = ("odor", "clean_level")


def _desc(key):
    return next(d for d in air_purifier.AIR_QUALITY.entities if d.key == key)


def test_particulate_sensors_record_long_term_statistics():
    for key in PARTICULATE:
        assert _desc(key).state_class == "measurement", key


def test_graded_sensors_are_left_without_a_state_class():
    """Odor and CleanLevel read 0-2 on every fixture -- graded indices, not
    concentrations. Whether averaging a grade is meaningful is a separate
    call, so they stay unstamped rather than being guessed into statistics."""
    for key in GRADED:
        assert _desc(key).state_class is None, key


def test_no_unit_or_device_class_is_asserted():
    """state_class alone makes the series recordable. pm1/pm25/pm10 with
    µg/m³ would additionally assert the reading is a mass concentration,
    which no dump states."""
    for key in PARTICULATE + GRADED:
        desc = _desc(key)
        assert desc.unit is None, key
        assert desc.device_class is None, key


def test_state_class_comes_from_the_shared_tuples_fourth_column():
    """The rows carry their own state_class rather than a parallel lookup, so
    a new sensor can't be added here without deciding the question."""
    for row in air_purifier._AIR_QUALITY_SENSORS:
        assert len(row) == 4, row
        assert row[3] in ("measurement", None), row


def test_air_monitor_keeps_stamping_every_shared_sensor():
    """air_monitor imports _AIR_QUALITY_SENSORS and discards the fourth column
    on purpose: that board (issue #210) has stamped all five as `measurement`
    since it was added, and consuming the column would silently drop long-term
    statistics for Odor/CleanLevel there. Guards the import end to end and the
    deliberate divergence together."""
    from custom_components.localthings.registry.capabilities import air_monitor

    assert air_monitor.SENSORS.href == "/sensors/vs/0"
    for key in PARTICULATE + GRADED:
        desc = next(
            d for d in air_monitor.SENSORS.entities if d.key == key and isinstance(d, SensorDesc)
        )
        assert desc.state_class == "measurement", key


def test_every_air_quality_sensor_still_reads_a_plain_int():
    """A state_class is only honoured for a numeric state, so the value
    contract this depends on is asserted here too."""
    from tests.conftest import _load_device

    resources = _load_device("air_purifier")
    rep = resources["/sensors/vs/0"]
    for key in PARTICULATE + GRADED:
        value = _desc(key).value_fn(rep["x.com.samsung.da.items"])
        assert isinstance(value, int), (key, value)
