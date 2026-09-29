"""Streamlit widget, AppTest and tab-source tests split out of tests/test_sources_auth_browser.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_sources_auth_browser.py."""

import inspect
import pytest
from sources import (store)


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    # B-28: an unresolvable start URL drops the browser tiers; these fake
    # `.invalid` hosts stand for public sites, so resolve them.
    monkeypatch.setattr("services.url_guard.resolve_public", lambda url: "93.184.216.34")


class TestPersistentProfiles:

    def test_backups_leave_saved_sign_ins_out(self):
        from tabs import library_tab
        assert "src_store.BROWSER_PROFILES_DIRNAME" in inspect.getsource(library_tab)
        assert store.BROWSER_PROFILES_DIRNAME == "profiles"
