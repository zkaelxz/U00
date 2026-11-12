"""Moving a saved novel chapter (services/novel_chapter_move_service.py and
POST /api/novel/dramas/{id}/raw-novel/chapters/{n}/move): the raw text, the chapter list and the
chapter's lines move together and keep their translations. No network or models."""

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from core import Line, line_from_row
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services import novel_chapter_move_service as svc
from services import novel_chapters_service, novel_line_chapters_service
from sources import chapter_manifest, pipeline, store
from sources.models import ChapterInfo


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _import(did, n, title, body):
    ch = ChapterInfo(source="xbanxia", series_id="s1", chapter_id=f"c{n}", title=title)
    assert pipeline._append_chapter_text("xbanxia", ch, did, body) == ""
    pipeline._clear_in_flight("xbanxia", ch, did)


TEXTS = ["One", "甲。", "乙。", "Two", "丙。", "Three", "丁。", "戊。"]


def _book(isolated_db):
    did = db.create_drama(title_en="D")
    _import(did, 1, "One", "甲。\n\n乙。")
    _import(did, 2, "Two", "丙。")
    _import(did, 3, "Three", "丁。\n\n戊。")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=t, en=f"en{i}")
                        for i, t in enumerate(TEXTS)])
    return did


def _raw(did):
    with open(chapter_manifest.raw_path(did), encoding="utf-8", newline="") as f:
        return f.read()


def _state(did):
    with open(chapter_manifest.manifest_path(did), encoding="utf-8") as f:
        return _raw(did), f.read(), [(r["id"], r["idx"], r["zh"], r["en"]) for r in db.load_lines(did)]


def _state_lines(did):
    return [(r["id"], r["idx"], r["zh"], r["en"]) for r in db.load_lines(did)]


def test_moving_a_chapter_reorders_text_list_and_lines_and_keeps_translations(isolated_db):
    did = _book(isolated_db)
    before_ids = {r["zh"]: (r["id"], r["en"]) for r in db.load_lines(did)}
    out = svc.move_chapter(did, 3, 1)
    assert out["lines_moved"] == 8
    titles = [c["title"] for c in novel_chapters_service.list_chapters(did)["chapters"]]
    assert titles == ["Three", "One", "Two"]
    assert _raw(did).startswith("Three")
    assert novel_chapters_service.read_chapter(did, 3)["text"].startswith("Two")
    rows = db.load_lines(did)
    assert [r["zh"] for r in rows] == ["Three", "丁。", "戊。", "One", "甲。", "乙。", "Two", "丙。"]
    assert [r["idx"] for r in rows] == list(range(8))
    assert [(r["start"], r["end"]) for r in rows] == [(float(i), i + 1.0) for i in range(8)]
    for r in rows:   # the same rows, with their own translations
        assert (r["id"], r["en"]) == before_ids[r["zh"]]
    mapped = novel_line_chapters_service.line_chapters(did)["chapters"]
    assert [(c["number"], c["title"], c["line_count"]) for c in mapped] == [
        (1, "Three", 3), (2, "One", 3), (3, "Two", 2)]


def test_a_failed_file_swap_puts_the_lines_back(isolated_db, monkeypatch):
    did = _book(isolated_db)
    before = _state(did)

    real, calls = svc.edit._swap_in, []

    def first_fails(tmp, dest):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk")
        return real(tmp, dest)
    monkeypatch.setattr(svc.edit, "_swap_in", first_fails)
    with pytest.raises(OSError):
        svc.move_chapter(did, 2, 1)
    monkeypatch.undo()
    assert _state(did) == before


def test_a_failed_manifest_swap_restores_the_raw_text_too(isolated_db, monkeypatch):
    did = _book(isolated_db)
    before = _state(did)
    real = svc.edit._swap_in
    calls = []

    def second_fails(tmp, dest):
        calls.append(dest)
        if len(calls) == 2:
            raise OSError("disk")
        return real(tmp, dest)
    monkeypatch.setattr(svc.edit, "_swap_in", second_fails)
    with pytest.raises(OSError):
        svc.move_chapter(did, 2, 1)
    monkeypatch.undo()
    assert _state(did) == before


def test_a_move_that_cannot_put_the_lines_back_says_where_they_are_saved(isolated_db, monkeypatch):
    did = _book(isolated_db)
    real_save = db.save_lines
    saves = []

    def second_fails(*a, **k):
        saves.append(1)
        if len(saves) > 1:
            raise RuntimeError("db")
        return real_save(*a, **k)
    monkeypatch.setattr(db, "save_lines", second_fails)
    monkeypatch.setattr(svc.edit, "_swap_in", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(ConflictError) as err:
        svc.move_chapter(did, 2, 1)
    assert "line history" in err.value.message


def test_lines_that_do_not_line_up_refuse_the_move(isolated_db):
    did = _book(isolated_db)
    rows = db.load_lines(did)
    rows[3]["zh"] = "Two (edited)"
    db.save_lines(did, [Line(idx=r["idx"], start=r["start"], end=r["end"], zh=r["zh"], en=r["en"],
                             id=r["id"]) for r in rows])
    before = _state(did)
    with pytest.raises(ConflictError):
        svc.move_chapter(did, 3, 1)
    assert _state(did) == before


def _rewrite_lines(did, texts, timed=False):
    db.save_lines(did, [Line(idx=r["idx"], start=r["start"] + (0.5 if timed else 0), end=r["end"],
                             zh=t, en=r["en"], id=r["id"])
                        for r, t in zip(db.load_lines(did), texts)], fields=("zh", "start", "end"))


def test_a_body_line_equal_to_a_later_title_refuses_instead_of_cutting_early(isolated_db):
    did = db.create_drama(title_en="D")
    _import(did, 1, "上", "甲。\n\n下\n\n乙。")
    _import(did, 2, "下", "丙。")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=t, en=f"en{i}")
                        for i, t in enumerate(["上", "甲。", "下", "乙。", "下", "丙。"])])
    before = _state(did)
    with pytest.raises(ConflictError):
        svc.move_chapter(did, 2, 1)
    assert _state(did) == before


def test_lines_with_their_own_times_refuse_the_move(isolated_db):
    did = _book(isolated_db)
    _rewrite_lines(did, TEXTS, timed=True)
    before = _state(did)
    with pytest.raises(ConflictError) as err:
        svc.move_chapter(did, 3, 1)
    assert "times" in err.value.message
    assert _state(did) == before


def test_a_raw_file_that_cannot_be_restored_still_gets_the_lines_put_back(isolated_db, monkeypatch):
    did = _book(isolated_db)
    before_lines = _state(did)[2]
    calls = []
    real = svc.edit._swap_in

    def second_fails(tmp, dest):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk")
        return real(tmp, dest)
    monkeypatch.setattr(svc.edit, "_swap_in", second_fails)
    monkeypatch.setattr(svc.edit, "_restore_raw", lambda *a: (_ for _ in ()).throw(OSError("gone")))
    with pytest.raises(ConflictError) as err:
        svc.move_chapter(did, 2, 1)
    monkeypatch.undo()
    assert ".before-edit" in err.value.message
    assert _state_lines(did) == before_lines
    # The old chapter list must not stay valid over the moved text: no list reads as unsplit text.
    assert not os.path.exists(chapter_manifest.manifest_path(did))
    assert novel_chapters_service.list_chapters(did)["split"] is False


def test_refusals(isolated_db):
    did = _book(isolated_db)
    with pytest.raises(NotFoundError):
        svc.move_chapter(did, 9, 1)
    with pytest.raises(InvalidInputError):
        svc.move_chapter(did, 2, 2)
    store.mark_text_in_flight("xbanxia", "s1", did, "c9", "Later", "Interrupted.", 5)
    with pytest.raises(ConflictError):
        svc.move_chapter(did, 3, 1)


def test_a_translation_text_that_copies_the_raw_chapters_follows_the_move(isolated_db):
    did = _book(isolated_db)
    copy = os.path.join(os.path.dirname(chapter_manifest.raw_path(did)), "novel_narration_source.txt")
    with open(chapter_manifest.raw_path(did), "rb") as f, open(copy, "wb") as g:
        g.write(f.read())
    assert svc.move_chapter(did, 3, 1)["translation_text_updated"] is True
    with open(copy, encoding="utf-8", newline="") as f:
        assert f.read() == _raw(did)


def test_a_translation_text_that_is_not_a_copy_is_left_alone_and_reported(isolated_db):
    did = _book(isolated_db)
    copy = os.path.join(os.path.dirname(chapter_manifest.raw_path(did)), "novel_narration_source.txt")
    with open(copy, "w", encoding="utf-8") as g:
        g.write("pasted and edited")
    out = svc.move_chapter(did, 3, 1)
    assert (out["translation_text_updated"], out["translation_text_stale"]) == (False, True)
    with open(copy, encoding="utf-8") as f:
        assert f.read() == "pasted and edited"


def test_route_moves_a_chapter(client, isolated_db):
    did = _book(isolated_db)
    r = client.post(f"/api/novel/dramas/{did}/raw-novel/chapters/2/move", json={"to_number": 1})
    assert r.status_code == 200 and r.json()["lines_moved"] == 8
    assert client.post(f"/api/novel/dramas/{did}/raw-novel/chapters/2/move",
                       json={"to_number": 9}).status_code == 404


class _Killed(BaseException):
    """Stands in for the process dying: raised where the in-process undo would run, so the
    move stops with whatever it had already written, as after a hard kill."""


def _kill_at(monkeypatch, step):
    """Makes the move fail at `step` and skips its undo."""
    monkeypatch.setattr(svc, "_roll_back", lambda *a, **k: (_ for _ in ()).throw(_Killed()))
    if step == "lines":
        monkeypatch.setattr(db, "save_lines", lambda *a, **k: (_ for _ in ()).throw(_Killed()))
    elif step == "backup":
        monkeypatch.setattr(svc.shutil, "copyfile", lambda *a, **k: (_ for _ in ()).throw(_Killed()))
    else:
        real, calls = svc.edit._swap_in, []

        def nth_fails(tmp, dest):
            calls.append(dest)
            if len(calls) == {"raw": 1, "manifest": 2}[step]:
                raise _Killed()
            return real(tmp, dest)
        monkeypatch.setattr(svc.edit, "_swap_in", nth_fails)


def _leftovers(did):
    folder = os.path.dirname(chapter_manifest.raw_path(did))
    return sorted(n for n in os.listdir(folder) if n.endswith(".part") or n == svc.JOURNAL_FILENAME)


@pytest.mark.parametrize("step", ["lines", "backup", "raw"])
def test_a_move_killed_before_the_text_swap_is_undone_at_the_next_start(isolated_db, monkeypatch, step):
    did = _book(isolated_db)
    before = _state(did)
    _kill_at(monkeypatch, step)
    with pytest.raises(_Killed):
        svc.move_chapter(did, 3, 1)
    monkeypatch.undo()
    assert svc.recover_interrupted_moves() == [did]
    assert _state(did) == before
    assert _leftovers(did) == []
    svc.move_chapter(did, 3, 1)   # and the title can be moved again
    assert _raw(did).startswith("Three")


def test_a_move_killed_after_the_text_swap_is_finished_at_the_next_start(isolated_db, monkeypatch):
    did = _book(isolated_db)
    done = _book(isolated_db)
    svc.move_chapter(done, 3, 1)
    copy = os.path.join(os.path.dirname(chapter_manifest.raw_path(did)), "novel_narration_source.txt")
    with open(chapter_manifest.raw_path(did), "rb") as f, open(copy, "wb") as g:
        g.write(f.read())
    _kill_at(monkeypatch, "manifest")
    with pytest.raises(_Killed):
        svc.move_chapter(did, 3, 1)
    monkeypatch.undo()
    assert svc.recover_interrupted_moves() == [did]
    raw, listed, lines = _state(did)
    want_raw, want_list, want_lines = _state(done)
    assert raw == want_raw and listed == want_list
    assert [r[1:] for r in lines] == [r[1:] for r in want_lines]
    assert [c["title"] for c in novel_chapters_service.list_chapters(did)["chapters"]] == [
        "Three", "One", "Two"]
    with open(copy, encoding="utf-8", newline="") as f:
        assert f.read() == raw
    assert _leftovers(did) == []


def test_an_interrupted_move_that_cannot_be_recognised_is_left_alone_and_blocks_moves(
        isolated_db, monkeypatch):
    did = _book(isolated_db)
    _kill_at(monkeypatch, "raw")
    with pytest.raises(_Killed):
        svc.move_chapter(did, 3, 1)
    monkeypatch.undo()
    rows = [line_from_row(r) for r in db.load_lines(did)]
    db.save_lines(did, rows[1:])   # the lines changed after the stop: neither order any more
    after = _state_lines(did)
    assert svc.recover_interrupted_moves() == []
    assert _state_lines(did) == after
    assert svc.JOURNAL_FILENAME in _leftovers(did)
    with pytest.raises(ConflictError) as err:
        svc.move_chapter(did, 1, 2)
    assert "line history" in err.value.message


def test_startup_finishes_interrupted_moves(isolated_db, monkeypatch):
    from api import background
    from services import auth_service, jobs_service
    for name in ("start_background_services", "start_gpu_queue_poller",
                 "start_reeval_scheduler", "stop_gpu_queue_poller", "stop_reeval_scheduler"):
        monkeypatch.setattr(background, name, lambda *_: None)
    monkeypatch.setattr(jobs_service, "sweep_stale_job_records", lambda: 0)
    monkeypatch.setattr(auth_service, "sweep_stale_sessions", lambda: 0)
    ran = []
    monkeypatch.setattr(svc, "recover_interrupted_moves", lambda: ran.append(1) or [])
    with TestClient(create_app(ApiSettings(background_services=True))):
        pass
    assert ran == [1]


def test_a_move_killed_after_the_list_swap_is_finished_at_the_next_start(isolated_db, monkeypatch):
    did = _book(isolated_db)
    done = _book(isolated_db)
    svc.move_chapter(done, 3, 1)
    copy = os.path.join(os.path.dirname(chapter_manifest.raw_path(did)), "novel_narration_source.txt")
    with open(chapter_manifest.raw_path(did), "rb") as f, open(copy, "wb") as g:
        g.write(f.read())
    monkeypatch.setattr(svc.edit, "_copy_to_translation_text",
                        lambda *a, **k: (_ for _ in ()).throw(_Killed()))
    with pytest.raises(_Killed):
        svc.move_chapter(did, 3, 1)
    monkeypatch.undo()
    assert svc.recover_interrupted_moves() == [did]
    assert _state(did)[:2] == _state(done)[:2]
    with open(copy, encoding="utf-8", newline="") as f:
        assert f.read() == _raw(did)
    assert _leftovers(did) == []


def test_a_recovery_killed_part_way_is_finished_by_the_next_one(isolated_db, monkeypatch):
    did = _book(isolated_db)
    before = _state(did)
    _kill_at(monkeypatch, "raw")       # lines moved, chapter list removed, old text
    with pytest.raises(_Killed):
        svc.move_chapter(did, 3, 1)
    monkeypatch.undo()
    # The recovery puts the lines back and is then killed writing the chapter list.
    monkeypatch.setattr(svc, "_write_durably", lambda *a, **k: (_ for _ in ()).throw(_Killed()))
    with pytest.raises(_Killed):
        with svc._move_lock(did):
            svc._recover(did)
    monkeypatch.undo()
    assert not os.path.exists(chapter_manifest.manifest_path(did))
    assert svc.recover_interrupted_moves() == [did]
    assert _state(did) == before
    assert _leftovers(did) == []


def test_a_damaged_journal_is_a_clear_refusal_not_a_crash(isolated_db, monkeypatch):
    did = _book(isolated_db)
    _kill_at(monkeypatch, "raw")
    with pytest.raises(_Killed):
        svc.move_chapter(did, 3, 1)
    monkeypatch.undo()
    with open(os.path.join(os.path.dirname(chapter_manifest.raw_path(did)), svc.JOURNAL_FILENAME),
              "w", encoding="utf-8") as f:
        f.write('{"old_ids": [1, 2')
    assert svc.recover_interrupted_moves() == []
    with pytest.raises(ConflictError) as err:
        svc.move_chapter(did, 1, 2)
    assert "damaged" in err.value.message


def test_a_second_copy_of_the_app_starting_waits_for_a_live_move(isolated_db, monkeypatch):
    """Startup recovery in another process must not undo a move that is still running: it
    waits on the move's lock, then finds the move finished."""
    import threading
    did = _book(isolated_db)
    done = _book(isolated_db)
    svc.move_chapter(done, 3, 1)
    real, calls = svc.edit._swap_in, []
    at_list_swap, go_on = threading.Event(), threading.Event()

    def pause_before_list_swap(tmp, dest):
        calls.append(dest)
        if len(calls) == 2:          # the text is swapped; the chapter list is next
            at_list_swap.set()
            assert go_on.wait(10)
        return real(tmp, dest)
    monkeypatch.setattr(svc.edit, "_swap_in", pause_before_list_swap)
    mover = threading.Thread(target=svc.move_chapter, args=(did, 3, 1))
    mover.start()
    assert at_list_swap.wait(10)
    recovered = []
    # lib.file_lock: separate open() calls conflict even inside one process, as two processes do.
    starter = threading.Thread(target=lambda: recovered.append(svc.recover_interrupted_moves()))
    starter.start()
    starter.join(0.5)
    assert starter.is_alive()        # waiting for the move, not undoing it
    go_on.set()
    mover.join(10)
    starter.join(10)
    assert recovered == [[]]
    assert _state(did)[:2] == _state(done)[:2]
    assert [r[2] for r in _state(did)[2]] == [r[2] for r in _state(done)[2]]


def test_startup_gives_up_waiting_on_a_move_lock_that_is_never_let_go(isolated_db, monkeypatch, caplog):
    from lib import file_lock
    did = _book(isolated_db)
    _kill_at(monkeypatch, "raw")
    with pytest.raises(_Killed):
        svc.move_chapter(did, 3, 1)
    monkeypatch.undo()
    monkeypatch.setattr(svc, "_STARTUP_WAIT_SECONDS", 0.1)
    lock = os.path.join(os.path.dirname(chapter_manifest.raw_path(did)), svc.LOCK_FILENAME)
    with file_lock.hold(lock):          # another copy of the app holding it, stuck
        assert svc.recover_interrupted_moves() == []
    assert svc.JOURNAL_FILENAME in _leftovers(did)
    assert any("still holds" in r.getMessage() for r in caplog.records)
    assert svc.recover_interrupted_moves() == [did]   # the next start puts it right


def test_a_title_with_no_folder_refuses_the_move_instead_of_crashing(isolated_db):
    import shutil
    did = db.create_drama(title_en="No folder")
    shutil.rmtree(os.path.join(db.DRAMAS_DIR, str(did)), ignore_errors=True)
    with pytest.raises(ConflictError):
        svc.move_chapter(did, 2, 1)


def test_the_move_lock_is_not_put_in_backups(isolated_db, tmp_path):
    import zipfile
    from services import library_backup_service, library_user_backup_service
    did = _book(isolated_db)
    svc.move_chapter(did, 3, 1)
    lock = os.path.join(os.path.dirname(chapter_manifest.raw_path(did)), svc.LOCK_FILENAME)
    assert os.path.exists(lock)
    full = str(tmp_path / "full.zip")
    library_backup_service.write_backup_zip(full)
    names = zipfile.ZipFile(full).namelist()
    assert any(n.endswith(chapter_manifest.RAW_NOVEL_FILENAME) for n in names)
    assert not any(n.endswith(svc.LOCK_FILENAME) for n in names)
    assert not any(p.endswith(svc.LOCK_FILENAME)
                   for p, _ in library_user_backup_service._drama_media_files(did))
