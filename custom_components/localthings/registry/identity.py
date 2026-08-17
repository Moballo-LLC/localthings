"""Read device identity from standard OCF resources (/oic/p, /oic/d, /oic/res)."""

from __future__ import annotations

from dataclasses import dataclass, field

import cbor2


@dataclass(frozen=True)
class DeviceIdentity:
    manufacturer: str
    model: str
    name: str
    serial: str | None
    # /oic/d's `di` and /oic/p's `pi` -- OCF's own device and platform
    # UUIDs. Promoted out of `raw` into named fields because
    # resolve_device_key mints permanent registry keys from them; see its
    # docstring for why `di` leads.
    device_id: str | None = None
    platform_id: str | None = None
    device_types: tuple[str, ...] = ()
    raw: dict[str, dict | list] = field(default_factory=dict)


def is_placeholder_serial(serial: str) -> bool:
    """True for a non-empty serialNum that isn't actually a real identity.

    The ARTIK051_DONGLE_REF firmware family reports the literal string
    'Nothing(SVC)' for every unit -- non-empty, so a plain `if not serial`
    check doesn't catch it, and two such units on the same install silently
    collide, dropping the second one's entities (issue #83).

    Issue #189: the DA_WM_A51_20_COMMON (ARTIK051) laundry family reports a
    flash-unset sentinel instead -- every character the same repeated hex
    digit -- which the 'nothing' check doesn't catch either, aborting the
    second unit's config flow as already configured.

    Lives here rather than duplicated in config_flow.py/coordinator.py: the
    config flow resolves the serial once and persists it for the
    coordinator to seed its registry keys from (issue #236), so two copies
    of this rule could let the two sides disagree and orphan a registry
    entry.
    """
    s = serial.strip()
    if s.lower().startswith("nothing"):
        return True
    upper = s.upper()
    return len(upper) >= 8 and len(set(upper)) == 1 and upper[0] in "0123456789ABCDEF"


def resolve_serial(raw_serial: str | None, host: str) -> str:
    """The device identity to mint registry keys from.

    `raw_serial` is /information/vs/0's x.com.samsung.da.serialNum as the
    device reported it. Boards that report nothing usable fall back to the
    host, which is stable per install and unique across devices on one
    network -- see is_placeholder_serial for the two families that need it.
    """
    s = (raw_serial or "").strip()
    if not s or is_placeholder_serial(s):
        return host
    return s


def is_usable_device_id(value: str | None) -> bool:
    """True for an OCF `di`/`pi` that actually identifies one unit.

    Firmware that never had a UUID assigned reports OCF's nil UUID
    (all-zero, dashes aside), which is identical on every unit of the
    family -- the #189 failure mode transplanted onto a new field, and not
    something `is_placeholder_serial`'s repeated-hex-digit rule catches,
    because the dashes make more than one distinct character.

    Past that, the same known-junk rules that disqualify a serialNum
    disqualify a UUID: a board firmware-flashed with 'Nothing(SVC)' in one
    identity field is not a board to trust in another. Anything else is
    accepted as-is. A *shared but well-formed* `di` would be undetectable
    here, exactly as issue #381's shared-but-well-formed serialNum is --
    heuristics can't see that, which is the whole reason this moves onto a
    field the protocol itself has to keep distinct.
    """
    s = (value or "").strip()
    if not s:
        return False
    if not set(s) - {"0", "-"}:
        return False
    return not is_placeholder_serial(s)


def ocf_device_key(identity: DeviceIdentity | None) -> str | None:
    """The OCF-derived half of resolve_device_key's chain, or None when the
    device reported no usable UUID.

    Split out because "the device has no UUID" and "the device has this
    UUID" are different answers to a caller holding an existing key: the
    coordinator must never demote an entry from a UUID back onto a
    serialNum just because one poll couldn't read /oic/d (a reconnect, a
    timeout), which is exactly what it would do if it only saw the
    collapsed string resolve_device_key returns.

    Normalized because the result is compared against its stored form on
    every poll; firmware that changes case between reads would otherwise
    look like a different appliance.
    """
    if identity is None:
        return None
    for candidate in (identity.device_id, identity.platform_id):
        if candidate is not None and is_usable_device_id(candidate):
            return candidate.strip().lower()
    return None


def resolve_device_key(identity: DeviceIdentity | None, raw_serial: str | None, host: str) -> str:
    """The identity to mint this device's permanent registry keys from.

    Tried in order: /oic/d's `di`, /oic/p's `pi`, the serialNum, the host.

    `di` leads because it is the identifier the protocol we are already
    speaking uses to address this endpoint -- if it were wrong or shared,
    OCF discovery and the DTLS association would not work in the first
    place. serialNum, by contrast, is a vendor-populated string no part of
    the stack depends on, which is why three separate firmware families
    have shipped it unusable: 'Nothing(SVC)' (#83), a flash-unset sentinel
    (#189), and -- unfixable by any heuristic -- a well-formed serial
    duplicated across every unit of the model (#381).

    `pi` is only the fallback despite the OCF spec calling it immutable:
    it identifies the *platform*, so a board hosting more than one logical
    OCF device shares one `pi` across all of them, reintroducing the very
    collision this exists to prevent. `di` is device-scoped, which is the
    granularity of a config entry.

    The serial stays in the chain below both so a board that answers
    neither OCF resource lands exactly where it did before this existed,
    and the host stays last for the same reason -- it is an address rather
    than an identity (a new DHCP lease silently makes it someone else's),
    so it is strictly a last resort.
    """
    return ocf_device_key(identity) or resolve_serial(raw_serial, host)


def resolve_model(model_num: str, identity: DeviceIdentity | None) -> str:
    """The model string to name and register a device under.

    `model_num` is /information/vs/0's modelNum, which many boards report
    as `<model>|<board>` -- only the part before the pipe is recognizable.
    A board reporting no modelNum falls back to /oic/p's mnmo. Shared with
    resolve_serial's motivation: two copies of this split rule could let
    the config flow and the coordinator's post-poll recompute disagree, and
    a device renaming itself after the first poll is the visible symptom.
    """
    if model_num:
        return model_num.split("|", 1)[0]
    return identity.model if identity else ""


def device_display_name(device_type_name: str | None, model: str) -> str:
    """The HA device name for a resolved device type + model. Shared by the
    config flow and the coordinator's post-discovery rebuild, so the name a
    device is first registered under matches what discovery produces later
    -- otherwise every setup would rename the device once the first poll
    landed."""
    device_type = device_type_name.replace("_", " ").title() if device_type_name else "Appliance"
    return f"Samsung {device_type} ({model})" if model else f"Samsung {device_type}"


def _get(sess, path) -> dict:
    try:
        code, pl = sess.get(path, timeout=10.0)
        if code == 0x45 and pl:
            body = cbor2.loads(pl)
            return body if isinstance(body, dict) else {}
    except Exception:
        pass
    return {}


def _get_links(sess, path) -> list:
    """Like _get, but for /oic/res: a baseline-Interface RETRIEVE on it
    returns a CBOR array of Link objects (href/rt/if/di/...), not a single
    Property map."""
    try:
        code, pl = sess.get(path, timeout=10.0)
        if code == 0x45 and pl:
            body = cbor2.loads(pl)
            return body if isinstance(body, list) else []
    except Exception:
        pass
    return []


def _device_types(d: dict) -> tuple[str, ...]:
    """/oic/d's `rt` -- the device's own OCF device-type declaration.

    The one standardized "what am I" field in OCF: alongside the generic
    'oic.wk.d' it carries a concrete type like 'oic.d.airconditioner' or a
    SmartThings 'x.com.st.d.*' equivalent. `registry/by_type/resolve()`
    consults this first, via `for_device_by_oic_type`, but only a minority
    of dumps populate it, so the modelNum/description path stays
    load-bearing. Kept whole in diagnostics (see `raw` below) so issue
    reports keep surfacing types the table doesn't know about yet.
    """
    rt = d.get("rt")
    if isinstance(rt, str):
        rt = [rt]
    if not isinstance(rt, (list, tuple)):
        return ()
    return tuple(t for t in rt if isinstance(t, str))


def read_identity(sess, serial: str | None) -> DeviceIdentity:
    p = _get(sess, ["oic", "p"])
    d = _get(sess, ["oic", "d"])
    # /oic/res is OCF's baseline resource-discovery endpoint: a unicast
    # RETRIEVE returns every Resource/Collection href this endpoint hosts,
    # not just /device/0. Relevant for the "Composite Device" model (issue
    # #177: one physical device exposing more than one logical subdevice,
    # each its own Collection). registry.subdevices.enumerate_subdevices
    # reads this to find a board's `/device/<n>` siblings -- that probing
    # used to run right here on every _connect_session/reconnect and moved
    # to that module so it only runs once, at first discovery.
    res = _get_links(sess, ["oic", "res"])
    return DeviceIdentity(
        manufacturer=p.get("mnmn") or "Samsung",
        model=p.get("mnmo") or "",
        name=d.get("n") or "",
        serial=serial,
        device_id=d.get("di") if isinstance(d.get("di"), str) else None,
        platform_id=p.get("pi") if isinstance(p.get("pi"), str) else None,
        device_types=_device_types(d),
        # Kept whole rather than field-by-field: outside the /device/0 dump
        # diagnostics already captures, and we don't yet know which fields
        # will turn out to identify a device type.
        raw={"/oic/p": p, "/oic/d": d, "/oic/res": res},
    )
