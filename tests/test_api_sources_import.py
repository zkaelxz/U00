"""Sources S-4 chapter import (POST /api/sources/{name}/import) and tracking
a new series (POST /api/sources/tracked). Fake adapters, no network."""
import os
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, drama_service
from services import sources_import_service as imp
from services import sources_search_service as search
from services.service_errors import ConflictError
from sources import chapter_check, pipeline, registry, store
from sources.adapters.missevan import MissEvanSource
from sources.base import SourceAdapter
from sources.http import PacingPolicy
from sources.models import (ChallengeDetected, ChapterInfo, FailureReason, PageRef,
                            SeriesInfo, SourceUnavailable)
from tests.sources_helpers import ScriptedTransport, png

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
HOST = "https://fake.invalid"


def _chapters(name, series_id):
    return [ChapterInfo(name, series_id, "c1", "第1章", f"{HOST}/c/1?t={SECRET}"),
            ChapterInfo(name, series_id, "c2", "第2章", f"{HOST}/c/2?t={SECRET}"),
            ChapterInfo(name, series_id, "c10", "第10章", f"{HOST}/c/10?t={SECRET}")]


def _make(name, comic=False, gate=None, calls=None, fail=None):
    """A fake novel (or comic) adapter. `gate`: an Event each chapter fetch
    waits on (then honours cancel, as SourceClient does); `fail`: a map
    chapter_id -> exception; `calls` records fetched chapter ids."""
    calls = calls if calls is not None else []

    class Fake(SourceAdapter):
        pass

    Fake.name = name
    Fake.display_name = name.title()

    def __init__(self, client=None, **kw):
        kw.setdefault("transport", ScriptedTransport({}))
        kw["policy"] = PacingPolicy(min_delay=0.0, max_delay=0.0, session_break_min_requests=0)
        SourceAdapter.__init__(self, client, **kw)

    def get_series(self, series_id):
        return SeriesInfo(name, series_id, "Series T", f"{HOST}/series/{series_id}?t={SECRET}")

    def get_chapters(self, series_id):
        return _chapters(name, series_id)

    def _fetch(self, ch):
        calls.append(ch.chapter_id)
        if gate is not None:
            gate.wait(5)
        self.client._check_cancel()
        if fail and ch.chapter_id in fail:
            raise fail[ch.chapter_id]

    def get_chapter_text(self, ch):
        _fetch(self, ch)
        return f"text of {ch.chapter_id} " * 20

    def get_pages(self, ch):
        _fetch(self, ch)
        return [PageRef(name, ch.chapter_id, i, f"{HOST}/p/{i}") for i in range(2)]

    def download_page(self, page):
        return png(20, 30, seed=page.index), ".png"

    Fake.__init__ = __init__
    Fake.get_series = get_series
    Fake.get_chapters = get_chapters
    if comic:
        Fake.get_pages = get_pages
        Fake.download_page = download_page
    else:
        Fake.get_chapter_text = get_chapter_text
    Fake.calls = calls
    return Fake


@pytest.fixture
def fakes(isolated_db, monkeypatch):
    classes = {}
    monkeypatch.setattr(registry, "adapter_classes", lambda: dict(classes))
    yield classes
    for jid in list(background_jobs.list_all_jobs()):
        if jid.startswith(("sources_", "sourceimport_")):
            _wait(jid)
            background_jobs.clear_job(jid)


@pytest.fixture
def client(fakes):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _wait(job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if not st or st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _clean(r):
    assert SECRET not in r.text and db.LIBRARY_DIR not in r.text and "?" not in r.text
    return r.json()


def _novel():
    return db.create_drama(title_en="N", media_type="novel", content_mode="novel_narration")


def _raw(did):
    path = os.path.join(db.drama_dir(did), pipeline.RAW_NOVEL_FILENAME)
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


def _result(client, job_id):
    _wait(job_id)
    r = client.get(f"/api/sources/jobs/{job_id}/result")
    return r, (_clean(r) if r.status_code == 200 else r.json())


# ---------------------------------------------------------------------------
# R3: chapter import
# ---------------------------------------------------------------------------

def test_import_by_id_not_found_and_reimport_skipped(client, fakes):
    fakes["alpha"] = _make("alpha")
    did = _novel()
    r = client.post("/api/sources/alpha/import",
                    json={"series_id": "s1", "chapter_ids": ["c2", "zz", "c1"], "drama_id": did})
    assert r.status_code == 200 and r.json() == {"job_id": f"sourceimport_{did}"}
    g, b = _result(client, f"sourceimport_{did}")
    res = b["result"]
    assert g.status_code == 200 and res["kind"] == "chapter_import"
    # chapter order from the source's own list, matched by id
    assert [(c["chapter_id"], c["outcome"]) for c in res["chapters"]] == [
        ("c1", "imported"), ("c2", "imported"), ("zz", "not_found")]
    assert res["chapters"][0]["chars"] > 0 and res["chapters"][0]["title"] == "第1章"
    assert (res["imported_count"], res["skipped_count"], res["failed_count"]) == (2, 0, 0)
    assert res["cancelled"] is False and res["handoff"] is None
    assert fakes["alpha"].calls == ["c1", "c2"]
    text = _raw(did)
    assert text.index("text of c1") < text.index("text of c2")
    assert store.imported_chapter_ids("alpha", "s1", did) == {"c1", "c2"}
    # re-import: skipped, nothing appended
    client.post("/api/sources/alpha/import",
                json={"series_id": "s1", "chapter_ids": ["c1", "c10"], "drama_id": did})
    _, b = _result(client, f"sourceimport_{did}")
    assert [(c["chapter_id"], c["outcome"]) for c in b["result"]["chapters"]] == [
        ("c1", "skipped"), ("c10", "imported")]
    assert b["result"]["skipped_count"] == 1
    assert fakes["alpha"].calls == ["c1", "c2", "c10"]
    assert _raw(did).count("text of c1 ") == text.count("text of c1 ") == 20


def test_comic_import_writes_pages(client, fakes):
    fakes["comic"] = _make("comic", comic=True)
    did = db.create_drama(title_en="M", media_type="manhua")
    r = client.post("/api/sources/comic/import",
                    json={"series_id": "s1", "chapter_ids": ["c1"], "drama_id": did})
    assert r.status_code == 200
    _, b = _result(client, f"sourceimport_{did}")
    assert b["result"]["chapters"][0]["pages"] == 2
    assert len(db.list_pages(did)) == 2


def test_failed_chapter_counts_and_projection(client, fakes):
    from services import jobs_service
    fakes["alpha"] = _make("alpha", fail={"c2": SourceUnavailable(f"down {SECRET} C:\\x\\y")})
    did = _novel()
    client.post("/api/sources/alpha/import",
                json={"series_id": "s1", "chapter_ids": ["c1", "c2"], "drama_id": did})
    _, b = _result(client, f"sourceimport_{did}")
    res = b["result"]
    assert [c["outcome"] for c in res["chapters"]] == ["imported", "failed"]
    assert res["failed_count"] == 1 and res["partial"] is True
    raw = background_jobs.get_status(f"sourceimport_{did}")["result"]
    projected = jobs_service.project_result(raw)
    assert projected == {"partial": True, "imported_count": 1, "skipped_count": 0,
                         "failed_count": 1}
    j = client.get(f"/api/jobs/sourceimport_{did}")
    assert j.status_code == 200 and SECRET not in j.text and "down" not in j.text


def test_handoff_in_result(client, fakes):
    fakes["alpha"] = _make("alpha", fail={"c1": ChallengeDetected(
        "challenge", f"{HOST}/v?cf={SECRET}", FailureReason.CLOUDFLARE_CHALLENGE)})
    did = _novel()
    client.post("/api/sources/alpha/import",
                json={"series_id": "s1", "chapter_ids": ["c1", "c2"], "drama_id": did})
    _, b = _result(client, f"sourceimport_{did}")
    h = b["result"]["handoff"]
    assert h["handoff"] is True and h["open_url"] == f"{HOST}/v" and h["chapter_id"] == "c1"
    # stopped at the challenge: c2 was never reached, so it is "not_attempted" (Step 107)
    assert [(c["chapter_id"], c["outcome"]) for c in b["result"]["chapters"]] == [
        ("c1", "failed"), ("c2", "not_attempted")]


def test_series_list_challenge_is_409(client, fakes):
    Fake = _make("alpha")

    def boom(self, series_id):
        raise ChallengeDetected("c", f"{HOST}/v?x={SECRET}", FailureReason.CLOUDFLARE_CHALLENGE)
    Fake.get_chapters = boom
    fakes["alpha"] = Fake
    did = _novel()
    client.post("/api/sources/alpha/import",
                json={"series_id": "s1", "chapter_ids": ["c1"], "drama_id": did})
    r, body = _result(client, f"sourceimport_{did}")
    assert r.status_code == 409 and body["error"]["details"]["handoff"] is True
    assert SECRET not in r.text


def test_validation_errors(client, fakes):
    fakes["alpha"] = _make("alpha")
    fakes["comic"] = _make("comic", comic=True)
    fakes["missevan"] = MissEvanSource
    did = _novel()
    audio = db.create_drama(title_en="A")
    ok = {"series_id": "s1", "chapter_ids": ["c1"], "drama_id": did}
    assert client.post("/api/sources/nope/import", json=ok).status_code == 404
    assert client.post("/api/sources/alpha/import", json={**ok, "drama_id": 99999}).status_code == 404
    for bad in ({**ok, "chapter_ids": []}, {**ok, "chapter_ids": ["c"] * 201},
                {**ok, "chapter_ids": [{"chapter_id": "c1", "url": f"{HOST}/c/1"}]},
                {**ok, "chapter_ids": [f"{HOST}/c/1"]}, {**ok, "chapter_ids": ["//evil/x"]},
                {**ok, "chapter_ids": ["a@b"]}, {**ok, "chapters": [{"chapter_id": "c1"}]},
                {**ok, "series_id": "http://evil/x"}, {**ok, "drama_id": 0},
                {**ok, "url": f"{HOST}/x"}):
        assert client.post("/api/sources/alpha/import", json=bad).status_code == 422, bad
    # wrong media type both ways
    assert client.post("/api/sources/alpha/import", json={**ok, "drama_id": audio}).status_code == 422
    assert client.post("/api/sources/comic/import", json=ok).status_code == 422
    # missevan can't import; a switched-off source is 400
    assert client.post("/api/sources/missevan/import", json=ok).status_code == 400
    registry.set_enabled("alpha", False)
    assert client.post("/api/sources/alpha/import", json=ok).status_code == 400
    assert background_jobs.get_status(f"sourceimport_{did}") is None


def test_cancel_mid_run_and_delete_refused(client, fakes):
    gate = threading.Event()
    fakes["alpha"] = _make("alpha", gate=gate)
    did = _novel()
    jid = f"sourceimport_{did}"
    assert client.post("/api/sources/alpha/import",
                       json={"series_id": "s1", "chapter_ids": ["c1", "c2"],
                             "drama_id": did}).status_code == 200
    # a second start for the same drama, and a delete, are refused while it runs
    assert client.post("/api/sources/alpha/import",
                       json={"series_id": "s1", "chapter_ids": ["c10"],
                             "drama_id": did}).status_code == 409
    with pytest.raises(ConflictError):
        drama_service.delete_drama(did, confirm=True, confirm_text="DELETE")
    assert client.post(f"/api/jobs/{jid}/cancel",
                       headers={"X-Baihe-Local": "1"}).status_code == 200
    gate.set()
    _, b = _result(client, jid)
    assert b["result"]["cancelled"] is True
    assert b["result"]["imported_count"] == 0
    assert "text of" not in _raw(did)


def test_job_result_accepts_only_our_ids(client, fakes):
    idle = client.get("/api/sources/jobs/sourceimport_1/result")
    assert idle.status_code == 200 and idle.json()["status"] == "idle"
    background_jobs.start_job("sourceimport_x", lambda: None)
    _wait("sourceimport_x")
    assert client.get("/api/sources/jobs/sourceimport_x/result").status_code == 404
    background_jobs.clear_job("sourceimport_x")
    assert not search._is_ours("sourceimport_") and search._is_ours("sourceimport_12")


def test_import_job_prefix_blocks_drama_delete():
    assert "sourceimport_" in background_jobs.DRAMA_JOB_PREFIXES


def test_does_not_shadow_catalog_routes(client, fakes):
    fakes["alpha"] = _make("alpha")
    assert client.get("/api/sources/alpha").status_code == 200
    assert client.get("/api/sources/tracked").status_code == 200


# ---------------------------------------------------------------------------
# R4: track a new series
# ---------------------------------------------------------------------------

def _load_series(client, name="alpha", series_id="s1"):
    assert client.post(f"/api/sources/{name}/series",
                       json={"series_id": series_id}).status_code == 200
    _wait(search.SERIES_JOB_PREFIX + name)


def test_track_needs_a_loaded_series(client, fakes):
    fakes["alpha"] = _make("alpha")
    body = {"source": "alpha", "series_id": "s1", "tracked": True}
    r = client.post("/api/sources/tracked", json=body)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "SERIES_NOT_LOADED"
    _load_series(client, series_id="other")
    assert client.post("/api/sources/tracked", json=body).status_code == 409
    assert store.list_tracked_series() == []
    assert client.post("/api/sources/tracked",
                       json={**body, "drama_id": 99999}).status_code == 404


def test_track_seeds_known_chapters_first_check_announces_nothing(client, fakes):
    fakes["alpha"] = _make("alpha")
    did = _novel()
    _load_series(client)
    r = client.post("/api/sources/tracked",
                    json={"source": "alpha", "series_id": "s1", "tracked": True,
                          "drama_id": did, "url": "https://evil.invalid/x"})
    rows = _clean(r)
    assert r.status_code == 200 and rows[0]["title"] == "Series T"
    assert rows[0]["url"] == f"{HOST}/series/s1" and rows[0]["drama_id"] == did
    assert store.known_chapter_ids("alpha", "s1") == {"c1", "c2", "c10"}
    row = store.list_tracked_series()[0]
    assert chapter_check.check_series(fakes["alpha"](), row) == []
    assert store.list_notifications() == []


# ---------------------------------------------------------------------------
# Security review MED-2: one import job per drama on every path, and page
# writes that never overwrite
# ---------------------------------------------------------------------------

def _chs(name="comic"):
    return [c for c in _chapters(name, "s1") if c.chapter_id in ("c1", "c2")]


def test_pipeline_start_import_claims_the_drama_job(client, fakes):
    gate = threading.Event()
    fakes["comic"] = _make("comic", comic=True, gate=gate)
    did = db.create_drama(title_en="M", media_type="manhua")
    assert pipeline.import_job_id(did) == imp.import_job_id(did) == f"sourceimport_{did}"
    assert pipeline.start_import("comic", "s1", _chs(), did) is True
    assert background_jobs.get_status(f"sourceimport_{did}")["status"] == "running"
    # the API refuses while the Streamlit / auto-import job writes this drama
    r = client.post("/api/sources/comic/import",
                    json={"series_id": "s1", "chapter_ids": ["c10"], "drama_id": did})
    assert r.status_code == 409
    assert pipeline.start_import("comic", "s1", _chs(), did) is False
    gate.set()
    _wait(f"sourceimport_{did}")
    assert len(db.list_pages(did)) == 4
    # and the other way round: the API's job blocks the pipeline path
    gate.clear()
    r = client.post("/api/sources/comic/import",
                    json={"series_id": "s1", "chapter_ids": ["c10"], "drama_id": did})
    assert r.status_code == 200
    assert pipeline.start_import("comic", "s1", _chs(), did) is False
    gate.set()
    _wait(f"sourceimport_{did}")


def test_pipeline_start_import_skips_chapters_already_imported(client, fakes):
    fakes["alpha"] = _make("alpha")
    did = _novel()
    store.record_imported("alpha", "s1", "c1", did)
    assert pipeline.start_import("alpha", "s1", _chs("alpha"), did) is True
    st = _wait(f"sourceimport_{did}")
    rows = {r["chapter_id"]: r for r in st["result"]["chapters"]}
    assert rows["c1"].get("skipped") is True and not rows["c2"].get("skipped")
    assert fakes["alpha"].calls == ["c2"]


def test_chapter_check_auto_import_skips_a_busy_drama(client, fakes, monkeypatch):
    fakes["alpha"] = _make("alpha")
    did = _novel()
    store.track_series("alpha", "s1", "Series T", "", did,
                       known_chapters=[c for c in _chapters("alpha", "s1") if c.chapter_id == "c1"])
    store.set_setting("auto_queue_new_chapters", True)
    monkeypatch.setattr(registry, "is_enabled", lambda name: True)
    hold = threading.Event()
    assert background_jobs.start_job(f"sourceimport_{did}", lambda: hold.wait(5))
    try:
        summary = chapter_check.run_check_cycle(adapter_factory=lambda n: fakes["alpha"]())
        assert summary["new"] == 2 and summary["queued"] == []
        assert background_jobs.get_status(f"sourceimport_{did}")["status"] == "running"
    finally:
        hold.set()
        _wait(f"sourceimport_{did}")
    assert fakes["alpha"].calls == []
    assert not [j for j in background_jobs.list_all_jobs() if str(j).startswith("source_import_")]


def test_add_page_images_never_overwrites_an_existing_file(fakes):
    from tests.sources_helpers import png
    did = db.create_drama(title_en="M", media_type="manhua")
    pages_dir = os.path.join(db.drama_dir(did), "pages")
    os.makedirs(pages_dir)
    foreign = os.path.join(pages_dir, "page_0000.png")   # another writer's page, no row yet
    with open(foreign, "wb") as f:
        f.write(png(10, 10, seed=99))
    before = open(foreign, "rb").read()
    assert pipeline.add_page_images(did, [(png(20, 30, seed=1), ".png")]) == 1
    assert open(foreign, "rb").read() == before
    assert [(p["idx"], p["filename"]) for p in db.list_pages(did)] == [
        (1, os.path.join("pages", "page_0001.png"))]


def test_add_page_images_concurrent_writers_get_distinct_pages(fakes):
    from tests.sources_helpers import png
    did = db.create_drama(title_en="M", media_type="manhua")
    errors = []

    def writer(seed):
        try:
            pipeline.add_page_images(did, [(png(20, 30, seed=seed * 10 + i), ".png")
                                           for i in range(3)])
        except Exception as e:  # pragma: no cover -- reported below
            errors.append(e)
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == []
    pages = db.list_pages(did)
    assert sorted(p["idx"] for p in pages) == list(range(12))
    assert len({p["filename"] for p in pages}) == 12
    assert len(os.listdir(os.path.join(db.drama_dir(did), "pages"))) == 12


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def test_auth_on(fakes):
    fakes["alpha"] = _make("alpha")
    did = _novel()
    c = TestClient(create_app(ApiSettings(auth_mode="on")),
                   base_url="https://baihe.example.com", raise_server_exceptions=False)
    body = {"series_id": "s1", "chapter_ids": ["c1"], "drama_id": did}
    assert c.post("/api/sources/alpha/import", json=body).status_code == 401
    u = auth_service.add_user("kid@example.com")
    s = auth_service.create_session(u["id"])
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
         api_auth.CSRF_HEADER: s["csrf_token"]}
    assert c.post("/api/sources/alpha/import", json=body, headers=h).status_code == 403
    track = {"source": "alpha", "series_id": "s1", "tracked": True}
    assert c.post("/api/sources/tracked", json=track, headers=h).status_code == 403
    auth_service.grant_permission(u["id"], "sources.import")
    r = c.post("/api/sources/alpha/import", json=body, headers=h)
    assert r.status_code == 200
    _wait(f"sourceimport_{did}")
    assert c.get(f"/api/sources/jobs/sourceimport_{did}/result", headers=h).status_code == 200


def test_add_page_images_claims_the_index_whatever_the_extension(fakes, monkeypatch):
    """Security review L-2: another process computed the same next index
    and wrote page_0000.jpg (or still holds its claim on 0001): this one
    must not add a second index-0/1 page as .png."""
    from tests.sources_helpers import png
    did = db.create_drama(title_en="M", media_type="manhua")
    pages_dir = os.path.join(db.drama_dir(did), "pages")
    os.makedirs(pages_dir)
    with open(os.path.join(pages_dir, "page_0000.jpg"), "wb") as f:
        f.write(b"other process")
    open(os.path.join(pages_dir, "page_0001.claim"), "wb").close()   # held elsewhere
    monkeypatch.setattr(pipeline, "_next_page_index", lambda drama_id, d: 0)   # stale scan
    assert pipeline.add_page_images(did, [(png(20, 30, seed=1), ".png")]) == 1
    assert [(p["idx"], p["filename"]) for p in db.list_pages(did)] == [
        (2, os.path.join("pages", "page_0002.png"))]
    assert sorted(os.listdir(pages_dir)) == ["page_0000.jpg", "page_0001.claim", "page_0002.png"]


# ---------------------------------------------------------------------------
# Chapters keep the source site's order (no re-sorting by title)
# ---------------------------------------------------------------------------

def _site_order_chapters(name, series_id):
    """A volume opening with a prologue, an unnumbered special between
    numbered chapters, and an afterword last -- the site's own order."""
    titles = [("p", "序章"), ("c1", "第1话"), ("sp", "特别篇 温泉"), ("c2", "第2话"),
              ("af", "后记")]
    return [ChapterInfo(name, series_id, cid, t, f"{HOST}/c/{cid}", group="第1卷")
            for cid, t in titles]


def test_import_keeps_site_order_for_specials_and_prologue(client, fakes):
    Fake = _make("alpha")
    Fake.get_chapters = lambda self, series_id: _site_order_chapters("alpha", series_id)
    fakes["alpha"] = Fake
    did = _novel()
    client.post("/api/sources/alpha/import",
                json={"series_id": "s1", "chapter_ids": ["af", "c2", "sp", "c1", "p"],
                      "drama_id": did})
    _, b = _result(client, f"sourceimport_{did}")
    assert [c["chapter_id"] for c in b["result"]["chapters"]] == ["p", "c1", "sp", "c2", "af"]
    assert Fake.calls == ["p", "c1", "sp", "c2", "af"]
    text = _raw(did)
    idx = [text.index(f"text of {c} ") for c in ("p", "c1", "sp", "c2", "af")]
    assert idx == sorted(idx)


def test_check_returns_new_chapters_in_site_order_and_leaves_known_alone(client, fakes):
    Fake = _make("alpha")
    listed = _site_order_chapters("alpha", "s1")
    Fake.get_chapters = lambda self, series_id: list(listed)
    fakes["alpha"] = Fake
    did = _novel()
    store.track_series("alpha", "s1", "Series T", "", drama_id=did, known_chapters=listed[:3])
    row = store.list_tracked_series()[0]
    before = store.known_chapter_ids("alpha", "s1")
    assert before == {"p", "c1", "sp"}
    new = chapter_check.check_series(Fake(), row)
    assert [c.chapter_id for c in new] == ["c2", "af"]
    assert store.known_chapter_ids("alpha", "s1") == before | {"c2", "af"}
    assert chapter_check.check_series(Fake(), row) == []
