"""Streamlit widget, AppTest and tab-source tests split out of tests/test_auto_qc.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_auto_qc.py."""

import auto_qc
from core import Line


def _ln(idx, zh, en, **kw):
    return Line(idx=idx, start=idx * 2.0, end=idx * 2.0 + 1.5, zh=zh, en=en, **kw)


def test_run_auto_qc_button_flags_lines_in_the_review_queue(isolated_db):
    """The button in Review queue writes the flag and reports the count;
    merely opening the page writes nothing."""
    from streamlit.testing.v1 import AppTest
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                   content_mode="audio_drama", status="translated")
    isolated_db.save_lines(did, [_ln(0, "他欠我三百块钱。", "He owes me money."),
                                 _ln(1, "我等了十年。", "I waited ten years.")])

    def _render():
        import tabs.workspace_tab as wt
        wt.render_workspace_tab()
    at = AppTest.from_function(_render)
    at.session_state["active_drama_id"] = did
    at.session_state["lines"] = None
    at.run(timeout=30)
    assert isolated_db.load_lines(did)[0]["flag"] is None

    [b for b in at.button if b.key == f"run_auto_qc_{did}"][0].click()
    at.run(timeout=30)

    rows = isolated_db.load_lines(did)
    assert [r["flag"] for r in rows] == [auto_qc.AUTO_QC_FLAG, None]
    assert any("Auto QC flagged 1 of 2" in w.value for w in at.warning)
    # Shown in the review table like any other flag.
    assert any("Auto QC" in w.value and "三百" in w.value for w in at.warning)


def test_auto_qc_before_export_is_read_only_until_clicked(isolated_db):
    """Step 12e's "Auto QC before export" toggle (on in the Release tier)
    lists mismatches in the Export section; rendering writes nothing, the
    button flags them."""
    from streamlit.testing.v1 import AppTest
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                   content_mode="audio_drama", status="translated")
    isolated_db.save_lines(did, [_ln(0, "他欠我三百块钱。", "He owes me money."),
                                 _ln(1, "我等了十年。", "I waited ten years.")])

    def _render():
        import tabs.workspace_tab as wt
        wt.render_workspace_tab()
    at = AppTest.from_function(_render)
    at.session_state["active_drama_id"] = did
    at.session_state["lines"] = None
    at.run(timeout=30)
    assert not any("Auto QC: " in w.value and "compared with the source" in w.value for w in at.warning)  # off by default

    at.checkbox(key=f"auto_qc_{did}").check().run(timeout=30)
    assert not at.exception
    assert any("Auto QC: 1 line(s)" in w.value and "#1" in w.value for w in at.warning)
    assert isolated_db.load_lines(did)[0]["flag"] is None  # not written just from rendering

    at.button(key=f"flag_auto_qc_{did}").click().run(timeout=30)
    assert [r["flag"] for r in isolated_db.load_lines(did)] == [auto_qc.AUTO_QC_FLAG, None]
