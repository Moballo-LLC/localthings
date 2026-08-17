"""Long-term statistics survive the move onto the OCF device UUID (#381).

The identity migration rewrites entity registry rows in place rather than
letting Home Assistant create replacements. The reason that matters most to
an existing user is history: statistics are keyed by `statistic_id`, which
for a sensor *is* its entity_id, so an entity that came back as
`sensor.foo_2` would leave years of recorded data stranded under a name
nothing writes to any more -- silently, with no error and no repair.

tests/localthings/test_identity_migration.py proves the entity_id is
preserved. It cannot prove that preserving it preserves the history,
because no recorder is running there. This drives a real in-memory recorder
end to end: statistics recorded, entry re-keyed, values read back.

Lives here rather than under tests/localthings/ for the same reason
test_statistics_migration_end_to_end.py does: that package's autouse
`enable_custom_integrations` fixture depends on `hass`, which starts Home
Assistant before `recorder_mock` can claim its database URL. Nothing here
loads the integration -- `rekey_entry` is called directly.
"""

from __future__ import annotations

from datetime import timedelta
from functools import partial
from typing import cast

import pytest
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.components.recorder.util import get_instance
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.localthings.const import CONF_HOST, CONF_SERIAL, DOMAIN
from custom_components.localthings.rekey import rekey_entry

OLD_KEY = "BS7SP9AW400114A"  # the shared serial from issue #381
NEW_KEY = "ccfd73b3-aeb4-792a-1100-68f06f5d603b"  # that unit's own /oic/d `di`
RECORDED = [11.0, 9.0, 14.0]


async def _seed_statistics(hass: HomeAssistant, entity_id: str) -> None:
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=4)
    async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.ARITHMETIC,
            "has_sum": False,
            "name": None,
            "source": "recorder",
            "statistic_id": entity_id,
            "unit_class": None,
            "unit_of_measurement": None,
        },
        [
            {"start": start + timedelta(hours=i), "mean": value, "min": value, "max": value}
            for i, value in enumerate(RECORDED)
        ],
    )
    await async_wait_recording_done(hass)


async def _means(hass: HomeAssistant, entity_id: str) -> list[float]:
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utcnow() - timedelta(days=1),
        None,
        {entity_id},
        "hour",
        None,
        {"mean"},
    )
    return [cast(float, row["mean"]) for row in rows.get(entity_id, [])]


@pytest.fixture
def purifier_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.0.3", CONF_SERIAL: OLD_KEY},
        unique_id=f"{DOMAIN}_{OLD_KEY}",
        version=3,
    )
    entry.add_to_hass(hass)
    return entry


async def test_recorded_history_survives_the_re_key(
    recorder_mock, hass: HomeAssistant, purifier_entry: MockConfigEntry
) -> None:
    """The claim the whole in-place rewrite exists to make good on."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=purifier_entry.entry_id, identifiers={(DOMAIN, OLD_KEY)}
    )
    dust = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{OLD_KEY}_dust",
        config_entry=purifier_entry,
        device_id=device.id,
        suggested_object_id="bedroom_purifier_dust",
    )
    await _seed_statistics(hass, dust.entity_id)
    assert await _means(hass, dust.entity_id) == RECORDED

    rekey_entry(hass, purifier_entry, OLD_KEY, NEW_KEY)
    await async_wait_recording_done(hass)

    # The row moved to the new identity...
    moved = ent_reg.async_get(dust.entity_id)
    assert moved is not None
    assert moved.unique_id == f"{DOMAIN}_{NEW_KEY}_dust"
    # ...without moving the entity_id, which is what the statistics are
    # filed under -- so the history is still there, unchanged and still
    # attached to the entity the user sees.
    assert moved.entity_id == "sensor.bedroom_purifier_dust"
    assert await _means(hass, dust.entity_id) == RECORDED

    # The metadata row is still filed under this entity_id too, so the
    # recorder has not quietly started a second series alongside it.
    metadata = await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_ids={dust.entity_id})
    )
    assert set(metadata) == {dust.entity_id}


async def test_a_removed_duplicate_does_not_take_the_surviving_history_with_it(
    recorder_mock, hass: HomeAssistant, purifier_entry: MockConfigEntry
) -> None:
    """Where the destination key is already taken, the old-key row is deleted
    rather than rewritten. The history belongs to whichever entity_id the
    user has been looking at all along -- the surviving row -- so deleting
    the dead duplicate must not disturb it.

    This is the one path in the re-key that destroys a registry row, so it
    is the one worth proving keeps its hands off the recorder.
    """
    ent_reg = er.async_get(hass)
    live = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{NEW_KEY}_dust",
        config_entry=purifier_entry,
        suggested_object_id="bedroom_purifier_dust",
    )
    stale = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{OLD_KEY}_dust", config_entry=purifier_entry
    )
    assert stale.entity_id != live.entity_id
    await _seed_statistics(hass, live.entity_id)

    rekey_entry(hass, purifier_entry, OLD_KEY, NEW_KEY)
    await async_wait_recording_done(hass)

    assert ent_reg.async_get(stale.entity_id) is None
    assert ent_reg.async_get(live.entity_id) is not None
    assert await _means(hass, live.entity_id) == RECORDED
