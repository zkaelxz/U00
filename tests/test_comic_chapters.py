"""Chapter labels and hidden pages for comic titles (comic_chapters,
comic_chapters_service, the pages response, the Scanlate run scope)."""
import io
import json
import os
import types

import pytest
from tests.saved_settings import patch_setting

pytest.importorskip("PIL")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from PIL import Image

import comic_chapters
from api.api_config import ApiSettings
from api.server import create_app
from sources import ladder, pipeline


def _png(n=0):
    buf = io.BytesIO()
    Image.new("RGB", (20 + n, 30), (n, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def drama(isolated_db):
    return isolated_db.create_drama(title_en="C", media_type="manhwa")


def _add(drama, chapter, n):
    ref = comic_chapters.chapter_ref(*chapter) if chapter else None
    return pipeline.add_page_images(drama, [(_png(i), ".png") for i in range(n)], chapter=ref)


def _pages(client, drama):
    r = client.get(f"/api/scanlate/dramas/{drama}/pages")
    assert r.status_code == 200
    return r.json()


def test_chapters_are_grouped_in_import_order(client, drama):
    _add(drama, ("c10", "Chapter 10", "src"), 2)
    _add(drama, ("c11", "Chapter 11", "src"), 3)
    body = _pages(client, drama)
    assert [(c["title"], c["first_page"], c["page_count"]) for c in body["chapters"]] == [
        ("Chapter 10", 1, 2), ("Chapter 11", 3, 3)]
    assert all("host" not in c for c in body["chapters"])
    p = body["pages"]
    assert [x["chapter_page"] for x in p] == [1, 2, 1, 2, 3]
    assert p[0]["chapter_id"] == body["chapters"][0]["id"] == "src:c10"
    assert body["page_count"] == 5 and body["hidden_count"] == 0


def test_flat_shape_is_unchanged(client, drama):
    _add(drama, ("c1", "One", "src"), 1)
    page = _pages(client, drama)["pages"][0]
    assert {"id", "ordinal", "width", "height", "has_rendered", "has_regions",
            "image_version"} <= set(page)


def test_old_title_is_one_unknown_group(client, drama, isolated_db):
    _add(drama, None, 2)
    body = _pages(client, drama)
    assert [(c["id"], c["known"], c["page_count"]) for c in body["chapters"]] == [("unknown", False, 2)]
    assert all(p["chapter_id"] == "unknown" for p in body["pages"])


@pytest.mark.parametrize("junk", ["{not json", "[]", '{"chapters": 3, "hidden": "x"}', ""])
def test_broken_manifest_never_fails_the_page_list(client, drama, junk):
    _add(drama, ("c1", "One", "src"), 1)
    import db
    with open(os.path.join(db.drama_dir(drama), comic_chapters.MANIFEST_NAME), "w") as f:
        f.write(junk)
    body = _pages(client, drama)
    assert body["page_count"] == 1 and body["chapters"][0]["known"] is False


def test_unlabelled_pages_after_a_chapter_form_their_own_group(client, drama):
    _add(drama, ("c1", "One", "src"), 2)
    _add(drama, None, 1)
    assert [(c["known"], c["page_count"]) for c in _pages(client, drama)["chapters"]] == [
        (True, 2), (False, 1)]


def test_hide_and_restore_pages(client, drama):
    _add(drama, ("c1", "One", "src"), 3)
    ids = [p["id"] for p in _pages(client, drama)["pages"]]
    url = f"/api/scanlate/dramas/{drama}/pages/visibility"
    r = client.post(url, json={"hidden": True, "page_ids": [ids[2]]})
    assert r.status_code == 200 and r.json() == {"changed": 1, "hidden_count": 1}
    body = _pages(client, drama)
    assert [p["hidden"] for p in body["pages"]] == [False, False, True]
    assert body["page_count"] == 3 and body["chapters"][0]["hidden_count"] == 1
    r = client.post(url, json={"hidden": False, "page_ids": [ids[2]]})
    assert r.json()["hidden_count"] == 0
    assert not any(p["hidden"] for p in _pages(client, drama)["pages"])


def test_hide_chapter_edges(client, drama):
    _add(drama, ("c1", "One", "src"), 4)
    _add(drama, ("c2", "Two", "src"), 3)
    url = f"/api/scanlate/dramas/{drama}/pages/visibility"
    cid = _pages(client, drama)["chapters"][1]["id"]
    assert client.post(url, json={"hidden": True, "chapter_id": cid, "edge": "first",
                                  "count": 1}).json()["changed"] == 1
    client.post(url, json={"hidden": True, "chapter_id": cid, "edge": "last", "count": 2})
    flags = [p["hidden"] for p in _pages(client, drama)["pages"]]
    assert flags == [False] * 4 + [True] * 3


def test_hide_rejects_bad_requests(client, drama, isolated_db):
    _add(drama, ("c1", "One", "src"), 1)
    other = isolated_db.create_drama(title_en="O", media_type="manga")
    _add(other, None, 1)
    foreign = isolated_db.list_pages(other)[0]["id"]
    url = f"/api/scanlate/dramas/{drama}/pages/visibility"
    assert client.post(url, json={"hidden": True, "page_ids": [foreign]}).status_code == 404
    assert client.post(url, json={"hidden": True}).status_code in (400, 422)
    assert client.post(url, json={"hidden": True, "page_ids": [1], "chapter_id": "x",
                                  "edge": "all"}).status_code in (400, 422)
    assert client.post(url, json={"hidden": True, "chapter_id": "nope",
                                  "edge": "all"}).status_code == 404
    assert client.post(f"/api/scanlate/dramas/999/pages/visibility",
                       json={"hidden": True, "page_ids": [1]}).status_code == 404


def test_responses_carry_no_paths(client, drama):
    _add(drama, ("c1", "One", "src"), 1)
    import db
    text = client.get(f"/api/scanlate/dramas/{drama}/pages").text
    assert db.LIBRARY_DIR not in text and "chapters.json" not in text and "pages/" not in text


def test_run_scope_counts(drama):
    from services import comic_chapters_service as svc
    _add(drama, ("c1", "One", "src"), 3)
    _add(drama, ("c2", "Two", "src"), 2)
    import db
    ids = [p["id"] for p in db.list_pages(drama)]
    assert svc.run_page_ids(drama) == ids
    assert svc.run_page_ids(drama, "src:c2") == ids[3:]
    svc.set_visibility(drama, True, page_ids=[ids[0], ids[4]])
    assert svc.run_page_ids(drama) == ids[1:4]
    assert svc.run_page_ids(drama, "src:c2") == [ids[3]]
    svc.set_visibility(drama, False, page_ids=[ids[0]])
    assert svc.run_page_ids(drama)[0] == ids[0]


def test_run_chapter_scope_is_validated(drama, monkeypatch):
    from services import scanlate_run_service as run
    _add(drama, ("c1", "One", "src"), 2)
    import db
    pid = db.list_pages(drama)[0]["id"]
    from services.service_errors import InvalidInputError, NotFoundError
    with pytest.raises(InvalidInputError):
        run.start_run(drama, mode="page", page_id=pid, chapter_id="src:c1")
    with pytest.raises(NotFoundError):
        run.start_run(drama, chapter_id="src:nope")


def test_run_starts_only_visible_pages_of_the_chapter(drama, monkeypatch):
    from services import comic_chapters_service as svc
    from services import scanlate_run_service as run
    _add(drama, ("c1", "One", "src"), 2)
    _add(drama, ("c2", "Two", "src"), 3)
    import db
    ids = [p["id"] for p in db.list_pages(drama)]
    svc.set_visibility(drama, True, page_ids=[ids[4]])
    seen = {}
    monkeypatch.setattr(run, "_require_ocr_backend", lambda d: None)
    monkeypatch.setattr(run, "_build_engine", lambda n: object())
    patch_setting(monkeypatch, "default_engine", "x")
    monkeypatch.setattr(run.pages_svc, "start_drama_job",
                        lambda d, fn, jid, *a, **k: seen.update(targets=a[2]) or {"job_id": jid})
    run.start_run(drama, chapter_id="src:c2")
    assert seen["targets"] == ids[2:4]
    run.start_run(drama)
    assert seen["targets"] == ids[:4]


def test_import_job_records_chapters_in_order(isolated_db, drama, monkeypatch):
    import background_jobs
    from sources.models import ChapterInfo
    monkeypatch.setattr(ladder, "check_terms", lambda *a, **k: None)

    class Client:
        cache = status_cb = cancel_check = None
        def snapshot(self): return {}

    class Adapter:
        client = Client()
        def capabilities(self): return None
        def supports(self, name): return name == "get_pages"
        def get_pages(self, ch): return list(range(2 if ch.chapter_id == "a" else 3))
        def download_page(self, page): return _png(page), ".png"

    chapters = [ChapterInfo("src", "s", "a", "Chapter 1", url="https://reader.example/a?t=1"),
                ChapterInfo("src", "s", "b", "Chapter 2", url="https://reader.example/b")]
    job = "chapter_order_test"
    background_jobs._jobs[job] = {"status": "running", "progress": 0.0, "message": "",
                                  "cancel_requested": False, "result": None}
    pipeline.run_import_job(job, "src", chapters, drama, adapter=Adapter())
    background_jobs.clear_job(job)
    groups = comic_chapters.group_pages(isolated_db.list_pages(drama), comic_chapters.load(drama))
    assert [(g["title"], g["page_count"]) for g in groups] == [
        ("Chapter 1", 2), ("Chapter 2", 3)]
    assert "host" not in json.dumps(comic_chapters.load(drama))


def test_rolled_back_pages_leave_no_label(drama, isolated_db):
    _add(drama, ("c1", "One", "src"), 2)
    ids = [p["id"] for p in isolated_db.list_pages(drama)]
    pipeline._discard_pages(drama, ids[1:])
    assert comic_chapters.load(drama)["chapters"][0]["files"] == [
        os.path.join("pages", "page_0000.png")]
    _add(drama, ("c2", "Two", "src"), 1)
    groups = comic_chapters.group_pages(isolated_db.list_pages(drama), comic_chapters.load(drama))
    assert [(g["title"], g["page_count"]) for g in groups] == [("One", 1), ("Two", 1)]


def test_chapter_ref_normalises():
    assert comic_chapters.chapter_ref() is None
    ref = comic_chapters.chapter_ref(None, "  Ch\x00 3 ")
    assert ref["title"] == "Ch  3" and ref["key"].startswith("title:") and "host" not in ref


def test_chapter_title_is_scrubbed():
    ref = comic_chapters.chapter_ref(
        "1", "Ch 1 https://reader.example/s/1?token=sekret#f read C:\\Users\\me\\Pics\\a.png "
             "and \\\\nas\\share\\x " + "z" * 500, "src")
    title = ref["title"]
    assert "sekret" not in title and "Users" not in title and "nas" not in title
    assert "reader.example/s/1" in title and "[path]" in title
    assert len(title) <= comic_chapters.MAX_TITLE


def _manifest_path(drama):
    return comic_chapters._path(drama)


def test_unreadable_manifest_is_not_overwritten(client, drama):
    _add(drama, ("c1", "One", "src"), 2)
    path = _manifest_path(drama)
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"version": 1, "chapters": [')  # truncated by a crash
    broken = open(path, encoding="utf-8").read()
    with pytest.raises(comic_chapters.ManifestUnreadable):
        comic_chapters.record_pages(drama, comic_chapters.chapter_ref("c2", "Two", "src"), ["pages/x.png"])
    with pytest.raises(comic_chapters.ManifestUnreadable):
        comic_chapters.forget_pages(drama, ["pages/x.png"])
    with pytest.raises(comic_chapters.ManifestUnreadable):
        comic_chapters.set_hidden(drama, ["pages/x.png"], True)
    assert open(path, encoding="utf-8").read() == broken
    ids = [p["id"] for p in _pages(client, drama)["pages"]]
    r = client.post(f"/api/scanlate/dramas/{drama}/pages/visibility",
                    json={"hidden": True, "page_ids": ids[:1]})
    assert r.status_code == 409
    assert open(path, encoding="utf-8").read() == broken


def test_import_keeps_pages_when_label_write_is_refused(drama, isolated_db):
    _add(drama, ("c1", "One", "src"), 1)
    with open(_manifest_path(drama), "w", encoding="utf-8") as f:
        f.write("not json")
    assert _add(drama, ("c2", "Two", "src"), 2) == 2
    assert len(isolated_db.list_pages(drama)) == 3
    assert open(_manifest_path(drama), encoding="utf-8").read() == "not json"


def test_oversized_manifest_is_unreadable_and_kept(drama):
    path = _manifest_path(drama)
    blob = "x" * (comic_chapters.MAX_MANIFEST_BYTES + 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(blob)
    assert comic_chapters.load(drama)["chapters"] == []
    with pytest.raises(comic_chapters.ManifestUnreadable):
        comic_chapters.set_hidden(drama, ["pages/a.png"], True)
    assert os.path.getsize(path) == len(blob)


def test_write_stays_under_the_read_cap(drama, monkeypatch):
    monkeypatch.setattr(comic_chapters, "MAX_WRITE_BYTES", 2000)
    for n in range(40):
        ref = comic_chapters.chapter_ref(f"c{n}", f"Chapter {n}", "src")
        comic_chapters.record_pages(drama, ref, [f"pages/page_{n:04d}.png"])
    assert os.path.getsize(_manifest_path(drama)) <= 2000
    chapters = comic_chapters.load(drama)["chapters"]
    assert chapters and chapters[-1]["id"] == "c39"  # the newest labels survive
    assert chapters[0]["id"] != "c0"


def test_hidden_flags_survive_compaction_and_oversize_is_refused(drama, monkeypatch):
    comic_chapters.set_hidden(drama, ["pages/page_0000.png"], True)
    monkeypatch.setattr(comic_chapters, "MAX_WRITE_BYTES", 2000)
    comic_chapters.record_pages(drama, comic_chapters.chapter_ref("c1", "t" * 150, "src"),
                                [f"pages/p{n}.png" for n in range(60)])
    assert comic_chapters.load(drama)["hidden"] == ["pages/page_0000.png"]
    with pytest.raises(comic_chapters.ManifestUnreadable):
        comic_chapters.set_hidden(drama, [f"pages/hidden_{n:05d}.png" for n in range(300)], True)


def test_load_waits_for_a_writer(drama):
    import threading
    started, result = threading.Event(), []
    with comic_chapters._lock(drama):
        def reader():
            started.set()
            result.append(comic_chapters.load(drama))
        t = threading.Thread(target=reader)
        t.start()
        started.wait(2)
        t.join(0.2)
        assert t.is_alive() and not result  # blocked on the per-drama lock
    t.join(2)
    assert result


def test_replace_is_retried_on_permission_error(drama, monkeypatch):
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) < 3:
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr(comic_chapters.time, "sleep", lambda s: None)
    comic_chapters.set_hidden(drama, ["pages/a.png"], True)
    assert len(calls) == 3
    assert comic_chapters.load(drama)["hidden"] == ["pages/a.png"]
    assert not [n for n in os.listdir(os.path.dirname(_manifest_path(drama))) if n.endswith(".tmp")]


def test_replace_gives_up_after_the_attempts(drama, monkeypatch):
    def always(src, dst):
        raise PermissionError("in use")

    monkeypatch.setattr(os, "replace", always)
    monkeypatch.setattr(comic_chapters.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        comic_chapters.set_hidden(drama, ["pages/a.png"], True)
