import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device


@pytest.mark.parametrize(
    "name,expected_type",
    [
        ("dishwasher", "dishwasher"),
        ("refrigerator", "refrigerator"),
    ],
)
def test_full_pipeline_v2(name, expected_type):
    resources = _load_device(name)

    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    assert reg is not None
    assert reg.name == expected_type

    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    assert bound

    state = flatten(bound, resources)
    assert state
