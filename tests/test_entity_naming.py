"""Tests for translated entity naming and dynamic instance placeholders."""
from custom_components.localthings.entity import LocalThingsEntity
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import BinarySensorDesc


class _FakeCoordinator:
    device_serial = 'TEST-SERIAL'

    def __init__(self, last_resources=None):
        self.last_resources = last_resources or {}


def _make_entity(desc, href='/x/vs/0', key_override=None, instance='', instance_name=None):
    capability = Capability(href=href, entities=(desc,))
    bound = BoundEntity(href=href, capability=capability, desc=desc,
                         instance=instance, key_override=key_override,
                         instance_name=instance_name)
    return LocalThingsEntity(_FakeCoordinator(), bound)


def test_descriptor_key_is_the_default_translation_key():
    """A descriptor names itself through the catalog, keyed by its own key.

    Nothing sets _attr_name: that would take precedence over HA's
    translation catalog and make the entity untranslatable.
    """
    desc = BinarySensorDesc(key='enabled')
    entity = _make_entity(desc, instance_name='Cubed Ice')
    assert entity.translation_key == 'enabled'
    assert not hasattr(entity, '_attr_name')


def test_device_instance_name_becomes_translation_placeholder():
    desc = BinarySensorDesc(
        key='enabled', translation_key='instance_enabled', use_instance_name=True
    )
    entity = _make_entity(desc, key_override='icemaker_one_enabled',
                           instance_name='Cubed Ice')
    assert entity.translation_key == 'instance_enabled'
    assert entity.translation_placeholders == {'instance_name': 'Cubed Ice'}
    assert not hasattr(entity, '_attr_name')


def test_href_instance_name_becomes_translation_placeholder():
    desc = BinarySensorDesc(
        key='enabled', translation_key='instance_enabled', use_instance_name=True
    )
    entity = _make_entity(desc, key_override='icemaker_one_enabled')
    assert entity.translation_placeholders == {'instance_name': 'Icemaker One'}
