"""Tests for services/quick_video_service.py: Quick video translate and its watch-once titles.

Stage starts are replaced by fakes (see test_queue_service.FakeJobs); the download is a fake
that records the link, so nothing touches the network."""
import pytest

import db
from core import Line
from lib.errors import ConflictError, ForbiddenError, InvalidInputError
from services import drama_service, profile_service, quick_video_service as qv, queue_service as q, url_media_service
from tests.test_queue_service import jobs  # noqa: F401  (fixture)

URL = "https://example.com/watch?v=abc"


@pytest.fixture
def started_downloads(monkeypatch):
    calls = []
    monkeypatch.setattr(url_media_service, "check_link", lambda url: url)
    monkeypatch.setattr(url_media_service, "start_url_download",
                        lambda drama_id, url, audio_only, *a, **kw: calls.append((drama_id, url, audio_only)) or {"job_id": f"urlmedia_{drama_id}"})
    return calls


def _profile(name="Quick", engine="ollama", **options):
    options = {"quick_source_language": "ja", **options}
    return db.save_preset(name, translation_engine=engine, options_json=profile_service.dump_options(options))


def _start(profile_id=None, watch_once=False, holds_paid=True):
    return qv.start(None, URL, profile_id, watch_once, holds_paid=holds_paid, at_pc=True)


def test_start_creates_a_title_and_queues_the_four_steps_in_order(jobs, started_downloads):
    pid = _profile(quick_export_format="vtt", quick_export_language="bilingual")
    out = _start(pid)
    drama = db.get_drama(out["drama_id"])
    assert drama["source_language"] == "ja" and drama["translation_engine"] == "ollama"
    assert drama["transcript_mode"] == "whisper"
    items = q.get_queue(None)["items"]
    assert [i["step"] for i in items] == ["download", "transcribe", "translate", "export"]
    assert {i["drama_id"] for i in items} == {out["drama_id"]}
    assert q.get_queue(None)["state"] == "running"
    export = [i for i in q._load()["items"] if i["step"] == "export"][0]
    assert export["settings"] == {"format": "vtt", "language": "bilingual"}
    assert "url" not in str(items)


def test_the_profile_chosen_in_settings_is_used_when_none_is_given(jobs, started_downloads):
    pid = _profile()
    qv.set_profile(pid)
    assert _start()["drama_id"]
    assert qv.get_settings()["profile_id"] == pid


@pytest.mark.parametrize("make", ["none", "no_language"])
def test_a_missing_profile_or_language_creates_nothing(jobs, started_downloads, make):
    pid = db.save_preset("Bare", translation_engine="ollama") if make == "no_language" else None
    with pytest.raises(InvalidInputError):
        _start(pid)
    assert db.list_dramas() == [] and q.get_queue(None)["items"] == []


def test_a_paid_profile_is_refused_without_paid_permission_and_creates_nothing(jobs, started_downloads):
    pid = _profile(engine="claude")
    with pytest.raises(ForbiddenError):
        _start(pid, holds_paid=False)
    assert db.list_dramas() == []


def test_a_failed_download_skips_the_later_steps_and_a_good_run_finishes(jobs, started_downloads, monkeypatch):
    monkeypatch.setattr(url_media_service, "start_url_download",
                        lambda drama_id, url, audio_only, *a, **kw: jobs._start(f"urlmedia_{drama_id}"))
    bad = _start(_profile())["drama_id"]
    jobs.fail.add(f"urlmedia_{bad}")
    q.tick()
    jobs.finish(f"urlmedia_{bad}")
    q.tick()
    q.tick()
    q.tick()
    assert _statuses(bad) == ["failed", "skipped", "skipped", "skipped"]
    assert jobs.started == [f"urlmedia_{bad}"]
    run = qv.runs(None)[0]
    assert run["state"] == "stopped" and run["step"] == "download"

    good = _start(_profile("Two"))["drama_id"]
    db.save_lines(good, [Line(idx=0, start=0, end=1, zh="你好", en="hi")])
    for job in (f"urlmedia_{good}", f"transcribe_{good}", f"translate_{good}"):
        q.tick()
        jobs.finish(job)
    q.tick()
    assert _statuses(good) == ["done"] * 4
    assert [r["state"] for r in qv.runs(None)] == ["done", "stopped"]


def _statuses(drama_id):
    return [i["status"] for i in q.get_queue(None)["items"] if i["drama_id"] == drama_id]


def test_a_later_step_does_not_start_after_an_unfinished_one(jobs, started_downloads):
    out = _start(_profile())
    items = q._load()["items"]
    with pytest.raises(ConflictError):
        qv.require_chain_finished(items[1], items)
    qv.require_chain_finished(items[0], items)


def test_runs_show_progress_without_the_link(jobs, started_downloads):
    out = _start(_profile(), watch_once=True)
    run = qv.runs(None)[0]
    assert run["drama_id"] == out["drama_id"] and run["watch_once"] is True
    assert run["state"] == "running" and run["step"] in ("download", "transcribe")
    assert "example.com" not in str(qv.runs(None))


def test_only_a_watch_once_title_can_be_removed_here(jobs, started_downloads):
    keep = db.create_drama(title_en="Mine")
    with pytest.raises(InvalidInputError):
        qv.delete_temporary(None, keep, confirm=True)
    plain = _start(_profile())["drama_id"]
    with pytest.raises(InvalidInputError):
        qv.delete_temporary(None, plain, confirm=True)
    assert db.get_drama(keep) and db.get_drama(plain)


def test_a_watch_once_title_is_removed_with_its_files_after_confirming(jobs, started_downloads, tmp_path):
    other = db.create_drama(title_en="Other")
    did = _start(_profile(), watch_once=True)["drama_id"]
    with pytest.raises(InvalidInputError):
        qv.delete_temporary(None, did, confirm=False)
    qv.delete_temporary(None, did, confirm=True)
    assert db.get_drama(did) is None and db.get_drama(other) is not None
    assert qv.runs(None) == []
    with pytest.raises(InvalidInputError):
        qv.delete_temporary(None, did, confirm=True)


def test_the_sweep_removes_only_old_watch_once_titles(jobs, started_downloads):
    old = _start(_profile(), watch_once=True)["drama_id"]
    fresh = _start(_profile("Two"), watch_once=True)["drama_id"]
    kept = _start(_profile("Three"), watch_once=False)["drama_id"]
    runs = qv._read_runs()
    runs[0]["created_at"] -= qv.TEMPORARY_MAX_AGE_SECONDS + 10
    runs[2]["created_at"] -= qv.TEMPORARY_MAX_AGE_SECONDS + 10
    qv._write_runs(runs)
    assert qv.sweep_temporary() == 1
    assert db.get_drama(old) is None and db.get_drama(fresh) and db.get_drama(kept)
