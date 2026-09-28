"""
Tests for Diagnostics' "Source access" section (Step 86's third item):
a standalone "Test Now" action, reachable without a thrown
ChallengeDetected/exception first, that records real access-ladder
evidence via sources.ladder.test_tier() -- never a bare, evidence-free
"mark as working" override.
"""

from streamlit.testing.v1 import AppTest


def _render():
    import tabs.diagnostics_tab as dt
    dt.render_diagnostics_tab()


def _app(isolated_db, **session):
    at = AppTest.from_function(_render)
    for k, v in session.items():
        at.session_state[k] = v
    at.run(timeout=30)
    assert not at.exception, [e.value for e in at.exception]
    return at


class TestDiagnosticsSourceAccess:
    def test_section_lists_a_source_with_no_prior_challenge(self, isolated_db, monkeypatch):
        from sources import chapter_check, store
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)
        store.set_setting("demo_source_enabled", True)
        at = _app(isolated_db)
        assert any("Source access" in e.label for e in at.expander)
        assert any("Demo" in m.value for m in at.markdown)

    def test_test_now_records_real_evidence_on_success(self, isolated_db, monkeypatch):
        from sources import chapter_check, ladder, store
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)
        store.set_setting("demo_source_enabled", True)

        class FakeOutcome:
            ok = True
            partial = False
            reasons = []
            detail = "fake success"
        monkeypatch.setattr(ladder, "static_tier", lambda client: (lambda url: FakeOutcome()))

        at = _app(isolated_db)
        at.text_input(key="diag_src_test_url_demo").set_value("https://demo.invalid/series/1")
        at.run(timeout=30)
        at.button(key="diag_src_test_demo").click()
        at.run(timeout=30)
        assert any("✅ works" in c.value for c in at.caption if "STATIC_HTTP" in c.value)

    def test_test_now_is_disabled_for_a_tos_prohibited_source(self, isolated_db, monkeypatch):
        from sources import chapter_check, registry
        from sources.base import SourceAdapter
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)

        class ProhibitedDemo(SourceAdapter):
            name = "diag_tos_prohibited_demo"
            display_name = "Diag ToS Prohibited Demo"
            content_types = ["manhua"]
            url_patterns = [r"diag-tos-prohibited-demo\.invalid/"]

            def capabilities(self):
                caps = super().capabilities()
                caps.terms = {"checked": True, "tos_prohibited": True}
                return caps
        monkeypatch.setitem(registry._ADAPTERS, "diag_tos_prohibited_demo", ProhibitedDemo)
        at = _app(isolated_db)
        at.text_input(key="diag_src_test_url_diag_tos_prohibited_demo").set_value(
            "https://diag-tos-prohibited-demo.invalid/x")
        at.run(timeout=30)
        matches = [b for b in at.button if b.key == "diag_src_test_diag_tos_prohibited_demo"]
        assert matches and matches[0].disabled
