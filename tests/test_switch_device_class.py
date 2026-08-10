"""Guards against a SwitchDesc.device_class HA's SwitchDeviceClass doesn't
recognize -- switch.py passes it straight to SwitchDeviceClass(...), and an
invalid value raises out of the whole switch platform's async_setup_entry,
taking down every switch entity for the device, not just the offending one
(issue #349: water_purifier.py's lock switches used device_class='lock',
which SwitchDeviceClass only ever supported as 'outlet'/'switch').

Scans every by_type registry's declared capabilities rather than a specific
fixture, so a new capability introducing the same mistake fails here instead
of only surfacing as a live crash report.
"""

import importlib
import pkgutil

from homeassistant.components.switch import SwitchDeviceClass

from custom_components.localthings.registry import by_type
from custom_components.localthings.registry.entities import SwitchDesc


def _all_registries():
    for mod_info in pkgutil.iter_modules(by_type.__path__):
        if mod_info.name.startswith("_"):
            continue
        mod = importlib.import_module(
            f"custom_components.localthings.registry.by_type.{mod_info.name}"
        )
        reg = getattr(mod, "REGISTRY", None)
        if reg is not None:
            yield reg


def test_every_switchdesc_device_class_is_valid_for_ha():
    bad = []
    for reg in _all_registries():
        caps = [c for cs in reg.capabilities.values() for c in cs] + list(reg.pattern_capabilities)
        for cap in caps:
            for entity in cap.entities:
                if isinstance(entity, SwitchDesc) and entity.device_class is not None:
                    try:
                        SwitchDeviceClass(entity.device_class)
                    except ValueError:
                        bad.append((reg.name, entity.key, entity.device_class))
    assert bad == []
