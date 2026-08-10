"""Cloud "Download" programs on a laundry device (issue #342).

Some course tables carry a course whose recipe isn't fixed in firmware --
selecting it runs whichever program was last pushed down from the
SmartThings cloud ("Download" / "Downloaded" in the course catalog). Three
tokens on ``/course/vs/0``'s ``x.com.samsung.da.options`` array drive it:

  ``CloudExtraCourse_<slot><slot>...``  the device's own list of downloaded
      program slots, one byte each -- the cloud counterpart of
      ``EditCourseList_`` for local courses.
  ``CloudCourse_<blob>``               the persisted default program.
  ``OneTimeCloudCourse_<blob>``        a this-run-only override.

A ``<blob>`` is an opaque fixed-width payload whose byte 2 is the slot id
it belongs to. Confirmed on two independent DA_WM_TP1_21_COMMON washers:
the issue #342 reporter's, whose ``CloudExtraCourse_0A5C286B2D0C55301A``
enumerates nine slots matching byte 2 of all nine of its programs exactly,
and the WA55A7700AV dump in ``tests/fixtures``, whose two-slot
``CloudExtraCourse_5958`` likewise matches its ``CloudCourse`` blob's byte
2. Blob width is *not* fixed across boards (20 bytes vs 16), which is one
reason nothing here ever synthesizes one.

This is not washer-only, and nothing here assumes an appliance type: the
DW5000C dishwasher in ``tests/fixtures`` advertises four slots on the same
token. It also carries no payload token at all, so a device can name
programs whose payloads have never been observed -- ``advertised_slots``
answers "which exist", the store answers "which are usable", and the two
are deliberately allowed to disagree.

What this module does and deliberately does not do
--------------------------------------------------
The device advertises *which* slots exist but never what any of them is
called, and never the full blob for a slot other than the one currently
loaded. A blob is only observable while the device happens to be sitting on
that program, so the full payload is *learned by observation* and persisted
(same rationale as learned.py's mode store), and the human-readable name is
supplied by the user in the options flow. Nothing is hardcoded: no catalog
of program ids, no table of blobs, no assumed Download course code. A
hardcoded catalog was considered and rejected -- a blob is cloud-assigned
per account/region, so one user's captured payload is not evidence about
anyone else's device.

Blobs are replayed byte-for-byte, exactly as captured, and never
decomposed or rebuilt. (Bytes 5/7/9 of the reporter's blobs do decode
cleanly to that program's temperature/rinse/spin, but the same offsets
produce nonsense against the WA55A7700AV blob, so that decode is recorded
in docs/investigations/download-cycle.md rather than shipped.)
"""

from __future__ import annotations

import threading

from .const import CONF_CLOUD_COURSES

COURSE_HREF = "/course/vs/0"

EXTRA_PREFIX = "CloudExtraCourse"
DEFAULT_PREFIX = "CloudCourse"
ONESHOT_PREFIX = "OneTimeCloudCourse"
COURSE_PREFIX = "Course"

# Synthetic, integration-owned field the coordinator merges onto
# /course/vs/0's rep so the registry's exists_fn/rep_fn/options/write_fn all
# reach this store through their existing signatures -- rep_fn in particular
# receives only its own href's rep, never the resource snapshot, so a
# sibling-resource lookup isn't available to it. Namespaced away from
# Samsung's own 'x.com.samsung.da.' fields so it can never collide with one,
# and merged at read time only: it is never written to the state cache, never
# sent to the device, and never part of a diagnostics dump.
FIELD = "x.localthings.cloudCourses"

# Raw-value namespace for a cloud program in the cycle select. A slot id is
# itself two hex chars, exactly like a local course code, so the two would be
# indistinguishable (and could collide outright) as bare select values.
RAW_PREFIX = "cloud:"

# A blob whose first two bytes are FFFF means "no program loaded" rather than
# naming one -- WA55A7700AV reports
# OneTimeCloudCourse_FFFF010049004D004A804C0037F0AC00 while sitting on a
# perfectly ordinary local course, and its byte 2 (01) is not one of the slots
# its own CloudExtraCourse_ advertises.
_SENTINEL_PREFIX = "FFFF"

# Byte offset within a blob that carries its slot id.
_SLOT_BYTE = 2
_MIN_BLOB_BYTES = 4


def _hex_bytes(blob):
    if not isinstance(blob, str) or len(blob) % 2 or len(blob) < _MIN_BLOB_BYTES * 2:
        return []
    try:
        int(blob, 16)
    except ValueError:
        return []
    return [blob[i : i + 2].upper() for i in range(0, len(blob), 2)]


def is_loaded(blob) -> bool:
    """True when `blob` names an actual program rather than 'none'."""
    parts = _hex_bytes(blob)
    return bool(parts) and not blob.upper().startswith(_SENTINEL_PREFIX)


def slot_of(blob) -> str | None:
    """The slot id `blob` belongs to, or None if it names no program."""
    parts = _hex_bytes(blob)
    if not parts or not is_loaded(blob):
        return None
    return parts[_SLOT_BYTE]


def option_value(options, prefix):
    """`<prefix>_<value>` from an options[] array. Duplicated from
    laundry.option_value rather than imported: this module is imported by
    the coordinator, and reaching into registry.capabilities from there
    would invert the dependency direction the rest of the integration
    keeps."""
    for o in options or []:
        if isinstance(o, str) and o.startswith(prefix + "_"):
            return o.split("_", 1)[1]
    return None


def advertised_slots(rep) -> list[str]:
    """Slot ids this device says it has downloaded programs in, from its own
    CloudExtraCourse_ token. The authority on *which* programs exist -- this
    is never inferred from what has been learned so far, so "3 of 9
    discovered" is answerable."""
    raw = option_value(rep.get("x.com.samsung.da.options"), EXTRA_PREFIX)
    if not isinstance(raw, str) or len(raw) % 2:
        return []
    slots = [raw[i : i + 2].upper() for i in range(0, len(raw), 2)]
    # Preserve the device's own order (first-seen wins) while dropping any
    # repeat, so the flow lists slots the way the appliance does.
    return list(dict.fromkeys(slots))


def supports_cloud_courses(rep) -> bool:
    """True for a device that advertises any downloaded-program slot."""
    return bool(advertised_slots(rep))


def _coerce(stored) -> tuple[str | None, dict[str, dict[str, str]]]:
    """Restore the persisted record, dropping anything not the shape this
    module writes -- it round-trips through the config entry as plain JSON
    and a hand-edited .storage file must not be able to crash setup (same
    posture as learned._coerce)."""
    if not isinstance(stored, dict):
        return None, {}
    download = stored.get("download_course")
    if not isinstance(download, str) or not download:
        download = None
    slots: dict[str, dict[str, str]] = {}
    raw_slots = stored.get("slots")
    if isinstance(raw_slots, dict):
        for slot, record in raw_slots.items():
            if not isinstance(slot, str) or not isinstance(record, dict):
                continue
            blob = record.get("blob")
            if not is_loaded(blob) or slot_of(blob) != slot.upper():
                continue
            name = record.get("name")
            slots[slot.upper()] = {
                "blob": blob.upper(),
                "name": name if isinstance(name, str) and name.strip() else "",
            }
    return download, slots


def stored(entry) -> dict:
    """What `entry` has persisted, coerced -- for a reader with no
    coordinator to go through (the options flow, on an unloaded entry)."""
    download, slots = _coerce(entry.data.get(CONF_CLOUD_COURSES))
    return {"download_course": download, "slots": slots}


def persist(hass, entry, record: dict) -> None:
    """Write `record` onto the entry. Runs on the event loop, which
    async_update_entry requires."""
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_CLOUD_COURSES: record})


class CloudCourses:
    """Per-device store of discovered cloud programs.

    Mutated from whichever thread applied the update (the DTLS reader for an
    OBSERVE notify, an executor thread for a poll -- see ObserveManager.apply),
    so every access takes the lock; persistence is the caller's job, on the
    event loop.
    """

    def __init__(self, stored_record=None) -> None:
        self._lock = threading.Lock()
        download, slots = _coerce(stored_record)
        self._download_course = download
        self._slots = slots
        # Course codes seen at the moment a one-time override was *loaded* --
        # candidates for "which course means Download on this board", pending
        # user confirmation (see download_candidates).
        self._candidates: dict[str, int] = {}
        # Last one-time payload seen, so a load can be told from a poll that
        # merely re-reports one. None means "nothing observed yet".
        self._last_oneshot: str | None = None

    # -- learning ---------------------------------------------------------

    def observe(self, rep: dict) -> bool:
        """Learn from one applied /course/vs/0 rep; True if anything changed.

        Two facts are learnable here. A blob is recorded against the slot its
        own byte 2 names, so a program only has to be sitting loaded once --
        on either token -- to be replayable forever after.

        The Download course code is only ever taken as a *candidate*, and
        only at the moment the one-time payload actually *changes* to a
        loaded value. That instant is the one the device is known to accept a
        program on, so the course selected then is real evidence. Counting
        every poll instead would rank by dwell time: tokens in this array are
        replaced by prefix and never evicted, so a stale OneTimeCloudCourse_
        outlives its run and sits there through however many polls the
        appliance spends on some ordinary course afterwards -- which is
        exactly the course that would then be suggested. A candidate is still
        never applied without confirmation in the options flow, but a
        confident wrong suggestion is most of the way to a wrong write, and a
        wrong write here starts a real wash cycle.
        """
        options = rep.get("x.com.samsung.da.options")
        if not options:
            return False
        known_slots = advertised_slots(rep)
        changed = False
        with self._lock:
            for prefix in (DEFAULT_PREFIX, ONESHOT_PREFIX):
                blob = option_value(options, prefix)
                slot = slot_of(blob)
                # A blob whose slot the device doesn't advertise is not a
                # program this appliance offers -- don't record it.
                if slot is None or (known_slots and slot not in known_slots):
                    continue
                record = self._slots.get(slot)
                if record is None:
                    self._slots[slot] = {"blob": blob.upper(), "name": ""}
                    changed = True
                elif record["blob"] != blob.upper():
                    record["blob"] = blob.upper()
                    changed = True
            oneshot = option_value(options, ONESHOT_PREFIX)
            course = option_value(options, COURSE_PREFIX)
            if course and is_loaded(oneshot) and oneshot != self._last_oneshot:
                self._candidates[course] = self._candidates.get(course, 0) + 1
            self._last_oneshot = oneshot
        return changed

    # -- reads ------------------------------------------------------------

    def download_course(self) -> str | None:
        with self._lock:
            return self._download_course

    def download_candidates(self) -> list[str]:
        """Course codes seen at the moment a one-time program was loaded,
        most-observed first -- what the options flow offers as the likely
        Download course. Never used for a write on its own."""
        with self._lock:
            ranked = sorted(self._candidates.items(), key=lambda kv: (-kv[1], kv[0]))
        return [code for code, _ in ranked]

    def blob(self, slot: str) -> str | None:
        with self._lock:
            record = self._slots.get(slot.upper())
            return record["blob"] if record else None

    def named(self) -> dict[str, str]:
        """Slots that are both learned and named -- the only ones offerable
        as a cycle option. An unnamed slot has no label that isn't either
        invented or an opaque hex id, so it stays out of the UI until the
        user supplies one."""
        with self._lock:
            return {slot: record["name"] for slot, record in self._slots.items() if record["name"]}

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "download_course": self._download_course,
                "slots": {slot: dict(record) for slot, record in self._slots.items()},
            }

    def view(self) -> dict:
        """What the registry sees under FIELD: only what a write or a label
        can actually be built from, so a descriptor never has to re-apply
        this module's rules."""
        with self._lock:
            if not self._download_course:
                return {}
            return {
                "download_course": self._download_course,
                "programs": {
                    slot: {"blob": record["blob"], "name": record["name"]}
                    for slot, record in self._slots.items()
                    if record["name"]
                },
            }

    # -- writes -----------------------------------------------------------

    def set_download_course(self, code: str | None) -> None:
        with self._lock:
            self._download_course = code or None

    def set_name(self, slot: str, name: str) -> None:
        with self._lock:
            record = self._slots.get(slot.upper())
            if record is not None:
                record["name"] = name.strip()

    def clear(self) -> None:
        with self._lock:
            self._download_course = None
            self._slots = {}
            self._candidates = {}


def undiscovered(rep: dict, record: dict) -> list[str]:
    """Advertised slots that aren't yet usable -- unlearned or unnamed. What
    the Repairs issue counts, and what the options flow asks the user to walk
    the appliance through."""
    programs = record.get("slots") or {}
    return [slot for slot in advertised_slots(rep) if not (programs.get(slot) or {}).get("name")]
