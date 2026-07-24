"""Unit tests for microwave-family capabilities (issue #66)."""
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities import microwave
from custom_components.localthings.registry.discovery import discover


# ---------------------------------------------------------------------------
# Device-type detection + full-dump coverage
# ---------------------------------------------------------------------------

def test_microwave_fixture_resolves_and_has_no_unbound_hrefs():
    """The issue #66 dump previously came back device_type='unknown' with
    /connected/vs/0 and /recipe/cook/vs/0 unbound -- resolving via the
    '-MICROWAVE-' modelNum token fallback must leave every href in the
    microwave registry bound or ignored."""
    from tests.conftest import _load_device
    resources = _load_device('microwave')
    info = resources['/information/vs/0']
    reg = for_device_by_model(
        info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'])
    assert reg is not None
    assert reg.name == 'microwave'

    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


# ---------------------------------------------------------------------------
# MICROWAVE_MODE -- SelectDesc with non-empty options
# ---------------------------------------------------------------------------

def test_microwave_mode_options_nonempty():
    assert len(microwave.MICROWAVE_MODE.entities[0].options) > 0


def test_microwave_mode_write_round_trips():
    desc = microwave.MICROWAVE_MODE.entities[0]
    valid_mode = desc.options[1]   # e.g. 'MicroWave'
    path, body = desc.write_fn(valid_mode, {})
    assert path == ['mode', 'vs', '0']
    assert body['x.com.samsung.da.modes'] == [valid_mode]


def test_microwave_mode_rejects_unknown():
    desc = microwave.MICROWAVE_MODE.entities[0]
    assert desc.write_fn('SpaghettiMode', {}) is None


# ---------------------------------------------------------------------------
# MICROWAVE_MODE -- sound options-array RMW
# ---------------------------------------------------------------------------

def _mode_rep(*extra_opts):
    return {'x.com.samsung.da.options': [
        'DeviceType_MW5300A-/KO0', 'ScreenTimeOut_10min', 'Sound_On', *extra_opts,
    ]}


def test_sound_write_preserves_other_options():
    desc = next(e for e in microwave.MICROWAVE_MODE.entities if e.key == 'sound')
    path, body = desc.write_fn('Off', _mode_rep())
    opts = body['x.com.samsung.da.options']
    assert 'Sound_Off' in opts
    assert 'ScreenTimeOut_10min' in opts     # other slot unchanged


def test_sound_write_requires_existing_options():
    desc = next(e for e in microwave.MICROWAVE_MODE.entities if e.key == 'sound')
    assert desc.write_fn('On', {}) is None


def test_sound_write_rejects_invalid_payload():
    desc = next(e for e in microwave.MICROWAVE_MODE.entities if e.key == 'sound')
    assert desc.write_fn('Loud', _mode_rep()) is None


# ---------------------------------------------------------------------------
# MICROWAVE_CAVITY -- plain sensors, no write path
# ---------------------------------------------------------------------------

def test_cavity_state_reads_state_field():
    desc = next(e for e in microwave.MICROWAVE_CAVITY.entities if e.key == 'cavity_state')
    assert desc.value_fn('Ready') == 'Ready'


def test_power_level_reads_raw_string():
    desc = next(e for e in microwave.MICROWAVE_CAVITY.entities if e.key == 'power_level')
    assert desc.value_fn('700W') == '700W'


# ---------------------------------------------------------------------------
# MICROWAVE_TEMPERATURE -- read-only current-temperature sensor
# ---------------------------------------------------------------------------

def test_cavity_temp_reads_current_field():
    desc = microwave.MICROWAVE_TEMPERATURE.entities[0]
    items = [{'x.com.samsung.da.current': '1', 'x.com.samsung.da.desired': '0'}]
    assert desc.value_fn(items) == 1


def test_cavity_temp_missing_items_is_none():
    desc = microwave.MICROWAVE_TEMPERATURE.entities[0]
    assert desc.value_fn([]) is None


def test_cavity_temp_unit_defaults_celsius():
    from custom_components.localthings.registry.capabilities.microwave import _cavity_temp_unit
    rep = {'x.com.samsung.da.items': [{'x.com.samsung.da.unit': 'Celsius'}]}
    assert _cavity_temp_unit(rep) == '°C'


def test_cavity_temp_unit_falls_back_when_absent():
    from custom_components.localthings.registry.capabilities.microwave import _cavity_temp_unit
    assert _cavity_temp_unit({'x.com.samsung.da.items': []}) == '°C'
