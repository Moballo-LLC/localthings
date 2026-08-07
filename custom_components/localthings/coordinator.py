"""Coordinator for Local Things integration."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import threading
import time
import zlib
from datetime import timedelta
from typing import Any

import cbor2
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from smartthings_local.ocf.state_cache import StateCache
from smartthings_local.protocol.dtls_session import DtlsCoapSession

from .const import (
    CONF_BYPASS_REMOTE_CONTROL,
    CONF_DEVICE_TYPE,
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_PORT,
    CONF_SERIAL,
    DEVICE_SUPPORT_ISSUE_URL,
    DOMAIN,
    DTLS_LOCAL_PORT_BASE,
    SUMMARY_INTERVAL_S,
)
from .observe import GRACE_PERIOD_S, MODE_OBSERVE, MODE_POLL, ObserveManager
from .registry import CAPABILITIES
from .registry.adapter import flatten
from .registry.batch import parse_device0_batch
from .registry.by_type import resolve as resolve_registry
from .registry.capabilities.common import (
    merge_items_field,
    merge_options_field,
    remote_control_enabled,
    remote_control_required_for_write,
)
from .registry.discovery import BoundEntity
from .registry.identity import (
    DeviceIdentity,
    device_display_name,
    read_identity,
    resolve_model,
    resolve_serial,
)
from .registry.subdevices import (
    Subdevice,
    canonical_view,
    discover_partitioned,
    enumerate_subdevices,
    normalize_seed_batch,
)

_LOGGER = logging.getLogger(__name__)

_SEED_PATH = ["device", "0"]


class _NoOpDescriptor:
    """No-op: StateCache requires an on_observation hook; this integration
    doesn't use per-capability observation hooks."""

    def on_observation(self, state: dict, href: str, rep: dict) -> None:
        return None


_RECOVERY_RETRY_S = 600.0  # re-attempt observe mode this often while polling


def _local_source_port(host: str) -> int:
    """Deterministic UDP source port for this device's DTLS socket.

    Binding the same source port across reconnects lets the appliance evict
    an orphaned session (unclean shutdown, no close_notify) at handshake
    time per RFC 6347 §4.2.8, instead of holding it 5-15 min. See
    DTLS_LOCAL_PORT_BASE. Requires smartthings-local >= 0.1.1.

    Must stay unique per device on this host too: the library's socket is
    unconnected, so two devices sharing a port would mis-demux each other's
    datagrams. Last IPv4 octet as offset for the common case; a stable
    CRC32 fold otherwise.
    """
    try:
        offset = int(ipaddress.IPv4Address(host)) & 0xFF
    except (ipaddress.AddressValueError, ValueError):
        offset = zlib.crc32(host.encode()) & 0xFF
    return DTLS_LOCAL_PORT_BASE + offset


# Debug raw write/read caps (issue #300) -- generous enough for a real
# probing session (the wall-oven reporter's own sequences run well under
# 10 steps) while bounding how long one service call can hold up polling.
_DEBUG_MAX_WRITES = 10
_DEBUG_MAX_SETTLE_S = 30.0
_DEBUG_MAX_VERIFY_AFTER_S = 60.0


def _href_to_path_segs(href: str) -> list[str]:
    """'/mode/vs/0' -> ['mode', 'vs', '0'], the shape `sess.get`/`sess.post`
    take. Shared by every raw debug read/write path."""
    return [s for s in str(href).strip("/").split("/") if s]


def normalize_href(href: str) -> str:
    """A user-typed href in one canonical spelling. Public because
    services.py must normalize before `Subdevice.to_actual`, which rewrites
    only a trailing '0' segment: '/mode/vs/0/' slips through it unchanged
    and would land on the master's resource, not the subdevice's."""
    return "/" + "/".join(_href_to_path_segs(href))


def _coap_code_str(code: int) -> str:
    """Raw CoAP response code -> its 'C.DD' rendering (e.g. 0x44 -> '2.04'),
    the class/detail split RFC 7252 §12.1.2 defines. `raw_code` is kept
    alongside it in every debug response so a caller can format it
    differently."""
    return f"{code >> 5}.{code & 0x1F:02d}"


def _coap_accepted(code: int) -> bool:
    """True for a 2.xx class CoAP response."""
    return (code >> 5) == 2


def _validate_debug_write_item(item: dict) -> tuple[list[str], str, dict, float]:
    """The checks `async_raw_write` has always applied to a single write
    (issue #54), reused per-item by `async_raw_write_sequence` (issue
    #300). Payload is checked before href, matching the original
    single-write order -- not load-bearing for any test, just avoiding a
    silent behavior change in the refactor."""
    payload = item.get("payload")
    if not isinstance(payload, dict) or not payload:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="debug_payload_empty",
        )
    path_segs = _href_to_path_segs(item.get("href", ""))
    if not path_segs:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="resource_href_required",
        )
    settle = item.get("settle") or 0.0
    if not 0 <= settle <= _DEBUG_MAX_SETTLE_S:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="debug_settle_out_of_range",
        )
    return path_segs, "/" + "/".join(path_segs), payload, settle


class LocalThingsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages one Samsung appliance: session, discovery, polling."""

    bound: list[BoundEntity]
    device_info: DeviceInfo
    device_serial: str

    # Class-level so tests can shrink these via patch.object() without
    # touching the production defaults.
    _SUBPOLL_STEP_S: float = SUMMARY_INTERVAL_S / 10  # 3.0 s
    _OBSERVE_GRACE_PERIOD_S: float = GRACE_PERIOD_S
    _RECONNECT_PAUSE_S: float = 5.0

    # A single reconnect is normal appliance behavior (README's "Known
    # device behavior"); only escalate once they pile up in a trailing
    # window (issue #119). Can't be a literal 60s: consecutive attempts are
    # always >= one summary interval + _RECONNECT_PAUSE_S apart, so at most
    # ~2 could ever land in 60s regardless of how unhealthy the connection
    # is. 300s/3 is reachable under normal polling and still a reasonable
    # "actually broken" proxy.
    _RECONNECT_WARN_WINDOW_S: float = 300.0
    _RECONNECT_WARN_THRESHOLD: int = 3

    # A block-level ACK timeout on the summary GET doesn't prove the session
    # is dead (see _poll_once) -- require this many in a row before treating
    # it as one, so one slow transfer doesn't tear down a working OBSERVE
    # subscription.
    _POLL_TIMEOUT_LIMIT: int = 3

    # Named (not inline literals) so the write-settle window in
    # async_send_command can be sized to outlast both round trips a write
    # triggers: the PUT itself, then the confirming summary poll.
    _POST_TIMEOUT_S: float = 8.0
    _POLL_TIMEOUT_S: float = 35.0

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # Per-device logger so every log line (including the base
        # coordinator's and ObserveManager's) identifies which device it's
        # about, instead of a shared module-level logger.
        self._log = logging.getLogger(f"{__name__}.{entry.data[CONF_HOST]}")
        super().__init__(
            hass,
            self._log,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.data[CONF_HOST]}",
            update_interval=timedelta(seconds=SUMMARY_INTERVAL_S),
        )
        self._entry = entry
        self._session: DtlsCoapSession | None = None
        self._identity: DeviceIdentity | None = None
        self._discovered = False
        self.bound = []
        # Sibling indoor subdevices on this connection (issue #177); set
        # once at first discovery, narrowed to the ones with live state (see
        # subdevices.discover_partitioned). Never includes MAIN itself.
        self.subdevices: list[Subdevice] = []
        # Candidates the liveness gate rejected (e.g. an unused SmartThings
        # slot that still answers its seed) -- surfaced in diagnostics.
        self._skipped_subdevices: list = []
        # Rejected candidates' raw reps, kept for diagnostics only (see
        # _live_subdevice_resources) -- never applied to the state cache, or
        # they'd sit frozen at first-discovery value looking live.
        self._skipped_subdevice_resources: dict[str, dict] = {}
        # /multidevice/vs/0's rep if this board answers it -- corroborates
        # the liveness gate without deciding it; kept outside `resources`.
        self._multidevice: dict = {}
        # What each subdevice probe found, keyed by seed href -- lets
        # diagnostics distinguish "checked, nothing there" from "never
        # checked".
        self._subdevice_probes: dict[str, bool] = {}
        # canonical_resources() memo; invalidated in _on_cache_changed so
        # climate.py's frequent per-property reads don't rebuild it from
        # scratch each time.
        self._canonical_cache: dict[tuple[str, str], dict] = {}
        self._cache = StateCache(_NoOpDescriptor())
        self._cache.set_on_change(self._on_cache_changed)
        self._observe = ObserveManager(self._cache, logger=self._log)
        self._push_pending = False
        self._push_pending_lock = threading.Lock()
        # Identity is resolved once by the config flow's probe (issue #236).
        # device_serial mints permanent registry keys, so it must be correct
        # before the first entity registers -- a placeholder corrected once
        # the first poll lands orphans the first device/entity pair instead.
        # The host fallback covers a pre-migration entry and matches what
        # resolve_serial itself returns for a placeholder-serial board
        # (issues #83/#189).
        self.device_serial = entry.data.get(CONF_SERIAL) or entry.data[CONF_HOST]
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, self.device_serial)},
            name=device_display_name(
                entry.data.get(CONF_DEVICE_TYPE), entry.data.get(CONF_MODEL) or ""
            ),
            manufacturer=entry.data.get(CONF_MANUFACTURER) or "Samsung",
            model=entry.data.get(CONF_MODEL) or None,
        )
        self._session_lock = asyncio.Lock()
        self._subpoll_task: asyncio.Task | None = None
        self._hot_hrefs: list[str] = []
        self._warm_hrefs: list[str] = []
        self.device_type_name: str | None = None
        self.one_ui_version: str = ""
        self._consecutive_poll_timeouts = 0
        self._unbound_hrefs: list[str] = []
        self._reconnect_times: list[float] = []
        # See _maybe_retry_observe_mode: last_mode_change_ts alone doesn't
        # move on a failed attempt, so this tracks attempts too.
        self._last_observe_attempt_ts = 0.0
        # Set by both reconnect paths (poll and command) that hand back a
        # session with zero OBSERVE registrations while mode was still
        # observe; consumed once to trigger an immediate resubscribe
        # instead of waiting out _RECOVERY_RETRY_S.
        self._resubscribe_due = False

    # ------------------------------------------------------------------
    # Session management (all blocking — must run in executor)
    # ------------------------------------------------------------------

    @property
    def last_resources(self) -> dict:
        return self._cache.snapshot()

    def resource(self, href: str) -> dict:
        """A single href's rep. Cheaper than `last_resources.get(href)`,
        which copies every tracked href to build the snapshot dict."""
        return self._cache.get(href) or {}

    def canonical_resources(self, subdevice: Subdevice) -> dict[str, dict]:
        """`subdevice`'s view of the live snapshot, rewritten to canonical
        hrefs (issue #177, see subdevices.canonical_view). Any platform
        property that scans the whole resources dict (exists_fn,
        is_legacy_board, ...) must use this instead of `last_resources`, or a
        sibling subdevice's own `/mode/vs/1` could leak into MAIN's canonical
        `/mode/vs/0` view. Memoized per cache generation -- see
        _canonical_cache.
        """
        view_key = (subdevice.kind, subdevice.key)
        cached = self._canonical_cache.get(view_key)
        if cached is not None:
            return cached
        view = canonical_view(subdevice, self._cache.snapshot(), self.subdevices)
        self._canonical_cache[view_key] = view
        return view

    def device_info_for(self, subdevice: Subdevice) -> DeviceInfo:
        """DeviceInfo for one logical subdevice on this connection (issue
        #177): the master's own device_info for MAIN, or a linked child
        device otherwise.

        Identifiers derive from the master's serial plus this subdevice's
        stable key, never the subdevice's own reported serial -- deterministic
        across reconnects regardless of whether its identity resource
        answered yet. `serial_number` is set from it when present anyway,
        but is informational only, not an identifier.
        """
        if subdevice.kind == "main":
            return self.device_info
        info = self.canonical_resources(subdevice).get("/information/vs/0", {})
        model_num = info.get("x.com.samsung.da.modelNum", "")
        model = model_num.split("|", 1)[0] if model_num else ""
        serial = info.get("x.com.samsung.da.serialNum") or None
        base_name = self.device_info.get("name") or "Samsung Appliance"
        if model:
            label = model.replace("_", " ").title()
        else:
            # No identity resource yet (or ever) for this subdevice -- fall
            # back to a generic label. 'Subdevice <n>' only applies to an
            # indexed subdevice; UUID-prefixed ones are never more than one
            # per connection today.
            label = (
                f"Subdevice {subdevice.key}"
                if subdevice.kind == "indexed"
                else "Secondary Subdevice"
            )
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.device_serial}_{subdevice.key}")},
            via_device=(DOMAIN, self.device_serial),
            name=f"{base_name} {label}",
            manufacturer=self.device_info.get("manufacturer") or "Samsung",
            model=model or None,
            serial_number=serial,
        )

    @property
    def observe_mode(self) -> str:
        return self._observe.mode

    def _connect_session(self) -> None:
        host = self._entry.data[CONF_HOST]
        port = self._entry.data[CONF_PORT]
        cert_pem = self._entry.data[CONF_LEAF_CERT_PEM]
        key_pem = self._entry.data[CONF_LEAF_KEY_PEM]

        sess = DtlsCoapSession(
            host,
            port,
            cert_pem=cert_pem,
            key_pem=key_pem,
            on_notification=self._observe.on_notification,
            local_port=_local_source_port(host),
        )
        sess.connect()
        sess.start_reader()
        self._session = sess
        self._log.debug("DTLS connected to %s:%d", host, port)
        try:
            self._identity = read_identity(sess, None)
        except Exception as e:
            self._log.debug("read_identity failed: %s", e)
            self._identity = None

    def _close_session(self) -> None:
        sess = self._session
        self._session = None
        if sess is not None:
            with contextlib.suppress(Exception):
                sess.close()

    async def async_close(self) -> None:
        if self._subpoll_task is not None:
            self._subpoll_task.cancel()
            self._subpoll_task = None
        self._observe.close()
        await self.hass.async_add_executor_job(self._close_session)

    def _on_cache_changed(self, changed: bool, source: str) -> None:
        """StateCache.set_on_change callback. Runs on whatever thread
        applied the update (DTLS reader thread for observe notifications,
        an executor thread for poll/sweep) — never the event loop, so the
        HA push must be scheduled thread-safely. A sweep/poll cycle can
        call apply_rep for dozens of hrefs in a tight loop; coalesce those
        into a single push instead of one hass.add_job per href."""
        if not changed:
            return
        self._canonical_cache.clear()
        with self._push_pending_lock:
            if self._push_pending:
                return
            self._push_pending = True
        self.hass.add_job(self._push_cache_snapshot)

    @callback
    def _push_cache_snapshot(self) -> None:
        with self._push_pending_lock:
            self._push_pending = False
        if self.bound:
            self.async_set_updated_data(flatten(self.bound, self._cache.snapshot()))

    def _poll_once(self) -> dict[str, dict]:
        """GET /device/0, return parsed resources. Blocking.

        A `TimeoutError` here means one block's ACK didn't arrive in time --
        not that the session is dead (earlier blocks succeeded). Left open;
        `_async_update_data` decides whether repeated timeouts warrant a
        reconnect. Any other exception is unambiguous -- close immediately.
        """
        if self._session is None:
            self._connect_session()
        sess = self._session
        assert sess is not None
        try:
            # 35s gives a slow blockwise transfer room to finish instead of
            # raising TimeoutError every cycle on an otherwise-fine device.
            code, payload = sess.get(_SEED_PATH, timeout=self._POLL_TIMEOUT_S)
        except TimeoutError:
            raise
        except Exception as e:
            self._close_session()
            raise RuntimeError(f"poll GET failed: {e}") from e
        if code != 0x45 or not payload:
            self._close_session()
            raise RuntimeError(f"poll: unexpected code {code:#04x}")
        try:
            body = cbor2.loads(payload)
        except Exception as e:
            raise RuntimeError(f"poll cbor decode: {e}") from e
        result = parse_device0_batch(body) if isinstance(body, list) else {}
        # Refresh every enumerated sibling's seed on this same poll (issue
        # #177) so its state doesn't freeze at enumeration time.
        for subdevice in self.subdevices:
            result.update(self._poll_subdevice_seed(subdevice))
        return result

    def _poll_subdevice_seed(self, subdevice: Subdevice) -> dict[str, dict]:
        """GET one subdevice's seed Collection, normalized to real hrefs. A
        sibling failing to answer is a debug log, never a failed poll -- the
        issue #177 reporter's /device/2 (an unused SmartThings slot) may not
        always respond, and the master must not go unavailable for that.
        Blocking -- called from _poll_once, already in executor."""
        sess = self._session
        if sess is None:
            return {}
        if subdevice.flat_hrefs:
            return self._poll_subdevice_flat_hrefs(subdevice, sess)
        try:
            code, payload = sess.get(list(subdevice.seed_path), timeout=10.0)
            if code == 0x45 and payload:
                body = cbor2.loads(payload)
                if isinstance(body, list):
                    return normalize_seed_batch(subdevice, parse_device0_batch(body))
        except Exception as e:
            self._log.debug("subdevice %s seed poll failed: %s", subdevice.key, e)
        return {}

    def _poll_subdevice_flat_hrefs(self, subdevice: Subdevice, sess) -> dict[str, dict]:
        """Re-poll a flat-mode subdevice's hrefs individually (issue #205) --
        it has no Collection endpoint to batch-refresh through (see
        enumerate_subdevices' fallback), so each confirmed href gets its own
        GET under the subdevice's prefix. A failing href just drops out of
        the result, same posture as the Collection path above.

        Takes `sess` from the caller rather than re-reading self._session --
        async_close() can null it without holding _session_lock. Skips hrefs
        already covered by the hot/warm sub-poll tiers, which
        _run_subpolls refreshes every 3s/6s, strictly more current than
        this once-per-summary-poll pass could offer."""
        skip = set(self._hot_hrefs) | set(self._warm_hrefs)
        result: dict[str, dict] = {}
        first = True
        for href in subdevice.flat_hrefs:
            actual = subdevice.to_actual(href)
            if actual in skip:
                continue
            try:
                if not first:
                    sess.pace()
                first = False
                path = actual.strip("/").split("/")
                code, payload = sess.get(path, timeout=10.0)
                if code == 0x45 and payload:
                    rep = cbor2.loads(payload)
                    if isinstance(rep, dict):
                        result[actual] = rep
            except Exception as e:
                self._log.debug(
                    "subdevice %s flat href %s poll failed: %s",
                    subdevice.key,
                    href,
                    e,
                )
        return result

    def _poll_hrefs_blocking(self, hrefs: list[str]) -> dict[str, dict]:
        """GET individual hrefs sequentially. Does not reconnect on failure. Blocking."""
        if self._session is None:
            return {}
        results = {}
        first = True
        for href in hrefs:
            if not first:
                self._session.pace()
            first = False
            try:
                path = href.strip("/").split("/")
                code, payload = self._session.get(path, timeout=10.0)
                if code == 0x45 and payload:
                    rep = cbor2.loads(payload)
                    if isinstance(rep, dict):
                        self._observe.apply(href, rep, source="poll")
                        results[href] = rep
            except Exception as e:
                self._log.debug("sub-poll %s: %s", href, e)
        return results

    # ------------------------------------------------------------------
    # Sub-poll loop (runs between summary polls)
    # ------------------------------------------------------------------

    async def _run_subpolls(self, force: bool = False) -> None:
        """Poll hot/warm hrefs in the gaps between summary polls. No-op in
        observe-primary mode (those hrefs are already covered by push)
        unless `force` is set -- set when this cycle's sweep found the
        cache disagreeing with a still-live observe session (see
        log_sweep_discrepancies): a bounded fallback for a channel gone
        silent without a reconnect."""
        if self._observe.mode == MODE_OBSERVE and not force:
            return
        hot = self._hot_hrefs
        warm = self._warm_hrefs
        if not hot and not warm:
            return
        step = self._SUBPOLL_STEP_S
        for i in range(1, 10):  # slots 1..9  (T+3 s … T+27 s)
            await asyncio.sleep(step)
            hrefs = list(hot) + (list(warm) if i % 2 == 0 else [])
            async with self._session_lock:
                try:
                    await self.hass.async_add_executor_job(self._poll_hrefs_blocking, hrefs)
                except Exception as e:
                    self._log.debug("sub-poll batch failed: %s", e)

    # ------------------------------------------------------------------
    # Discovery (runs once on first successful poll)
    # ------------------------------------------------------------------

    def _enumerate_subdevices_blocking(self, resources: dict[str, dict]) -> dict[str, dict]:
        """One-time (first discovery only) probe for sibling indoor
        subdevices on this connection (issue #177) -- see
        registry.subdevices.enumerate_subdevices for the two detection
        patterns. Runs in executor, under the session lock.

        Sets self.subdevices to every candidate found and returns
        `resources` merged with each candidate's seed, so this cycle's
        _run_discovery sees every candidate without a second round trip.
        _run_discovery is what narrows this down to the ones actually live
        (see discover_partitioned) -- this method can't tell an unused
        SmartThings slot from a real sibling, only that something answered.
        """
        if self._session is None:
            self._connect_session()
        sess = self._session
        if sess is None:
            return resources
        oic_res = self._identity.raw.get("/oic/res", []) if self._identity else []
        probes: dict[str, bool] = {}
        subdevices, extra = enumerate_subdevices(
            sess,
            resources,
            oic_res,
            probe_log=lambda href, found: probes.__setitem__(href, found),
        )
        self.subdevices = subdevices
        self._subdevice_probes = probes
        # /multidevice/vs/0 is corroborating metadata, not appliance state,
        # and is probed on every device -- it must not join `resources`, or
        # it would bind to nothing on families that don't ignore the href
        # (raising a spurious coverage-gap repair) and freeze in the cache
        # since it's never polled again (see _live_subdevice_resources).
        # Kept aside for diagnostics and the numofsubdevice cross-check in
        # _run_discovery instead.
        self._multidevice = extra.pop("/multidevice/vs/0", {})
        return {**resources, **extra}

    def _live_subdevice_resources(self, resources: dict[str, dict]) -> dict[str, dict]:
        """`resources` minus every href belonging to a rejected subdevice
        candidate (issue #177). Called once, between _run_discovery and the
        first cache apply, so a rejected slot's reps are seen by the gate
        and then dropped rather than frozen into the cache forever. Kept in
        _skipped_subdevice_resources for diagnostics.
        """
        if not self._skipped_subdevices:
            return resources
        kept: dict[str, dict] = {}
        skipped: dict[str, dict] = {}
        for href, rep in resources.items():
            bucket = (
                skipped
                if any(skip.subdevice.owns(href) for skip in self._skipped_subdevices)
                else kept
            )
            bucket[href] = rep
        self._skipped_subdevice_resources = skipped
        return kept

    def _persist_identity(
        self,
        serial: str,
        model: str,
        manufacturer: str,
        device_type_name: str | None,
    ) -> None:
        """Write this device's resolved identity back onto the config entry.

        A no-op for an entry the current config flow already fully stored.
        Matters for an entry migrated from before identity was stored: the
        first poll is where model/type become known, and persisting them
        means the next restart names the device fully instead of renaming it
        again once a poll lands.

        Runs on the event loop, which async_update_entry requires.
        """
        identity = {
            CONF_SERIAL: serial,
            CONF_MODEL: model,
            CONF_MANUFACTURER: manufacturer,
            CONF_DEVICE_TYPE: device_type_name,
        }
        if all(self._entry.data.get(k) == v for k, v in identity.items()):
            return
        self.hass.config_entries.async_update_entry(
            self._entry, data={**self._entry.data, **identity}
        )

    def _run_discovery(self, resources: dict[str, dict]) -> None:
        # Diagnostics only -- names the firmware generation (e.g. '7.0 Air
        # conditioner' is Tizen Lite); doesn't route, since every device
        # that reports it is already typed by modelNum.
        self.one_ui_version = (
            resources.get("/otninformation/vs/0", {})
            .get("swVersionInfo", {})
            .get("oneUiVersion", "")
        )
        info = resources.get("/information/vs/0", {})
        unbound: list[str] = []
        hot, warm = set(), set()

        def _tier_log(href: str, tier: str) -> None:
            if tier == "hot":
                hot.add(href)
            elif tier == "warm":
                warm.add(href)

        model_num = info.get("x.com.samsung.da.modelNum", "")
        description = info.get("x.com.samsung.da.description", "")

        # Partitioned discovery (issue #177): the main pass binds every href
        # owned by no subdevice; one further pass per candidate subdevice
        # binds its own canonical view (see subdevices.discover_partitioned),
        # gated on whether it actually produced live primary state (an
        # unused SmartThings slot answers its seed but never does). A device
        # with no candidates behaves exactly like the old single discover()
        # call.
        bound, device_type_name, materialized, skipped = discover_partitioned(
            resources,
            self.subdevices,
            resolve_registry,
            CAPABILITIES,
            log=unbound.append,
            tier_log=_tier_log,
            oic_device_types=self._identity.device_types if self._identity else (),
        )
        self.subdevices = materialized
        self._skipped_subdevices = skipped
        for skip in skipped:
            self._log.info(
                "subdevice %s (%s) answered its seed but produced no live "
                "primary state; not materialized (hrefs=%s)",
                skip.subdevice.key,
                skip.subdevice.kind,
                list(skip.hrefs),
            )
        # Corroborating signal, not a gate: log, don't raise, on a
        # disagreement -- only one known board family exposes
        # numofsubdevice at all, so a mismatch is a triage signal, not proof
        # either side is wrong.
        numofsubdevice = self._multidevice.get("x.com.samsung.da.numofsubdevice")
        if numofsubdevice is not None:
            try:
                reported = int(numofsubdevice)
            except (TypeError, ValueError):
                reported = None
            subdevice_count = len(materialized) + 1  # +1 for the master itself
            if reported is not None and reported != subdevice_count:
                self._log.debug(
                    "/multidevice/vs/0 reports numofsubdevice=%r but %d "
                    "subdevice(s) materialized (including the master)",
                    numofsubdevice,
                    subdevice_count,
                )
        if device_type_name is not None:
            self._log.debug("device type: %s (modelNum=%r)", device_type_name, model_num)
        else:
            # modelNum alone doesn't identify every type, and device_types
            # is often empty even on hardware we don't map yet -- log all
            # three so a user can paste this into an issue.
            self._log.warning(
                "unknown device type modelNum=%r description=%r device_types=%r; using common caps",
                model_num,
                description,
                self._identity.device_types if self._identity else (),
            )
        self.device_type_name = device_type_name
        self.bound = bound
        self._unbound_hrefs = unbound

        # The entry's stored identity wins; this poll's answer is only
        # adopted when nothing is stored (a legacy migration couldn't
        # recover it), then written back. Re-keying an entry with existing
        # registry entries orphans them (issue #236).
        polled_serial = resolve_serial(
            info.get("x.com.samsung.da.serialNum"), self._entry.data[CONF_HOST]
        )
        serial = self._entry.data.get(CONF_SERIAL) or polled_serial
        if serial != polled_serial:
            # Same IP, different appliance (or firmware that changed what it
            # reports) -- keep the registered identity; re-adding is the
            # user's call.
            self._log.warning(
                "device at %s reports serial %r but this entry is registered "
                "as %r; keeping the registered identity",
                self._entry.data[CONF_HOST],
                polled_serial,
                serial,
            )
        self.device_serial = serial

        ident = self._identity
        model = resolve_model(model_num, ident)
        name = device_display_name(device_type_name, model)
        mfr = (ident.manufacturer if ident else "") or "Samsung"

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=name,
            manufacturer=mfr,
            model=model,
        )
        self._persist_identity(serial, model, mfr, device_type_name)
        self._update_coverage_gap_issue(device_type_name is None, unbound, name)

        self._hot_hrefs = sorted(hot)
        self._warm_hrefs = sorted(warm)

        self._discovered = True
        self._log.info(
            "discovered %d entities (serial=%s) hot=%s warm=%s subdevices=%s",
            len(bound),
            serial,
            self._hot_hrefs,
            self._warm_hrefs,
            [su.key for su in self.subdevices],
        )

    def _update_coverage_gap_issue(
        self,
        unknown_type: bool,
        unbound_hrefs: list[str],
        device_name: str,
    ) -> None:
        """Raise or clear a Repairs issue when capability coverage is
        incomplete -- unrecognized device type or unbound resources.
        Diagnostics (diagnostics.py) is what a user downloads to help; this
        just tells them there's something to send.
        """
        issue_id = f"device_gap_{self._entry.entry_id}"
        if unknown_type or unbound_hrefs:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="device_gap",
                translation_placeholders={"device_name": device_name},
                learn_more_url=DEVICE_SUPPORT_ISSUE_URL,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def _attempt_observe_mode(self) -> None:
        """Called once, right after first discovery, after a reconnect
        downgrades from observe, and periodically while polling. Two
        phases: subscribing holds `_session_lock` (each send is fire-and-
        forget, not a network round trip); the grace wait that follows
        does not, so a concurrent command write isn't blocked for the
        whole ~15s wait (issue #294) -- only the brief subscribe burst.

        The wait can outlast a reconnect elsewhere (the poll path's own
        recovery, or a command retry), which would otherwise let a stale
        success commit observe mode against a session that's already been
        replaced -- claiming "Push" with nothing left to notice it's dead.
        `self._session is sess` re-checked under the lock right before
        committing closes that: `sess` keeps the old object alive, so
        identity can't be recycled onto a new one.
        """
        hrefs = self._hot_hrefs + self._warm_hrefs
        if not hrefs:
            return
        self._last_observe_attempt_ts = time.monotonic()
        async with self._session_lock:
            if self._session is None:
                # _poll_once already connects on a real poll; only fires if
                # the session was closed out from under us concurrently.
                await self.hass.async_add_executor_job(self._connect_session)
            sess = self._session
            if sess is None:
                return
            subscribed = await self.hass.async_add_executor_job(
                self._observe.subscribe_hrefs, sess, hrefs
            )
        if not subscribed:
            self._observe.abandon_observe_attempt()
            return
        reached = await self.hass.async_add_executor_job(
            self._observe.await_observe_notifies, subscribed, self._OBSERVE_GRACE_PERIOD_S
        )
        async with self._session_lock:
            stale_session = self._session is not sess
            if not reached or stale_session:
                self._observe.abandon_observe_attempt()
                if stale_session:
                    # A reconnect elsewhere replaced the session while this
                    # attempt waited -- that session has never been tried,
                    # so retry it next cycle instead of leaving it
                    # unsubscribed for up to _RECOVERY_RETRY_S, which
                    # _last_observe_attempt_ts (already stamped above, for
                    # the now-abandoned session) would otherwise throttle
                    # for (issue #294).
                    self._resubscribe_due = True
                return
            self._observe.enter_observe_mode(sess, subscribed)

    async def _maybe_retry_observe_mode(self) -> None:
        """While in poll-only mode, periodically re-attempt observe mode
        so a device that gains internet access recovers push automatically.

        Gated on the more recent of the two timestamps, not just
        `last_mode_change_ts`: `_set_mode` only stamps that on an actual
        transition, so a device that never successfully enters observe
        mode would otherwise leave this throttle open forever after the
        first `_RECOVERY_RETRY_S` window -- re-attempting (and paying the
        subscribe-burst lock) on every single poll cycle instead of every
        `_RECOVERY_RETRY_S`.
        """
        last_attempt = max(self._observe.last_mode_change_ts, self._last_observe_attempt_ts)
        if time.monotonic() - last_attempt < _RECOVERY_RETRY_S:
            return
        await self._attempt_observe_mode()

    def _defer_reconnect_for(self, e: Exception) -> bool:
        """True if this poll failure should NOT trigger a reconnect this
        cycle.

        A `TimeoutError` means one block's ACK was late, not that the
        session is dead (see `_poll_once`). A recent OBSERVE notify is proof
        the channel is live, so always defer then. Otherwise defer until
        `_POLL_TIMEOUT_LIMIT` consecutive timeouts pile up. Any other
        exception reconnects immediately.

        Never defers before first discovery (issue #254): deferring returns
        an empty dict, which the base coordinator treats as a successful
        first refresh -- and since platforms enumerate `bound` once, the
        entry would load with zero entities and stay that way.
        """
        if not self._discovered:
            return False
        if not isinstance(e, TimeoutError):
            return False
        if self._observe.mode == MODE_OBSERVE and self._observe.recently_notified():
            # Recent push is proof of life -- reset the counter too, so
            # timeouts from an earlier quiet stretch don't carry over and
            # trigger a false reconnect once the device goes quiet again.
            self._consecutive_poll_timeouts = 0
            return True
        self._consecutive_poll_timeouts += 1
        return self._consecutive_poll_timeouts < self._POLL_TIMEOUT_LIMIT

    def _reconnect_is_frequent(self) -> bool:
        """True once this cycle's reconnect is the Nth within the trailing
        warn window -- see _RECONNECT_WARN_WINDOW_S/_RECONNECT_WARN_THRESHOLD."""
        now = time.monotonic()
        self._reconnect_times = [
            t for t in self._reconnect_times if now - t < self._RECONNECT_WARN_WINDOW_S
        ]
        self._reconnect_times.append(now)
        return len(self._reconnect_times) >= self._RECONNECT_WARN_THRESHOLD

    # ------------------------------------------------------------------
    # DataUpdateCoordinator hook
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        # Stop any in-flight sub-poll before taking the session lock.
        if self._subpoll_task is not None:
            self._subpoll_task.cancel()
            self._subpoll_task = None

        async with self._session_lock:
            try:
                resources = await self.hass.async_add_executor_job(self._poll_once)
                self._consecutive_poll_timeouts = 0
            except Exception as e:
                if self._defer_reconnect_for(e):
                    self._log.debug(
                        "poll failed (%s), not yet treated as session "
                        "death; skipping this cycle: %s",
                        type(e).__name__,
                        e,
                    )
                    return flatten(self.bound, self._cache.snapshot())
                self._consecutive_poll_timeouts = 0
                # A lone reconnect is routine (README's "Known device
                # behavior"); only warn once they pile up. Pause first so
                # the device can clean up its DTLS state before we knock
                # again.
                if self._reconnect_is_frequent():
                    self._log.warning("poll failed, reconnecting: %s", e)
                else:
                    self._log.info("poll failed, reconnecting: %s", e)
                await self.hass.async_add_executor_job(self._close_session)
                await asyncio.sleep(self._RECONNECT_PAUSE_S)
                try:
                    resources = await self.hass.async_add_executor_job(self._poll_once)
                except Exception as e2:
                    self._log.error("poll failed after reconnect: %s", e2)
                    # Without this, a fully unreachable device left the
                    # connection-mode sensor stuck on "Push" forever -- only
                    # the success branch below ever downgraded it (issue
                    # #287). No just_downgraded_from_observe here: there's no
                    # live session this cycle to resubscribe on.
                    if self._observe.mode == MODE_OBSERVE:
                        self._observe.downgrade_to_poll()
                    snapshot = self._cache.snapshot()
                    # Same precondition as _defer_reconnect_for (issue #254):
                    # degraded-but-successful data only makes sense once
                    # there are bound entities to carry it.
                    if self._discovered and snapshot:
                        self._log.debug("Full error:", exc_info=e2)
                        return flatten(self.bound, snapshot)
                    raise UpdateFailed(f"poll failed after reconnect: {e2}") from e2
                else:
                    # A fresh session has zero OBSERVE registrations; if we
                    # were in observe mode that state is now stale. Tear it
                    # down and resubscribe immediately below instead of
                    # waiting for the poll-mode retry timer, which exists to
                    # throttle devices that never had observe working at all
                    # -- a reconnect just proved this session is healthy.
                    if self._observe.mode == MODE_OBSERVE:
                        self._log.debug(
                            "reconnect while in observe mode; downgrading to "
                            "poll and resubscribing on the new session"
                        )
                        self._observe.downgrade_to_poll()
                        self._resubscribe_due = True

        if not self._discovered:
            # One-time (issue #177): find sibling subdevices before the
            # first discovery pass, folding their seed resources into this
            # cycle's snapshot so discovery sees every subdevice on the
            # first poll rather than waiting a cycle.
            async with self._session_lock:
                resources = await self.hass.async_add_executor_job(
                    self._enumerate_subdevices_blocking, resources
                )

        source = "sweep" if self._discovered else "poll"
        first_cycle = not self._discovered
        if first_cycle:
            # Discovery runs before the apply loop so a rejected candidate's
            # resources never reach the state cache (issue #177) --
            # StateCache has no eviction, so the only way to keep them out
            # is to not put them in. Safe to reorder: _run_discovery reads
            # the passed dict, never the cache.
            self._run_discovery(resources)
            resources = self._live_subdevice_resources(resources)
        sweep_mismatch = False
        if self._observe.mode == MODE_OBSERVE:
            # A mismatch never tears down a still-live OBSERVE session (see
            # log_sweep_discrepancies) -- the sweep below re-applies
            # authoritative state regardless. It only triggers extra
            # hot/warm subpolls this cycle so a channel gone silent without
            # a reconnect still gets fresher-than-30s data.
            sweep_mismatch = self._observe.log_sweep_discrepancies(resources)
        for href, rep in resources.items():
            self._observe.apply(href, rep, source=source)

        if first_cycle or self._resubscribe_due:
            self._resubscribe_due = False
            await self._attempt_observe_mode()
        elif self._observe.mode == MODE_POLL:
            await self._maybe_retry_observe_mode()

        # Background task, not async_create_task: self-limiting (cancelled
        # and recreated every cycle, see above) and owned entirely by the
        # coordinator, so it shouldn't be tied into HA's startup/shutdown
        # sequencing -- a subpoll in flight (up to ~27s) would delay both
        # (issue #207).
        if self._hot_hrefs or self._warm_hrefs:
            self._subpoll_task = self.hass.async_create_background_task(
                self._run_subpolls(force=sweep_mismatch), name="localthings_subpoll"
            )

        return flatten(self.bound, self._cache.snapshot())

    # ------------------------------------------------------------------
    # Command dispatch (called by entity platforms in Task 5)
    # ------------------------------------------------------------------

    async def async_send_command(self, bound_entity: BoundEntity, payload: Any) -> None:
        """Write a value to the device. Retries once on a dead session
        (issue #294); raises HomeAssistantError if that retry fails too.

        A description-level validate_fn (SwitchDesc only, currently) rejects
        a write with a user-facing message ahead of write_fn's silent
        no-op. The remote-control check runs first, unconditionally, unless
        the user opted out via CONF_BYPASS_REMOTE_CONTROL (issue #54: some
        devices accept some writes even while reporting remote control off)
        or the laundry firmware declares itself writable without Smart
        Control."""
        desc = bound_entity.desc
        write_fn = getattr(desc, "write_fn", None)
        if write_fn is None:
            return
        href = bound_entity.href
        rep = self._cache.get(href or "") or {}
        resources = self._cache.snapshot()
        bypass_remote_control = self._entry.options.get(CONF_BYPASS_REMOTE_CONTROL, False)
        if (
            not bypass_remote_control
            and remote_control_required_for_write(resources, href or "")
            and not remote_control_enabled(resources)
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="remote_control_disabled",
            )
        validate_fn = getattr(desc, "validate_fn", None)
        if validate_fn is not None:
            error = validate_fn(payload, rep, resources)
            if error:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key=error,
                )
        try:
            result = write_fn(payload, rep, href, resources)
        except TypeError:
            result = write_fn(payload, rep, href)
        if result is None:
            self._log.warning("write_fn rejected payload %r for %s", payload, href)
            return
        path_segs, body = result

        # The write's actual target, not necessarily bound_entity.href -- a
        # composite entity (the AC's ClimateDesc) drives writes to sibling
        # resources via path_segs (see airconditioner._climate_write).
        # Applying the optimistic value to bound_entity.href instead caused
        # the 20-60s lag in issues #17/#53: the wrong resource got the
        # optimistic merge while the one HA actually displays from never
        # did.
        #
        # path_segs are canonical (issue #177); translate through this
        # entity's own subdevice so a subdevice's actual href (e.g.
        # /mode/vs/1) is targeted instead -- identity transform for MAIN.
        write_href = bound_entity.subdevice.to_actual("/" + "/".join(path_segs))
        path_segs = [s for s in write_href.strip("/").split("/") if s]

        # Apply optimistically before starting the settle guard -- guard and
        # apply share the same gate (mark_write_pending), so reversing the
        # order would drop the very update it exists to protect (issue #27).
        #
        # settle_s must outlast the PUT plus the confirming refresh, not
        # DEFAULT_SETTLE_S's fixed few seconds: the refresh is a full
        # summary poll that can legitimately take tens of seconds (see
        # _poll_once), and some writes (issue #9's washer course/detergent/
        # softener selection) settle on-device well after that. A short
        # fixed window let a stale confirm poll land unprotected and revert
        # the optimistic value, read by users as the write "reverting, then
        # re-applying" itself a few seconds later. Releasing the guard early
        # (right after the first confirming refresh) was tried and reverted
        # for the same reason, plus races on overlapping writes to the same
        # href.
        #
        # write_fn bodies touching options/items now carry only the changed
        # token(s) (issue #54), not the whole array -- but apply()'s
        # field-level merge doesn't know that and would wipe every sibling
        # option/item for the settle window. Pre-merge here the way the
        # device does, so the optimistic cache entry stays complete; the
        # wire `body` stays minimal.
        optimistic_body = body
        new_options = body.get("x.com.samsung.da.options")
        if isinstance(new_options, list):
            cached_options = (self._cache.get(write_href) or {}).get("x.com.samsung.da.options")
            optimistic_body = {
                **optimistic_body,
                "x.com.samsung.da.options": merge_options_field(cached_options, new_options),
            }
        # Same fact, items[] shape (see airconditioner._climate_write's
        # vendor temperature write).
        new_items = body.get("x.com.samsung.da.items")
        if isinstance(new_items, list):
            cached_items = (self._cache.get(write_href) or {}).get("x.com.samsung.da.items")
            optimistic_body = {
                **optimistic_body,
                "x.com.samsung.da.items": merge_items_field(cached_items, new_items),
            }
        self._observe.apply(write_href, optimistic_body, source="optimistic")
        self._observe.mark_write_pending(
            write_href, settle_s=self._POST_TIMEOUT_S + self._POLL_TIMEOUT_S
        )

        def _do_put():
            if self._session is None:
                self._connect_session()
            sess = self._session
            if sess is None:
                raise RuntimeError("no session")
            code, _ = sess.post(path_segs, cbor2.dumps(body), timeout=self._POST_TIMEOUT_S)
            self._log.info("PUT %s → code %#04x", write_href, code)

        # Mirrors the poll path's reconnect-and-retry (issue #294): a PUT
        # landing on a session Samsung's firmware closed between polls used
        # to be silently lost -- no retry, no user-facing error.
        async with self._session_lock:
            try:
                await self.hass.async_add_executor_job(_do_put)
            except Exception as e:
                self._log.warning("command failed for %s, reconnecting: %s", write_href, e)
                await self.hass.async_add_executor_job(self._close_session)
                # The session is dead the moment it's closed, so any OBSERVE
                # subscriptions on it are too -- downgrade here, before the
                # retry, so a retry that also fails doesn't leave mode
                # claiming "Push" on a session that no longer exists
                # (issue #294; the poll path handles the same fact for its
                # own reconnect the same way, unconditionally on close).
                if self._observe.mode == MODE_OBSERVE:
                    self._observe.downgrade_to_poll()
                    self._resubscribe_due = True
                await asyncio.sleep(self._RECONNECT_PAUSE_S)
                try:
                    await self.hass.async_add_executor_job(_do_put)
                except Exception as e2:
                    self._log.error("command failed for %s after reconnect: %s", write_href, e2)
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="command_failed",
                        translation_placeholders={"href": write_href, "error": str(e2)},
                    ) from e2
                # The retry's own reconnect pause + second PUT can eat well
                # into the settle window armed above, leaving too little of
                # it for the confirming poll below and reviving the
                # revert-then-reapply symptom settle_s exists to prevent
                # (issue #9). Re-arm it fresh now that the write actually
                # landed.
                self._observe.mark_write_pending(
                    write_href, settle_s=self._POST_TIMEOUT_S + self._POLL_TIMEOUT_S
                )
        await self.async_request_refresh()

    # ------------------------------------------------------------------
    # Debug raw write/read (issue #54, extended for issue #300): a
    # power-user escape hatch shared by the options-flow debug panel and
    # the write_resource/read_resource services (services.py) for probing
    # a device's write contract directly. Deliberately bypasses the
    # remote-control block and all write_fn/validate_fn above -- use with
    # care.
    # ------------------------------------------------------------------

    def _raw_write_blocking(self, path_segs: list[str], body: dict, href: str) -> tuple[int, dict]:
        """Debug primitive: POST an arbitrary patch, then read the href
        back for ground truth. Blocking -- runs in executor."""
        if self._session is None:
            self._connect_session()
        sess = self._session
        if sess is None:
            raise RuntimeError("no session")
        code, _ = sess.post(path_segs, cbor2.dumps(body), timeout=self._POST_TIMEOUT_S)
        self._log.warning("DEBUG raw write POST %s %r → code %#04x", href, body, code)
        new_rep: dict = {}
        try:
            sess.pace()
            rcode, payload = sess.get(path_segs, timeout=10.0)
            if rcode == 0x45 and payload:
                rep = cbor2.loads(payload)
                if isinstance(rep, dict):
                    self._observe.apply(href, rep, source="poll")
                    new_rep = rep
        except Exception as e:
            self._log.debug("raw write follow-up read failed: %s", e)
        return code, new_rep

    def _raw_read_blocking(self, path_segs: list[str], href: str) -> tuple[int, dict]:
        """Debug primitive: a live GET, deliberately bypassing the cache
        (issue #300) -- the cache can be up to a poll interval stale,
        exactly the staleness that makes testing whether a write held or
        got silently reverted by the board unreliable. Blocking -- runs in
        executor."""
        if self._session is None:
            self._connect_session()
        sess = self._session
        if sess is None:
            raise RuntimeError("no session")
        code, payload = sess.get(path_segs, timeout=10.0)
        rep: dict = {}
        if code == 0x45 and payload:
            try:
                body = cbor2.loads(payload)
            except Exception as e:
                self._log.debug("raw read decode failed for %s: %s", href, e)
                body = None
            if isinstance(body, dict):
                self._observe.apply(href, body, source="poll")
                rep = body
        return code, rep

    async def async_raw_read(self, href: str) -> tuple[int, dict]:
        """Debug-only live GET (issue #300, backs the read_resource
        service). Same href validation as async_raw_write."""
        path_segs = _href_to_path_segs(href)
        if not path_segs:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="resource_href_required",
            )
        norm_href = "/" + "/".join(path_segs)
        async with self._session_lock:
            return await self.hass.async_add_executor_job(
                self._raw_read_blocking, path_segs, norm_href
            )

    async def async_raw_write_sequence(
        self,
        writes: list[dict],
        *,
        verify_after: float = 0.0,
        hold_session_lock: bool = True,
    ) -> dict[str, Any]:
        """Debug-only ordered multi-write (issue #300): a Samsung wall oven
        board discards settings writes while idle and only keeps them once
        a cycle is already running, which no single-write debug pass can
        probe for.

        `hold_session_lock` (default) keeps `_session_lock` for the whole
        sequence, settle waits included, so nothing interleaves between
        steps and blurs which write the appliance reacted to -- at the cost
        of blocking polls and entity writes for the sequence's full length
        (up to 10 x 30s). Pass False to take the lock per write and release
        it across the waits, for a long sequence where a stalled poll costs
        more than an interleaved read.

        `writes` are already on-the-wire hrefs: subdevice translation
        (canonical -> actual) is services.py's job, not this method's --
        this primitive has no notion of subdevices, same as the original
        single-write async_raw_write never did.

        `async_raw_write` below delegates here with a one-item sequence, so
        its signature/return and tests/test_coordinator_raw_write.py stay
        unchanged.
        """
        if not writes or len(writes) > _DEBUG_MAX_WRITES:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="debug_too_many_writes",
            )
        if not 0 <= verify_after <= _DEBUG_MAX_VERIFY_AFTER_S:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="debug_verify_after_out_of_range",
            )
        # Validate every item before touching the session -- a rejected
        # call must fail before any write goes out (same posture the
        # single-write path has always had; see
        # test_raw_write_validation_errors_do_not_touch_the_session).
        parsed = [_validate_debug_write_item(w) for w in writes]

        results: list[dict[str, Any]] = []
        last_payload_by_href: dict[str, dict] = {}
        # Exactly one of these is the real lock, never both -- asyncio.Lock
        # isn't reentrant, so nesting the same one would deadlock.
        outer_lock = self._session_lock if hold_session_lock else contextlib.nullcontext()
        try:
            async with outer_lock:
                for i, (path_segs, href, payload, settle) in enumerate(parsed):
                    per_write_lock = (
                        contextlib.nullcontext() if hold_session_lock else self._session_lock
                    )
                    async with per_write_lock:
                        before = self.resource(href)
                        code, after = await self.hass.async_add_executor_job(
                            self._raw_write_blocking, path_segs, payload, href
                        )
                    last_payload_by_href[href] = payload
                    results.append(
                        {
                            "href": href,
                            "code": _coap_code_str(code),
                            "raw_code": code,
                            "accepted": _coap_accepted(code),
                            "before": before,
                            "after": after,
                            "changed": all(after.get(k) == v for k, v in payload.items()),
                        }
                    )
                    # Under the default this wait happens inside the lock, so
                    # nothing lands between two writes to blur which one the
                    # appliance reacted to; see hold_session_lock above.
                    if settle and i < len(parsed) - 1:
                        await asyncio.sleep(settle)
        except Exception as err:
            # A drop partway leaves the appliance holding whatever already
            # landed, so the error has to say which writes got through.
            done = ", ".join(r["href"] for r in results) or "none"
            self._log.warning(
                "raw write sequence failed after %d of %d writes (completed: %s): %s",
                len(results),
                len(parsed),
                done,
                err,
            )
            await self.async_request_refresh()
            raise HomeAssistantError(
                f"Raw write sequence failed after {len(results)} of {len(parsed)} writes "
                f"(completed: {done}). The appliance may be holding a partial sequence."
            ) from err

        response: dict[str, Any] = {"results": results}
        if verify_after > 0:
            # Released, not held, across this wait: holding _session_lock
            # through up to 60s would stall the summary poll for that whole
            # window (same reasoning as _attempt_observe_mode's grace wait,
            # issue #294). A poll interleaving here is harmless -- just
            # another read of the same hrefs.
            await asyncio.sleep(verify_after)
            verified: dict[str, Any] = {}
            async with self._session_lock:
                for href in dict.fromkeys(r["href"] for r in results):
                    vcode, vrep = await self.hass.async_add_executor_job(
                        self._raw_read_blocking, _href_to_path_segs(href), href
                    )
                    # None, not False, when the re-read brought back nothing
                    # to compare: every comparison against an empty rep is
                    # False, which would report a 4.04 as a revert -- the one
                    # distinction verify_after exists to draw.
                    read_ok = _coap_accepted(vcode) and bool(vrep)
                    verified[href] = {
                        "code": _coap_code_str(vcode),
                        "raw_code": vcode,
                        "rep": vrep,
                        "held": (
                            all(vrep.get(k) == v for k, v in last_payload_by_href[href].items())
                            if read_ok
                            else None
                        ),
                    }
            response["verified"] = verified

        # Hasten a summary poll so entities on other resources catch up
        # too -- a debug write can affect siblings, not just its href. Once
        # per sequence, not per write: the whole point of ordering writes
        # under one lock hold is to control exactly what the device sees
        # and when, which a refresh racing in mid-sequence would undermine.
        await self.async_request_refresh()
        return response

    async def async_raw_write(self, href: str, body: dict) -> tuple[int, dict]:
        """Debug-only arbitrary write (issue #54). Bypasses remote-control
        and write_fn/validate_fn; sends `body` verbatim as a partial-rep
        PATCH to `href`. Returns (coap_code, new_rep) read back right
        after -- a thin single-write wrapper over async_raw_write_sequence
        (issue #300)."""
        sequence = await self.async_raw_write_sequence([{"href": href, "payload": body}])
        only = sequence["results"][0]
        return only["raw_code"], only["after"]
