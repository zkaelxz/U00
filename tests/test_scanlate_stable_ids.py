"""Scanlate S0: id-preserving region writes in db.py (docs/archive/scanlate-api-spec.md §2, §6)."""
import sqlite3

import pytest


def _bubble(x=0, **kw):
    b = {"x": x, "y": 0, "w": 50, "h": 40, "source_text": f"src{x}", "translated_text": ""}
    b.update(kw)
    return b


@pytest.fixture
def page(isolated_db):
    db = isolated_db
    did = db.create_drama(title_zh="漫画", title_en="Comic", source_language="zh")
    pid = db.create_page(did, 0, "pages/page_0000.png", 100, 200)
    db.save_bubbles(pid, [_bubble(1), _bubble(2), _bubble(3)])
    return db, did, pid


def _ids(db, pid):
    return [b["id"] for b in db.load_bubbles(pid)]


def _rev(db, pid):
    return db.get_page(pid)["rev"]


def test_new_page_columns_exist_and_alter_is_idempotent(page):
    db, did, pid = page
    p = db.get_page(pid)
    assert p["rev"] == 0 and p["context_summary"] is None and p["run_notes"] is None
    db.init_db()  # second run: the check-then-ALTER must not fail
    assert db.get_page(pid)["rev"] == 0


def test_get_page_scoped_to_drama(page):
    db, did, pid = page
    other = db.create_drama(title_zh="別", title_en="Other", source_language="zh")
    assert db.get_page(pid, drama_id=did)["id"] == pid
    assert db.get_page(pid, drama_id=other) is None
    assert db.get_page(999999) is None


def test_next_page_idx_uses_max_not_count(page):
    db, did, pid = page
    db.create_page(did, 7, "pages/page_0007.png", 10, 10)
    assert db.next_page_idx(did) == 8
    empty = db.create_drama(title_zh="空", title_en="Empty", source_language="zh")
    assert db.next_page_idx(empty) == 0


def test_update_page_whitelists_fields(page):
    db, did, pid = page
    db.update_page(pid, context_summary="ctx", run_notes="[]")
    assert db.get_page(pid)["context_summary"] == "ctx"
    with pytest.raises(ValueError):
        db.update_page(pid, **{"rev = 5, idx": 1})
    with pytest.raises(ValueError):
        db.update_page(pid, drama_id=2)


def test_update_bubble_fields_keeps_ids_and_bumps_rev(page):
    db, did, pid = page
    ids = _ids(db, pid)
    assert db.update_bubble_fields(ids[1], {"translated_text": "Hi"},
                                   expected={"translated_text": None}, page_id=pid)
    assert _ids(db, pid) == ids
    assert db.load_bubbles(pid)[1]["translated_text"] == "Hi"
    assert _rev(db, pid) == 1


def test_update_bubble_fields_stale_expected_writes_nothing(page):
    db, did, pid = page
    ids = _ids(db, pid)
    assert not db.update_bubble_fields(ids[0], {"translated_text": "X"},
                                       expected={"source_text": "something else"})
    assert db.load_bubbles(pid)[0]["translated_text"] == ""
    assert _rev(db, pid) == 0
    # wrong page, gone bubble
    assert not db.update_bubble_fields(ids[0], {"translated_text": "X"}, page_id=pid + 1)
    assert not db.update_bubble_fields(999999, {"translated_text": "X"})


def test_update_bubble_fields_rejects_unknown_columns(page):
    db, did, pid = page
    ids = _ids(db, pid)
    with pytest.raises(ValueError):
        db.update_bubble_fields(ids[0], {"page_id": 5})
    with pytest.raises(ValueError):
        db.update_bubble_fields(ids[0], {"translated_text": "x"}, expected={"id; DROP": 1})


def test_insert_delete_reorder_keep_other_ids(page):
    db, did, pid = page
    a, b, c = _ids(db, pid)
    new = db.insert_bubble(pid, _bubble(9), position=1)
    assert _ids(db, pid) == [a, new, b, c]
    assert [x["idx"] for x in db.load_bubbles(pid)] == [0, 1, 2, 3]
    assert db.delete_bubble(pid, b)
    assert _ids(db, pid) == [a, new, c]
    assert not db.delete_bubble(pid, b)
    assert db.reorder_bubbles(pid, [c, a, new])
    assert _ids(db, pid) == [c, a, new]
    assert not db.reorder_bubbles(pid, [c, a])            # missing id
    assert not db.reorder_bubbles(pid, [c, a, new, 999])  # extra id
    assert not db.reorder_bubbles(pid, [c, c, a])         # repeat
    assert _ids(db, pid) == [c, a, new]
    assert _rev(db, pid) == 3
    appended = db.insert_bubble(pid, _bubble(10))
    assert _ids(db, pid)[-1] == appended


def test_replace_bubbles_if_unchanged(page):
    db, did, pid = page
    ids = _ids(db, pid)
    assert db.replace_bubbles_if_unchanged(pid, ids[:2], [_bubble(5)]) is None
    assert _ids(db, pid) == ids
    new_ids = db.replace_bubbles_if_unchanged(pid, ids, [_bubble(5), _bubble(6)])
    assert new_ids == _ids(db, pid) and len(new_ids) == 2
    assert _rev(db, pid) == 1
    # an empty page: expected set is empty
    p2 = db.create_page(did, 1, "pages/page_0001.png", 10, 10)
    assert db.replace_bubbles_if_unchanged(p2, [], [_bubble(1)]) is not None
    assert db.replace_bubbles_if_unchanged(p2, [], [_bubble(2)]) is None
    assert db.replace_bubbles_if_unchanged(999999, [], [_bubble(1)]) is None


def test_replace_bubbles_if_unchanged_checks_rev(page):
    db, did, pid = page
    ids = _ids(db, pid)
    rev = _rev(db, pid)
    # an id-preserving edit (the user fixing a translation mid-run)
    assert db.update_bubble_fields(ids[0], {"translated_text": "mine"})
    assert db.replace_bubbles_if_unchanged(pid, ids, [_bubble(7)], expected_rev=rev) is None
    assert db.load_bubbles(pid)[0]["translated_text"] == "mine"
    assert db.replace_bubbles_if_unchanged(pid, ids, [_bubble(7)],
                                           expected_rev=_rev(db, pid)) is not None


def test_save_bubbles_unchanged(page):
    # The legacy full replace is untouched: new ids every save, no rev bump.
    db, did, pid = page
    before = _ids(db, pid)
    db.save_bubbles(pid, db.load_bubbles(pid))
    assert set(_ids(db, pid)).isdisjoint(before)
    assert _rev(db, pid) == 0
