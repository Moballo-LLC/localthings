"""Stick-vacuum clean/auto-empty station device registry (issue #131).

See capabilities/vacuum_station.py's module docstring for why this only
covers the station's own dustbag/dustbin/UV-sanitize state and not any
vacuum-body control (suction, battery, cleaning mode) -- the diagnostics
dump this was built from reports none of that.
"""

from ..capabilities import common, ignored, vacuum_station
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="vacuum_station",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *common.UNIVERSAL,
            *common.POWER,
            vacuum_station.DUSTBAG,
            vacuum_station.DUSTBAG_USAGE,
            vacuum_station.DUSTBIN_SETTING,
            vacuum_station.CLEANSTATION_STATUS,
        ]
    ),
)
