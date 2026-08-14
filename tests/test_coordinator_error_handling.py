"""Regression tests for a handful of call sites in coordinator.py that used
to let a smartthings-local exception (EndpointError, SessionError,
SessionTimeoutError, SessionClosedError, ... -- or the equivalent bare
ConnectionError/TimeoutError/OSError an older library version raised) escape
uncaught instead of going through this integration's own reconnect/logging
or getting translated into a HomeAssistantError for a service caller.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_PORT,
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator

ENTRY_DATA = {
    CONF_HOST: "10.0.0.198",
    CONF_PORT: 49154,
    CONF_LEAF_CERT_PEM: "-----BEGIN CERTIFICATE-----\nTEST-LEAF\n-----END CERTIFICATE-----",
    CONF_LEAF_KEY_PEM: "-----BEGIN PRIVATE KEY-----\nTEST-LEAF-KEY\n-----END PRIVATE KEY-----",
}


def _coordinator(hass: HomeAssistant) -> LocalThingsCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id="localthings_ERRHANDLING-TEST",
    )
    entry.add_to_hass(hass)
    return LocalThingsCoordinator(hass, entry)


async def test_subdevice_enumeration_failure_does_not_abort_first_discovery(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """_enumerate_subdevices_blocking's own _connect_session() call only
    fires if the session the poll above just used got closed out from under
    it within the same cycle -- rare, but until this fix, unguarded: an
    exception there escaped _async_update_data entirely instead of going
    through this integration's own logging, matching what already happens
    for the main poll's own reconnect.

    Not a one-cycle blip once caught, though: `_discovered` flips True this
    same cycle regardless (gating first discovery, not subdevice success),
    so this is the *only* attempt a composite appliance's siblings ever get
    without a config-entry reload -- logged at warning for exactly that
    reason, not debug.

    Empty resources keep _run_discovery from binding anything (hot/warm
    hrefs stay empty), so _attempt_observe_mode's own session touch never
    runs either -- this test is purely about the enumeration failure not
    escaping _async_update_data.
    """
    coordinator = _coordinator(hass)
    monkeypatch.setattr(coordinator, "_poll_once", dict)

    def _boom(_resources):
        raise ConnectionError("session closed")

    monkeypatch.setattr(coordinator, "_enumerate_subdevices_blocking", _boom)

    with caplog.at_level("WARNING"):
        result = await coordinator._async_update_data()

    assert coordinator._discovered is True
    assert result == {}
    assert "subdevice enumeration failed" in caplog.text
