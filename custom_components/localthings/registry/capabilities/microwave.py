"""Capabilities for the microwave family (Samsung MW5300A-class, issue #66).

/doors/vs/0, /connected/vs/0, and /operational/state/vs/0 report the exact
same field shapes as the oven family's resources of the same name (all
generically-keyed: door_open, cloud_connected, machine_state, cycle_active,
progress_percentage, operation_time_minutes, finish_time, cook_time, stop),
so those capabilities are reused directly from oven.py rather than
re-declared here -- see by_type/microwave.py.

/mode/vs/0's cook-mode vocabulary (MicroWave/MicroWaveGrill/Grill/Autocook)
and options-array toggles (only a Sound_On/Off slot on this dump -- no
UpperLamp/fastpreheat/NaturalSteam like the oven family) are microwave-
specific, so it gets its own capability here. /oven/vs/0 additionally
reports a powerLevel field (a wattage with the unit embedded in the string,
e.g. "700W") the oven family's cavity capability doesn't carry, so it also
gets its own.

Cycle start/pause is deliberately not modeled: unlike the oven family
(whose module docstring documents confirmed-unreliable local cycle-start
writes from live testing), no live microwave has exercised writes at all --
this module is built from a single diagnostics dump. The shared
STOP_BUTTON's state='Ready' RMW is safe and reused via
oven.OVEN_OPERATIONAL_STATE. The /temperatures/vs/0 reading is exposed
read-only for the same reason: desired='0' on the only dump seen, well
below any plausible cook temperature, with no confirmed write contract.

/recipe/cook/vs/0 (a JSON-encoded {language, menu, servingSize, option}
display string) is not modeled here -- every field is empty on this dump
(device idle, no active cook program); see ignored.py.
"""
import re

from ..capability import Capability
from ..entities import SelectDesc, SensorDesc, SwitchDesc
from .common import normalize_temp_unit


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


_POWER_LEVEL_RE = re.compile(r'^(-?\d+)W$')


def _power_level_watts(v):
    """'0'/'700W' -> 0/700 -- the unit is embedded in the string on every
    dump seen so far; strip it so the sensor reports a plain wattage."""
    if not isinstance(v, str):
        return None
    m = _POWER_LEVEL_RE.match(v)
    return int(m.group(1)) if m else None


def _cavity_temp_unit(rep):
    items = rep.get('x.com.samsung.da.items') or []
    unit = items[0].get('x.com.samsung.da.unit') if items else None
    return normalize_temp_unit(unit, default='°C')


def _option_value(options, prefix):
    """Find `<prefix>_<value>` in an options array and return <value>."""
    for o in (options or []):
        if isinstance(o, str) and o.startswith(prefix + '_'):
            return o.split('_', 1)[1]
    return None


def _replace_in_options(options, prefix, new_value):
    """Return a new options list with the `<prefix>_*` slot replaced."""
    return [f"{prefix}_{new_value}" if o.startswith(prefix + '_') else o
            for o in options]


def _sound_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    opts = list(rep.get('x.com.samsung.da.options') or [])
    if not opts:
        return None
    return ['mode', 'vs', '0'], {
        'x.com.samsung.da.options': _replace_in_options(opts, 'Sound', p),
    }


# Cook modes as reported by /mode/vs/0's supportedModes (issue #66 dump) --
# no oven-style Bake/Broil vocabulary applies to a microwave.
_MICROWAVE_MODES = (
    'NoOperation',
    'MicroWave',
    'MicroWaveGrill',
    'Grill',
    'Autocook',
    'AutocookCustom',
)


def _mode_write(p, rep, href=None):
    if p not in _MICROWAVE_MODES:
        return None
    return ['mode', 'vs', '0'], {'x.com.samsung.da.modes': [p]}


MICROWAVE_MODE = Capability(
    href='/mode/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='cook_mode', field='x.com.samsung.da.modes',
                   icon='mdi:tune',
                   options=_MICROWAVE_MODES,
                   value_fn=lambda v: v[0] if v else None,
                   write_fn=_mode_write),
        SwitchDesc(key='sound', field='x.com.samsung.da.options',
                   icon='mdi:volume-high', entity_category='config',
                   value_fn=lambda opts: _option_value(opts, 'Sound') == 'On',
                   write_fn=_sound_write),
    ),
)

MICROWAVE_CAVITY = Capability(
    href='/oven/vs/0',
    poll_tier='hot',
    entities=(
        SensorDesc(key='cavity_state', field='x.com.samsung.da.state'),
        SensorDesc(key='power_level', field='x.com.samsung.da.powerLevel',
                   icon='mdi:flash', device_class='power',
                   state_class='measurement', unit='W',
                   value_fn=_power_level_watts),
    ),
)

MICROWAVE_TEMPERATURE = Capability(
    href='/temperatures/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='cavity_temp', field='x.com.samsung.da.items',
                   device_class='temperature',
                   state_class='measurement', entity_category='diagnostic',
                   unit_fn=_cavity_temp_unit,
                   value_fn=lambda items: _int(
                       (items[0].get('x.com.samsung.da.current') if items else None))),
    ),
)
