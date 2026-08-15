"""Guards against a SensorDesc unit Home Assistant won't accept for the
device_class it's paired with.

Unlike the SwitchDesc case (issue #349), a bad sensor unit doesn't raise --
sensor.py hands `unit` to `_attr_native_unit_of_measurement` and HA only
logs a warning per entity, once, telling the user to report a bug against
this integration. So the failure mode is a quiet stream of "not a valid
unit for the device class" warnings plus a support burden, with nothing in
the UI to hint anything is wrong.

The specific trap this exists for: HA spells its micrograms-per-cubic-metre
unit with U+03BC GREEK SMALL LETTER MU, and DEVICE_CLASS_UNITS holds only
that spelling. U+00B5 MICRO SIGN renders identically in an editor, in a
terminal, and in a code review diff, but is a different string and fails
the membership test. PR #365 shipped all three particulate units with
U+00B5.

Mirrors test_switch_device_class.py: scans every by_type registry rather
than a fixture, so a new capability making the same mistake fails here.
"""

import importlib
import pkgutil

from homeassistant.components.sensor.const import DEVICE_CLASS_UNITS, SensorDeviceClass

from custom_components.localthings.registry import by_type
from custom_components.localthings.registry.entities import SensorDesc


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


def _sensor_descs():
    seen = set()
    for reg in _all_registries():
        caps = [c for cs in reg.capabilities.values() for c in cs] + list(reg.pattern_capabilities)
        for cap in caps:
            for entity in cap.entities:
                if isinstance(entity, SensorDesc) and (reg.name, entity.key) not in seen:
                    seen.add((reg.name, entity.key))
                    yield reg.name, entity


def test_every_sensordesc_device_class_is_valid_for_ha():
    bad = []
    for reg_name, desc in _sensor_descs():
        if desc.device_class is None:
            continue
        try:
            SensorDeviceClass(desc.device_class)
        except ValueError:
            bad.append((reg_name, desc.key, desc.device_class))
    assert bad == []


def test_every_declared_unit_is_valid_for_its_device_class():
    """Descriptors carrying a `unit_fn` are exempt: those resolve their unit
    from the live rep (a device reporting Celsius vs Fahrenheit), so there
    is no static value to check here."""
    bad = []
    for reg_name, desc in _sensor_descs():
        if desc.device_class is None or desc.unit_fn is not None:
            continue
        units = DEVICE_CLASS_UNITS.get(SensorDeviceClass(desc.device_class))
        if units is not None and desc.unit not in units:
            bad.append(
                (reg_name, desc.key, desc.device_class, desc.unit, sorted(str(u) for u in units))
            )
    assert bad == []


def test_particulate_units_use_has_own_mu_codepoint():
    """The membership test above already fails on U+00B5, but only while a
    PM device_class is attached. Asserting the codepoint directly keeps the
    reason legible when someone re-types the literal."""
    from custom_components.localthings.registry.capabilities import air_purifier

    micro_sign, greek_mu = chr(0x00B5), chr(0x03BC)
    for _key, _icon, _type, _state_class, device_class, unit in air_purifier._AIR_QUALITY_SENSORS:
        if device_class is None:
            continue
        assert unit is not None, device_class
        assert unit == f"{greek_mu}g/m³", (device_class, [hex(ord(c)) for c in unit])
        assert micro_sign not in unit, device_class
