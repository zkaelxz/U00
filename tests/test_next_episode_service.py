"""services/next_episode_service.py: the next title in a series and queueing it ahead of time."""
import pytest

import db
from core import Line
from lib.errors import ConflictError, NotFoundError
from services import next_episode_service as nxt, queue_service as q


def _series(numbered=True):
    sid = db.get_or_create_series("Show")
    ids = [db.create_drama(title_en=f"Ep {i}", title_zh="题", series_id=sid,
                           episode_number=i if numbered else None, audio_filename=f"{i}.mp3",
                           transcript_mode="whisper") for i in (1, 2, 3)]
    return ids


def test_the_next_title_follows_episode_numbers(isolated_db):
    e1, e2, e3 = _series()
    assert nxt.get_next(None, e1) == {"drama_id": e2, "title": "Ep 2", "state": "idle", "free": False}
    assert nxt.get_next(None, e3)["drama_id"] is None


def test_without_episode_numbers_the_next_title_is_the_one_created_after(isolated_db):
    e1, e2, _ = _series(numbered=False)
    assert nxt.get_next(None, e1)["drama_id"] == e2


def test_a_translated_title_is_ready(isolated_db):
    e1, e2, _ = _series()
    db.save_lines(e2, [Line(idx=0, start=0, end=1, zh="你好", en="Hello")])
    assert nxt.get_next(None, e1)["state"] == "ready"


def test_prepare_queues_the_steps_once_and_reports_working(isolated_db):
    e1, e2, _ = _series()
    assert nxt.prepare_next(None, e1, True, at_pc=True)["state"] == "working"
    assert [(i["drama_id"], i["step"]) for i in q.get_queue(None)["items"]] == [
        (e2, "transcribe"), (e2, "translate"), (e2, "export")]
    nxt.prepare_next(None, e1, True, at_pc=True)
    assert len(q.get_queue(None)["items"]) == 3


def test_prepare_refuses_when_there_is_no_next_title_or_no_media(isolated_db):
    e1, e2, e3 = _series()
    with pytest.raises(NotFoundError):
        nxt.prepare_next(None, e3, True, at_pc=True)
    db.update_drama(e2, audio_filename="")
    with pytest.raises(ConflictError):
        nxt.prepare_next(None, e1, True, at_pc=True)


def test_only_a_free_engine_is_free_and_ollama_is_billed(isolated_db, free_engine):
    e1, e2, _ = _series()
    db.update_drama(e2, translation_engine=free_engine)
    assert nxt.get_next(None, e1)["free"] is True
    db.update_drama(e2, translation_engine="ollama")
    assert nxt.get_next(None, e1)["free"] is False


def test_a_transcribed_title_gets_only_translate_and_export(isolated_db):
    e1, e2, _ = _series()
    db.save_lines(e2, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    nxt.prepare_next(None, e1, True, at_pc=True)
    assert [i["step"] for i in q.get_queue(None)["items"]] == ["translate", "export"]


def test_unnumbered_titles_after_numbered_ones_read_oldest_first(isolated_db):
    sid = db.get_or_create_series("Mixed")
    one = db.create_drama(title_en="One", title_zh="题", series_id=sid, episode_number=1, audio_filename="1.mp3")
    old = db.create_drama(title_en="Old", title_zh="题", series_id=sid, audio_filename="2.mp3")
    new = db.create_drama(title_en="New", title_zh="题", series_id=sid, audio_filename="3.mp3")
    assert nxt.get_next(None, one)["drama_id"] == old
    assert nxt.get_next(None, old)["drama_id"] == new
