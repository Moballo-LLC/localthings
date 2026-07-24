"""Microwave device registry — issue #66.

Reuses the oven family's door/connected/operational-state capabilities
wholesale (byte-for-byte identical resource shapes, all generically-keyed)
and adds microwave-specific mode/cavity/temperature capabilities for the
fields that differ. See capabilities/microwave.py's module docstring.
"""
from ..capabilities import common, ignored, microwave, oven
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='microwave',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        *common.POWER,
        oven.OVEN_DOOR,
        oven.OVEN_CONNECTED,
        oven.OVEN_OPERATIONAL_STATE,
        microwave.MICROWAVE_MODE,
        microwave.MICROWAVE_CAVITY,
        microwave.MICROWAVE_TEMPERATURE,
    ]),
)
