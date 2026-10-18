"""Merging titles and moving chapters between titles
(services/title_restructure_service.py and its routes): comic pages with
their files and chapter labels, novel chapters in the raw-novel file, the
refusals, the preview fingerprint, the snapshot, and failures halfway."""
import contextlib
import io
import json
import os
import sqlite3
import threading
import time

import pytest

pytest.importorskip("PIL")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from PIL import Image

import background_jobs
import comic_chapters
from core import Line
import db
from api.api_config import ApiSettings
from api.server import create_app
from jobs import job_launch
from services import drama_service, novel_chapters_service
from services import title_restructure_service as trs
from services.service_errors import ConflictError, InvalidInputError
from sources import chapter_manifest, pipeline, store


def _png(n=0):
    buf = io.BytesIO()
    Image.new("RGB", (20 + n, 30), (n, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def _comic(**kw):
    return db.create_drama(title_en=kw.pop("title_en", "C"), media_type=kw.pop("media_type", "manhwa"),
                           source_language=kw.pop("source_language", "ko"), **kw)


def _novel(**kw):
    return db.create_drama(title_en=kw.pop("title_en", "N"), media_type="novel",
                           source_language=kw.pop("source_language", "zh"), **kw)


def _add(drama, chapter, n, seed=0):
    ref = comic_chapters.chapter_ref(*chapter) if chapter else None
    return pipeline.add_page_images(
        drama, [(_png(seed + i), ".png") for i in range(n)], chapter=ref)


def _files(drama):
    folder = os.path.join(db.drama_dir(drama), "pages")
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def _bytes_of(drama, filename):
    with open(os.path.join(db.drama_dir(drama), filename), "rb") as f:
        return f.read()


def _groups(drama):
    return comic_chapters.group_pages(db.list_pages(drama), comic_chapters.load(drama))


def _snapshots():
    root = os.path.join(db.LIBRARY_DIR, *trs.SNAPSHOT_SUBDIR)
    return sorted(os.listdir(root)) if os.path.isdir(root) else []


def _run(source, target, kind="move", chapter_ids=None, move_duplicates=False):
    plan = trs.preview(source, target, kind, chapter_ids=chapter_ids,
                       move_duplicates=move_duplicates)
    assert not plan["blocked"], plan["blocked"]
    return trs.apply(source, target, kind, chapter_ids=chapter_ids,
                     move_duplicates=move_duplicates, fingerprint=plan["fingerprint"],
                     confirm=True)


def _novel_text(drama, chapters):
    for title, text in chapters:
        pipeline.save_novel_text(drama, text, append=True, heading=title,
                                 url=f"https://example.com/{title}", source="ex")


def _novel_chapters(drama):
    listed = novel_chapters_service.list_chapters(drama)
    out = []
    for row in listed["chapters"]:
        text = novel_chapters_service.read_chapter(drama, row["number"], limit=50_000)["text"]
        out.append((row["title"], text))
    return listed["split"], out


# ---------------------------------------------------------------------------
# Comic chapters
# ---------------------------------------------------------------------------

def test_moving_a_comic_chapter_moves_rows_files_bubbles_and_label(isolated_db):
    a, b = _comic(title_en="A"), _comic(title_en="B")
    _add(b, ("c1", "Chapter 1", "src"), 2, seed=50)
    _add(a, ("c2", "Chapter 2", "src"), 2, seed=0)
    _add(a, ("c3", "Chapter 3", "src"), 3, seed=10)
    moving = [p for p in db.list_pages(a) if p["idx"] >= 2]
    contents = {p["id"]: _bytes_of(a, p["filename"]) for p in moving}
    db.save_bubbles(moving[0]["id"], [{"x": 1, "y": 2, "w": 3, "h": 4, "source_text": "안녕"}])
    comic_chapters.set_hidden(a, [moving[1]["filename"]], True)

    result = _run(a, b, chapter_ids=["src:c3"])

    assert result["moved_chapters"] == 1 and result["moved_pages"] == 3
    assert result["source_empty"] is False
    pages_b = db.list_pages(b)
    assert [p["id"] for p in pages_b][2:] == [p["id"] for p in moving]
    assert [p["idx"] for p in pages_b] == [0, 1, 2, 3, 4]
    for p in pages_b[2:]:
        assert _bytes_of(b, p["filename"]) == contents[p["id"]]
    assert db.load_bubbles(moving[0]["id"])[0]["source_text"] == "안녕"
    assert [g["title"] for g in _groups(b)] == ["Chapter 1", "Chapter 3"]
    assert comic_chapters.load(b)["hidden"] == [pages_b[3]["filename"]]
    # A keeps chapter 2, and only its two files.
    assert [p["idx"] for p in db.list_pages(a)] == [0, 1]
    assert [g["title"] for g in _groups(a)] == ["Chapter 2"]
    assert _files(a) == ["page_0000.png", "page_0001.png"]
    assert comic_chapters.load(a)["hidden"] == []


def test_a_rendered_page_keeps_its_typeset_file(isolated_db):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    page = db.list_pages(a)[0]
    name = f"pages/typeset_id{page['id']}.png"
    with open(os.path.join(db.drama_dir(a), name), "wb") as f:
        f.write(b"render")
    db.update_page(page["id"], rendered_filename=name)

    _run(a, b, "merge")

    moved = db.get_page(page["id"], b)
    assert moved["rendered_filename"] == name
    assert _bytes_of(b, name) == b"render"
    assert _files(a) == []


def test_merge_moves_every_chapter_and_leaves_an_empty_title(isolated_db):
    a = _comic(title_en="A", custom_tags="gl, favorite", source_url="https://example.com/a")
    b = _comic(title_en="B", custom_tags="gl")
    _add(a, ("c1", "Chapter 1", "src"), 2)
    _add(a, None, 1, seed=5)   # pages with no label: "Chapter unknown"

    result = _run(a, b, "merge")

    assert result["moved_pages"] == 3 and result["source_empty"] is True
    assert db.get_drama(a) is not None          # never deleted
    assert db.list_pages(a) == [] and _files(a) == []
    assert len(db.list_pages(b)) == 3
    assert [g["known"] for g in _groups(b)] == [True, False]
    drama_b = db.get_drama(b)
    assert drama_b["custom_tags"] == "gl, favorite"
    assert drama_b["source_url"] == "https://example.com/a"
    assert db.has_custom_tag(db.get_drama(a), db.MERGED_TAG)
    assert not db.has_custom_tag(drama_b, db.MERGED_TAG)


def test_merge_never_replaces_the_target_link(isolated_db):
    a = _comic(source_url="https://example.com/a")
    b = _comic(source_url="https://example.com/b")
    _add(a, ("c1", "Chapter 1", "src"), 1)
    _run(a, b, "merge")
    assert db.get_drama(b)["source_url"] == "https://example.com/b"
    assert db.get_drama(a)["source_url"] == "https://example.com/a"


def test_preview_writes_nothing(isolated_db):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 2)
    before = (db.list_pages(a), _files(a), comic_chapters.load(a))

    plan = trs.preview(a, b, "merge")

    assert plan["moving_pages"] == 2 and plan["moving_chapters"] == 1
    assert plan["source_empty_after"] is True
    assert (db.list_pages(a), _files(a), comic_chapters.load(a)) == before
    assert db.list_pages(b) == [] and _files(b) == []
    assert _snapshots() == []


def test_duplicate_chapters_are_skipped_unless_asked(isolated_db):
    a, b = _comic(), _comic()
    _add(b, ("c1", "Chapter 1", "src"), 1)
    _add(a, ("c1", "Chapter 1", "src"), 2, seed=3)
    _add(a, ("c2", "Chapter 2", "src"), 1, seed=6)

    plan = trs.preview(a, b, "merge")
    dup = next(c for c in plan["chapters"] if c["id"] == "src:c1")
    assert dup["duplicate"] is True and dup["moves"] is False
    assert plan["skipped_duplicates"] == 1 and plan["source_empty_after"] is False

    result = _run(a, b, "merge")
    assert result["moved_chapters"] == 1 and result["skipped_duplicates"] == 1
    assert [g["title"] for g in _groups(a)] == ["Chapter 1"]
    assert not db.has_custom_tag(db.get_drama(a), db.MERGED_TAG)   # still holds a chapter

    result = _run(a, b, "merge", move_duplicates=True)
    assert result["moved_pages"] == 2 and result["source_empty"] is True
    # Both copies are kept, as separate chapters.
    assert [g["title"] for g in _groups(b)] == ["Chapter 1", "Chapter 2", "Chapter 1"]
    assert len({c["key"] for c in comic_chapters.load(b)["chapters"]}) == 3


def test_a_change_after_the_preview_is_refused(isolated_db):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    plan = trs.preview(a, b, "merge")
    _add(a, ("c2", "Chapter 2", "src"), 1, seed=4)
    with pytest.raises(ConflictError):
        trs.apply(a, b, "merge", fingerprint=plan["fingerprint"], confirm=True)
    assert len(db.list_pages(a)) == 2 and db.list_pages(b) == []


def test_apply_needs_confirm(isolated_db):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    plan = trs.preview(a, b, "merge")
    with pytest.raises(InvalidInputError):
        trs.apply(a, b, "merge", fingerprint=plan["fingerprint"], confirm=False)
    assert db.list_pages(b) == []


@pytest.mark.parametrize("busy", ["source", "target"])
def test_refused_while_a_job_runs_on_either_title(isolated_db, monkeypatch, busy):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    plan = trs.preview(a, b, "merge")
    busy_id = a if busy == "source" else b
    monkeypatch.setattr(drama_service, "job_running_for_drama", lambda d, **_k: d == busy_id)
    assert trs.preview(a, b, "merge")["blocked"]
    with pytest.raises(ConflictError):
        trs.apply(a, b, "merge", fingerprint=plan["fingerprint"], confirm=True)
    assert len(db.list_pages(a)) == 1


@pytest.mark.parametrize("make_target, why", [
    (lambda: _novel(source_language="ko"), "kind"),
    (lambda: _comic(source_language="ja"), "language"),
    (lambda: db.create_drama(title_en="Audio", media_type="audio_drama", source_language="ko"),
     "kind"),
])
def test_refused_between_different_kinds_or_languages(isolated_db, make_target, why):
    a = _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    plan = trs.preview(a, make_target(), "merge")
    assert plan["blocked"] and plan["fingerprint"]


def test_refused_on_the_same_title(isolated_db):
    a = _comic()
    with pytest.raises(InvalidInputError):
        trs.preview(a, a, "merge")


def test_merge_refused_when_the_source_has_lines(isolated_db):
    a, b = _novel(), _novel()
    _novel_text(a, [("Ch 1", "一")])
    db.save_lines(a, [Line(idx=0, start=0.0, end=1.0, zh="一", en="One")])
    plan = trs.preview(a, b, "merge")
    assert plan["blocked"]
    # Moving a chapter is still fine: the lines stay where they are.
    assert not trs.preview(a, b, "move", chapter_ids=["1"])["blocked"]


def test_unknown_chapter_ids_are_refused(isolated_db):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    with pytest.raises(InvalidInputError):
        trs.preview(a, b, "move", chapter_ids=["src:nope"])
    with pytest.raises(InvalidInputError):
        trs.preview(a, b, "move", chapter_ids=[])


def test_a_snapshot_is_taken_first_and_only_the_newest_are_kept(isolated_db, monkeypatch):
    monkeypatch.setattr(trs, "KEEP_SNAPSHOTS", 2)
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    _add(a, ("c2", "Chapter 2", "src"), 1, seed=2)
    _add(a, ("c3", "Chapter 3", "src"), 1, seed=4)
    names = []
    for cid in ("src:c1", "src:c2", "src:c3"):
        names.append(_run(a, b, chapter_ids=[cid])["snapshot"])
    assert _snapshots() == sorted(names[1:])
    folder = os.path.join(db.LIBRARY_DIR, *trs.SNAPSHOT_SUBDIR, names[-1])
    conn = sqlite3.connect(os.path.join(folder, "library.db"))
    try:
        # Taken before the last move: chapter 3's page was still in A.
        assert conn.execute("SELECT COUNT(*) FROM pages WHERE drama_id = ?", (a,)).fetchone()[0] == 1
    finally:
        conn.close()
    assert "/" not in names[-1] and "\\" not in names[-1]


def test_failure_before_the_database_commit_changes_nothing(isolated_db, monkeypatch):
    a, b = _comic(), _comic()
    _add(b, ("c0", "Chapter 0", "src"), 1, seed=9)
    _add(a, ("c1", "Chapter 1", "src"), 2)
    before_a = (db.list_pages(a), _files(a), comic_chapters.load(a))
    before_b = (db.list_pages(b), _files(b), comic_chapters.load(b))

    def broken(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(db, "move_pages", broken)
    plan = trs.preview(a, b, "merge")
    with pytest.raises(Exception):
        trs.apply(a, b, "merge", fingerprint=plan["fingerprint"], confirm=True)

    assert (db.list_pages(a), _files(a), comic_chapters.load(a)) == before_a
    assert (db.list_pages(b), _files(b), comic_chapters.load(b)) == before_b


def test_failure_after_the_commit_loses_no_page(isolated_db, monkeypatch):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 2)
    real_remove = os.remove

    def stuck(path, *args, **kw):
        if os.path.dirname(os.path.dirname(path)) == db.drama_dir(a):
            raise PermissionError("in use")
        return real_remove(path, *args, **kw)
    monkeypatch.setattr(trs.os, "remove", stuck)

    result = _run(a, b, "merge")

    assert result["moved_pages"] == 2 and result["warnings"]
    for p in db.list_pages(b):
        assert os.path.exists(os.path.join(db.drama_dir(b), p["filename"]))
    assert len(_files(a)) == 2   # spare originals, never missing pages


def test_a_title_from_before_chapter_labels_and_an_old_database(isolated_db):
    """An old library: pages and bubbles without their later columns, titles
    without tags or links, and no chapters.json at all."""
    conn = sqlite3.connect(isolated_db.DB_PATH)
    try:
        for table, cols in {"pages": ("rev", "context_summary", "run_notes"),
                            "bubbles": ("font_category", "kind", "kind_confidence", "confidence",
                                        "language", "orientation", "panel_id", "include_sfx"),
                            "dramas": ("custom_tags", "source_url", "source_url_full")}.items():
            for col in cols:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        conn.execute("INSERT INTO dramas (id, title_en, media_type, source_language) "
                     "VALUES (40, 'Old A', 'manga', 'ja'), (41, 'Old B', 'manga', 'ja')")
        conn.execute("INSERT INTO pages (id, drama_id, idx, filename, width, height) "
                     "VALUES (7, 40, 0, 'pages/page_0000.png', 20, 30)")
        conn.execute("INSERT INTO bubbles (page_id, idx, x, y, w, h, source_text) "
                     "VALUES (7, 0, 1, 1, 5, 5, 'こんにちは')")
        conn.commit()
    finally:
        conn.close()
    os.makedirs(os.path.join(db.drama_dir(40), "pages"))
    with open(os.path.join(db.drama_dir(40), "pages", "page_0000.png"), "wb") as f:
        f.write(_png())
    isolated_db.init_db()

    plan = trs.preview(40, 41, "merge")
    assert [c["id"] for c in plan["chapters"]] == [comic_chapters.UNKNOWN_ID]
    _run(40, 41, "merge")

    assert db.get_page(7, 41)["filename"] == "pages/page_0000.png"
    assert db.load_bubbles(7)[0]["source_text"] == "こんにちは"
    assert _files(40) == []
    assert not os.path.exists(os.path.join(db.drama_dir(41), comic_chapters.MANIFEST_NAME)) or \
        comic_chapters.load(41)["chapters"] == []


def test_an_unreadable_label_file_blocks_the_move(isolated_db):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    with open(os.path.join(db.drama_dir(b), comic_chapters.MANIFEST_NAME), "w") as f:
        f.write("{not json")
    assert trs.preview(a, b, "merge")["blocked"]


def test_source_tracking_follows_the_moved_chapters(isolated_db):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    _add(a, ("c2", "Chapter 2", "src"), 1, seed=2)
    store.record_imported("src", "s1", "c1", a)
    store.record_imported("src", "s1", "c2", a)
    store.track_series("src", "s1", "Series", drama_id=a)

    _run(a, b, chapter_ids=["src:c1"])
    assert store.imported_chapter_ids("src", "s1", b) == {"c1"}
    assert store.imported_chapter_ids("src", "s1", a) == {"c2"}
    assert next(r for r in store.list_tracked_series() if r["series_id"] == "s1")["drama_id"] == a

    _run(a, b, "merge")
    assert store.imported_chapter_ids("src", "s1", b) == {"c1", "c2"}
    assert next(r for r in store.list_tracked_series() if r["series_id"] == "s1")["drama_id"] == b


# ---------------------------------------------------------------------------
# Novel chapters
# ---------------------------------------------------------------------------

def test_moving_a_novel_chapter_splices_both_raw_files(isolated_db):
    a, b = _novel(title_en="A"), _novel(title_en="B")
    _novel_text(a, [("Ch 1", "第一章的内容"), ("Ch 2", "第二章的内容"), ("Ch 3", "第三章")])
    _novel_text(b, [("Ch 0", "序章")])

    plan = trs.preview(a, b, "move", chapter_ids=["2"])
    assert [c["title"] for c in plan["chapters"] if c["moves"]] == ["Ch 2"]
    result = _run(a, b, chapter_ids=["2"])

    assert result["moved_chapters"] == 1
    split_a, chapters_a = _novel_chapters(a)
    split_b, chapters_b = _novel_chapters(b)
    assert split_a and split_b
    assert [t for t, _ in chapters_a] == ["Ch 1", "Ch 3"]
    assert [t for t, _ in chapters_b] == ["Ch 0", "Ch 2"]
    assert "第二章的内容" in chapters_b[1][1] and all("第二章" not in x for _, x in chapters_a)
    moved = chapter_manifest.load(b)["chapters"][1]
    assert moved["url"] == "https://example.com/Ch 2" or moved["url"].startswith("https://example.com/")
    assert moved["source"] == "ex"


@pytest.mark.parametrize("number, kept", [("1", ["Ch 2", "Ch 3"]), ("3", ["Ch 1", "Ch 2"])])
def test_moving_the_first_or_last_novel_chapter(isolated_db, number, kept):
    a, b = _novel(), _novel()
    _novel_text(a, [("Ch 1", "一"), ("Ch 2", "二"), ("Ch 3", "三")])
    _run(a, b, chapter_ids=[number])
    split_a, chapters_a = _novel_chapters(a)
    assert split_a and [t for t, _ in chapters_a] == kept
    with open(chapter_manifest.raw_path(a), "rb") as f:
        raw = f.read()
    assert not raw.startswith(b"\n") and not raw.startswith(b"\r")
    assert not raw.endswith(b"\n\n") and not raw.endswith(b"\r\n\r\n")


def test_merging_an_old_novel_without_a_chapter_list(isolated_db):
    a, b = _novel(title_en="Old"), _novel()
    with open(os.path.join(db.drama_dir(a), chapter_manifest.RAW_NOVEL_FILENAME), "w",
              encoding="utf-8") as f:
        f.write("很久以前的文字")
    _novel_text(b, [("Ch 1", "一")])

    result = _run(a, b, "merge")

    assert result["source_empty"] is True
    assert not os.path.exists(chapter_manifest.raw_path(a))
    split_b, chapters_b = _novel_chapters(b)
    assert split_b and len(chapters_b) == 2
    assert "很久以前的文字" in chapters_b[1][1]
    assert chapters_b[1][0]   # it gets a readable name, not "Earlier text"


def test_merging_a_novel_into_a_title_with_no_text_yet(isolated_db):
    a, b = _novel(), _novel()
    _novel_text(a, [("Ch 1", "一"), ("Ch 2", "二")])
    _run(a, b, "merge")
    split_b, chapters_b = _novel_chapters(b)
    assert split_b and [t for t, _ in chapters_b] == ["Ch 1", "Ch 2"]
    with open(chapter_manifest.raw_path(b), "rb") as f:
        assert not f.read().startswith(b"\n")


def test_duplicate_novel_chapters_are_found_by_link_or_text(isolated_db):
    a, b = _novel(), _novel()
    _novel_text(b, [("Ch 1", "一")])
    _novel_text(a, [("Ch 1", "一"), ("Ch 2", "二")])
    plan = trs.preview(a, b, "merge")
    assert [c["duplicate"] for c in plan["chapters"]] == [True, False]


def test_a_novel_chapter_already_in_the_translation_text_says_so(isolated_db):
    a, b = _novel(), _novel()
    _novel_text(a, [("Ch 1", "第一章的内容很长")])
    with open(os.path.join(db.drama_dir(a), "novel_narration_source.txt"), "w",
              encoding="utf-8") as f:
        f.write("Ch 1\n\n第一章的内容很长")
    plan = trs.preview(a, b, "move", chapter_ids=["1"])
    assert plan["chapters"][0]["in_translation"] is True
    assert any("translation" in s for s in plan["stays"])
    assert trs.preview(a, b, "merge")["blocked"]   # translation text can't be emptied


def test_novel_failure_halfway_puts_the_target_back(isolated_db, monkeypatch):
    a, b = _novel(), _novel()
    _novel_text(a, [("Ch 1", "一"), ("Ch 2", "二")])
    _novel_text(b, [("Ch 0", "零")])
    paths = [chapter_manifest.raw_path(d) for d in (a, b)] + \
        [chapter_manifest.manifest_path(d) for d in (a, b)]
    before = {}
    for p in paths:
        with open(p, "rb") as f:
            before[p] = f.read()
    real = trs._swap_in

    def fail_on_source(tmp, dest):
        if os.path.dirname(dest) == db.drama_dir(a):
            raise OSError("disk full")
        return real(tmp, dest)
    monkeypatch.setattr(trs, "_swap_in", fail_on_source)

    plan = trs.preview(a, b, "move", chapter_ids=["2"])
    with pytest.raises(Exception):
        trs.apply(a, b, "move", chapter_ids=["2"], fingerprint=plan["fingerprint"],
                  confirm=True)

    for p in paths:
        with open(p, "rb") as f:
            assert f.read() == before[p], p
    for d in (a, b):
        assert not [n for n in os.listdir(db.drama_dir(d)) if n.endswith(".part")]


# ---------------------------------------------------------------------------
# Running alongside other work
# ---------------------------------------------------------------------------

def test_jobs_and_deletes_are_refused_while_chapters_move(isolated_db, monkeypatch):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    real_snapshot = trs._take_snapshot
    seen = []

    def during_move(plan):
        for d in (a, b):
            with pytest.raises(ConflictError):
                job_launch.start_job(f"scanlate_{d}", lambda: None)
            with pytest.raises(ConflictError):
                drama_service.delete_drama(d, confirm=True, confirm_text="DELETE")
            with pytest.raises(ConflictError):
                trs.apply(a, b, "merge", fingerprint="x", confirm=True)
            seen.append(d)
        return real_snapshot(plan)
    monkeypatch.setattr(trs, "_take_snapshot", during_move)

    _run(a, b, "merge")

    assert seen == [a, b]
    # The holds end with the move: the next job on either title starts.
    for d in (a, b):
        assert job_launch.start_job(f"scanlate_{d}", lambda: None)
        _wait_for_job(f"scanlate_{d}")
        background_jobs.clear_job(f"scanlate_{d}")


def _wait_for_job(job_id):
    deadline = time.monotonic() + 5
    while background_jobs.is_running(job_id) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not background_jobs.is_running(job_id)


def test_a_move_is_refused_while_another_one_holds_the_title(isolated_db):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    plan = trs.preview(a, b, "merge")
    release = threading.Event()
    assert job_launch.start_job(f"restructure_{b}", release.wait)
    try:
        with pytest.raises(ConflictError):
            trs.apply(a, b, "merge", fingerprint=plan["fingerprint"], confirm=True)
    finally:
        release.set()
        _wait_for_job(f"restructure_{b}")
    assert len(db.list_pages(a)) == 1
    _run(a, b, "merge")
    assert len(db.list_pages(b)) == 1


@pytest.mark.parametrize("write", ["upload", "paste", "remove"])
def test_raw_novel_writers_take_the_page_lock(isolated_db, monkeypatch, write):
    from services import delete_service, novel_files_service
    n = _novel()
    _novel_text(n, [("Ch 1", "第一章")])
    path = os.path.join(db.drama_dir(n), chapter_manifest.RAW_NOVEL_FILENAME)
    with open(path, "rb") as f:
        before = f.read()
    real_lock = pipeline._page_lock
    inside = []

    @contextlib.contextmanager
    def watched(drama_id):
        with real_lock(drama_id):
            inside.append(drama_id)
            yield
            # Still under the lock: the file must already be in its new state.
            inside.append(os.path.exists(path) and open(path, "rb").read() != before)
    monkeypatch.setattr(pipeline, "_page_lock", watched)

    if write == "upload":
        novel_files_service.upload_raw_novel(n, "new.txt", io.BytesIO("新的文本".encode()))
    elif write == "paste":
        novel_files_service.save_raw_novel_text(n, "新的文本")
    else:
        delete_service.remove_raw_novel(n, confirm=True)

    assert inside == [n, write != "remove"]


def test_a_failed_label_move_leaves_no_label_on_the_freed_page_names(isolated_db, monkeypatch):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 2)
    _add(a, ("c2", "Chapter 2", "src"), 1, seed=5)
    moved = {p["filename"] for p in db.list_pages(a)[:2]}

    def broken(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(comic_chapters, "move_labels", broken)

    result = _run(a, b, chapter_ids=["src:c1"])

    assert trs._LABELS_NOT_MOVED in result["warnings"]
    labelled = {f for c in comic_chapters.load(a)["chapters"] for f in c["files"]}
    assert not labelled & moved
    assert [g["id"] for g in _groups(a)] == ["src:c2"]
    # The next import reuses a freed name; it must not land in the moved chapter.
    _add(a, ("c3", "Chapter 3", "src"), 1, seed=9)
    assert [g["id"] for g in _groups(a)] == ["src:c2", "src:c3"]


def test_moving_part_of_a_split_chapter_keeps_its_record_on_both_titles(isolated_db):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    _add(a, ("c2", "Chapter 2", "src"), 1, seed=2)
    _add(a, ("c1", "Chapter 1", "src"), 1, seed=4)
    assert [g["id"] for g in _groups(a)] == ["src:c1", "src:c2", "src:c1~2"]
    store.record_imported("src", "s1", "c1", a)
    store.record_imported("src", "s1", "c2", a)

    _run(a, b, chapter_ids=["src:c1~2"])

    assert store.imported_chapter_ids("src", "s1", a) == {"c1", "c2"}
    assert store.imported_chapter_ids("src", "s1", b) == {"c1"}


def test_import_records_keep_their_own_series(isolated_db):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 1)
    store.record_imported("src", "s1", "c1", a)
    store.record_imported("src", "s2", "c1", a)

    _run(a, b, chapter_ids=["src:c1"])

    assert store.imported_chapter_ids("src", "s1", b) == {"c1"}
    assert store.imported_chapter_ids("src", "s2", b) == {"c1"}
    assert store.imported_chapter_ids("src", "s1", a) == set()


def test_a_novel_move_says_its_import_records_stay(isolated_db):
    a, b = _novel(), _novel()
    _novel_text(a, [("Ch 1", "第一章"), ("Ch 2", "第二章")])
    store.record_imported("ex", "s1", "1", a)

    result = _run(a, b, chapter_ids=["2"])

    assert trs._NOVEL_TRACKING in result["warnings"]
    assert store.imported_chapter_ids("ex", "s1", a) == {"1"}

    c, d = _novel(), _novel()
    _novel_text(c, [("Ch 1", "第一章"), ("Ch 2", "第二章")])
    assert trs._NOVEL_TRACKING not in _run(c, d, chapter_ids=["2"])["warnings"]


@pytest.mark.parametrize("fail", ["target_gone", "tag", "prune"])
def test_a_failure_after_the_move_is_a_warning(isolated_db, monkeypatch, fail):
    a, b = _comic(), _comic()
    _add(a, ("c1", "Chapter 1", "src"), 2)

    def broken(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")
    if fail == "target_gone":
        real_get = db.get_drama
        monkeypatch.setattr(trs.db, "get_drama",
                            lambda d, *x, **k: None if d == b and seen else real_get(d, *x, **k))
        real_move = db.move_pages
        seen = []

        def then_gone(*args):
            seen.append(1)   # deleted elsewhere right after the pages moved
            return real_move(*args)
        monkeypatch.setattr(trs.db, "move_pages", then_gone)
    elif fail == "tag":
        monkeypatch.setattr(trs.db, "set_custom_tag", broken)
    else:
        monkeypatch.setattr(trs, "_prune_snapshots", broken)

    result = _run(a, b, "merge")

    assert result["moved_pages"] == 2
    assert len(db.list_pages(b)) == 2
    if fail == "prune":
        assert result["warnings"] == []
    else:
        assert trs._TITLE_FIELDS_NOT_MERGED in result["warnings"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def test_routes_list_preview_and_apply(client):
    a, b = _comic(title_en="A"), _comic(title_en="B")
    _novel()   # not a target for a comic
    _add(a, ("c1", "Chapter 1", "src"), 2)

    r = client.get(f"/api/dramas/{a}/restructure")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "comic"
    assert [c["id"] for c in body["chapters"]] == ["src:c1"]
    assert [t["id"] for t in body["targets"]] == [b]

    req = {"target_id": b, "kind": "merge"}
    r = client.post(f"/api/dramas/{a}/restructure/preview", json=req)
    assert r.status_code == 200, r.text
    plan = r.json()
    assert "pages/" not in json.dumps(plan)

    r = client.post(f"/api/dramas/{a}/restructure", json={**req, "fingerprint": "x" * 64,
                                                           "confirm": True})
    assert r.status_code == 409
    r = client.post(f"/api/dramas/{a}/restructure", json={**req, "fingerprint": plan["fingerprint"]})
    assert r.status_code in (400, 422)
    r = client.post(f"/api/dramas/{a}/restructure", json={**req, "fingerprint": plan["fingerprint"],
                                                           "confirm": True})
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["moved_pages"] == 2 and done["source_empty"] is True
    assert os.sep not in done["snapshot"]


def test_routes_404_for_a_missing_title(client):
    b = _comic()
    assert client.get("/api/dramas/9999/restructure").status_code == 404
    r = client.post("/api/dramas/9999/restructure/preview", json={"target_id": b, "kind": "merge"})
    assert r.status_code == 404
