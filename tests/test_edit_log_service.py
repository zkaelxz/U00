"""The corrections report counts the edit log (English and source text)."""
import db
from core import Line
from services import edit_log_service as svc
from tools import corrections_report


def _title():
    did = db.create_drama(title_en="D")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=z, en="e")
                        for i, z in enumerate("甲乙丙丁")])
    ids = [ln.id for ln in db.load_line_objects(did)]
    db.update_line_fields_if(did, ids[3], {"flag": "uncertain"}, {})
    return did, ids


def test_counts_per_engine_flag_and_flagged_against_unflagged(isolated_db):
    did, ids = _title()
    db.record_edit_sample(did, "甲", "a", "A", line_id=ids[0], engine="claude", model="m",
                          prompt_version="6", flag="uncertain")
    db.record_edit_sample(did, "乙", "b", "B", line_id=ids[1], engine="claude", model="m",
                          prompt_version="6")
    db.record_edit_sample(did, "丙", "c", "C")  # saved before the log existed
    db.record_source_edits(did, [
        {"line_id": ids[0], "path": "person", "before_zh": "甲", "after_zh": "申",
         "engine": "whisper", "model": "large-v3", "confidence": 0.3, "flag": "source_mismatch"},
        {"line_id": ids[1], "path": "compare", "before_zh": "乙", "after_zh": "已"},
        {"line_id": ids[2], "path": "name_fix", "before_zh": "丙", "after_zh": "丙"}])
    report = svc.corrections_report()
    en, src = report["english"], report["source"]
    assert report["line_count"] == 4
    assert (en["corrections"], en["linked_to_a_line"]) == (3, 2)
    assert en["by_engine"] == {"claude": 2, "(none)": 1}
    assert en["by_prompt_version"] == {"6": 2, "(none)": 1}
    assert en["by_flag"] == {"(none)": 2, "uncertain": 1}
    # Line 0 was flagged when corrected, line 3 is flagged now and never corrected.
    assert en["flag_rates"] == {"flagged_lines": 2, "flagged_corrected": 1,
                                "unflagged_corrected": 1}
    # Machine fixes are counted apart; an unchanged text is not logged at all.
    assert src["corrections"] == 1 and src["machine_fixes_by_path"] == {"compare": 1}
    assert (src["by_engine"], src["by_confidence"]) == ({"whisper": 1}, {"low": 1})
    assert src["flag_rates"] == {"flagged_lines": 2, "flagged_corrected": 1,
                                 "unflagged_corrected": 0}
    text = corrections_report.render(report)
    assert "Source-text corrections: 1 (0 from deleted titles)" in text and "machine fixes by path: compare 1" in text
    assert "flagged lines corrected:   1 of 2 (50.0%)" in text


def test_a_deleted_titles_corrections_are_still_counted(isolated_db):
    did, ids = _title()
    db.record_edit_sample(did, "甲", "a", "A", line_id=ids[0], engine="claude")
    db.record_source_edits(did, [{"line_id": ids[0], "path": "person", "before_zh": "甲",
                                  "after_zh": "申", "engine": "whisper"}])
    db.delete_drama(did)
    report = svc.corrections_report()
    assert (report["english"]["corrections"], report["english"]["from_deleted_titles"],
            report["english"]["by_engine"]) == (1, 1, {"claude": 1})
    assert (report["source"]["corrections"], report["source"]["from_deleted_titles"],
            report["source"]["by_engine"]) == (1, 1, {"whisper": 1})
    assert [r["deleted_title"] for r in db.list_source_edits(limit=-1)] == ["D"]


def test_empty_library_reports_zeroes(isolated_db):
    report = svc.corrections_report()
    assert report["english"]["corrections"] == report["source"]["corrections"] == 0
    assert "n/a" in corrections_report.render(report)


def test_a_logging_failure_never_fails_the_edit(isolated_db, monkeypatch):
    did, ids = _title()
    monkeypatch.setattr(db, "record_source_edits", lambda *a: 1 / 0)
    [ln] = [x for x in db.load_line_objects(did) if x.id == ids[0]]
    assert svc.record_source_edits(did, [(ln, "申")], svc.PERSON) == 0
