"""HA fan-entity mapping tests for the range hood."""

from typing import ClassVar, cast

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.fan import LocalThingsRangeHoodFan
from custom_components.localthings.registry.by_type import microwave, range_hood
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import FanDesc
from tests.conftest import _load_device


class _FakeCoordinator:
    device_serial = "TEST-HOOD-SERIAL"
    device_info: ClassVar[dict] = {}
    data: ClassVar[dict] = {}

    def __init__(self, resources):
        self.last_resources = resources
        self.commands = []

    def resource(self, href):
        return self.last_resources.get(href, {})

    def canonical_resources(self, subdevice):
        # Every bound entity in this test uses the default MAIN
        # subdevice, so the canonical view is just the raw snapshot
        # (issue #177 -- see LocalThingsEntity._resources).
        return self.last_resources

    async def async_send_command(self, bound, payload):
        self.commands.append((bound, payload))


def _entity(resources, coordinator=None, registry=range_hood.REGISTRY):
    bound = discover(
        resources,
        registry.capabilities,
        registry.pattern_capabilities,
    )
    fan_bound = next(item for item in bound if isinstance(item.desc, FanDesc))
    return LocalThingsRangeHoodFan(
        cast(LocalThingsCoordinator, coordinator or _FakeCoordinator(resources)),
        fan_bound,
    )


def _microwave_entity(resources, coordinator=None):
    return _entity(resources, coordinator, registry=microwave.REGISTRY)


def test_power_off_maps_to_zero_percent_and_four_retained_speeds():
    entity = _entity(_load_device("range_hood"))
    assert entity.is_on is False
    assert entity.speed_count == 4
    assert entity.percentage == 0


def test_active_codes_map_to_ordered_percentages():
    resources = _load_device("range_hood")
    resources["/power/0"]["value"] = True

    resources["/hood/fanspeed/vs/0"]["x.com.samsung.da.hood.fanSpeed"] = "14"
    assert _entity(resources).percentage == 25

    resources["/hood/fanspeed/vs/0"]["x.com.samsung.da.hood.fanSpeed"] = "15"
    assert _entity(resources).percentage == 50

    resources["/hood/fanspeed/vs/0"]["x.com.samsung.da.hood.fanSpeed"] = "16"
    assert _entity(resources).percentage == 75

    resources["/hood/fanspeed/vs/0"]["x.com.samsung.da.hood.fanSpeed"] = "17"
    assert _entity(resources).percentage == 100


async def test_power_write_prefers_standard_resource_when_both_exist():
    resources = _load_device("range_hood")
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_turn_on()

    assert coordinator.commands[-1][1] == ("power", True, "/power/0")


async def test_power_write_falls_back_to_vendor_resource():
    resources = _load_device("range_hood")
    resources.pop("/power/0")
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_turn_off()

    assert coordinator.commands[-1][1] == ("power", False, "/power/vs/0")


# ---------------------------------------------------------------------------
# Microwave built-in vent fan (issues #137/#142): reuses HOOD_FAN, but this
# board has no sibling /power/0 or /power/vs/0 resource -- fan speed 0 is
# itself the off state.
# ---------------------------------------------------------------------------


def test_standalone_hood_has_separate_power():
    """Explicit converse of the microwave case below: guards the
    discriminator itself, not just its downstream effects, so a future
    change to it fails loudly here instead of only via behavioral drift."""
    entity = _entity(_load_device("range_hood"))
    assert entity._has_separate_power() is True
    assert entity._speed_zero_is_off() is False


def test_microwave_vent_fan_has_no_separate_power_resource():
    resources = _load_device("microwave_me7500d")
    assert "/power/0" not in resources
    assert "/power/vs/0" not in resources
    entity = _microwave_entity(resources)
    assert entity._has_separate_power() is False
    assert entity._speed_zero_is_off() is True


async def test_combi_microwave_with_cavity_power_still_treats_zero_speed_as_fan_off():
    """A combi over-the-range microwave can report a /power/0 resource for
    the cavity while the vent fan still has no power resource of its own
    (settableMinFanSpeed '0' -- same board shape as microwave_me7500d).
    _speed_zero_is_off must key off the hood resource itself, not merely
    "some power resource exists on this device", so turning the fan off
    writes fan speed rather than the shared cavity power resource."""
    resources = _load_device("microwave_me7500d")
    resources["/power/0"] = {"value": True}
    resources["/hood/fanspeed/vs/0"]["x.com.samsung.da.hood.fanSpeed"] = "2"
    coordinator = _FakeCoordinator(resources)
    entity = _microwave_entity(resources, coordinator)

    assert entity._has_separate_power() is True
    assert entity._speed_zero_is_off() is True

    await entity.async_turn_off()

    assert coordinator.commands[-1][1] == ("speed", "0")


def test_microwave_vent_fan_off_state_excludes_zero_from_speed_codes():
    """supportedFanSpeed reports ['0'..'4'] with 0 meaning off -- unlike the
    standalone hood's codes, which never include an off entry."""
    resources = _load_device("microwave_me7500d")
    entity = _microwave_entity(resources)
    assert entity.is_on is False
    assert entity.percentage == 0
    assert entity.speed_count == 4  # ['1', '2', '3', '4'], '0' excluded


def test_microwave_vent_fan_nonzero_speed_reads_as_on():
    resources = _load_device("microwave_me7500d")
    resources["/hood/fanspeed/vs/0"]["x.com.samsung.da.hood.fanSpeed"] = "2"
    entity = _microwave_entity(resources)
    assert entity.is_on is True
    assert entity.percentage == 50  # 2nd of ['1', '2', '3', '4']


async def test_microwave_vent_fan_turn_off_writes_zero_speed_not_power():
    resources = _load_device("microwave_me7500d")
    resources["/hood/fanspeed/vs/0"]["x.com.samsung.da.hood.fanSpeed"] = "2"
    coordinator = _FakeCoordinator(resources)
    entity = _microwave_entity(resources, coordinator)

    await entity.async_turn_off()

    assert coordinator.commands[-1][1] == ("speed", "0")


async def test_microwave_vent_fan_turn_on_without_percentage_picks_lowest_speed():
    resources = _load_device("microwave_me7500d")
    coordinator = _FakeCoordinator(resources)
    entity = _microwave_entity(resources, coordinator)

    await entity.async_turn_on()

    assert coordinator.commands[-1][1] == ("speed", "1")


async def test_microwave_vent_fan_set_percentage_writes_speed_only():
    resources = _load_device("microwave_me7500d")
    coordinator = _FakeCoordinator(resources)
    entity = _microwave_entity(resources, coordinator)

    await entity.async_set_percentage(100)

    assert coordinator.commands == [(entity._bound, ("speed", "4"))]


async def test_microwave_vent_fan_turn_on_with_percentage_writes_speed_directly():
    resources = _load_device("microwave_me7500d")
    coordinator = _FakeCoordinator(resources)
    entity = _microwave_entity(resources, coordinator)

    await entity.async_turn_on(percentage=75)

    assert coordinator.commands == [(entity._bound, ("speed", "3"))]


async def test_microwave_vent_fan_turn_on_without_percentage_when_already_on_is_a_noop():
    """A scene or automation calling fan.turn_on on an already-running vent
    fan must not reset it to the lowest speed."""
    resources = _load_device("microwave_me7500d")
    resources["/hood/fanspeed/vs/0"]["x.com.samsung.da.hood.fanSpeed"] = "3"
    coordinator = _FakeCoordinator(resources)
    entity = _microwave_entity(resources, coordinator)

    await entity.async_turn_on()

    assert coordinator.commands == []


async def test_microwave_vent_fan_set_percentage_zero_turns_off():
    resources = _load_device("microwave_me7500d")
    resources["/hood/fanspeed/vs/0"]["x.com.samsung.da.hood.fanSpeed"] = "2"
    coordinator = _FakeCoordinator(resources)
    entity = _microwave_entity(resources, coordinator)

    await entity.async_set_percentage(0)

    assert coordinator.commands[-1][1] == ("speed", "0")


def test_microwave_vent_fan_missing_supported_fan_speed_falls_back_to_settable_min_max():
    """Hardware omitting supportedFanSpeed (e.g. ME8000T / issue #172) falls back
    to building speed codes from settableMinFanSpeed ('0') and settableMaxFanSpeed ('3')."""
    resources = _load_device("microwave_me7500d")
    resources["/hood/fanspeed/vs/0"].pop("x.com.samsung.da.hood.supportedFanSpeed", None)
    resources["/hood/fanspeed/vs/0"]["x.com.samsung.da.hood.settableMinFanSpeed"] = "0"
    resources["/hood/fanspeed/vs/0"]["x.com.samsung.da.hood.settableMaxFanSpeed"] = "3"

    entity = _microwave_entity(resources)
    assert entity._all_speed_codes() == ["0", "1", "2", "3"]
    assert entity._active_speed_codes() == ["1", "2", "3"]
    assert entity.speed_count == 3
    assert entity.supported_features & 1  # FanEntityFeature.SET_SPEED is present
