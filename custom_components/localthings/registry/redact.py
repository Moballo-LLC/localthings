"""Redact account/identity data from a raw resource tree before it leaves
the user's Home Assistant instance (diagnostics downloads, issue reports).

/device/0 dumps mix appliance state with genuinely sensitive data when
Bixby/voice is set up on the device: a Samsung account email, a Bixby
access token, a hashed device ID, WiFi/BLE MAC addresses, the serial
number, and otnDUID. This walks the whole tree and redacts any value whose
key matches a known-sensitive substring, regardless of which href it's
under — new device types will have unknown-shaped data we can't fully
enumerate in advance, so this errs on catching the field by name rather
than only redacting inside hrefs we already recognize.
"""

from __future__ import annotations

REDACTED = "**REDACTED**"

_SENSITIVE_SUBSTRINGS = (
    "mac",
    "serial",
    "token",
    "login",
    "account",
    "email",
    "userid",
    "deviceid",
    "uuid",
    "duid",
    "password",
    "secret",
)

# Matched whole, not as substrings: OCF's /oic/d and /oic/p identify the
# unit with bare one/two-letter keys too short for the substring rules above
# ('di' is a substring of 'condition', 'display', ...). 'di'/'pi' are the
# device/platform UUIDs; 'n' is /oic/d's free-text device name, which may
# carry a person's name -- the device-type signal we actually want from
# that resource is `rt`, which is not redacted.
_SENSITIVE_EXACT = frozenset({"di", "pi", "n"})


# Fields this integration merges onto a rep for its own use, which the
# device never reported (see coordinator.entity_resources). A diagnostics
# dump is meant to be exactly what the appliance said, so these are dropped
# rather than redacted -- keeping them would both misrepresent the device and
# publish data the user typed (cloud program names are user-supplied).
_SYNTHETIC_KEY_PREFIX = "x.localthings."


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT:
        return True
    return any(s in lowered for s in _SENSITIVE_SUBSTRINGS)


def strip_synthetic(resources):
    """Drop this integration's own merged-in fields, leaving only what the
    appliance actually reported.

    Separate from redact_resources because the two answer different
    questions: the debug read service wants the device's unredacted state
    (serial and all -- that is the point of it) but still shouldn't present
    our own bookkeeping as something the device said.
    """
    if isinstance(resources, dict):
        return {
            key: strip_synthetic(value)
            for key, value in resources.items()
            if not key.startswith(_SYNTHETIC_KEY_PREFIX)
        }
    if isinstance(resources, list):
        return [strip_synthetic(item) for item in resources]
    return resources


def redact_resources(resources):
    """Recursively redact dict values whose key matches a sensitive substring,
    and drop this integration's own synthetic fields entirely.

    Works on the shape produced by parse_device0_batch (dict[href, rep]) or
    any nested dict/list structure within a rep.
    """
    if isinstance(resources, dict):
        return {
            key: (REDACTED if _is_sensitive_key(key) else redact_resources(value))
            for key, value in resources.items()
            if not key.startswith(_SYNTHETIC_KEY_PREFIX)
        }
    if isinstance(resources, list):
        return [redact_resources(item) for item in resources]
    return resources
