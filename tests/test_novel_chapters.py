"""Saved raw chapters (services/novel_chapters_service.py, the manifest in
sources/chapter_manifest.py and GET /api/novel/dramas/{id}/raw-novel/chapters).
Real-shaped files written through the import pipeline, plus old titles with
no manifest. No network or models."""

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from services import novel_chapters_service as svc
from services.service_errors import InvalidInputError, NotFoundError
from sources import chapter_manifest, pipeline
from sources.models import ChapterInfo

LOCAL = {"X-Baihe-Local": "1"}


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False, headers=LOCAL)


def _drama():
    return db.create_drama(title_en="D")


def _chapter(n, title=None):
    return ChapterInfo(source="xbanxia", series_id="s1", chapter_id=f"c{n}",
                       title=title if title is not None else f"第{n}章 标题")


def _import(did, n, body=None, title=None):
    err = pipeline._append_chapter_text("xbanxia", _chapter(n, title), did,
                                        body if body is not None else f"这是第{n}章。\n\n第二段。")
    assert err == ""


def _raw(did):
    return os.path.join(db.DRAMAS_DIR, str(did), "raw_novel_context.txt")


def _translation(did, text):
    with open(os.path.join(db.drama_dir(did), "novel_narration_source.txt"), "w",
              encoding="utf-8") as f:
        f.write(text)


def test_imported_chapters_are_listed_with_titles_and_sources(isolated_db):
    did = _drama()
    for n in (1, 2, 3):
        _import(did, n)
    out = svc.list_chapters(did)
    assert out["split"] and out["total"] == 3
    rows = out["chapters"]
    assert [r["number"] for r in rows] == [1, 2, 3]
    assert rows[1]["title"] == "第2章 标题" and rows[1]["source"] == "xbanxia"
    assert rows[0]["imported_at"].endswith("Z") and not rows[0]["unsplit"]
    assert out["char_count"] == sum(r["chars"] for r in rows)
    assert out["in_translation"] == 0 and out["translation_chars"] == 0


def test_chapter_text_is_the_block_for_that_chapter_only(isolated_db):
    did = _drama()
    _import(did, 1)
    _import(did, 2, body="Second chapter in English.")
    got = svc.read_chapter(did, 2)
    assert got["text"] == "第2章 标题\n\nSecond chapter in English."
    assert got["next_offset"] is None and got["chars"] == len(got["text"])
    assert "第1章" not in got["text"]


def test_slices_page_through_a_chapter_and_stop_at_its_end(isolated_db):
    did = _drama()
    _import(did, 1, body="字" * 25)
    first = svc.read_chapter(did, 1, offset=0, limit=10)
    assert len(first["text"]) == 10 and first["next_offset"] == 10
    last = svc.read_chapter(did, 1, offset=first["chars"] - 3, limit=50)
    assert len(last["text"]) == 3 and last["next_offset"] is None


def test_old_file_without_manifest_is_one_unsplit_block(isolated_db):
    did = _drama()
    with open(os.path.join(db.drama_dir(did), "raw_novel_context.txt"), "w",
              encoding="utf-8") as f:
        f.write("プロローグ\n\n本文。" * 3)
    out = svc.list_chapters(did)
    assert not out["split"] and out["total"] == 1
    row = out["chapters"][0]
    assert row["title"] == "Unsplit text" and row["unsplit"] and row["chars"] == out["char_count"]
    assert svc.read_chapter(did, 1)["text"].startswith("プロローグ")


def test_missing_and_empty_files(isolated_db):
    did = _drama()
    out = svc.list_chapters(did)
    assert not out["present"] and out["total"] == 0 and out["chapters"] == []
    with pytest.raises(NotFoundError):
        svc.read_chapter(did, 1)
    open(os.path.join(db.drama_dir(did), "raw_novel_context.txt"), "w").close()
    out = svc.list_chapters(did)
    assert out["present"] and out["char_count"] == 0


def test_chapters_added_to_an_old_file_keep_the_earlier_text_as_a_block(isolated_db):
    did = _drama()
    with open(os.path.join(db.drama_dir(did), "raw_novel_context.txt"), "w",
              encoding="utf-8") as f:
        f.write("old pasted text\n\nmore")
    _import(did, 1)
    rows = svc.list_chapters(did)["chapters"]
    assert [r["unsplit"] for r in rows] == [True, False]
    assert rows[0]["title"] == "Earlier text (not split)"
    assert svc.read_chapter(did, 1)["text"] == "old pasted text\n\nmore"
    assert svc.read_chapter(did, 2)["text"].startswith("第1章")


def test_url_imports_are_recorded_by_heading(isolated_db):
    did = _drama()
    pipeline.save_novel_text(did, "Body one", append=True, heading="Heading 1")
    pipeline.save_novel_text(did, "Body two", append=True, heading="")
    rows = svc.list_chapters(did)["chapters"]
    assert [r["title"] for r in rows] == ["Heading 1", ""]
    assert svc.read_chapter(did, 1)["text"] == "Heading 1\n\nBody one"


def test_stale_manifest_is_ignored_when_the_file_changes(isolated_db):
    did = _drama()
    _import(did, 1)
    with open(_raw(did), "a", encoding="utf-8") as f:
        f.write("\n\nedited by hand")
    out = svc.list_chapters(did)
    assert not out["split"] and out["chapters"][0]["title"] == "Unsplit text"


def test_replacing_or_removing_the_raw_novel_drops_the_manifest(isolated_db):
    from services import novel_files_service
    did = _drama()
    _import(did, 1)
    assert os.path.exists(chapter_manifest.manifest_path(did))
    novel_files_service.save_raw_novel_text(did, "fresh paste")
    assert not os.path.exists(chapter_manifest.manifest_path(did))
    assert not svc.list_chapters(did)["split"]


def test_a_corrupt_manifest_falls_back(isolated_db):
    did = _drama()
    _import(did, 1)
    with open(chapter_manifest.manifest_path(did), "w") as f:
        f.write("{not json")
    assert not svc.list_chapters(did)["split"]
    with open(chapter_manifest.manifest_path(did), "w") as f:
        f.write('{"version": 1, "size": %d, "chapters": [{"start": 5, "length": 9999999, '
                '"chars": 1}]}' % os.path.getsize(_raw(did)))
    assert not svc.list_chapters(did)["split"]


def test_in_translation_marks_copied_chapters(isolated_db):
    from services import novel_attach_service
    did = _drama()
    for n in (1, 2, 3):
        _import(did, n)
    _translation(did, "unrelated text")
    assert svc.list_chapters(did)["in_translation"] == 0
    novel_attach_service.attach_from_sources(did, "replace")
    out = svc.list_chapters(did)
    assert out["in_translation"] == 3 and all(r["in_translation"] for r in out["chapters"])
    assert out["translation_chars"] > 0
    assert svc.read_chapter(did, 2)["in_translation"]


def test_in_translation_is_per_chapter(isolated_db):
    did = _drama()
    for n in (1, 2):
        _import(did, n)
    _translation(did, svc.read_chapter(did, 2)["text"])
    flags = [r["in_translation"] for r in svc.list_chapters(did)["chapters"]]
    assert flags == [False, True]


def test_large_file_is_paged_and_sliced_without_loading_it_whole(isolated_db):
    did = _drama()
    for n in range(1, 61):
        _import(did, n, body="段落" * 50)
    page = svc.list_chapters(did, offset=50, limit=20)
    assert page["total"] == 60 and [r["number"] for r in page["chapters"]] == list(range(51, 61))
    with pytest.raises(InvalidInputError):
        svc.list_chapters(did, limit=svc.MAX_PAGE + 1)
    with pytest.raises(InvalidInputError):
        svc.read_chapter(did, 1, limit=svc.MAX_SLICE_CHARS + 1)


def test_huge_unsplit_text_is_counted_and_sliced_in_ranges(isolated_db):
    did = _drama()
    with open(os.path.join(db.drama_dir(did), "raw_novel_context.txt"), "w",
              encoding="utf-8") as f:
        f.write("字" * 3_000_000)
    out = svc.list_chapters(did)
    assert out["char_count"] == 3_000_000
    got = svc.read_chapter(did, 1, offset=2_999_990, limit=svc.MAX_SLICE_CHARS)
    assert len(got["text"]) == 10 and got["next_offset"] is None
    assert len(svc.read_chapter(did, 1)["text"]) == svc.DEFAULT_SLICE_CHARS


def test_mixed_language_titles_and_text_round_trip(isolated_db):
    did = _drama()
    _import(did, 1, body="English line.\n\n日本語の行。\n\n한국어 줄.", title="Ch.1 第一章 제1장")
    got = svc.read_chapter(did, 1)
    assert got["title"] == "Ch.1 第一章 제1장"
    assert got["text"].endswith("한국어 줄.")


# ---- API -------------------------------------------------------------

def test_routes_return_rows_and_text_without_paths(client):
    did = _drama()
    for n in (1, 2):
        _import(did, n)
    body = client.get(f"/api/novel/dramas/{did}/raw-novel/chapters").json()
    assert body["total"] == 2 and body["chapters"][0]["title"] == "第1章 标题"
    text = client.get(f"/api/novel/dramas/{did}/raw-novel/chapters/2?limit=5").json()
    assert text["text"] == "第2章 标题"[:5] and text["next_offset"] == 5
    for payload in (body, text):
        flat = str(payload)
        assert db.DRAMAS_DIR not in flat and "raw_novel" not in flat and "/" not in flat.replace(
            "xbanxia", "")


def test_routes_bound_their_parameters_and_404(client):
    did = _drama()
    _import(did, 1)
    base = f"/api/novel/dramas/{did}/raw-novel/chapters"
    assert client.get(f"{base}?limit=0").status_code == 422
    assert client.get(f"{base}?limit={svc.MAX_PAGE + 1}").status_code == 422
    assert client.get(f"{base}?offset=-1").status_code == 422
    assert client.get(f"{base}/1?limit={svc.MAX_SLICE_CHARS + 1}").status_code == 422
    assert client.get(f"{base}/9").status_code == 404
    assert client.get("/api/novel/dramas/9999/raw-novel/chapters").status_code == 404


# ---- confinement, bounded work, manifest integrity -----------------------

def _symlink(link, target):
    os.makedirs(os.path.dirname(link), exist_ok=True)
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlinks are not supported here")


def _outside(tmp_path, text):
    path = tmp_path / "outside.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_raw_text_symlink_leaving_the_folder_reads_as_absent(isolated_db, tmp_path):
    did = _drama()
    _symlink(_raw(did), _outside(tmp_path, "secret outside text"))
    out = svc.list_chapters(did)
    assert not out["present"] and out["chapters"] == []
    with pytest.raises(NotFoundError):
        svc.read_chapter(did, 1)


def test_manifest_symlink_is_not_trusted(isolated_db, tmp_path):
    did = _drama()
    _import(did, 1)
    _import(did, 2)
    with open(chapter_manifest.manifest_path(did), encoding="utf-8") as f:
        saved = f.read()
    os.remove(chapter_manifest.manifest_path(did))
    _symlink(chapter_manifest.manifest_path(did), _outside(tmp_path, saved))
    assert chapter_manifest.load(did) is None
    assert not svc.list_chapters(did)["split"]


def test_translation_symlink_is_not_read(isolated_db, tmp_path):
    did = _drama()
    _import(did, 1)
    text = svc.read_chapter(did, 1)["text"]
    _symlink(os.path.join(db.drama_dir(did), "novel_narration_source.txt"),
             _outside(tmp_path, text))
    out = svc.list_chapters(did)
    assert out["translation_chars"] == 0 and not out["chapters"][0]["in_translation"]


def _big_title(did, count=2000):
    db.drama_dir(did)
    chapters, parts, pos = [], [], 0
    for n in range(1, count + 1):
        block = f"第{n}章\n\n正文{n}。".encode("utf-8")
        sep = b"\n\n" if parts else b""
        pos += len(sep)
        chapters.append({"title": f"第{n}章", "source": "x", "imported_at": "", "start": pos,
                         "length": len(block), "chars": 10, "unsplit": False})
        parts.append(sep + block)
        pos += len(block)
    with open(_raw(did), "wb") as f:
        f.write(b"".join(parts))
    chapter_manifest._save(did, pos, chapters)


def test_a_page_does_bounded_work_and_a_repeat_does_none(isolated_db, monkeypatch):
    did = _drama()
    _big_title(did)
    _translation(did, "无关的文字" * 1000)
    calls = {"slice": 0, "tail": 0, "count": 0, "open": 0}

    def counted(name, fn):
        def wrapper(*a, **k):
            calls[name] += 1
            return fn(*a, **k)
        return wrapper

    real_open = open

    def counting_open(*a, **k):
        calls["open"] += 1
        return real_open(*a, **k)

    monkeypatch.setattr(chapter_manifest, "slice_text", counted("slice", chapter_manifest.slice_text))
    monkeypatch.setattr(chapter_manifest, "tail_text", counted("tail", chapter_manifest.tail_text))
    monkeypatch.setattr(chapter_manifest, "count_chars", counted("count", chapter_manifest.count_chars))
    monkeypatch.setattr(svc, "open", counting_open, raising=False)

    first = svc.list_chapters(did, offset=500, limit=20)
    assert first["total"] == 2000 and len(first["chapters"]) == 20
    assert calls["slice"] == 20 and calls["tail"] == 20 and calls["count"] == 0
    assert calls["open"] == 2   # the raw file and the translation text, once each

    before = dict(calls)
    svc.list_chapters(did, offset=500, limit=20)
    assert calls["slice"] == before["slice"] and calls["tail"] == before["tail"]
    assert calls["count"] == 0


def test_unsplit_file_is_decoded_once_until_it_changes(isolated_db, monkeypatch):
    did = _drama()
    db.drama_dir(did)
    with open(_raw(did), "w", encoding="utf-8") as f:
        f.write("字" * 5000)
    seen = []
    real = chapter_manifest.index_block
    monkeypatch.setattr(chapter_manifest, "index_block",
                        lambda *a, **k: seen.append(1) or real(*a, **k))
    for _ in range(3):
        assert svc.list_chapters(did)["char_count"] == 5000
    svc.read_chapter(did, 1)
    assert len(seen) == 1
    with open(_raw(did), "a", encoding="utf-8") as f:
        f.write("字" * 10)
    assert svc.list_chapters(did)["char_count"] == 5010 and len(seen) == 2


def test_retry_of_an_already_recorded_chapter_keeps_the_list(isolated_db):
    did = _drama()
    for n in (1, 2, 3):
        _import(did, n)
    known = chapter_manifest.load(did)
    second = known["chapters"][1]
    # A retry noting chapter 2 after chapter 3 was written: neither size matches.
    chapter_manifest.record(did, pre_size=second["start"], post_size=second["start"] + second["length"],
                            content_start=second["start"], content_length=second["length"],
                            content_chars=second["chars"], title=second["title"], source="xbanxia")
    after = chapter_manifest.load(did)
    assert after == known


class _CountingFile:
    """Binary file that adds the bytes read through it to `tally`."""

    def __init__(self, f, tally):
        self._f, self._tally = f, tally

    def read(self, n=-1):
        data = self._f.read(n)
        self._tally[0] += len(data)
        return data

    def __getattr__(self, name):
        return getattr(self._f, name)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._f.close()


def _count_reads(monkeypatch):
    tally = [0]
    real_open = open
    monkeypatch.setattr(svc, "open", lambda p, m="r", *a, **k: _CountingFile(
        real_open(p, m, *a, **k), tally) if "b" in m else real_open(p, m, *a, **k), raising=False)
    return tally


def test_offset_past_the_end_reads_nothing(isolated_db, monkeypatch):
    did = _drama()
    db.drama_dir(did)
    with open(_raw(did), "w", encoding="utf-8") as f:
        f.write("字" * 100_000)
    svc.list_chapters(did)
    tally = _count_reads(monkeypatch)
    for offset in (100_000, 10**12):
        got = svc.read_chapter(did, 1, offset=offset)
        assert got["text"] == "" and got["next_offset"] is None
    assert tally[0] == 0


def test_paging_a_five_million_char_block_reads_each_byte_about_once(isolated_db, monkeypatch):
    did = _drama()
    db.drama_dir(did)
    line = "字" * 49 + "\n"
    with open(_raw(did), "w", encoding="utf-8", newline="") as f:
        f.write(line * 100_000)
    size = os.path.getsize(_raw(did))
    tally = _count_reads(monkeypatch)
    offset, got_chars, pages = 0, 0, 0
    while offset is not None:
        got = svc.read_chapter(did, 1, offset=offset, limit=svc.MAX_SLICE_CHARS)
        got_chars += len(got["text"])
        offset, pages = got["next_offset"], pages + 1
    assert got_chars == 5_000_000 and pages == 100
    assert tally[0] < 3 * size


def test_deep_slices_match_a_plain_decode(isolated_db):
    did = _drama()
    db.drama_dir(did)
    body = ("甲乙丙\r\n丁戊\r己庚辛壬\n" * 30_000).encode("utf-8")
    body = body[:-1] + b"\xe4\xb8"   # ends inside a character
    with open(_raw(did), "wb") as f:
        f.write(body)
    expected = body.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    assert svc.list_chapters(did)["char_count"] == len(expected)
    for offset in (0, 19_999, 20_000, 65_537, 123_457, len(expected) - 3):
        got = svc.read_chapter(did, 1, offset=offset, limit=1000)
        assert got["text"] == expected[offset:offset + 1000], offset


def test_a_manifest_that_cannot_be_dropped_blocks_the_replace(isolated_db, monkeypatch):
    from services import novel_files_service
    from services.service_errors import ConflictError
    did = _drama()
    _import(did, 1)
    before = open(_raw(did), "rb").read()
    real_remove = os.remove

    def locked(path):
        if os.path.basename(path) == chapter_manifest.MANIFEST_FILENAME:
            raise PermissionError("in use")
        return real_remove(path)

    monkeypatch.setattr(chapter_manifest.os, "remove", locked)
    assert chapter_manifest.drop(did) is False
    with pytest.raises(ConflictError) as err:
        novel_files_service.save_raw_novel_text(did, "fresh paste")
    assert str(db.DRAMAS_DIR) not in str(err.value)
    assert open(_raw(did), "rb").read() == before


def test_a_manifest_that_cannot_be_dropped_blocks_the_removal(isolated_db, monkeypatch):
    from services import delete_service
    from services.service_errors import ConflictError
    did = _drama()
    _import(did, 1)
    real_remove = os.remove

    def locked(path):
        if os.path.basename(path) == chapter_manifest.MANIFEST_FILENAME:
            raise PermissionError("in use")
        return real_remove(path)

    monkeypatch.setattr(chapter_manifest.os, "remove", locked)
    with pytest.raises(ConflictError) as err:
        delete_service.remove_raw_novel(did, confirm=True)
    assert str(db.DRAMAS_DIR) not in str(err.value)
    assert os.path.exists(_raw(did))


def test_crlf_files_read_back_whole_with_the_right_next_offset(isolated_db, monkeypatch):
    # On Windows the file is written with os.linesep, so each newline is two bytes on disk.
    monkeypatch.setattr(os, "linesep", "\r\n")
    did = _drama()
    body = "第一行。\n第二行。\n\n第三行。"
    _import(did, 1, body=body, title="T")
    _import(did, 2, body="Second.\nchapter.", title="U")
    with open(_raw(did), "rb") as f:
        assert b"\r\n" in f.read()
    full = svc.read_chapter(did, 1, offset=0, limit=500)
    assert full["text"] == "T\n\n" + body and full["next_offset"] is None
    assert full["chars"] == len(full["text"])
    part = svc.read_chapter(did, 1, offset=0, limit=10)
    assert part["next_offset"] == 10
    assert svc.read_chapter(did, 1, offset=10, limit=500)["text"] == full["text"][10:]
    assert svc.read_chapter(did, 2, offset=0, limit=500)["text"] == "U\n\nSecond.\nchapter."
