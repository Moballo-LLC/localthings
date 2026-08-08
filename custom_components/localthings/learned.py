"""Modes a device reports itself in but never advertises as supported.

Some firmwares report a current mode that is missing from the same
resource's own supported list (issue #327: an ARTIK051 air conditioner
sitting in 'Quiet' with supportedModes [Off, Sleep, Speed, Nano,
NanoSleep]). The mode is real -- the remote and the SmartThings app select
it, and the unit accepts it written back -- so once the device has been
seen in it, it is remembered and offered alongside the advertised ones.

Learning is deliberately not global. A current value that isn't a
selectable option is common across this corpus -- an oven idling in
'NoOperation', a fridge's /mode/vs/0 carrying capability tokens like
'WATERFILTER_DISABLE' -- and remembering one of those permanently would
put an option in the UI that the device can only reject. LEARNABLE is the
allowlist of canonical hrefs where a device-reported current mode is known
to be a genuine selectable option; every consumer of it (climate's
_supported today) must read its supported list through the coordinator.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .registry.capabilities.airconditioner import HREF_CONVENIENT

MODES_FIELD = "x.com.samsung.da.modes"
SUPPORTED_FIELD = "x.com.samsung.da.supportedModes"


@dataclass(frozen=True)
class LearnRule:
    """Which field of a rep names the current mode, and which lists the
    supported ones. Both are per-href because Samsung spells them
    differently across resources (`supportedModes` on the vendor `/vs/`
    ones, bare `modes`/`supportedModes` on a few OCF-shaped ones)."""

    current_field: str
    supported_field: str


LEARNABLE: dict[str, LearnRule] = {
    # Convenient (preset) mode. Reported by two independent reporters on
    # ARTIK051_PRAC_20K, one of whom has three identical units where only
    # the two sharing an outdoor unit hide Quiet -- so this is a firmware
    # reporting gap, not a real capability difference.
    HREF_CONVENIENT: LearnRule(MODES_FIELD, SUPPORTED_FIELD),
}


def _codes(value) -> list[str]:
    """The mode codes in a `modes`-style field, which is an array on every
    board seen but a bare string on none -- tolerated anyway, since this
    runs against whatever the device sends."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str)]
    return []


def _coerce(stored) -> dict[str, dict[str, list[str]]]:
    """Restore the persisted map, dropping anything that isn't the shape
    this module writes. It round-trips through the config entry as plain
    JSON, and a hand-edited .storage file shouldn't be able to crash
    setup."""
    restored: dict[str, dict[str, list[str]]] = {}
    if not isinstance(stored, dict):
        return restored
    for href, fields in stored.items():
        if not isinstance(href, str) or not isinstance(fields, dict):
            continue
        for field, codes in fields.items():
            if not isinstance(field, str):
                continue
            if valid := [c for c in _codes(codes) if c]:
                restored.setdefault(href, {})[field] = valid
    return restored


class LearnedModes:
    """Per-device store of learned codes, keyed by actual (on-the-wire)
    href so two subdevices of one composite appliance learn separately.

    Mutated from whichever thread applied the update (the DTLS reader for
    an OBSERVE notify, an executor thread for a poll -- see
    ObserveManager.apply), so every access takes the lock; persistence is
    the caller's job, on the event loop.
    """

    def __init__(self, stored=None) -> None:
        self._lock = threading.Lock()
        self._learned = _coerce(stored)

    def observe(self, canonical_href: str, actual_href: str, rep: dict) -> bool:
        """Learn from one applied rep; True when something new was learned
        (i.e. the caller should persist).

        A rep that carries no supported list teaches nothing: "missing from
        the list" is only meaningful against a list that exists, and
        inventing one for a device that publishes none would offer options
        nothing ever said were selectable. `rep` is the merged rep
        ObserveManager.apply stores, so a partial notify carrying `modes`
        alone (issue #27) still sees the supported list from the last full
        poll.
        """
        rule = LEARNABLE.get(canonical_href)
        if rule is None:
            return False
        supported = _codes(rep.get(rule.supported_field))
        if not supported:
            return False
        with self._lock:
            known = self._learned.get(actual_href, {}).get(rule.supported_field, [])
            new = [
                code
                for code in _codes(rep.get(rule.current_field))
                if code and code not in supported and code not in known
            ]
            if not new:
                return False
            self._learned.setdefault(actual_href, {})[rule.supported_field] = [*known, *new]
        return True

    def codes(self, actual_href: str, field: str = SUPPORTED_FIELD) -> list[str]:
        with self._lock:
            return list(self._learned.get(actual_href, {}).get(field, ()))

    def snapshot(self) -> dict[str, dict[str, list[str]]]:
        with self._lock:
            return {
                href: {f: list(c) for f, c in fields.items()}
                for href, fields in self._learned.items()
            }

    def clear(self) -> bool:
        """Forget everything; True when there was something to forget."""
        with self._lock:
            if not self._learned:
                return False
            self._learned = {}
        return True
