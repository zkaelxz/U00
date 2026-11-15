"""Tests for services/page_run_service.py: a title page's one-button run as a chain on the PC queue."""
import pytest

import background_jobs
import db
from jobs import gpu_guard
from lib.errors import ConflictError, ForbiddenError, NotFoundError, UnsupportedOperationError
from services import auth_service, glossary_extract_service, page_run_service as runs, queue_service as q
from tests.test_queue_service import _drama, _items, jobs  # noqa: F401  (fixture)


@pytest.fixture
def names(jobs, monkeypatch):  # noqa: F811
    monkeypatch.setattr(glossary_extract_service, "start_lines_glossary_run",
                        lambda drama_id, engine_name=None: jobs._start(f"lines_glossary_{drama_id}"))
    return jobs


def _bare(title="T", **fields):
    """A title with audio and no lines yet."""
    return _drama(title, lines=(), **fields)


def _flow(did):
    return [(i["step"], i["status"]) for i in _items() if i["drama_id"] == did]


def test_the_run_transcribes_then_finds_names_without_the_queue_being_started(names):
    did = _bare()
    view = runs.start_title_flow(None, did, True, at_pc=True)
    assert view["state"] == "waiting" and view["step"] == "transcribe"
    assert _flow(did) == [("transcribe", "waiting"), ("names", "waiting")]
    assert q.get_queue(None)["state"] == "idle"

    q.tick()
    assert names.started == [f"transcribe_{did}"]
    assert runs.title_flow(None, did) == {"state": "running", "step": "transcribe", "reason": None}
    names.finish(f"transcribe_{did}")
    q.tick()
    assert names.started[-1] == f"lines_glossary_{did}"
    assert runs.title_flow(None, did)["step"] == "names"
    names.finish(f"lines_glossary_{did}")
    q.tick()
    assert _flow(did) == [("transcribe", "done"), ("names", "done")]
    # Stops at the glossary review: nothing translates.
    assert runs.title_flow(None, did) == {"state": "review", "step": None, "reason": None}
    assert not any(j.startswith("translate_") for j in names.started)


def test_a_title_with_lines_only_finds_names(names):
    did = _drama("Has lines")
    runs.start_title_flow(None, did, True, at_pc=True)
    assert _flow(did) == [("names", "waiting")]


def test_a_pressed_start_goes_ahead_of_items_waiting_for_the_queue_and_starts_none_of_them(names):
    other = _drama("Other")
    q.add_item(None, other, "transcribe", True, at_pc=True)
    did = _bare()
    runs.start_title_flow(None, did, True, at_pc=True)
    q.tick()
    assert names.started == [f"transcribe_{did}"]
    names.finish(f"transcribe_{did}")
    q.tick()
    names.finish(f"lines_glossary_{did}")
    q.tick()
    q.tick()
    assert names.started == [f"transcribe_{did}", f"lines_glossary_{did}"]
    assert _flow(other) == [("transcribe", "waiting")]

    # With the queue started the pressed run still goes first, and runs one item at a time.
    q.start(None)
    later = _bare("Later")
    runs.start_title_flow(None, later, True, at_pc=True)
    q.tick()
    assert names.started[-1] == f"transcribe_{later}"
    assert names.concurrent[-1] == 0


def test_a_second_press_gets_the_same_run_and_another_queued_step_refuses(names):
    did = _bare()
    runs.start_title_flow(None, did, True, at_pc=True)
    again = runs.start_title_flow(None, did, True, at_pc=True)
    assert again["state"] == "waiting"
    assert len(_flow(did)) == 2

    other = _drama("Queued")
    q.add_item(None, other, "translate", True, at_pc=True)
    with pytest.raises(ConflictError):
        runs.start_title_flow(None, other, True, at_pc=True)


def test_a_failed_step_stops_the_run_with_its_reason_but_not_the_queue(names):
    q.set_policy(None, True)
    did = _bare()
    names.fail.add(f"transcribe_{did}")
    runs.start_title_flow(None, did, True, at_pc=True)
    q.tick()
    names.finish(f"transcribe_{did}")
    q.tick()
    q.tick()
    assert _flow(did) == [("transcribe", "failed"), ("names", "skipped")]
    view = runs.title_flow(None, did)
    assert view["state"] == "stopped" and view["step"] == "transcribe"
    assert "engine said no" in view["reason"]
    state = q.get_queue(None)
    assert state["notice"] is None

    # Pressing again starts a fresh run from where the title is.
    names.fail.clear()
    names.gates.pop(f"transcribe_{did}")
    assert runs.start_title_flow(None, did, True, at_pc=True)["state"] == "waiting"


def test_after_a_restart_a_pressed_run_waits_for_a_new_press(names):
    did = _bare()
    runs.start_title_flow(None, did, True, at_pc=True)
    q.restore_at_startup()
    assert _flow(did) == [("transcribe", "interrupted"), ("names", "interrupted")]
    q.tick()
    assert names.started == []
    assert runs.title_flow(None, did)["state"] == "stopped"


def test_a_job_the_gpu_guard_stopped_to_start_again_keeps_its_item_running(names):
    did = _drama("Guarded")
    runs.start_title_flow(None, did, True, at_pc=True)
    q.tick()
    job_id = f"lines_glossary_{did}"
    with gpu_guard._guard_lock:
        gpu_guard._pending[job_id] = {"run": {}, "restarts": 0}
    try:
        background_jobs.request_cancel(job_id)
        names.finish(job_id)
        q.tick()
        assert _flow(did) == [("names", "running")]
    finally:
        with gpu_guard._guard_lock:
            gpu_guard._pending.pop(job_id, None)
    q.tick()
    assert _flow(did)[0][1] in ("cancelled", "done")


def test_a_pasted_transcript_title_is_refused_before_anything_is_queued(names):
    did = _bare(transcript_mode="have_transcript")
    with pytest.raises(UnsupportedOperationError):
        runs.start_title_flow(None, did, True, at_pc=True)
    assert _flow(did) == []


def test_a_member_cannot_see_or_start_another_members_title(names):
    owner = auth_service.add_user("a@example.com")
    other = auth_service.add_user("b@example.com")
    for u in (owner, other):
        auth_service.grant_permission(u["id"], "jobs.start")
    theirs = _bare("Theirs", owner_user_id=other["id"], is_private=0)
    pa = {"user_id": owner["id"], "is_admin": False, "permissions": ["jobs.start"]}
    with pytest.raises(NotFoundError):
        runs.title_flow(pa, theirs)
    with pytest.raises(NotFoundError):
        runs.start_title_flow(pa, theirs, False, at_pc=True)
    assert db.get_drama(theirs) is not None and _flow(theirs) == []


@pytest.fixture
def novel(names, monkeypatch):
    from services import narration_service, novel_attach_service, sources_import_service
    calls = {"import": [], "text": 0, "state": {"chapters": 0, "text": False, "lines": False}}

    def start_import(url, drama_id, local=True, principal=None, **kw):
        calls["import"].append((url, local))
        return names._start(f"sourceimport_{drama_id}")

    def attach(drama_id, mode="replace"):
        calls["text"] += 1
        return {"char_count": 10}

    monkeypatch.setattr(sources_import_service, "start_url_import", start_import)
    monkeypatch.setattr(novel_attach_service, "attach_from_sources", attach)
    monkeypatch.setattr(narration_service, "start_narration_run",
                        lambda drama_id, engine_name=None, **kw: names._start(f"narration_{drama_id}"))
    monkeypatch.setattr(glossary_extract_service, "start_novel_glossary_run",
                        lambda drama_id, engine_name=None: names._start(f"novel_glossary_{drama_id}"))
    monkeypatch.setattr(runs, "_novel_state", lambda drama_id: dict(calls["state"]))
    monkeypatch.setattr(runs, "lines_engine", lambda drama_id: "ollama")
    names.calls = calls
    return names


def _novel_start(did, **kw):
    args = {"at_pc": True, "local": True, "text_allowed": True, "holds_import": True, **kw}
    return runs.start_novel_flow(None, did, kw.pop("url", "https://example.com/ch1"), **{
        k: v for k, v in args.items() if k != "url"})


def test_a_novel_link_run_imports_makes_the_text_and_lines_then_finds_names(novel, monkeypatch):
    from services import sources_url_service
    monkeypatch.setattr(sources_url_service, "check_public_url", lambda url: url)
    did = _drama("Novel", lines=(), media_type="novel")
    view = _novel_start(did)
    assert view["state"] == "waiting" and view["step"] == "import"
    assert [s for s, _ in _flow(did)] == ["import", "text", "lines", "names"]
    q.tick()
    assert novel.calls["import"] == [("https://example.com/ch1", True)]
    novel.finish(f"sourceimport_{did}")
    q.tick()                       # the text is made inline; the next pass starts the lines
    assert novel.calls["text"] == 1
    q.tick()
    assert novel.started[-1] == f"narration_{did}"
    novel.finish(f"narration_{did}")
    q.tick()
    assert novel.started[-1] == f"novel_glossary_{did}"
    novel.finish(f"novel_glossary_{did}")
    q.tick()
    assert runs.novel_flow(None, did)["state"] == "review"
    # The link is never shown back.
    assert "example.com" not in str(q.get_queue(None)) + str(runs.novel_flow(None, did))


def test_a_novel_with_saved_chapters_and_text_starts_at_its_lines(novel):
    novel.calls["state"] = {"chapters": 3, "text": True, "lines": False}
    did = _drama("Novel", lines=(), media_type="novel")
    _novel_start(did, url="")
    assert [s for s, _ in _flow(did)] == ["lines", "names"]


def test_the_text_step_stays_at_the_pc_and_the_import_needs_its_permission(novel):
    novel.calls["state"] = {"chapters": 3, "text": False, "lines": False}
    did = _drama("Novel", lines=(), media_type="novel")
    with pytest.raises(ForbiddenError):
        _novel_start(did, text_allowed=False)
    novel.calls["state"] = {"chapters": 0, "text": False, "lines": False}
    with pytest.raises(ForbiddenError):
        _novel_start(did, holds_import=False)
    assert _flow(did) == []
