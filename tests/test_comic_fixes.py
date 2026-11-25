"""Per-series comic fixes: a hand fix of a box's English is kept for the series and shown to later
comic translations of that series, and an undone fix is dropped. Mocked: no models or network."""

import os

import pytest

pytest.importorskip("PIL")

import db
from services import comic_fixes_service
from services import scanlate_pages_service as pages_svc


def _comic(series=True, source="你好", english="Hello"):
    sid = db.create_series("Fix series") if series else None
    did = db.create_drama(title_en="Comic", source_language="zh", series_id=sid)
    os.makedirs(os.path.join(db.drama_dir(did), "pages"), exist_ok=True)
    pid = db.create_page(did, 0, "pages/page_0000.png", 200, 120)
    db.save_bubbles(pid, [{"x": 10, "y": 10, "w": 80, "h": 40, "source_text": source,
                           "translated_text": english}])
    return sid, did, pid, db.load_bubbles(pid)[0]["id"]


def _memory(sid):
    return {(e["source_text"], e["translation"]) for e in db.list_translation_memory(sid)}


def test_an_english_fix_is_kept_for_the_series(isolated_db):
    sid, did, pid, rid = _comic()
    pages_svc.edit_region(did, pid, rid, {"translated_text": "Hi there, Senpai"}, 0)
    assert _memory(sid) == {("你好", "Hi there, Senpai")}


def test_a_title_without_a_series_keeps_nothing(isolated_db):
    _sid, did, pid, rid = _comic(series=False)
    pages_svc.edit_region(did, pid, rid, {"translated_text": "Hi"}, 0)
    assert db.list_translation_memory(0) == []


@pytest.mark.parametrize("changes", [{"x": 12}, {"translated_text": "Hello"},
                                     {"translated_text": "Hello  "}, {"translated_text": ""}])
def test_only_a_changed_non_empty_english_is_a_fix(isolated_db, changes):
    sid, did, pid, rid = _comic()
    try:
        pages_svc.edit_region(did, pid, rid, changes, 0)
    except Exception:
        pass  # an unchanged value may be refused; either way no fix is kept
    assert _memory(sid) == set()


def test_a_box_with_no_source_text_keeps_nothing(isolated_db):
    sid, did, pid, rid = _comic(source="")
    pages_svc.edit_region(did, pid, rid, {"translated_text": "Typed by hand"}, 0)
    assert _memory(sid) == set()


def test_undo_drops_the_fix(isolated_db):
    sid, did, pid, rid = _comic()
    pages_svc.edit_region(did, pid, rid, {"translated_text": "Hi there"}, 0)
    pages_svc.undo_region_edit(did, pid, 1)
    assert _memory(sid) == set()


def test_undo_drops_only_the_wording_it_undid(isolated_db):
    sid, did, pid, rid = _comic()
    pages_svc.edit_region(did, pid, rid, {"translated_text": "One"}, 0)
    pages_svc.edit_region(did, pid, rid, {"translated_text": "Two"}, 1)
    pages_svc.undo_region_edit(did, pid, 2)
    assert _memory(sid) == {("你好", "One")}
    # Undoing "One" never deletes a different approval the series made since.
    db.record_translation_memory(sid, "你好", "Hey")
    pages_svc.undo_region_edit(did, pid, 3)
    assert _memory(sid) == {("你好", "Hey")}


def test_undo_puts_back_the_approval_the_series_had(isolated_db):
    sid, did, pid, rid = _comic()
    for _ in range(3):
        db.record_translation_memory(sid, "你好", "Hi")  # approved in the subtitle Review
    pages_svc.edit_region(did, pid, rid, {"translated_text": "Yo"}, 0)
    assert _memory(sid) == {("你好", "Yo")}
    pages_svc.undo_region_edit(did, pid, 1)
    [entry] = db.list_translation_memory(sid)
    assert (entry["translation"], entry["use_count"]) == ("Hi", 3)


def test_a_fix_matching_the_approval_leaves_it_alone_on_undo(isolated_db):
    sid, did, pid, rid = _comic()
    db.record_translation_memory(sid, "你好", "Hi")
    pages_svc.edit_region(did, pid, rid, {"translated_text": "Hi"}, 0)
    pages_svc.undo_region_edit(did, pid, 1)
    assert _memory(sid) == {("你好", "Hi")}


def test_undo_of_a_box_move_leaves_the_fixes(isolated_db):
    sid, did, pid, rid = _comic()
    pages_svc.edit_region(did, pid, rid, {"translated_text": "Hi there"}, 0)
    pages_svc.edit_region(did, pid, rid, {"x": 20}, 1)
    pages_svc.undo_region_edit(did, pid, 2)
    assert _memory(sid) == {("你好", "Hi there")}


def test_fixes_block_carries_matching_fixes_only(isolated_db):
    sid = db.create_series("S")
    db.record_translation_memory(sid, "前辈你好", "Hello, Senpai")
    db.record_translation_memory(sid, "完全无关的一句话", "Unrelated")
    drama = {"series_id": sid}
    block = comic_fixes_service.fixes_block(drama, [{"source_text": "前辈你好"}])
    assert "前辈你好 → Hello, Senpai" in block and "Unrelated" not in block
    assert comic_fixes_service.fixes_block(drama, [{"source_text": "别的"}]) == ""
    assert comic_fixes_service.fixes_block({}, [{"source_text": "前辈你好"}]) == ""


def test_a_page_run_keeps_a_fix_the_box_already_holds_but_redo_of_the_box_does_not(isolated_db):
    sid = db.create_series("S")
    db.record_translation_memory(sid, "前辈你好", "Hello, Senpai")
    region = [{"source_text": "前辈你好", "translated_text": "Hello, Senpai"}]
    assert "Hello, Senpai" in comic_fixes_service.fixes_block({"series_id": sid}, region)
    assert comic_fixes_service.fixes_block({"series_id": sid}, region, fresh_attempt=True) == ""


def test_with_fixes_never_fails_the_translation(isolated_db, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(db, "list_translation_memory", broken)
    assert comic_fixes_service.with_fixes("notes", {"series_id": 1}, [{"source_text": "x"}]) == "notes"


def test_a_studio_page_translation_is_sent_the_series_fixes(isolated_db, monkeypatch):
    from scanlate import scanlate_translate
    from services import scanlate_run_service as run_svc
    sid = db.create_series("S")
    db.record_translation_memory(sid, "前辈你好", "Hello, Senpai")
    seen = {}

    def fake_translate(keyed, engine, drama, **kw):
        seen["notes"] = kw.get("character_notes")
        return {k: "x" for k in keyed}, ""
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", fake_translate)
    monkeypatch.setattr(scanlate_translate, "recheck_source_script", lambda keyed, result, *a, **k: (result, []))
    bubbles = [{"source_text": "前辈你好", "translated_text": ""}]
    ok, _ctx = run_svc._translate(bubbles, object(), "ollama", {"series_id": sid, "source_language": "zh"},
                                  None, "", 1, [])
    assert ok and "前辈你好 → Hello, Senpai" in seen["notes"]


def test_an_extension_page_of_a_saved_title_gets_the_fixes_and_quick_read_does_not(isolated_db, monkeypatch):
    from services import extension_service, page_server
    sid = db.create_series("S")
    db.record_translation_memory(sid, "前辈你好", "Hello, Senpai")
    monkeypatch.setattr(extension_service, "page_extras", lambda *a, **k: {"character_notes": ""})
    bubbles = [{"source_text": "前辈你好"}]
    saved = page_server._page_extras({"series_id": sid, "source_language": "zh"}, {}, None, b"", bubbles, "zh")
    assert "Hello, Senpai" in saved["character_notes"]
    quick = page_server._page_extras(None, {}, None, b"", bubbles, "zh")
    assert "Hello, Senpai" not in quick.get("character_notes", "")
