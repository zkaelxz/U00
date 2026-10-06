"""A title whose every line has a translation must not stay at "aligned"."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bulk_translate as bt
from core import Line
from services import lines_service


def _two_line_drama(db, status="aligned", en=("done", "")):
    did = db.create_drama(title_en="T", status=status)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="一", en=en[0]),
                        Line(idx=1, start=1, end=2, zh="二", en=en[1])])
    return did


def _status(db, did):
    return db.get_drama(did)["status"]


def test_typing_the_last_missing_translation_marks_translated(isolated_db):
    did = _two_line_drama(isolated_db)
    last = isolated_db.load_lines(did)[1]["id"]
    lines_service.patch_line(did, last, en="two")
    assert _status(isolated_db, did) == "translated"


def test_typing_a_non_final_translation_leaves_aligned(isolated_db):
    did = _two_line_drama(isolated_db, en=("", ""))
    first = isolated_db.load_lines(did)[0]["id"]
    lines_service.patch_line(did, first, en="one")
    assert _status(isolated_db, did) == "aligned"


@pytest.mark.parametrize("status", ["dubbed", "exported", "not started"])
def test_other_statuses_are_never_touched(isolated_db, status):
    did = _two_line_drama(isolated_db, status=status)
    last = isolated_db.load_lines(did)[1]["id"]
    lines_service.patch_line(did, last, en="two")
    assert _status(isolated_db, did) == status


def test_tm_suggestion_filling_the_last_line_marks_translated(isolated_db):
    did = _two_line_drama(isolated_db)
    last = isolated_db.load_lines(did)[1]["id"]
    # Accepting a TM entry writes through update_line_fields_if.
    # Accepting a TM entry (lines_service.accept_tm_suggestion) is a CAS write
    assert isolated_db.update_line_fields_if(did, last, {"en": "two"}, {"en": ""})
    assert bt.mark_translated_if_complete(did)
    assert _status(isolated_db, did) == "translated"


class TestStartupRepair:
    def test_fixes_only_aligned_titles_with_every_line_translated(self, isolated_db):
        stuck = _two_line_drama(isolated_db, en=("a", "b"))
        partial = _two_line_drama(isolated_db)
        empty = isolated_db.create_drama(title_en="E", status="aligned")
        dubbed = _two_line_drama(isolated_db, status="dubbed", en=("a", "b"))
        assert bt.repair_stale_aligned_statuses() == 1
        assert _status(isolated_db, stuck) == "translated"
        assert _status(isolated_db, partial) == "aligned"
        assert _status(isolated_db, empty) == "aligned"
        assert _status(isolated_db, dubbed) == "dubbed"
        assert bt.repair_stale_aligned_statuses() == 0   # idempotent


class TestBulkApplyWithNothingNewlyApplied:
    def test_all_lines_typed_meanwhile_still_marks_translated(self, isolated_db):
        from tests.test_bulk_translate import (_answer_every_request, _claude_engine, _drama,
                                               _submit)
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        bulk_id = _submit(isolated_db, did, engine)
        lines = isolated_db.load_line_objects(did)
        for ln in lines:
            ln.en = "My own wording."
        isolated_db.save_lines(did, lines, fields=("en",))

        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"
        bt.check_once(bulk_id, bt.make_provider("claude", engine))

        assert isolated_db.get_bulk_job(bulk_id)["result_summary"]["applied"] == 0
        assert _status(isolated_db, did) == "translated"


def test_retranscribe_then_full_finish_run_marks_translated(isolated_db):
    """The reported sequence: the run's own finish step already handles it."""
    from types import SimpleNamespace as NS
    did = _two_line_drama(isolated_db, en=("", ""))
    lines = isolated_db.load_line_objects(did)
    for ln in lines:
        ln.en = "x"
    isolated_db.save_lines(did, lines, fields=("en",))
    bt.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                              glossary_terms=[], errors=[])
    assert _status(isolated_db, did) == "translated"
