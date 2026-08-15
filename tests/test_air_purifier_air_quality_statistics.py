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


def test_particulate_sensors_declare_pm_device_class_and_unit():
    """Dust/FineDust/SuperFineDust map to PM10/PM2.5/PM1 (issue #325) -- see
    air_purifier._AIR_QUALITY_SENSORS for the three lines of evidence and
    tests/test_air_quality_grade_column.py for the device-side ones.

    The expected unit comes from HA's own constant rather than a literal:
    typing it out is how PR #365 landed U+00B5 MICRO SIGN where HA uses
    U+03BC, which renders identically and would make this test agree with
    the bug."""
    from homeassistant.const import CONCENTRATION_MICROGRAMS_PER_CUBIC_METER as UG_M3

    expected = {
        "dust": ("pm10", UG_M3),
        "fine_dust": ("pm25", UG_M3),
        "super_fine_dust": ("pm1", UG_M3),
    }
    for key, (device_class, unit) in expected.items():
        desc = _desc(key)
        assert desc.device_class == device_class, key
        assert desc.unit == unit, key
    for key in GRADED:
        desc = _desc(key)
        assert desc.device_class is None, key
        assert desc.unit is None, key


def test_metadata_comes_from_the_shared_tuples_own_columns():
    """The rows carry their own state_class/device_class/unit rather than a
    parallel lookup, so a new sensor can't be added here without deciding
    each question. Unit validity against HA is a separate guard --
    tests/test_sensor_device_class_units.py."""
    for row in air_purifier._AIR_QUALITY_SENSORS:
        assert len(row) == 6, row
        assert row[3] in ("measurement", None), row
        assert row[4] in ("pm10", "pm25", "pm1", None), row
        # A device_class without a unit would leave HA inferring one.
        assert (row[4] is None) == (row[5] is None), row


def test_air_monitor_takes_the_pm_labels_but_not_the_state_class():
    """air_monitor imports _AIR_QUALITY_SENSORS and consumes device_class and
    unit -- the mapping rests on device-side grading that board shares (issue
    #325), so typing one family and not the other would be an inconsistency.

    state_class is still discarded: that board (issue #210) has stamped all
    five as `measurement` since it was added, and consuming the column would
    silently drop long-term statistics for Odor/CleanLevel there. Guards the
    import end to end and the deliberate divergence together."""
    from custom_components.localthings.registry.capabilities import air_monitor

    assert air_monitor.SENSORS.href == "/sensors/vs/0"
    for key in PARTICULATE + GRADED:
        desc = next(
            d for d in air_monitor.SENSORS.entities if d.key == key and isinstance(d, SensorDesc)
        )
        assert desc.state_class == "measurement", key
        assert desc.device_class == _desc(key).device_class, key
        assert desc.unit == _desc(key).unit, key
    # And the graded pair stays untyped on both families.
    for key in GRADED:
        desc = next(
            d for d in air_monitor.SENSORS.entities if d.key == key and isinstance(d, SensorDesc)
        )
        assert (desc.device_class, desc.unit) == (None, None), key


def test_every_air_quality_sensor_still_reads_a_plain_int():
    """A state_class is only honoured for a numeric state, so the value
    contract this depends on is asserted here too."""
    from tests.conftest import _load_device

    resources = _load_device("air_purifier")
    rep = resources["/sensors/vs/0"]
    for key in PARTICULATE + GRADED:
        value = _desc(key).value_fn(rep["x.com.samsung.da.items"])
        assert isinstance(value, int), (key, value)
