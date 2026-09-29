"""
services/extension_service.py -- the browser-extension bridge's on/off
switch and token, for the PC-only React control that replaces the Settings
sidebar's "Browser extension" expander (tabs/settings_tab.py:430-484) once
Streamlit is gone. User decision, 2026-09-29.

The bridge is `page_server.py`: a loopback-only HTTP endpoint (port 8756)
that needs its own shared token on every request. Its on/off state is the
Sources setting `page_server_enabled` (sources/store.py), the same one the
API startup hook (api/background.py) reads.

Every function here is meant for `local_only()` routes. `get_status` never
returns the port or the token; only `reveal_token` returns the token.

Stopping: `page_server` has no stop function (Streamlit never stopped it
either; unticking the box only stopped the tab from starting it again), so
turning the bridge off persists the setting and reports `restart_needed`
while this process still serves it. It stops at the next API restart.
"""

import page_server
from services.service_errors import InvalidInputError
from sources import store as src_store


def get_status() -> dict:
    """{enabled, running}: the stored setting and whether this process
    serves the endpoint. No port, no token."""
    return {"enabled": bool(src_store.get_setting("page_server_enabled")),
            "running": bool(page_server.server_running())}


def set_enabled(enabled, start_now: bool = True) -> dict:
    """Persists `page_server_enabled`. When turning it on and `start_now`
    (the API passes its own background-services flag, so a process that
    starts no background pieces never opens the port) the endpoint is
    started in this process, as the startup hook would. Returns
    {enabled, running, restart_needed}."""
    if not isinstance(enabled, bool):
        raise InvalidInputError("enabled must be true or false.")
    src_store.set_setting("page_server_enabled", enabled)
    if enabled and start_now:
        page_server.ensure_server_started()
    status = get_status()
    status["restart_needed"] = bool(status["running"] and not enabled)
    return status


def reveal_token(confirm=False) -> dict:
    """{token}: the extension's shared token (created on first use). Needs
    confirm=True. The caller must not log or cache the response."""
    if confirm is not True:
        raise InvalidInputError("Showing the extension token needs confirm=true.")
    return {"token": page_server.load_or_create_token()}
