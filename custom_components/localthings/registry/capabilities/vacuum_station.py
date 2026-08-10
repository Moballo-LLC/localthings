"""Capabilities for the Samsung stick-vacuum clean/auto-empty station
(model A-VSKR-TP1-22-VS9500AL, "Bespoke Jet" clean station, issue #131).

The reporter's diagnostics dump shows no vacuum-body state at all (no
suction level, no battery, no cleaning-mode/room-mapping control, no
docked/undocked status even) -- only the clean station's own dustbag,
dustbin auto-empty settings, and UV-C sanitizing-cycle status. This
strongly suggests the WiFi/DTLS module lives in the station, not the
handheld stick: the station is the only "device" this integration's local
API can see, and the wand's own controls are unrelated hardware not
reachable this way. Modeled as its own device type -- these hrefs don't
overlap with any existing family, so there's no shared-href ambiguity to
resolve against another type (see registry/by_type/__init__.py's
docstring for that rule).

Resources verified against the issue #131 diagnostics dump.
"""

from ..capability import Capability
from ..entities import BinarySensorDesc, SelectDesc, SensorDesc, SwitchDesc
from .common import int_or_none
from .common import parse_iso_utc as _parse_iso_utc

DUSTBAG = Capability(
    href="/component/station/dustbag/vs/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="dustbag_full",
            field="x.com.samsung.da.status",
            device_class="problem",
            icon="mdi:bag-personal",
            value_fn=lambda v: v == "full",
        ),
    ),
)

# dustbagUsage/dustbagPrevUsage are raw counters with no capacity/resolution
# field alongside them to normalize into a percentage (unlike the AC/range
# families' filterUsage, which always ships filterCapacity) -- exposed as a
# plain diagnostic count rather than guessing a unit.
DUSTBAG_USAGE = Capability(
    href="/component/station/dustbagusage/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="dustbag_usage",
            field="x.com.samsung.da.dustbagUsage",
            icon="mdi:counter",
            entity_category="diagnostic",
            state_class="total_increasing",
            value_fn=int_or_none,
        ),
    ),
)

DUSTBIN_SETTING = Capability(
    href="/setting/dustbin/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="auto_empty",
            field="x.com.samsung.da.autoEmpty",
            icon="mdi:delete-empty",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["setting", "dustbin", "vs", "0"],
                {"x.com.samsung.da.autoEmpty": "On" if p == "On" else "Off"},
            ),
        ),
        SwitchDesc(
            key="dustbin_auto_close",
            field="x.com.samsung.da.autoClose",
            icon="mdi:door-sliding",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["setting", "dustbin", "vs", "0"],
                {"x.com.samsung.da.autoClose": "On" if p == "On" else "Off"},
            ),
        ),
        SelectDesc(
            key="discharging_time",
            field="x.com.samsung.da.desiredDischargingTime",
            icon="mdi:timer-outline",
            entity_category="config",
            options_field="x.com.samsung.da.supportedDischargingTime",
            write_fn=lambda p, rep, href=None: (
                ["setting", "dustbin", "vs", "0"],
                {"x.com.samsung.da.desiredDischargingTime": p},
            ),
        ),
    ),
)

# stickStatus's exact meaning (docked? powered? charging?) isn't confirmed
# from this single On/Off dump -- exposed as a plain diagnostic sensor
# rather than a binary_sensor, so no polarity/semantic is asserted that
# might be wrong.
CLEANSTATION_STATUS = Capability(
    href="/status/cleanstation/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="cleanstation_status",
            field="x.com.samsung.da.status",
            icon="mdi:home-lightning-bolt",
            entity_category="diagnostic",
        ),
        SensorDesc(
            key="stick_status",
            field="x.com.samsung.da.stickStatus",
            icon="mdi:broom",
            entity_category="diagnostic",
        ),
        SwitchDesc(
            key="uvc_intensive_mode",
            field="x.com.samsung.da.uvcIntensive",
            icon="mdi:lightbulb-on-outline",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["status", "cleanstation", "vs", "0"],
                {"x.com.samsung.da.uvcIntensive": "On" if p == "On" else "Off"},
            ),
        ),
        SensorDesc(
            key="uvc_operation_time",
            field="x.com.samsung.da.uvcOperationTime",
            icon="mdi:timer-sand",
            entity_category="diagnostic",
            value_fn=int_or_none,
        ),
        SensorDesc(
            key="uvc_total_operation_time",
            field="x.com.samsung.da.uvcTotalOperationTime",
            icon="mdi:timer-sand",
            entity_category="diagnostic",
            state_class="total_increasing",
            value_fn=int_or_none,
        ),
        SensorDesc(
            key="uvc_finished_time",
            field="x.com.samsung.da.uvcFinishedTime",
            device_class="timestamp",
            entity_category="diagnostic",
            value_fn=_parse_iso_utc,
        ),
        SensorDesc(
            key="uvc_emitted_time",
            field="x.com.samsung.da.emittedTime",
            device_class="timestamp",
            entity_category="diagnostic",
            value_fn=_parse_iso_utc,
        ),
    ),
)
