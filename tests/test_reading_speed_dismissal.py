"""A dismissed reading-speed flag stays dismissed until the English changes."""
import os

import pytest

import subtitle_formats as sf
from core import Line
from services import drama_service, export_service, lines_service, reading_speed_dismissal_service as svc

DENSE = "This translation is far too long to read in two seconds."


@pytest.fixture
def title(isolated_db):
    did = isolated_db.create_drama(title_en="D")
    isolated_db.save_lines(did, [
        Line(idx=0, start=0.0, end=2.0, zh="a", en=DENSE),
        Line(idx=1, start=3.0, end=5.0, zh="b", en=DENSE + " Again, quite long."),
        Line(idx=2, start=6.0, end=9.0, zh="c", en="Fine.")])
    return did


def _by_idx(db, did):
    return {ln.idx: ln for ln in db.load_line_objects(did)}


def test_dismissed_line_stays_unflagged_others_flag(isolated_db, title):
    assert export_service.flag_dense_lines(title)["flagged_count"] == 2
    first = _by_idx(isolated_db, title)[0]
    lines_service.dismiss_flag(title, first.id)
    # Clear the other flag too, so the re-run has both to decide on.
    other = _by_idx(isolated_db, title)[1]
    other.flag, other.flag_note = None, ""
    isolated_db.save_lines(title, [other], fields=("flag", "flag_note"))

    assert export_service.flag_dense_lines(title)["flagged_count"] == 1
    lines = _by_idx(isolated_db, title)
    assert lines[0].flag is None and lines[1].flag == sf.READING_SPEED_FLAG


def test_edited_text_can_be_flagged_again_when_still_too_fast(isolated_db, title):
    export_service.flag_dense_lines(title)
    first = _by_idx(isolated_db, title)[0]
    lines_service.dismiss_flag(title, first.id)
    first = _by_idx(isolated_db, title)[0]
    first.en = DENSE + " Edited."
    isolated_db.save_lines(title, [first], fields=("en",))
    assert export_service.flag_dense_lines(title)["flagged_count"] >= 1
    assert _by_idx(isolated_db, title)[0].flag == sf.READING_SPEED_FLAG


def test_edited_text_that_is_now_fine_stays_unflagged(isolated_db, title):
    export_service.flag_dense_lines(title)
    first = _by_idx(isolated_db, title)[0]
    lines_service.dismiss_flag(title, first.id)
    first = _by_idx(isolated_db, title)[0]
    first.en = "Short."
    isolated_db.save_lines(title, [first], fields=("en",))
    export_service.flag_dense_lines(title)
    assert _by_idx(isolated_db, title)[0].flag is None


def test_dismissing_another_kind_of_flag_records_nothing(isolated_db, title):
    line = _by_idx(isolated_db, title)[2]
    line.flag = "name_uncertain"
    isolated_db.save_lines(title, [line], fields=("flag", "flag_note"))
    lines_service.dismiss_flag(title, line.id)
    assert svc.load_dismissed(title) == set()


def test_store_is_capped_and_keeps_the_newest(isolated_db, title, monkeypatch):
    monkeypatch.setattr(svc, "_MAX_ENTRIES", 3)
    for n in range(1, 6):
        svc.note_dismissed(title, n, f"text {n}")
    assert {i for i, _ in svc.load_dismissed(title)} == {3, 4, 5}


def test_garbage_sidecar_is_ignored(isolated_db, title):
    with open(svc._path(title), "w", encoding="utf-8") as fh:
        fh.write('{"dismissed": [[1], "x", [true, "h"], [2, "ok"]]}')
    assert svc.load_dismissed(title) == {(2, "ok")}


def test_deleting_the_title_removes_the_record(isolated_db, title):
    svc.note_dismissed(title, 1, "x")
    path = svc._path(title)
    assert os.path.exists(path)
    drama_service.delete_drama(title, confirm=True, confirm_text="DELETE")
    assert not os.path.exists(path)
