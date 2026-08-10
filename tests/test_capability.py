from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.entities import BinarySensorDesc


def test_capability_defaults():
    c = Capability(
        href="/kidslock/vs/0",
        entities=(BinarySensorDesc(key="child_lock", field="x.com.samsung.da.kidsLock"),),
    )
    assert c.poll_tier == "cold"
    assert len(c.entities) == 1


def test_capability_is_frozen():
    c = Capability(href="/kidslock/vs/0", entities=())
    # setattr(), not `c.href = ...` -- `href` is a read-only property to the
    # type checker (frozen dataclass field), so a literal attribute
    # assignment doesn't type-check; setattr's signature is untyped enough
    # to accept it, and it still goes through the same frozen __setattr__
    # at runtime, so this is still a faithful test of the frozen guarantee.
    attr_name = "href"
    try:
        setattr(c, attr_name, "/other/vs/0")
    except Exception:
        return
    raise AssertionError("expected frozen dataclass")
