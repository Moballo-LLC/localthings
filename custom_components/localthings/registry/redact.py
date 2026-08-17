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

# Matched whole, not as substrings: these are bare one/two-letter keys too
# short for the substring rules above ('n' is a substring of very nearly
# everything). 'n' is /oic/d's free-text device name, which the owner sets
# from the SmartThings app and can carry a person's name -- the device-type
# signal we actually want from that resource is `rt`, which is not redacted.
#
# OCF's /oic/d `di` and /oic/p `pi` used to be redacted here too. They are
# not account data: they're randomly-assigned per-unit UUIDs, carrying no
# more about their owner than the appliance-internal subdeviceIdList
# diagnostics already reports for the same reason (see diagnostics.py).
# Redacting them cost more than it bought -- issue #381 was two units
# colliding on a duplicated serialNum, and the first diagnostics download
# asking whether their `di`/`pi` differed came back with both values
# blanked, so the question could only be answered by walking the reporter
# through a manual read_resource call. They are now also what
# resolve_device_key mints registry keys from, so a report that hides them
# hides the identity every entity in it is named after.
_SENSITIVE_EXACT = frozenset({"n"})


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT:
        return True
    return any(s in lowered for s in _SENSITIVE_SUBSTRINGS)


def redact_resources(resources):
    """Recursively redact dict values whose key matches a sensitive substring.

    Works on the shape produced by parse_device0_batch (dict[href, rep]) or
    any nested dict/list structure within a rep.
    """
    if isinstance(resources, dict):
        return {
            key: (REDACTED if _is_sensitive_key(key) else redact_resources(value))
            for key, value in resources.items()
        }
    if isinstance(resources, list):
        return [redact_resources(item) for item in resources]
    return resources
