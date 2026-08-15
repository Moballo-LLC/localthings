"""The v2 -> v3 statistics relabel against a real recorder, not a mock.

tests/localthings/test_statistics_migration.py proves the migration calls
HA's API with the right arguments for the right entities. It cannot prove
that call does what the migration needs, because the recorder is patched
out. This drives an in-memory recorder end to end: statistics recorded
unitless, migration run, metadata inspected -- and, most importantly, the
recorded *values* checked to be untouched, which is the claim that makes
doing this automatically safe rather than something to ask each user about.

Lives here rather than under tests/localthings/ on purpose: that package's
autouse `enable_custom_integrations` fixture depends on `hass`, which
starts Home Assistant before `recorder_mock` can claim its database URL.
Nothing here loads the integration -- `async_migrate_entry` is called
directly -- so the entry only needs the one key the v2 -> v3 step reads.
"""

from __future__ import annotations

from datetime import timedelta
from functools import partial
from typing import cast

import pytest
from homeassistant.components.recorder.models import StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.components.recorder.util import get_instance
from homeassistant.const import CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.localthings import async_migrate_entry
from custom_components.localthings.const import CONF_DEVICE_TYPE, DOMAIN
from custom_components.localthings.registry.entities import SensorDesc

SERIAL = "TEST-SERIAL-0000"
RECORDED = [11.0, 9.0, 14.0]


async def _seed_unitless_statistics(hass: HomeAssistant, entity_id: str) -> None:
    """Record hourly statistics the way these sensors always have: numeric
    means, no unit of measurement at all."""
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


async def _metadata(hass: HomeAssistant, entity_id: str) -> StatisticMetaData:
    result = await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_ids={entity_id})
    )
    return result[entity_id][1]


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
        data={CONF_DEVICE_TYPE: "air_purifier"},
        unique_id=f"{DOMAIN}_{SERIAL}",
        version=2,
    )
    entry.add_to_hass(hass)
    return entry


def _dust_entity(hass: HomeAssistant, entry: MockConfigEntry):
    return er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{SERIAL}_dust", config_entry=entry
    )


async def test_relabels_metadata_without_touching_recorded_values(
    recorder_mock, hass: HomeAssistant, purifier_entry: MockConfigEntry
) -> None:
    dust = _dust_entity(hass, purifier_entry)
    await _seed_unitless_statistics(hass, dust.entity_id)

    before = await _metadata(hass, dust.entity_id)
    assert before["unit_of_measurement"] is None
    assert await _means(hass, dust.entity_id) == RECORDED

    assert await async_migrate_entry(hass, purifier_entry) is True
    await async_wait_recording_done(hass)

    after = await _metadata(hass, dust.entity_id)
    assert after["unit_of_measurement"] == CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
    assert after["unit_class"] == "concentration"
    # The readings were always µg/m³; only the label was missing. Nothing is
    # converted, so the recorded history still says exactly what it said.
    assert await _means(hass, dust.entity_id) == RECORDED


async def test_recorded_unit_ends_up_matching_what_the_descriptor_declares(
    recorder_mock, hass: HomeAssistant, purifier_entry: MockConfigEntry
) -> None:
    """The invariant the migration exists to establish, stated directly.

    HA raises units_changed -- and suppresses statistics generation -- when
    an entity's unit disagrees with the unit recorded against its
    statistic_id. Rather than drive HA's validation to observe that, this
    asserts the condition that validation reads: after migrating, the
    recorded metadata says exactly what the descriptor says. Asserting our
    own invariant instead of Home Assistant's reaction to it keeps the test
    off internals that change between versions (both `_update_issues` and
    `validate_statistics` have gained parameters), and tests this repo
    rather than that one."""
    from custom_components.localthings.registry.capabilities import air_purifier

    dust = _dust_entity(hass, purifier_entry)
    await _seed_unitless_statistics(hass, dust.entity_id)

    desc = next(
        d
        for d in air_purifier.AIR_QUALITY.entities
        if d.key == "dust" and isinstance(d, SensorDesc)
    )
    assert (await _metadata(hass, dust.entity_id))["unit_of_measurement"] != desc.unit

    assert await async_migrate_entry(hass, purifier_entry) is True
    await async_wait_recording_done(hass)

    assert (await _metadata(hass, dust.entity_id))["unit_of_measurement"] == desc.unit
