"""Tests for the diagnostics platform."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from homeassistant.core import HomeAssistant

from custom_components.localthings import diagnostics as diagnostics_mod
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.diagnostics import async_get_config_entry_diagnostics
from custom_components.localthings.registry.redact import REDACTED

_MANIFEST_VERSION = json.loads(
    (Path(__file__).parents[2] / "custom_components" / "localthings" / "manifest.json").read_text()
)["version"]


async def test_diagnostics_shape_and_redaction(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_entry)

    assert diagnostics["device_type"] == "refrigerator"
    assert diagnostics["one_ui_version"] == "7.0 Refrigerator"
    assert diagnostics["unbound_hrefs"] == []
    assert diagnostics["integration_version"] == _MANIFEST_VERSION
    assert diagnostics["smartthings_local_version"]

    resources = diagnostics["resources"]
    assert resources["/information/vs/0"]["x.com.samsung.da.serialNum"] == REDACTED
    assert resources["/wirelessinfo/vs/0"]["macaddressWiFi"] == REDACTED
    # Ordinary state survives.
    assert resources["/status/lock/vs/0"]["x.com.samsung.da.ado.devicecontrol"] == "On"


async def test_diagnostics_include_ocf_identity(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """/oic/p and /oic/d are outside the /device/0 batch, so diagnostics is
    the only place an issue report can carry them -- and `rt` there is OCF's
    own device-type declaration."""
    from custom_components.localthings.registry.identity import DeviceIdentity

    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    coordinator._identity = DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="RF9000B",
        name="Family Hub",
        serial=None,
        device_types=("oic.wk.d", "oic.d.refrigerator"),
        raw={
            "/oic/p": {"mnmn": "Samsung Electronics", "pi": "12-34-56"},
            "/oic/d": {
                "n": "Family Hub",
                "di": "ab-cd-ef",
                "rt": ["oic.wk.d", "oic.d.refrigerator"],
            },
        },
    )

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    identity = diag["identity"]
    assert identity["model"] == "RF9000B"
    assert identity["device_types"] == ["oic.wk.d", "oic.d.refrigerator"]
    # The raw payloads ride along whole -- we don't yet know which of their
    # fields identify a device type, so nothing is dropped up front beyond
    # what redaction takes out.
    assert identity["resources"]["/oic/p"]["mnmn"] == "Samsung Electronics"
    assert identity["resources"]["/oic/d"]["di"] == REDACTED
    assert identity["resources"]["/oic/p"]["pi"] == REDACTED
    # The owner-settable device name is redacted; `rt` -- the reason this
    # block exists -- is not.
    assert identity["resources"]["/oic/d"]["n"] == REDACTED
    assert identity["resources"]["/oic/d"]["rt"] == ["oic.wk.d", "oic.d.refrigerator"]


async def test_diagnostics_include_oic_res_links(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """/oic/res -- OCF's resource-discovery endpoint -- rides along in
    identity.raw the same way /oic/p and /oic/d do. Its response is a list
    of Link objects (one per href/Collection the endpoint hosts), not a
    single Property map, so redaction has to walk into the list too."""
    from custom_components.localthings.registry.identity import DeviceIdentity

    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    coordinator._identity = DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="RF9000B",
        name="Family Hub",
        serial=None,
        device_types=("oic.wk.d", "oic.d.refrigerator"),
        raw={
            "/oic/p": {"mnmn": "Samsung Electronics"},
            "/oic/d": {"rt": ["oic.wk.d", "oic.d.refrigerator"]},
            "/oic/res": [
                {
                    "di": "aaaa-1111",
                    "href": "/device/0",
                    "rt": ["x.com.samsung.devcol", "oic.wk.col"],
                },
                {
                    "di": "bbbb-2222",
                    "href": "/device/1",
                    "rt": ["x.com.samsung.devcol", "oic.wk.col"],
                },
            ],
        },
    )

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    links = diag["identity"]["resources"]["/oic/res"]
    assert len(links) == 2
    assert links[0]["di"] == REDACTED
    assert links[0]["href"] == "/device/0"
    assert links[1]["di"] == REDACTED
    assert links[1]["href"] == "/device/1"
    assert links[1]["rt"] == ["x.com.samsung.devcol", "oic.wk.col"]


async def test_diagnostics_include_speculative_device_probes(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """/device/1 and /device/2 (issue #177's Composite Device follow-up) ride
    along in identity.raw the same {href: rep} shape as the main /device/0
    dump under "resources" -- ordinary redaction, no special-casing needed."""
    from custom_components.localthings.registry.identity import DeviceIdentity

    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    coordinator._identity = DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="RF9000B",
        name="Family Hub",
        serial=None,
        device_types=("oic.wk.d", "oic.d.refrigerator"),
        raw={
            "/oic/p": {},
            "/oic/d": {},
            "/oic/res": [],
            "/device/1": {
                "/information/vs/0": {"x.com.samsung.da.serialNum": "SECRET123"},
                "/power/vs/0": {"x.com.samsung.da.power": "On"},
            },
            "/device/2": {},
        },
    )

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    device1 = diag["identity"]["resources"]["/device/1"]
    assert device1["/information/vs/0"]["x.com.samsung.da.serialNum"] == REDACTED
    assert device1["/power/vs/0"]["x.com.samsung.da.power"] == "On"
    assert diag["identity"]["resources"]["/device/2"] == {}


async def test_diagnostics_identity_none_when_unavailable(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """read_identity is best-effort: a device that answers neither resource
    (or a session that never connected) must not break the download."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    assert diag["identity"] is None


async def test_diagnostics_include_observe_mode_fields(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    assert diag["observe_mode"] == "poll"
    assert diag["observe_subscribed_hrefs"] == []
    assert diag["observe_fallback_hrefs"] == []
    assert "observe_last_mode_change" in diag
    assert diag["observe_href_freshness_s"] == {}


async def test_dependency_version_read_off_the_event_loop(
    hass: HomeAssistant, mock_entry, mock_coordinator_session, monkeypatch
) -> None:
    """importlib.metadata.version() does blocking disk I/O, so it must run in
    the executor, not on the event loop (issue #9's logs flagged it)."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    loop_thread_id = threading.get_ident()  # this coroutine runs on the loop
    seen: dict[str, int] = {}
    real_pkg_version = diagnostics_mod.pkg_version

    def _spy(name: str) -> str:
        seen["thread_id"] = threading.get_ident()
        return real_pkg_version(name)

    monkeypatch.setattr(diagnostics_mod, "pkg_version", _spy)

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    assert diag["smartthings_local_version"]
    assert seen["thread_id"] != loop_thread_id
