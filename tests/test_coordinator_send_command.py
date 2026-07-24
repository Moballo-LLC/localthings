"""Tests for LocalThingsCoordinator.async_send_command's optimistic-apply
step, specifically for x.com.samsung.da.options[] writes (issue #54).

The write itself only needs to carry the single changed token -- confirmed
on real hardware, the device merges by prefix and evicts the stale token
itself. But observe.ObserveManager.apply() does a shallow {**cached, **rep}
field merge, so handing it that same minimal single-token body would
overwrite the *whole* cached options[] field, wiping every sibling option
(other courses/levels/toggles packed into the same array) until the next
real poll lands. async_send_command must pre-merge the token into the
cached array itself before applying it optimistically, while still POSTing
only the minimal body over the wire.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import cbor2
import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_HOST, CONF_LEAF_CERT_PEM, CONF_LEAF_KEY_PEM, CONF_PORT, DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.capabilities import laundry
from custom_components.localthings.registry.discovery import BoundEntity

ENTRY_DATA = {
    CONF_HOST: '10.0.0.199',
    CONF_PORT: 49154,
    CONF_LEAF_CERT_PEM: '-----BEGIN CERTIFICATE-----\nTEST-LEAF\n-----END CERTIFICATE-----',
    CONF_LEAF_KEY_PEM: '-----BEGIN PRIVATE KEY-----\nTEST-LEAF-KEY\n-----END PRIVATE KEY-----',
}


class _FakeSendSession:
    def __init__(self):
        self.post_calls: list[tuple[list[str], bytes]] = []

    def post(self, path_segs, payload, timeout=None):
        self.post_calls.append((list(path_segs), payload))
        return 0x44, b''

    def pace(self):
        pass


@pytest.fixture
def coordinator(hass: HomeAssistant) -> LocalThingsCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id='localthings_SENDCMD-TEST',
    )
    entry.add_to_hass(hass)
    coord = LocalThingsCoordinator(hass, entry)
    coord.async_request_refresh = AsyncMock()
    coord._session = _FakeSendSession()
    return coord


async def test_options_write_posts_only_the_changed_token(coordinator) -> None:
    href = '/course/vs/0'
    coordinator._observe.apply(href, {
        'x.com.samsung.da.options': ['DeviceType_0167', 'Course_16', 'GMT_04'],
    }, source='poll')

    desc = laundry.cycle_select(translation_key='dryer_cycle', icon='x')
    bound = BoundEntity(href=href, capability=None, desc=desc)

    await coordinator.async_send_command(bound, '1D')

    posted_path, posted_bytes = coordinator._session.post_calls[0]
    assert posted_path == ['course', 'vs', '0']
    assert cbor2.loads(posted_bytes) == {'x.com.samsung.da.options': ['Course_1D']}


async def test_options_write_optimistic_cache_keeps_sibling_tokens(coordinator) -> None:
    """The regression this guards: applying the minimal wire body straight
    to the cache would wipe DeviceType_0167/GMT_04 until the next poll."""
    href = '/course/vs/0'
    coordinator._observe.apply(href, {
        'x.com.samsung.da.options': ['DeviceType_0167', 'Course_16', 'GMT_04'],
    }, source='poll')

    desc = laundry.cycle_select(translation_key='dryer_cycle', icon='x')
    bound = BoundEntity(href=href, capability=None, desc=desc)

    await coordinator.async_send_command(bound, '1D')

    cached = coordinator._cache.get(href)
    assert cached['x.com.samsung.da.options'] == [
        'DeviceType_0167', 'Course_1D', 'GMT_04',
    ]
