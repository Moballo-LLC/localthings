"""Tests for LocalThingsSelect's option-list resolution
(custom_components/localthings/select.py) -- the static tuple, options_field,
and callable forms of SelectDesc.options.
"""
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import SelectDesc
from custom_components.localthings.select import LocalThingsSelect


class _FakeCoordinator:
    device_serial = 'TEST-SERIAL'

    def __init__(self, last_resources):
        self.last_resources = last_resources


def _make_select(desc, href, last_resources):
    capability = Capability(href=href, entities=(desc,))
    bound = BoundEntity(href=href, capability=capability, desc=desc)
    return LocalThingsSelect(_FakeCoordinator(last_resources), bound)


def test_static_options_unaffected():
    desc = SelectDesc(key='x', options=('A', 'B'))
    entity = _make_select(desc, '/x/vs/0', {})
    assert entity.options == ['A', 'B']


def test_options_field_unaffected():
    desc = SelectDesc(key='x', options_field='supported')
    entity = _make_select(desc, '/x/vs/0', {'/x/vs/0': {'supported': ['Lo', 'Hi']}})
    assert entity.options == ['Lo', 'Hi']


def test_callable_options_receives_full_resource_snapshot():
    """A callable options is handed the coordinator's full href->rep
    snapshot, not just this entity's own href's rep -- needed for course
    lists decoded from a sibling resource (see laundry.cycle_options)."""
    calls = []

    def _options_fn(resources):
        calls.append(resources)
        return list(resources.get('/other/vs/0', {}).get('codes', []))

    desc = SelectDesc(key='cycle', translation_key='fake_cycle', options=_options_fn)
    resources = {
        '/x/vs/0': {},
        '/other/vs/0': {'codes': ['1C', '1D']},
    }
    entity = _make_select(desc, '/x/vs/0', resources)
    assert entity.options == ['1C', '1D']
    assert calls == [resources]


def test_callable_options_empty_result():
    desc = SelectDesc(key='cycle', options=lambda resources: [])
    entity = _make_select(desc, '/x/vs/0', {})
    assert entity.options == []


def test_callable_translation_key_reresolves_live_not_once_at_construction():
    """A callable translation_key (laundry.cycle_select's table-id-gated
    resolver) must be re-evaluated against current coordinator data on
    every access, not baked in once at __init__ -- discovery can run while
    a sibling resource (e.g. /st/washercourse/vs/0) is still an empty stub
    (see entity.py's _is_included docstring), and a one-time resolution
    would permanently show untranslated codes even after a later poll
    populates the real value."""
    desc = SelectDesc(key='cycle', translation_key=lambda resources: resources.get('key'))
    resources = {'key': None}
    entity = _make_select(desc, '/x/vs/0', resources)
    assert entity.translation_key is None

    resources['key'] = 'washer_cycle_table_02'
    assert entity.translation_key == 'washer_cycle_table_02'


async def test_unknown_vendor_option_round_trips_to_exact_raw_value():
    """Readable fallback labels must still write the exact Samsung token."""
    class _WritableCoordinator(_FakeCoordinator):
        data = {'mode': 'FutureVendorMode'}

        def __init__(self, last_resources):
            super().__init__(last_resources)
            self.writes = []

        async def async_send_command(self, bound, value):
            self.writes.append(value)

    desc = SelectDesc(
        key='mode', translation_key='door_alert',
        options=('Known', 'FutureVendorMode'), write_fn=lambda *args: None,
    )
    capability = Capability(href='/x/vs/0', entities=(desc,))
    bound = BoundEntity(href='/x/vs/0', capability=capability, desc=desc)
    coordinator = _WritableCoordinator({})
    entity = LocalThingsSelect(coordinator, bound)

    assert entity.options[-1] == 'Future Vendor Mode'
    await entity.async_select_option('Future Vendor Mode')
    assert coordinator.writes == ['FutureVendorMode']
