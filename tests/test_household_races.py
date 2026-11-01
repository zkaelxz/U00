"""Races between members of a shared (household) server: several people
editing, uploading and deleting at once. Each test makes the overlap happen,
either by releasing threads through a Barrier or by running the other
member's action at the exact point between a check and its write."""
import contextlib
import io
import os
import threading

import pytest

import core
import db
import page_server
from api.api_config import ApiSettings
from api.server import create_app
from fastapi.testclient import TestClient
from jobs import job_launch, job_runner
import background_jobs as bg
from lib import storage_errors
from services import drama_service, library_admin_service, media_upload_service
from services import ownership_service as own
from services import scanlate_pages_service
from lib.errors import ConflictError, NotFoundError

THREADS = 8
LOCAL = {"user_id": None, "is_admin": True, "is_local_owner": True}


def _race(worker, threads=THREADS):
    barrier = threading.Barrier(threads)
    outcomes = [None] * threads

    def go(n):
        barrier.wait()
        try:
            outcomes[n] = worker(n)
        except Exception as exc:   # an outcome to assert on, not a crash of the thread
            outcomes[n] = exc

    pool = [threading.Thread(target=go, args=(n,)) for n in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join(timeout=20.0)
    assert not any(t.is_alive() for t in pool)
    return outcomes


def _title(lines=6):
    did = db.create_drama(title_en="Shared", source_language="zh")
    db.save_lines(did, [core.Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f"你好{i}",
                                  en=f"hello {i}", speaker="S0") for i in range(lines)])
    return did


@pytest.fixture
def local_client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _line_ids(client, did):
    return [ln["id"] for ln in
            client.get(f"/api/review/dramas/{did}/lines?page_size=200").json()["lines"]]


# --- a write that loses a race with a delete --------------------------------

def test_a_line_add_racing_a_title_delete_is_a_conflict_not_a_crash(local_client, monkeypatch):
    did = _title()
    ids = _line_ids(local_client, did)
    real = db.save_line_history_snapshot

    def deleted_meanwhile(drama_id, *a, **k):
        db.delete_drama(drama_id)   # another member's delete lands after every check passed
        return real(drama_id, *a, **k)
    monkeypatch.setattr(db, "save_line_history_snapshot", deleted_meanwhile)
    resp = local_client.post(f"/api/restructure/dramas/{did}/lines/add",
                             json={"expected_line_ids": ids, "after_line_id": ids[0],
                                   "start": 1.0, "end": 1.5, "zh": "新", "en": "new"})
    assert resp.status_code == 409
    assert resp.json()["error"] == {"code": "conflict",
                                    "message": storage_errors.GONE_MEANWHILE.message}


def test_a_job_whose_title_is_deleted_under_it_says_so(isolated_db):
    did = _title()

    def job():
        db.delete_drama(did)
        db.save_line_history_snapshot(did, db.load_line_objects(did), "before")
    assert job_launch.start_job(f"translate_{did}", job, line_fields=())
    assert job_runner.wait_for_job_threads(10.0)
    status = bg.get_status(f"translate_{did}")
    assert status["status"] == "error"
    assert status["error"] == storage_errors.GONE_MEANWHILE.message
    bg.clear_job(f"translate_{did}")


def test_members_restructuring_while_one_deletes_never_get_a_500(local_client):
    """The stress version: everyone loads the lines, then all write at once
    while one member deletes the title."""
    did = _title(lines=12)
    ids = _line_ids(local_client, did)

    def member(n):
        if n == 0:
            return local_client.delete(f"/api/dramas/{did}?confirm=true&confirm_text=DELETE")
        if n % 2:
            return local_client.post(f"/api/restructure/dramas/{did}/lines/add",
                                     json={"expected_line_ids": ids, "after_line_id": ids[n],
                                           "start": 1.0, "end": 1.5, "zh": "新", "en": "new"})
        return local_client.post(f"/api/characters/dramas/{did}/rename-speaker",
                                 json={"speaker_label": "S0", "new_name": f"N{n}"})
    for resp in _race(member):
        assert not isinstance(resp, Exception), resp
        assert resp.status_code in (200, 404, 409), (resp.status_code, resp.text)
    assert db.get_drama(did) is None
    assert db.load_line_objects(did) == []


# --- two members naming the same new series at once ------------------------

def test_members_creating_the_same_series_at_once_all_get_it(isolated_db):
    """Each lookup sees no series, every insert but one hits the UNIQUE name,
    and those callers can see the winner's series, so they join it."""
    outcomes = _race(lambda _n: own.get_or_create_series_for(LOCAL, "Same new series"))
    assert all(isinstance(o, int) for o in outcomes), outcomes
    assert len(set(outcomes)) == 1
    assert db.get_series_id_by_name("Same new series") == outcomes[0]


def test_losing_the_series_race_to_a_visible_series_joins_it(isolated_db, monkeypatch):
    b = db.auth_create_user("b@example.com")
    member = {"user_id": b, "is_admin": False, "is_local_owner": False}
    theirs = db.create_series("Open")   # public, created after b's lookup
    real = db.get_series_id_by_name
    looked = []

    def lookup(name):
        looked.append(name)
        return None if len(looked) == 1 else real(name)
    monkeypatch.setattr(db, "get_series_id_by_name", lookup)
    assert own.get_or_create_series_for(member, "Open") == theirs


def test_losing_the_series_race_to_a_private_series_is_still_refused(isolated_db, monkeypatch):
    a = db.auth_create_user("a@example.com")
    b = db.auth_create_user("b@example.com")
    member = {"user_id": b, "is_admin": False, "is_local_owner": False}
    db.create_series("Hidden", owner_user_id=a, is_private=True)
    real = db.get_series_id_by_name
    looked = []

    def lookup(name):
        looked.append(name)
        return None if len(looked) == 1 else real(name)
    monkeypatch.setattr(db, "get_series_id_by_name", lookup)
    with pytest.raises(ConflictError, match="That series name is taken"):
        own.get_or_create_series_for(member, "Hidden")


# --- deleting a title while a file is uploaded to it ------------------------

class _SlowUpload(io.RawIOBase):
    """An upload body that stops after its first chunk until `go` is set."""

    def __init__(self, started, go):
        self.started, self.go, self.reads = started, go, 0

    def read(self, size=-1):
        self.reads += 1
        if self.reads == 2:
            self.started.set()
            self.go.wait(10.0)
        return b"" if self.reads > 3 else b"\x00" * 1000


def _upload_in_flight(did):
    started, go, out = threading.Event(), threading.Event(), {}

    def upload():
        try:
            out["result"] = media_upload_service.upload_media(did, "a.wav",
                                                              _SlowUpload(started, go))
        except Exception as exc:
            out["error"] = exc
    t = threading.Thread(target=upload)
    t.start()
    assert started.wait(10.0)
    return go, t, out


def test_a_title_is_not_deleted_while_a_file_uploads_to_it(isolated_db):
    did = _title()
    go, t, out = _upload_in_flight(did)
    try:
        with pytest.raises(ConflictError, match="still being uploaded"):
            drama_service.delete_drama(did, confirm=True, confirm_text="DELETE")
    finally:
        go.set()
        t.join(10.0)
    assert "error" not in out, out.get("error")
    assert db.get_drama(did)["audio_filename"] == out["result"]["name"]
    assert os.path.isfile(os.path.join(db.drama_dir(did), out["result"]["name"]))
    # Once the upload is in place the delete goes through.
    assert drama_service.delete_drama(did, confirm=True, confirm_text="DELETE")["deleted"]


def test_bulk_delete_skips_a_title_with_an_upload_in_flight(isolated_db):
    busy, idle = _title(), _title()
    go, t, out = _upload_in_flight(busy)
    try:
        result = library_admin_service.bulk_delete([busy, idle], confirm=True,
                                                   confirm_text="DELETE")
    finally:
        go.set()
        t.join(10.0)
    assert result["results"] == [{"drama_id": busy, "ok": False, "error": "upload_running"},
                                 {"drama_id": idle, "ok": True}]
    assert "error" not in out and db.get_drama(busy) is not None


def test_an_upload_or_page_import_is_refused_while_the_delete_runs(isolated_db, monkeypatch):
    did = _title()
    seen = {}

    def delete_while_others_try(drama_id):
        with pytest.raises(ConflictError) as upload:
            media_upload_service.upload_media(drama_id, "a.wav", io.BytesIO(b"\x00" * 10))
        with pytest.raises(ConflictError) as pages:
            with scanlate_pages_service.upload_claim(drama_id):
                pass
        seen["refused"] = (str(upload.value), str(pages.value))
        return real(drama_id)
    real = drama_service.hard_delete_drama
    monkeypatch.setattr(drama_service, "hard_delete_drama", delete_while_others_try)
    drama_service.delete_drama(did, confirm=True, confirm_text="DELETE")
    assert seen["refused"]
    assert did not in media_upload_service.claimed


# --- the extension token on first use --------------------------------------

def test_the_first_extension_token_is_the_same_for_everyone_who_asks_at_once(isolated_db):
    with contextlib.suppress(FileNotFoundError):
        os.remove(page_server.token_path())
    tokens = _race(lambda _n: page_server.load_or_create_token(), threads=16)
    assert all(isinstance(t, str) and t for t in tokens), tokens
    assert len(set(tokens)) == 1
    with open(page_server.token_path(), encoding="utf-8") as fh:
        assert fh.read() == tokens[0]
    assert not [n for n in os.listdir(db.LIBRARY_DIR) if n.startswith(".extension_token_")]


def test_an_empty_token_file_left_by_a_crash_is_replaced(isolated_db):
    open(page_server.token_path(), "w").close()
    token = page_server.load_or_create_token()
    assert token
    assert page_server.load_or_create_token() == token


# --- novel uploads take the same claim as media uploads ---------------------

class _BlockingRead(io.RawIOBase):
    def __init__(self, started, go, data=b""):
        self.started, self.go, self.data = started, go, data

    def read(self, size=-1):
        self.started.set()
        self.go.wait(10.0)
        return self.data


def test_a_title_is_not_deleted_while_an_epub_is_being_attached(isolated_db):
    from services import novel_attach_service
    did = _title()
    started, go, out = threading.Event(), threading.Event(), {}

    def attach():
        try:
            novel_attach_service.attach_epub(did, _BlockingRead(started, go))
        except Exception as exc:   # an empty body: refused once it is read
            out["error"] = exc
    t = threading.Thread(target=attach)
    t.start()
    assert started.wait(10.0)
    try:
        with pytest.raises(drama_service.UploadBusy):
            drama_service.delete_drama(did, confirm=True, confirm_text="DELETE")
    finally:
        go.set()
        t.join(10.0)
    assert db.get_drama(did) is not None


@pytest.mark.parametrize("write", ["attach_text", "reference_text", "raw_novel_text"])
def test_a_novel_write_never_brings_back_a_deleted_titles_folder(isolated_db, monkeypatch, write):
    from services import novel_attach_service, novel_files_service
    did = _title()
    real = own.require_drama
    calls = []

    def deleted_after_first_check(drama_id, *a, **k):
        calls.append(drama_id)
        drama = real(drama_id, *a, **k)
        if len(calls) == 1:   # another member's delete lands right after the first check
            drama_service.delete_drama(drama_id, confirm=True, confirm_text="DELETE")
        return drama
    monkeypatch.setattr(own, "require_drama", deleted_after_first_check)
    action = {"attach_text": lambda: novel_attach_service.attach_text(did, "第一章 你好"),
              "reference_text": lambda: novel_files_service.save_reference_text(did, "Chapter 1"),
              "raw_novel_text": lambda: novel_files_service.save_raw_novel_text(did, "第一章 你好")}
    with pytest.raises(NotFoundError):
        action[write]()
    assert not os.path.exists(os.path.join(db.DRAMAS_DIR, str(did)))


def test_bulk_delete_calls_a_busy_scanlate_job_a_job(isolated_db, monkeypatch):
    did = _title()
    # The page-import claim refuses while the title's Scanlate job runs too;
    # reached when the job starts after bulk delete's own job check.
    monkeypatch.setattr(scanlate_pages_service, "job_busy", lambda drama_id: True)
    result = library_admin_service.bulk_delete([did], confirm=True, confirm_text="DELETE")
    assert result["results"] == [{"drama_id": did, "ok": False, "error": "job_running"}]
    assert db.get_drama(did) is not None


def test_a_token_still_being_written_without_hard_links_is_not_replaced(isolated_db):
    # Without hard links another request's link_new leaves an empty file in
    # place for a moment before its token lands there.
    path = page_server.token_path()
    open(path, "w").close()

    def finish_writing():
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("their-token")
    writer = threading.Timer(0.05, finish_writing)
    writer.start()
    try:
        assert page_server.load_or_create_token() == "their-token"
    finally:
        writer.join(5.0)
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == "their-token"
