"""Step 107: partial-import retry state. A chapter import records which
chapters failed (and why, redacted) and which were never attempted after a
browser check / terms stop / cancel; GET /api/sources/{name}/import-state
returns those plus the chapters already imported, so the picker can mark
them and retry only the failed ones. Fake adapters, no network."""
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import background_jobs
import db
from sources import store
from sources.models import (ChallengeDetected, FailureReason, SourceUnavailable,
                            TermsProhibited)

from tests.test_api_sources_import import (HOST, SECRET, _make, _novel, _result,  # noqa: F401
                                           _wait, client, fakes)


def _start(client, did, ids, name="alpha"):
    r = client.post(f"/api/sources/{name}/import",
                    json={"series_id": "s1", "chapter_ids": ids, "drama_id": did})
    assert r.status_code == 200, r.text
    return _result(client, f"sourceimport_{did}")[1]["result"]


def _state(client, did, name="alpha", series_id="s1"):
    return client.get(f"/api/sources/{name}/import-state",
                      params={"series_id": series_id, "drama_id": did})


def test_failed_chapter_manifest_and_retry_only_those(client, fakes):
    fail = {"c2": SourceUnavailable(f"down {SECRET} C:\\x\\y")}
    calls = []
    fakes["alpha"] = _make("alpha", fail=fail, calls=calls)
    did = _novel()
    res = _start(client, did, ["c1", "c2", "c10"])
    assert res["retry_chapter_ids"] == ["c2"] and res["not_attempted_count"] == 0

    r = _state(client, did)
    assert r.status_code == 200 and SECRET not in r.text and "C:\\" not in r.text
    body = r.json()
    assert body["imported_chapter_ids"] == ["c1", "c10"]
    assert body["retry_count"] == 1
    row = body["retry"][0]
    assert row["chapter_id"] == "c2" and row["status"] == "failed" and "down" in row["error"]
    # the stored manifest is redacted too
    assert SECRET not in str(store.import_retry_rows("alpha", "s1", did))

    # the retry fetches only c2; once it works the manifest is empty
    fail.clear()
    calls.clear()
    res = _start(client, did, [x["chapter_id"] for x in body["retry"]])
    assert calls == ["c2"] and res["retry_chapter_ids"] == []
    after = _state(client, did).json()
    assert after["retry"] == [] and after["imported_chapter_ids"] == ["c1", "c10", "c2"]


@pytest.mark.parametrize("stop", [
    ChallengeDetected("challenge", f"{HOST}/v?cf={SECRET}", FailureReason.CLOUDFLARE_CHALLENGE),
    TermsProhibited(f"terms {SECRET}"),
])
def test_stop_records_remaining_chapters_as_not_attempted(client, fakes, stop):
    calls = []
    fakes["alpha"] = _make("alpha", fail={"c1": stop}, calls=calls)
    did = _novel()
    res = _start(client, did, ["c1", "c2", "c10"])
    # the job still stops at the first chapter (by design) ...
    assert calls == ["c1"]
    outcomes = {c["chapter_id"]: c["outcome"] for c in res["chapters"]}
    assert outcomes == {"c1": "failed", "c2": "not_attempted", "c10": "not_attempted"}
    assert res["not_attempted_count"] == 2 and res["partial"] is True
    assert res["retry_chapter_ids"] == ["c1", "c2", "c10"]
    # ... and every chapter it didn't finish is in the manifest
    body = _state(client, did).json()
    assert [(x["chapter_id"], x["status"]) for x in body["retry"]] == [
        ("c1", "failed"), ("c2", "not_attempted"), ("c10", "not_attempted")]
    assert SECRET not in str(body)


def test_cancel_records_unreached_chapters(client, fakes):
    gate = threading.Event()
    fakes["alpha"] = _make("alpha", gate=gate)
    did = _novel()
    jid = f"sourceimport_{did}"
    client.post("/api/sources/alpha/import",
                json={"series_id": "s1", "chapter_ids": ["c1", "c2"], "drama_id": did})
    client.post(f"/api/jobs/{jid}/cancel", headers={"X-Baihe-Local": "1"})
    gate.set()
    res = _result(client, jid)[1]["result"]
    assert res["cancelled"] is True
    # c1 was interrupted mid-fetch and c2 never started: neither finished
    assert {c["chapter_id"] for c in res["chapters"] if c["outcome"] == "not_attempted"} == \
        {"c1", "c2"}
    assert [x["chapter_id"] for x in _state(client, did).json()["retry"]] == ["c1", "c2"]


def test_imported_elsewhere_leaves_the_manifest(client, fakes):
    fakes["alpha"] = _make("alpha", fail={"c2": SourceUnavailable("down")})
    did = _novel()
    _start(client, did, ["c2"])
    assert _state(client, did).json()["retry_count"] == 1
    store.record_imported("alpha", "s1", "c2", did)   # e.g. the tracked-series auto-import
    body = _state(client, did).json()
    assert body["retry_count"] == 0 and body["imported_chapter_ids"] == ["c2"]


def test_manifest_is_per_drama_and_series(client, fakes):
    fakes["alpha"] = _make("alpha", fail={"c2": SourceUnavailable("down")})
    did, other = _novel(), _novel()
    _start(client, did, ["c2"])
    assert _state(client, other).json()["retry"] == []
    assert _state(client, did, series_id="s2").json()["retry"] == []


def test_import_state_validation(client, fakes):
    fakes["alpha"] = _make("alpha")
    did = _novel()
    assert _state(client, did).status_code == 200
    assert _state(client, did, name="nope").status_code == 404
    assert _state(client, 99999).status_code == 404
    assert _state(client, did, series_id="http://evil/x").status_code == 422
    assert _state(client, did, series_id="//evil/x").status_code == 422
    assert client.get("/api/sources/alpha/import-state",
                      params={"series_id": "s1", "drama_id": 0}).status_code == 422
    assert client.get("/api/sources/alpha/import-state",
                      params={"series_id": "s1"}).status_code == 422
    assert db.get_drama(did) is not None
    assert background_jobs.get_status(f"sourceimport_{did}") is None


def test_retried_chapter_gone_from_the_site_leaves_the_manifest(client, fakes):
    fakes["alpha"] = _make("alpha", fail={"c2": SourceUnavailable("down")})
    did = _novel()
    _start(client, did, ["c2"])
    assert _state(client, did).json()["retry_count"] == 1
    Fake = _make("alpha")
    Fake.get_chapters = lambda self, series_id: []     # c2 is no longer listed
    fakes["alpha"] = Fake
    res = _start(client, did, ["c2"])
    assert [c["outcome"] for c in res["chapters"]] == ["not_found"]
    assert _state(client, did).json()["retry_count"] == 0


def _fail_page_two(monkeypatch):
    from sources import pipeline
    real_claim = pipeline._claim_page_index
    written = []

    def claim(pages_dir, idx):
        if written:            # page 1 is on disk; page 2 blows up
            raise OSError("disk full")
        written.append(idx)
        return real_claim(pages_dir, idx)
    monkeypatch.setattr(pipeline, "_claim_page_index", claim)
    return written


def test_page_write_failure_removes_the_pages_and_stays_retryable(client, fakes, monkeypatch):
    """An error while writing page 2 removes page 1 again, so the chapter is
    retryable; the import stops there and the chapters after it are
    "not_attempted"."""
    fakes["comic"] = _make("comic", comic=True)
    did = db.create_drama(title_en="M", media_type="manhua")
    written = _fail_page_two(monkeypatch)
    client.post("/api/sources/comic/import",
                json={"series_id": "s1", "chapter_ids": ["c1", "c2"], "drama_id": did})
    _wait(f"sourceimport_{did}")
    assert written and db.list_pages(did) == []   # page 1 of c1 was written, then removed
    body = _state(client, did, name="comic").json()
    rows = {x["chapter_id"]: x["status"] for x in body["retry"]}
    assert rows == {"c1": "failed", "c2": "not_attempted"}
    assert body["retry_count"] == 2


def test_pages_that_could_not_be_removed_mark_the_chapter_partial(client, fakes, monkeypatch):
    """Lead review: if page 1 can't be removed again, that chapter must not
    be auto-retried (it would duplicate page 1)."""
    from sources import pipeline
    fakes["comic"] = _make("comic", comic=True)
    did = db.create_drama(title_en="M", media_type="manhua")
    _fail_page_two(monkeypatch)

    def cannot_remove(drama_id, page_ids):
        raise OSError("library.db is locked")
    monkeypatch.setattr(pipeline, "_discard_pages", cannot_remove)
    client.post("/api/sources/comic/import",
                json={"series_id": "s1", "chapter_ids": ["c1", "c2"], "drama_id": did})
    _wait(f"sourceimport_{did}")
    assert len(db.list_pages(did)) == 1
    body = _state(client, did, name="comic").json()
    rows = {x["chapter_id"]: x for x in body["retry"]}
    assert rows["c1"]["status"] == "partial" and "partly imported" in rows["c1"]["error"]
    assert rows["c2"]["status"] == "not_attempted"
    assert body["retry_count"] == 1                # only c2 is retried automatically


def test_stop_while_listing_records_requested_chapters(client, fakes):
    Fake = _make("alpha")

    def challenged(self, series_id):
        raise ChallengeDetected("c", f"{HOST}/v?x={SECRET}", FailureReason.CLOUDFLARE_CHALLENGE)
    Fake.get_chapters = challenged
    fakes["alpha"] = Fake
    did = _novel()
    client.post("/api/sources/alpha/import",
                json={"series_id": "s1", "chapter_ids": ["c1", "c2"], "drama_id": did})
    _wait(f"sourceimport_{did}")
    body = _state(client, did).json()
    assert [(x["chapter_id"], x["status"]) for x in body["retry"]] == [
        ("c1", "not_attempted"), ("c2", "not_attempted")]
    assert SECRET not in str(body)


def test_unexpected_error_still_saves_the_manifest(client, fakes, monkeypatch):
    from sources import pipeline
    fakes["alpha"] = _make("alpha", fail={"c1": SourceUnavailable("down")})
    did = _novel()
    real = pipeline._append_chapter_text

    def boom(source, ch, drama_id, text):
        if ch.chapter_id == "c2":
            raise RuntimeError("unexpected")
        return real(source, ch, drama_id, text)
    monkeypatch.setattr(pipeline, "_append_chapter_text", boom)
    client.post("/api/sources/alpha/import",
                json={"series_id": "s1", "chapter_ids": ["c1", "c2", "c10"], "drama_id": did})
    _wait(f"sourceimport_{did}")
    rows = {x["chapter_id"]: x["status"] for x in _state(client, did).json()["retry"]}
    # c2 was being saved when the error hit: shown, but not retried automatically
    assert rows == {"c1": "failed", "c2": "partial", "c10": "not_attempted"}


def test_a_text_write_error_leaves_the_chapter_retryable(client, fakes, monkeypatch):
    """The text is appended with its offset recorded first, so a failed
    write is cut off again: that chapter is "failed", not "partial"."""
    from sources import pipeline
    fakes["alpha"] = _make("alpha")
    did = _novel()

    def disk_full(fd):
        raise OSError("disk full")
    monkeypatch.setattr(pipeline, "_fsync", disk_full)
    client.post("/api/sources/alpha/import",
                json={"series_id": "s1", "chapter_ids": ["c2"], "drama_id": did})
    _wait(f"sourceimport_{did}")
    body = _state(client, did).json()
    assert [(x["chapter_id"], x["status"]) for x in body["retry"]] == [("c2", "failed")]
    assert body["retry_count"] == 1


def test_a_crash_after_the_text_is_written_retries_through_the_api_once(client, fakes,
                                                                      monkeypatch):
    """The text's offset survives the manifest the service saves after the
    crash, so the retry finds the text already there."""
    import os

    from sources import pipeline
    fakes["alpha"] = _make("alpha")
    did = _novel()
    real = pipeline._record_imported

    def crash(*a, **kw):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(pipeline, "_record_imported", crash)
    client.post("/api/sources/alpha/import",
                json={"series_id": "s1", "chapter_ids": ["c2"], "drama_id": did})
    _wait(f"sourceimport_{did}")
    assert [x["chapter_id"] for x in _state(client, did).json()["retry"]] == ["c2"]
    assert store.import_text_offset("alpha", "s1", did, "c2") is not None

    monkeypatch.setattr(pipeline, "_record_imported", real)
    res = _start(client, did, ["c2"])
    assert [c["outcome"] for c in res["chapters"]] == ["imported"]
    path = os.path.join(db.drama_dir(did), pipeline.RAW_NOVEL_FILENAME)
    with open(path, encoding="utf-8") as f:
        assert f.read().count("text of c2") == 20
    assert _state(client, did).json()["retry"] == []


def test_rows_older_than_the_drama_are_not_shown(client, fakes):
    """A library reset starts drama ids at 1 again while sources.db stays:
    an old library's manifest must not show up on the new drama."""
    fakes["alpha"] = _make("alpha")
    did = _novel()
    store.record_import_retry("alpha", "s1", did, [("c2", "old", "failed", "old error")])
    with store.connect() as conn:
        conn.execute("UPDATE import_retry SET updated_at=0")
    assert _state(client, did).json()["retry"] == []


def test_import_state_auth_on_permission_and_ownership(fakes):
    from fastapi.testclient import TestClient
    from api import auth as api_auth
    from api.api_config import ApiSettings
    from api.server import create_app
    from services import auth_service
    fakes["alpha"] = _make("alpha")
    a = auth_service.add_user("a@example.com")
    b = auth_service.add_user("b@example.com")
    private = db.create_drama(title_en="A private", media_type="novel",
                              content_mode="novel_narration", owner_user_id=a["id"],
                              is_private=1)
    store.record_import_retry("alpha", "s1", private, [("c2", "secret title", "failed", "x")])
    c = TestClient(create_app(ApiSettings(auth_mode="on")),
                   base_url="https://baihe.example.com", raise_server_exceptions=False)
    q = {"series_id": "s1", "drama_id": private}
    assert c.get("/api/sources/alpha/import-state", params=q).status_code == 401

    def hdrs(user):
        s = auth_service.create_session(user["id"])
        return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}"}
    hb = hdrs(b)
    assert c.get("/api/sources/alpha/import-state", params=q, headers=hb).status_code == 403
    auth_service.grant_permission(b["id"], "sources.import")
    hidden = c.get("/api/sources/alpha/import-state", params=q, headers=hb)
    missing = c.get("/api/sources/alpha/import-state",
                    params={**q, "drama_id": 999999}, headers=hb)
    assert hidden.status_code == missing.status_code == 404
    assert "secret title" not in hidden.text
    assert hidden.json()["error"]["message"].replace(str(private), "N") == \
        missing.json()["error"]["message"].replace("999999", "N")
    auth_service.grant_permission(a["id"], "sources.import")
    mine = c.get("/api/sources/alpha/import-state", params=q, headers=hdrs(a))
    assert mine.status_code == 200 and mine.json()["retry_count"] == 1


# ---------------------------------------------------------------------------
# AI recovery of a chapter whose page layout changed ("needs_ai")
# ---------------------------------------------------------------------------

import translate_engines  # noqa: E402
from api import auth as api_auth  # noqa: E402
from api.api_config import ApiSettings  # noqa: E402
from api.server import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from services import auth_service, settings_service  # noqa: E402
from services import sources_extraction_service as extraction  # noqa: E402
from sources import pipeline  # noqa: E402
from sources.models import SourceError  # noqa: E402
from tests.sources_helpers import ScriptedTransport, html  # noqa: E402
from tests.test_adaptive_extraction import (FakeEngine, chapter_html,  # noqa: E402,F401
                                            fake_llm)
from tests.test_api_sources_import import _raw  # noqa: E402

C2_URL = f"{HOST}/c/2?t={SECRET}"
LAYOUT = SourceError("layout", FailureReason.LAYOUT_CHANGED)


def _layout_fake(name, pages=None, read_page=True):
    """A novel adapter whose chapter c2 fails with LAYOUT_CHANGED after it
    GETs its page through the client (as a real adapter does)."""
    Base = _make(name, fail={"c2": LAYOUT})

    class Fake(Base):
        def __init__(self, client=None, **kw):
            kw["transport"] = ScriptedTransport(pages if pages is not None else {
                C2_URL: html(chapter_html(12))})
            super().__init__(client, **kw)

        def get_chapter_text(self, ch):
            if read_page and ch.chapter_id == "c2":
                self.client.get(ch.url)
            return super().get_chapter_text(ch)
    return Fake


@pytest.fixture
def engines(monkeypatch, fake_llm, isolated_db):  # noqa: F811
    built = []
    monkeypatch.setattr(extraction, "_REVIEWS", {})
    monkeypatch.setattr(extraction, "_LAYOUT_PAGES", {})
    monkeypatch.setattr(settings_service, "resolve_key", lambda name, env_path=None: SECRET)

    def get_engine(name, api_key=None, model=None, free_tier=False, base_url=None):
        e = FakeEngine(lambda prompt: None)
        built.append((name, e))
        return e
    monkeypatch.setattr(translate_engines, "get_engine", get_engine)
    return built


def _recover(client, did, body=None, cid="c2", name="alpha", headers=None):
    payload = {"series_id": "s1", "drama_id": did, "engine": "claude", "confirm": True}
    payload.update(body or {})
    return client.post(f"/api/sources/{name}/import/{cid}/ai-recover", json=payload,
                       headers=headers or {})


def _stopped(client, fakes, did, **kw):
    fakes["alpha"] = _layout_fake("alpha", **kw)
    return _start(client, did, ["c1", "c2", "c10"])


def test_layout_change_is_needs_ai_and_stops_the_run(client, fakes):
    did = _novel()
    res = _stopped(client, fakes, did)
    outcomes = {c["chapter_id"]: c["outcome"] for c in res["chapters"]}
    assert outcomes == {"c1": "imported", "c2": "needs_ai", "c10": "not_attempted"}
    assert [c["error"] for c in res["chapters"] if c["outcome"] == "needs_ai"] == [
        pipeline.NEEDS_AI_TEXT]
    assert res["partial"] is True and res["retry_chapter_ids"] == ["c10"]
    body = _state(client, did).json()
    assert [(x["chapter_id"], x["status"]) for x in body["retry"]] == [
        ("c2", "needs_ai"), ("c10", "not_attempted")]
    assert body["retry_count"] == 1


def test_page_is_kept_for_the_confirm_and_never_returned(client, fakes, engines):
    did = _novel()
    _stopped(client, fakes, did)
    state = _state(client, did)
    assert "第12章" not in state.text and "t=" not in state.text
    kept = extraction.take_layout_page(did, "alpha", "s1", "c2")
    assert kept is not None and kept[0] == C2_URL and "第12章" in kept[1]


def test_run_without_a_callback_records_needs_ai(fakes, isolated_db):
    did = _novel()
    fakes["alpha"] = _layout_fake("alpha")
    adapter = fakes["alpha"]()
    pipeline.run_import_job("sourceimport_x", "alpha", adapter.get_chapters("s1"), did,
                            adapter=adapter)
    assert [(r["chapter_id"], r["status"]) for r in store.import_retry_rows("alpha", "s1", did)] \
        == [("c2", "needs_ai")]
    assert "text of c1" in _raw(did) and "text of c10" not in _raw(did)


def test_ai_recover_opens_a_review_and_writes_nothing(client, fakes, engines):
    did = _novel()
    _stopped(client, fakes, did)
    before = _raw(did)
    r = _recover(client, did)
    assert r.status_code == 200, r.text
    res = _result(client, f"sourceimport_{did}")[1]["result"]
    assert res["review_open"] is True and res["llm_calls"] <= 1
    assert len(engines) == 1 and len(engines[0][1].calls) <= 1
    assert _raw(did) == before
    assert store.imported_chapter_ids("alpha", "s1", did) == {"c1"}
    rv = client.get(f"/api/sources/dramas/{did}/extraction")
    assert rv.status_code == 200 and rv.json()["why"] == "recovery"
    assert SECRET not in rv.text and "t=" not in rv.text


def test_reviewed_recovery_imports_like_the_adapter_would(client, fakes, engines):
    did = _novel()
    _stopped(client, fakes, did)
    _recover(client, did)
    _wait(f"sourceimport_{did}")
    rev = client.get(f"/api/sources/dramas/{did}/extraction").json()["revision"]
    r = client.post(f"/api/sources/dramas/{did}/extraction/import", json={"revision": rev})
    assert r.status_code == 200, r.text
    assert _wait(f"sourceimport_{did}")["status"] == "done"
    assert store.imported_chapter_ids("alpha", "s1", did) == {"c1", "c2"}
    assert "第2章" in _raw(did) and "第12章" in _raw(did)
    assert [x["chapter_id"] for x in _state(client, did).json()["retry"]] == ["c10"]
    assert client.get(f"/api/sources/dramas/{did}/extraction").status_code == 404


def test_ai_recover_fetches_once_when_the_kept_page_is_gone(client, fakes, engines):
    did = _novel()
    _stopped(client, fakes, did)
    extraction.take_layout_page(did, "alpha", "s1", "c2")
    r = _recover(client, did)
    assert r.status_code == 200, r.text
    assert _result(client, f"sourceimport_{did}")[1]["result"]["review_open"] is True


def test_ai_recover_needs_confirm_and_an_engine(client, fakes, engines):
    did = _novel()
    _stopped(client, fakes, did)
    assert _recover(client, did, {"confirm": False}).status_code == 422
    r = client.post("/api/sources/alpha/import/c2/ai-recover",
                    json={"series_id": "s1", "drama_id": did, "engine": "claude"})
    assert r.status_code == 422
    r = client.post("/api/sources/alpha/import/c2/ai-recover",
                    json={"series_id": "s1", "drama_id": did, "confirm": True})
    assert r.status_code == 422
    assert _recover(client, did, {"engine": "nope"}).status_code == 422
    assert engines == []


def test_ai_recover_conflicts(client, fakes, engines):
    did = _novel()
    _stopped(client, fakes, did)
    assert _recover(client, did, cid="c1").status_code == 409        # already imported
    assert _recover(client, did).status_code == 200
    _wait(f"sourceimport_{did}")
    assert _recover(client, did).status_code == 409                  # a review is open
    assert _recover(client, 99999).status_code == 404


def test_ai_recover_refuses_while_a_job_runs(client, fakes, engines):
    did = _novel()
    _stopped(client, fakes, did)
    gate = threading.Event()
    fakes["alpha"] = _make("alpha", gate=gate)
    client.post("/api/sources/alpha/import",
                json={"series_id": "s1", "chapter_ids": ["c10"], "drama_id": did})
    try:
        assert _recover(client, did).status_code == 409
    finally:
        gate.set()


def test_ai_recover_with_no_text_keeps_needs_ai(client, fakes, engines):
    did = _novel()
    _stopped(client, fakes, did, pages={C2_URL: html("<html><body>nothing</body></html>")})
    assert _recover(client, did).status_code == 200
    assert _wait(f"sourceimport_{did}")["status"] == "error"
    assert client.get(f"/api/sources/dramas/{did}/extraction").status_code == 404
    assert _state(client, did).json()["retry"][0]["status"] == "needs_ai"


def test_ai_recover_paid_engine_needs_engines_paid(fakes, engines):
    did = _novel()
    fakes["alpha"] = _layout_fake("alpha")
    c = TestClient(create_app(ApiSettings(auth_mode="on")),
                   base_url="https://baihe.example.com", raise_server_exceptions=False)
    u = auth_service.add_user("kid@example.com")
    auth_service.grant_permission(u["id"], "sources.import")
    s = auth_service.create_session(u["id"])
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
         api_auth.CSRF_HEADER: s["csrf_token"]}
    assert _recover(c, did, headers=h).status_code == 403
    assert engines == []


def test_kept_pages_are_capped(monkeypatch):
    monkeypatch.setattr(extraction, "_LAYOUT_PAGES", {})
    for cid in ("a", "b", "c", "d"):
        extraction.stash_layout_page(1, "alpha", "s1", cid, "https://x.invalid/" + cid, "<p>x</p>")
    assert extraction.take_layout_page(1, "alpha", "s1", "a") is None     # oldest dropped
    assert extraction.take_layout_page(1, "alpha", "s1", "d")[0] == "https://x.invalid/d"
    assert extraction.take_layout_page(1, "alpha", "s1", "d") is None     # taken once
    big = "x" * (extraction.MAX_LAYOUT_PAGE_CHARS + 1)
    extraction.stash_layout_page(1, "alpha", "s1", "e", "https://x.invalid/e", big)
    extraction.stash_layout_page(1, "alpha", "s1", "f", None, "<p>x</p>")
    assert extraction.take_layout_page(1, "alpha", "s1", "e") is None
    assert extraction.take_layout_page(1, "alpha", "s1", "f") is None
