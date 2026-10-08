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
