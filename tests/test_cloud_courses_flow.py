"""End-to-end wiring for cloud "Download" cycles (issue #342).

The store's own rules are covered in test_cloud_courses.py. This file covers
the parts that only exist once a coordinator is running: learning from an
applied rep, persisting to the config entry, surfacing the store to entity
descriptors through the synthetic field, the Repairs nudge, and the options
flow that supplies the names.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import cbor2
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import cloudcourse
from custom_components.localthings.const import CONF_CLOUD_COURSES, DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.entities import SelectDesc
from custom_components.localthings.registry.subdevices import MAIN
from tests.conftest import _load_device
from tests.test_subdevice_discovery import ENTRY_DATA

FIXTURE = "washer_ww5000c_cloud"
COURSE = cloudcourse.COURSE_HREF

SPORTS = "0021550449284D134AA04C0035F004F005F0AC00"
JEANS = "001F6B0449284D134AA04C0035F004F005F0AC00"


async def _flush(hass: HomeAssistant) -> None:
    """hass.add_job hops used by the persist path."""
    for _ in range(3):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


def _entry(hass: HomeAssistant, data=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, **(data or {})},
        unique_id="localthings_CLOUD-COURSE-TEST",
    )
    entry.add_to_hass(hass)
    return entry


async def _coordinator(hass: HomeAssistant, entry=None) -> LocalThingsCoordinator:
    coordinator = LocalThingsCoordinator(hass, entry or _entry(hass))
    resources = _load_device(FIXTURE)
    coordinator._run_discovery(resources)
    for href, rep in resources.items():
        coordinator._observe.apply(href, rep, source="poll")
    await _flush(hass)
    return coordinator


def _cycle_state(coordinator: LocalThingsCoordinator):
    from custom_components.localthings.registry.adapter import flatten

    return flatten(coordinator.bound, coordinator.entity_resources()).get("cycle")


def _cycle_desc(coordinator: LocalThingsCoordinator) -> SelectDesc:
    return cast(SelectDesc, next(b.desc for b in coordinator.bound if b.desc.key == "cycle"))


def _cycle_options(coordinator: LocalThingsCoordinator):
    from custom_components.localthings.registry.subdevices import MAIN

    return _cycle_desc(coordinator).options(coordinator.canonical_resources(MAIN))


async def test_a_poll_learns_and_persists_the_loaded_programs(hass: HomeAssistant):
    entry = _entry(hass)
    coordinator = await _coordinator(hass, entry)

    assert coordinator.cloud_courses.snapshot()["slots"]["55"]["blob"] == SPORTS
    assert coordinator.cloud_courses.snapshot()["slots"]["6B"]["blob"] == JEANS
    # Survives a restart -- the payload is only visible while loaded.
    assert entry.data[CONF_CLOUD_COURSES]["slots"]["55"]["blob"] == SPORTS


async def test_an_optimistic_write_teaches_nothing(hass: HomeAssistant):
    """An optimistic rep is what this integration just wrote, not what the
    appliance reported."""
    coordinator = LocalThingsCoordinator(hass, _entry(hass))
    coordinator._observe.apply(
        COURSE,
        {"x.com.samsung.da.options": ["CloudExtraCourse_55", f"CloudCourse_{SPORTS}"]},
        source="optimistic",
    )
    await _flush(hass)
    assert "55" not in coordinator.cloud_courses.snapshot()["slots"]


async def test_learned_but_unnamed_programs_stay_out_of_the_cycle_select(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    options = _cycle_options(coordinator)
    assert all(not o.startswith(cloudcourse.RAW_PREFIX) for o in options)
    # The local courses are untouched.
    assert "87" in options


async def test_naming_a_program_puts_it_in_the_cycle_select(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)

    assert "cloud:55" in _cycle_options(coordinator)
    # The fixture is sitting on Download with a Jeans one-time override, and
    # Jeans is still unnamed -- so the state falls through to the raw course.
    assert _cycle_state(coordinator) == "87"

    coordinator.apply_cloud_courses({"6B": "Jeans"}, "87")
    await _flush(hass)
    assert _cycle_state(coordinator) == "cloud:6B"


async def test_the_synthetic_field_never_reaches_the_device_snapshot(hass: HomeAssistant):
    """It is merged at read time only: last_resources stays exactly what the
    appliance reported, so it can't be polled over, written back, or land in
    a diagnostics dump."""
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)

    assert cloudcourse.FIELD in coordinator.entity_resources()[COURSE]
    assert cloudcourse.FIELD not in coordinator.last_resources[COURSE]
    assert cloudcourse.FIELD not in coordinator.resource(COURSE)


async def test_selecting_a_named_program_writes_both_tokens(hass: HomeAssistant):
    """Driven through async_send_command rather than write_fn directly: the
    command path builds its own rep, and a rep taken straight off the state
    cache carries no cloud programs, so the write would silently no-op."""
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)

    sent: list[tuple[list[str], bytes]] = []

    class _FakeSession:
        def post(self, path_segs, payload, timeout=None):
            sent.append((path_segs, payload))
            return 0x44, b""

    coordinator._session = cast(Any, _FakeSession())
    bound = next(b for b in coordinator.bound if b.desc.key == "cycle")
    await coordinator.async_send_command(bound, "cloud:55")

    assert len(sent) == 1
    path_segs, payload = sent[0]
    assert path_segs == ["course", "vs", "0"]
    assert cbor2.loads(payload)["x.com.samsung.da.options"] == [
        "Course_87",
        f"OneTimeCloudCourse_{SPORTS}",
    ]
    # The optimistic merge keeps the rest of the array intact for the settle
    # window -- including the device's own slot list and the saved default.
    merged = coordinator.resource(COURSE)["x.com.samsung.da.options"]
    assert "Course_87" in merged
    assert f"OneTimeCloudCourse_{SPORTS}" in merged
    assert "CloudExtraCourse_0A5C286B2D0C55301A" in merged


async def test_a_repair_is_raised_until_every_program_is_named(hass: HomeAssistant):
    entry = _entry(hass)
    coordinator = await _coordinator(hass, entry)
    coordinator._refresh_cloud_course_issue()
    await _flush(hass)

    registry = ir.async_get(hass)
    issue_id = f"cloud_courses_{entry.entry_id}"
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert (issue.translation_placeholders or {})["total"] == "9"

    for slot in cloudcourse.advertised_slots(coordinator.cloud_course_rep()):
        # Only two are learned; name every advertised slot to close the gap.
        coordinator.cloud_courses.observe(
            {
                "x.com.samsung.da.options": [
                    "CloudExtraCourse_0A5C286B2D0C55301A",
                    f"CloudCourse_0000{slot}0449284D134AA04C0035F004F005F0AC00",
                ]
            }
        )
        coordinator.apply_cloud_courses({slot: f"Program {slot}"}, "87")
    await _flush(hass)

    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_no_repair_for_a_device_without_downloaded_programs(hass: HomeAssistant):
    entry = _entry(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    resources = _load_device("washer")
    coordinator._run_discovery(resources)
    for href, rep in resources.items():
        coordinator._observe.apply(href, rep, source="poll")
    coordinator._refresh_cloud_course_issue()
    await _flush(hass)

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"cloud_courses_{entry.entry_id}") is None


async def test_a_malformed_entry_record_does_not_block_setup(hass: HomeAssistant):
    entry = _entry(hass, data={CONF_CLOUD_COURSES: {"slots": {"55": {"blob": "junk"}}}})
    coordinator = await _coordinator(hass, entry)
    # Dropped on restore, then relearned from the live poll.
    assert coordinator.cloud_courses.snapshot()["slots"]["55"]["blob"] == SPORTS


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


async def _options_handler(hass: HomeAssistant, coordinator: LocalThingsCoordinator):
    from custom_components.localthings.config_flow import LocalThingsOptionsFlow

    entry = coordinator.config_entry
    assert entry is not None
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    handler = LocalThingsOptionsFlow()
    handler.hass = hass
    # OptionsFlow.config_entry resolves through `handler`, which the flow
    # manager normally sets when it starts the flow.
    handler.handler = entry.entry_id
    return handler


async def test_the_menu_offers_download_cycles_only_when_the_device_has_them(
    hass: HomeAssistant,
):
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    result = await handler.async_step_init()
    assert "cloud_courses" in result["menu_options"]


async def test_naming_through_the_flow_persists(hass: HomeAssistant):
    entry = _entry(hass)
    coordinator = await _coordinator(hass, entry)
    handler = await _options_handler(hass, coordinator)

    await handler.async_step_cloud_courses()
    await handler.async_step_cloud_courses(
        {"name_55": "Sports", "name_6B": "Jeans", "download_course": "87"}
    )
    await _flush(hass)

    assert coordinator.cloud_courses.named() == {"55": "Sports", "6B": "Jeans"}
    assert entry.data[CONF_CLOUD_COURSES]["download_course"] == "87"


async def test_the_flow_rejects_two_programs_sharing_a_name(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)

    result = await handler.async_step_cloud_courses(
        {"name_55": "Sports", "name_6B": "sports", "download_course": "87"}
    )
    assert result["errors"] == {"base": "cloud_course_name_duplicate"}
    assert coordinator.cloud_courses.named() == {}


async def test_clearing_a_name_removes_the_program_from_the_select(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_courses({"name_55": "Sports", "download_course": "87"})
    await _flush(hass)
    assert "cloud:55" in _cycle_options(coordinator)

    await handler.async_step_cloud_courses({"name_55": "", "download_course": "87"})
    await _flush(hass)
    assert "cloud:55" not in _cycle_options(coordinator)


async def test_without_a_confirmed_download_course_nothing_is_offered(hass: HomeAssistant):
    """Names alone aren't enough -- there'd be no course code to write."""
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_courses({"name_55": "Sports", "download_course": ""})
    await _flush(hass)

    assert coordinator.cloud_courses.named() == {"55": "Sports"}
    assert "cloud:55" not in _cycle_options(coordinator)


async def test_the_form_proposes_the_observed_download_course(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    result = await handler.async_step_cloud_courses()

    assert result["step_id"] == "cloud_courses"
    assert result["description_placeholders"]["total"] == "9"
    # Two learned so far, seven still to walk through on the appliance.
    assert result["description_placeholders"]["found"] == "2"
    assert coordinator.cloud_courses.download_candidates() == ["87"]


async def test_no_repair_until_a_program_has_actually_been_seen(hass: HomeAssistant):
    """The DW5000C advertises four downloaded programs and has never loaded
    one, so nothing about them is learnable and no name field can be offered.
    Warning its owner about a feature they may never use -- with no action
    that could clear it -- is worse than staying quiet until they run one."""
    entry = _entry(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    resources = _load_device("dishwasher_dw5000c_cloud")
    coordinator._run_discovery(resources)
    for href, rep in resources.items():
        coordinator._observe.apply(href, rep, source="poll")
    coordinator._refresh_cloud_course_issue()
    await _flush(hass)

    assert cloudcourse.advertised_slots(coordinator.cloud_course_rep()) == ["8E", "8D", "8F", "02"]
    assert coordinator.cloud_courses.snapshot()["slots"] == {}
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"cloud_courses_{entry.entry_id}") is None


async def test_the_synthetic_field_never_reaches_diagnostics(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """canonical_resources carries it so entity descriptors can read it, and
    diagnostics reads canonical_resources -- so the drop has to happen at the
    redaction boundary. A dump is meant to be what the appliance said, and
    cloud program names are text the user typed."""
    from custom_components.localthings.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = _entry(hass)
    coordinator = await _coordinator(hass, entry)
    coordinator.apply_cloud_courses({"55": "Marc's weekend towels"}, "87")
    await _flush(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    diag = await async_get_config_entry_diagnostics(hass, entry)
    dump = json.dumps(diag)
    assert cloudcourse.FIELD not in dump
    # The device's own tokens are still there -- resources stays what it said.
    assert "CloudExtraCourse_0A5C286B2D0C55301A" in dump

    # The store is reported in its own block, so a triager can see the
    # payloads -- but not the user's chosen names.
    assert "Marc's weekend towels" not in dump
    assert diag["cloud_courses"]["payloads"]["55"] == SPORTS
    assert diag["cloud_courses"]["named_slots"] == ["55"]
    assert diag["cloud_courses"]["download_course"] == "87"


async def test_the_debug_read_service_reports_only_device_state(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)

    stripped = coordinator.device_resources(MAIN)
    assert cloudcourse.FIELD not in stripped[COURSE]
    assert "x.com.samsung.da.options" in stripped[COURSE]


async def test_the_flow_rejects_a_download_course_the_device_does_not_offer(
    hass: HomeAssistant,
):
    """Whatever lands here becomes the Course_ token of a real write."""
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)

    result = await handler.async_step_cloud_courses({"name_55": "Sports", "download_course": "FF"})
    assert result["errors"] == {"base": "cloud_course_unknown_course"}
    assert coordinator.cloud_courses.snapshot()["download_course"] is None
    assert coordinator.cloud_courses.named() == {}


async def test_the_flow_rejects_a_name_shadowing_a_personal_course(hass: HomeAssistant):
    """Personal course names come from the device, not the catalog, and the
    select renders them the same way -- so they collide the same way."""
    coordinator = await _coordinator(hass)
    # 'MyCo' as a personal course label on a code the device offers.
    rep = dict(coordinator.resource("/wm/personalcourse/vs/0") or {})
    rep["x.com.samsung.da.courses"] = ["1C_01044D79436F"]
    coordinator._observe.apply("/wm/personalcourse/vs/0", rep, source="poll")
    await _flush(hass)

    handler = await _options_handler(hass, coordinator)
    result = await handler.async_step_cloud_courses({"name_55": "MyCo", "download_course": "87"})
    assert result["errors"] == {"base": "cloud_course_name_duplicate"}
