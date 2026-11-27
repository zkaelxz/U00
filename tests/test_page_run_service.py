"""Tests for services/page_run_service.py: a title page's one-button run as a chain on the PC queue."""
import pytest

import background_jobs
import db
from core import Line
from jobs import gpu_guard
from lib.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError, UnsupportedOperationError
from services import (auth_service, glossary_extract_service, page_run_service as runs, queue_service as q,
                      settings_service)
from tests.test_queue_service import _drama, _items, jobs  # noqa: F401  (fixture)


@pytest.fixture
def names(jobs, monkeypatch):  # noqa: F811
    monkeypatch.setattr(glossary_extract_service, "start_lines_glossary_run",
                        lambda drama_id, engine_name=None, model=None: jobs._start(f"lines_glossary_{drama_id}"))
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
    assert runs.title_flow(None, did) == {"state": "running", "step": "transcribe", "reason": None,
                                         "job_id": f"transcribe_{did}"}
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
    q.pause(None)     # an added item starts the queue by itself; this one is left waiting
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
    # The page links the stopped step's job, where its log is.
    assert view["job_id"] == f"transcribe_{did}"
    state = q.get_queue(None)
    assert state["notice"] is None

    # Pressing again starts a fresh run from where the title is.
    names.fail.clear()
    names.gates.pop(f"transcribe_{did}")
    assert runs.start_title_flow(None, did, True, at_pc=True)["state"] == "waiting"


def test_continuing_after_a_failed_names_step_skips_the_transcription_already_done(names):
    did = _bare()
    names.fail.add(f"lines_glossary_{did}")
    runs.start_title_flow(None, did, True, at_pc=True)
    q.tick()
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])   # what the transcription wrote
    names.finish(f"transcribe_{did}")
    q.tick()
    names.finish(f"lines_glossary_{did}")
    q.tick()
    assert runs.title_flow(None, did)["step"] == "names"

    names.fail.clear()
    runs.start_title_flow(None, did, True, at_pc=True)
    assert _flow(did) == [("transcribe", "done"), ("names", "failed"), ("names", "waiting")]


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
    calls["lines"] = []

    def make_lines(drama_id, engine_name=None, **kw):
        calls["lines"].append({"engine": engine_name, **kw})
        return names._start(f"narration_{drama_id}")
    monkeypatch.setattr(narration_service, "start_narration_run", make_lines)
    calls["cost"] = {"has_key": True, "cap_applies": False, "monthly_cap_set": False, "estimated_usd": 0.0}
    monkeypatch.setattr(narration_service, "estimate_tag_cost", lambda *a, **kw: dict(calls["cost"]))
    monkeypatch.setattr(glossary_extract_service, "start_novel_glossary_run",
                        lambda drama_id, engine_name=None, model=None: names._start(f"novel_glossary_{drama_id}"))
    monkeypatch.setattr(runs, "_novel_state", lambda drama_id: dict(calls["state"]))
    monkeypatch.setattr(narration_service, "get_narration_config", lambda drama_id: {"default_engine": "ollama"})
    names.calls = calls
    return names


def _novel_start(did, choice=None, **kw):
    args = {"at_pc": True, "local": True, "holds_import": True, **kw}
    return runs.start_novel_flow(None, did, kw.pop("url", "https://example.com/ch1"), runs.lines_choice(did, choice or {}), **{
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
    # No engine was named: the title's default makes the lines, not the first engine with a key.
    assert novel.calls["lines"][0]["engine"] == "ollama"
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


def test_the_lines_step_uses_the_engine_model_and_chapters_the_page_chose(novel):
    novel.calls["state"] = {"chapters": 3, "text": True, "lines": False}
    did = _drama("Novel", lines=(), media_type="novel")
    _novel_start(did, {"engine": "gemini", "model": "m", "chapter_from": 2, "chapter_to": 3}, url="")
    q.tick()
    assert novel.calls["lines"] == [{"engine": "gemini", "model": "m", "one_narrator": False,
                                     "chapter_from": 2, "chapter_to": 3}]


def test_an_unaccepted_paid_estimate_stops_the_lines_step_and_an_accepted_one_starts_it(novel):
    novel.calls["state"] = {"chapters": 3, "text": True, "lines": False}
    novel.calls["cost"] = {"has_key": True, "cap_applies": True, "monthly_cap_set": False, "estimated_usd": 1.5}
    did = _drama("Novel", lines=(), media_type="novel")
    _novel_start(did, {"engine": "gemini"}, url="")
    q.tick()
    q.tick()
    view = runs.novel_flow(None, did)
    assert view["state"] == "stopped" and "$1.50" in view["reason"]
    assert novel.calls["lines"] == []
    _novel_start(did, {"engine": "gemini", "cost_accepted": True}, url="")
    q.tick()
    assert [c["engine"] for c in novel.calls["lines"]] == ["gemini"]


def test_one_narrator_makes_the_lines_without_pricing_them(novel):
    novel.calls["state"] = {"chapters": 3, "text": True, "lines": False}
    novel.calls["cost"] = {"has_key": True, "cap_applies": True, "monthly_cap_set": False, "estimated_usd": 1.5}
    did = _drama("Novel", lines=(), media_type="novel")
    _novel_start(did, {"one_narrator": True}, url="")
    q.tick()
    assert novel.calls["lines"][0]["one_narrator"] is True


def test_the_text_step_is_queued_from_a_phone_and_the_import_needs_its_permission(novel):
    novel.calls["state"] = {"chapters": 3, "text": False, "lines": False}
    did = _drama("Novel", lines=(), media_type="novel")
    _novel_start(did, at_pc=False, local=False)
    assert [s for s, _ in _flow(did)] == ["text", "lines", "names"]
    other = _drama("Other", lines=(), media_type="novel")
    novel.calls["state"] = {"chapters": 0, "text": False, "lines": False}
    with pytest.raises(ForbiddenError):
        _novel_start(other, holds_import=False)
    assert _flow(other) == []


def test_an_add_box_run_follows_the_uploads_transcription_then_translates_with_the_boxs_choices(jobs, monkeypatch):  # noqa: F811
    from services import translate_run_service
    seen = []

    def translate(drama_id, **kw):
        seen.append(kw)
        return jobs._start(f"translate_{drama_id}")
    monkeypatch.setattr(translate_run_service, "start_translate_run", translate)
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda: 0)
    did = _bare("File")
    jobs._start(f"transcribe_{did}")            # as the upload started it
    view = runs.start_file_run(None, did, f"transcribe_{did}", "deepseek", "en-GB", at_pc=True)
    assert view["state"] == "waiting"
    q.tick()
    assert runs.file_run(None, did) == {"state": "running", "step": "follow", "reason": None,
                                        "job_id": f"transcribe_{did}"}
    assert jobs.started == [f"transcribe_{did}"]     # following starts nothing
    jobs.finish(f"transcribe_{did}")
    q.tick()
    q.tick()
    # A paid engine with no monthly cap set still translates, as the box did before.
    assert seen and seen[0]["engine_name"] == "deepseek" and seen[0]["locale"] == "en-GB"
    assert runs.file_run(None, did)["job_id"] == f"translate_{did}"
    jobs.finish(f"translate_{did}")
    q.tick()
    assert runs.file_run(None, did)["state"] == "done"


def test_an_add_box_run_follows_only_its_own_titles_upload(jobs):  # noqa: F811
    did = _bare("File")
    other = _bare("Other")
    with pytest.raises(InvalidInputError):
        runs.start_file_run(None, did, f"transcribe_{other}", "ollama", None, at_pc=True)
    with pytest.raises(InvalidInputError):
        runs.start_file_run(None, did, f"transcribe_{did}", "nope", None, at_pc=True)
    assert runs.file_run(None, did)["state"] == "idle"


def test_a_lost_upload_job_stops_the_add_box_run_with_a_reason(jobs):  # noqa: F811
    did = _bare("File")
    runs.start_file_run(None, did, f"transcribe_{did}", "ollama", None, at_pc=True)
    q.tick()
    q.tick()
    view = runs.file_run(None, did)
    assert view["state"] == "stopped" and "Lost track" in view["reason"]


def test_a_queued_step_on_an_ollama_cloud_model_needs_paid_engines(isolated_db, monkeypatch):
    """Ollama is free, but its cloud tags are billed: a member who can't use paid
    engines (or whose limit is used up) must not start one from the queue."""
    from engine_backends import engine_registry
    free_member = {"user_id": 7, "permissions": ("lines.edit", "jobs.start")}
    paid_member = {"user_id": 7, "permissions": ("lines.edit", "jobs.start", "engines.paid")}
    with pytest.raises(ForbiddenError, match="paid engine"):
        runs._require_free_or_paid(free_member, "ollama", "gpt-oss:120b-cloud", "translates")
    runs._require_free_or_paid(paid_member, "ollama", "gpt-oss:120b-cloud", "translates")
    runs._require_free_or_paid(free_member, "ollama", "qwen3:8b", "translates")
    # The hosted model every account may use (local.MEMBER_CLOUD_MODELS).
    runs._require_free_or_paid(free_member, "ollama", "gemma4:31b-cloud", "translates")
    with pytest.raises(ForbiddenError):
        runs._require_free_or_paid(free_member, "claude", None, "translates")
    with pytest.raises(ForbiddenError):
        runs._require_free_or_paid(free_member, "", None, "translates")
    # No model named: the engine's default model is the one that runs.
    monkeypatch.setattr(engine_registry, "effective_default_model",
                        lambda name: "gpt-oss:120b-cloud" if name == "ollama" else None)
    with pytest.raises(ForbiddenError):
        runs._require_free_or_paid(free_member, "ollama", None, "finds names")


def test_a_queued_cloud_step_runs_on_the_adders_own_ollama_key(isolated_db, monkeypatch):
    """The add-time gate (require_cloud_model_allowed) lets a member with their own
    Ollama key use a cloud tag without engines.paid; the start must agree."""
    from services import member_key_service
    free_member = {"user_id": 7, "permissions": ("lines.edit", "jobs.start")}
    monkeypatch.setattr(member_key_service, "has_usable_key", lambda uid: uid == 7)
    runs._require_free_or_paid(free_member, "ollama", "gpt-oss:120b-cloud", "makes lines")
    with pytest.raises(ForbiddenError):    # the key covers Ollama only
        runs._require_free_or_paid(free_member, "claude", None, "makes lines")
    with pytest.raises(ForbiddenError):
        runs._require_free_or_paid({**free_member, "user_id": 8}, "ollama", "gpt-oss:120b-cloud", "makes lines")


def test_a_queued_names_step_is_held_to_the_titles_picked_cloud_model(isolated_db, monkeypatch):
    """The names step runs on the title's Translate model; a cloud tag there needs
    engines.paid (or the adder's own key) even though Ollama itself is open to every account."""
    started = []
    monkeypatch.setattr(glossary_extract_service, "start_lines_glossary_run",
                        lambda did, **kw: started.append(kw) or {"job_id": "j"})
    did = _bare("Names", engine="ollama", translate_start_json='{"engine_model": "gpt-oss:120b-cloud"}')
    free_member = {"user_id": 7, "permissions": ("lines.edit", "jobs.start")}
    with pytest.raises(ForbiddenError):
        runs.start_step(free_member, db.get_drama(did), q.NAMES, {})
    assert started == []
    paid_member = {**free_member, "permissions": (*free_member["permissions"], "engines.paid")}
    assert runs.start_step(paid_member, db.get_drama(did), q.NAMES, {}) == "j"
    assert started == [{"engine_name": "ollama", "model": "gpt-oss:120b-cloud"}]


# --- translating after a chapter import ---------------------------------------------

def test_a_comic_import_is_followed_by_one_scanlate_run_over_the_untranslated_pages(jobs, monkeypatch):  # noqa: F811
    from services import scanlate_run_service
    seen = []

    def scanlate(drama_id, **kw):
        seen.append(kw)
        return jobs._start(f"scanlate_{drama_id}")
    monkeypatch.setattr(scanlate_run_service, "start_run", scanlate)
    monkeypatch.setattr(runs, "require_cap_for_after_import", lambda engine, model: None)
    did = _bare("Comic", media_type="manhua")
    jobs._start(f"sourceimport_{did}")                 # as the chapter import started it
    view = runs.start_after_import(None, did, "ollama", None, at_pc=True)
    assert view["state"] == "waiting"
    assert [s for s, _ in _flow(did)] == ["follow", "run_scanlate"]
    q.tick()
    assert jobs.started == [f"sourceimport_{did}"] and not seen        # following starts nothing
    jobs.finish(f"sourceimport_{did}")
    q.tick()
    q.tick()
    assert seen == [{"mode": "missing", "engine": "ollama"}]
    assert runs.after_import_run(None, did)["job_id"] == f"scanlate_{did}"
    jobs.finish(f"scanlate_{did}")
    q.tick()
    assert runs.after_import_run(None, did)["state"] == "done"


def test_a_failed_import_stops_the_translating_that_waits_for_it(jobs, monkeypatch):  # noqa: F811
    from services import scanlate_run_service
    monkeypatch.setattr(scanlate_run_service, "start_run", lambda *a, **kw: pytest.fail("must not start"))
    did = _bare("Comic", media_type="manga")
    jobs.fail.add(f"sourceimport_{did}")
    jobs._start(f"sourceimport_{did}")
    runs.start_after_import(None, did, "ollama", None, at_pc=False)
    q.tick()
    jobs.finish(f"sourceimport_{did}")
    q.tick()
    q.tick()
    assert [st for _, st in _flow(did)] == ["failed", "skipped"]
    assert runs.after_import_run(None, did)["state"] == "stopped"


def test_a_second_press_gets_the_same_after_import_run(jobs):  # noqa: F811
    did = _bare("Comic", media_type="manhwa")
    jobs._start(f"sourceimport_{did}")
    first = runs.start_after_import(None, did, "ollama", None, at_pc=True)
    assert runs.start_after_import(None, did, "ollama", None, at_pc=True) == first
    assert len(_flow(did)) == 2


def test_a_novel_after_import_run_makes_text_lines_names_then_translates(novel, monkeypatch):
    from services import translate_run_service
    seen = []
    monkeypatch.setattr(translate_run_service, "start_translate_run",
                        lambda drama_id, **kw: seen.append(kw) or novel._start(f"translate_{drama_id}"))
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda: 5)
    did = _drama("Novel", lines=(), media_type="novel")
    novel._start(f"sourceimport_{did}")
    runs.start_after_import(None, did, "ollama", runs.lines_choice(did, {}), at_pc=True)
    assert [s for s, _ in _flow(did)] == ["follow", "text", "lines", "names", "run_translate"]
    novel.finish(f"sourceimport_{did}")
    for job in (f"narration_{did}", f"novel_glossary_{did}", f"translate_{did}"):
        for _ in range(5):
            q.tick()
            if novel.started[-1] == job:
                break
        assert novel.started[-1] == job
        novel.finish(job)
    q.tick()
    assert seen[0]["engine_name"] == "ollama" and novel.calls["text"] == 1
    assert runs.after_import_run(None, did)["state"] == "done"


def test_a_novel_after_import_run_needs_a_title_without_lines(novel):
    did = _drama("Novel", lines=(), media_type="novel")
    has_lines = _drama("Has lines", media_type="novel")
    novel.calls["state"]["lines"] = True
    with pytest.raises(ConflictError):
        runs.start_after_import(None, has_lines, "ollama", {}, at_pc=True)
    novel.calls["state"].update(lines=False, text=True)       # the title's own text, no chapters to rebuild it from
    with pytest.raises(ConflictError):
        runs.start_after_import(None, has_lines, "ollama", {}, at_pc=True)
    other = _drama("Audio", lines=(), media_type="audio_drama")
    with pytest.raises(InvalidInputError):
        runs.start_after_import(None, other, "ollama", {}, at_pc=True)
    assert _flow(has_lines) == [] and _flow(other) == []


def test_a_novel_with_chapters_and_text_skips_the_text_step(novel):
    did = _drama("Novel", lines=(), media_type="novel")
    novel.calls["state"].update(chapters=3, text=True)
    runs.start_after_import(None, did, "ollama", {}, at_pc=False, model="m")
    assert [s for s, _ in _flow(did)] == ["follow", "lines", "names", "run_translate"]


def test_the_tick_needs_a_monthly_cap_when_the_engine_is_billed(monkeypatch):
    from lib.errors import UnsupportedOperationError
    from services import translate_run_service
    monkeypatch.setattr(translate_run_service, "engine_cap_applies", lambda *a: True)
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 0)
    with pytest.raises(UnsupportedOperationError):
        runs.require_cap_for_after_import("claude", None)
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 5)
    runs.require_cap_for_after_import("claude", None)


def test_the_scanlate_step_is_held_to_the_adders_paid_engine_permission(isolated_db, monkeypatch):
    from services import scanlate_run_service
    monkeypatch.setattr(scanlate_run_service, "start_run", lambda *a, **kw: pytest.fail("must not start"))
    drama = {"id": 1}
    member = {"user_id": 5, "permissions": ["jobs.start"]}
    with pytest.raises(ForbiddenError):
        runs.start_step(member, drama, q.RUN_SCANLATE, {"engine": "claude"})


def test_the_novel_link_run_refuses_a_title_whose_own_text_it_would_replace(novel):
    did = _drama("Novel", lines=(), media_type="novel")
    novel.calls["state"].update(chapters=0, text=True)
    with pytest.raises(ConflictError):
        _novel_start(did, at_pc=False, local=False)
    assert _flow(did) == []


def test_a_retried_text_step_leaves_a_title_that_got_lines_meanwhile(novel):
    did = _drama("Novel", media_type="novel")           # already has lines
    with pytest.raises(ConflictError):
        runs.start_step(None, db.get_drama(did), q.TEXT, {})
    assert novel.calls["text"] == 0


def test_the_names_step_runs_on_the_engine_stored_for_the_adder(isolated_db, monkeypatch):
    seen = []
    monkeypatch.setattr(glossary_extract_service, "start_novel_glossary_run",
                        lambda drama_id, engine_name=None, model=None: seen.append((engine_name, model)) or {"job_id": "j"})
    did = _drama("Novel", media_type="novel")
    runs.start_step(None, db.get_drama(did), q.NAMES, {"source": "novel", "engine": "ollama", "model": "m"})
    assert seen == [("ollama", "m")]


def test_the_after_import_translate_needs_a_cap_when_it_starts_but_the_add_boxs_does_not(isolated_db, monkeypatch):
    from lib.errors import UnsupportedOperationError
    from services import translate_run_service
    started = []
    monkeypatch.setattr(translate_run_service, "start_translate_run",
                        lambda drama_id, *a, **kw: started.append(kw) or {"job_id": "j"})
    monkeypatch.setattr(translate_run_service, "engine_cap_applies", lambda *a: True)
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 0)     # the owner cleared the cap meanwhile
    did = _drama("Novel", media_type="novel")
    admin = {"user_id": 1, "permissions": ["jobs.start", "engines.paid"]}
    with pytest.raises(UnsupportedOperationError):
        runs.start_step(admin, db.get_drama(did), q.RUN_TRANSLATE, {"engine": "claude", "capped": True})
    assert started == []
    runs.start_step(admin, db.get_drama(did), q.RUN_TRANSLATE, {"engine": "claude"})
    assert len(started) == 1


# --- later chapters saved from the phone into a novel that has lines -----------------

def _novel_with_lines(translated=True, **fields):
    """A novel whose text is its one saved chapter, cut into lines (translated when asked)."""
    from services import narration_service, novel_attach_service
    from sources import pipeline
    from jobs import job_runner
    did = db.create_drama(title_en="Novel", title_zh="题", media_type="novel", translation_engine="ollama", **fields)
    pipeline.append_novel_chapter_once(did, "甲说了话。\n\n乙回答了。", "第一章 开始", source="x")
    novel_attach_service.attach_from_sources(did)
    narration_service.start_narration_run(did, one_narrator=True)
    assert job_runner.wait_for_job_threads(10, job_ids=[f"narration_{did}"])
    lines = db.load_line_objects(did)
    assert lines
    if translated:
        for ln in lines:
            ln.en = f"English {ln.id}"
        db.save_lines(did, lines, fields=("en",))
    return did


def _text(did):
    import os
    import novel_text
    with open(os.path.join(db.drama_dir(did), novel_text.NOVEL_SOURCE_FILENAME), encoding="utf-8") as f:
        return f.read()


def _rows(did):
    return [(ln["id"], ln["zh"], ln["en"]) for ln in db.load_lines(did)]


def _start_append(did, principal=None, **kw):
    kw.setdefault("new_chapters", 1)
    return runs.start_after_import(principal, did, "ollama", {"engine": "ollama", "one_narrator": True}, at_pc=True,
                                   follow_import=False, add_chapters=True, **kw)


@pytest.fixture
def translate_calls(jobs, monkeypatch):  # noqa: F811
    from services import translate_run_service
    seen = []
    monkeypatch.setattr(translate_run_service, "start_translate_run",
                        lambda drama_id, **kw: seen.append(kw) or jobs._start(f"translate_{drama_id}"))
    monkeypatch.setattr(runs, "require_cap_for_after_import", lambda engine, model: None)
    return seen


def _run_chain(jobs, did, until):  # noqa: F811
    from jobs import job_runner
    for _ in range(8):
        q.tick()
        job_runner.wait_for_job_threads(10, job_ids=[f"narration_{did}"])
        if until():
            return
    pytest.fail(f"the chain did not get there: {_flow(did)}")


def test_new_chapters_get_appended_lines_for_the_new_text_only_and_a_translation_of_those(translate_calls, jobs):  # noqa: F811
    from sources import pipeline
    did = _novel_with_lines()
    before_rows, before_text = _rows(did), _text(did)
    pipeline.append_novel_chapter_once(did, "丙走了。", "第二章 继续", source="x")
    view = _start_append(did)
    assert view["state"] == "waiting"
    assert [s for s, _ in _flow(did)] == ["append", "lines", "run_translate"]
    # The phone added one chapter, so the append starts at chapter 2 of the two saved.
    assert q._load()["items"][0]["settings"] == {"from_chapter": 2}
    _run_chain(jobs, did, lambda: bool(translate_calls))
    after = _rows(did)
    # The saved lines are untouched, in place; the new ones come after them.
    assert after[:len(before_rows)] == before_rows and len(after) > len(before_rows)
    assert _text(did).startswith(before_text) and _text(did).endswith("丙走了。")
    new_ids = [i for i, _zh, _en in after[len(before_rows):]]
    assert translate_calls[0]["line_ids"] == new_ids and translate_calls[0]["own_lines_only"] is True
    jobs.finish(f"translate_{did}")
    q.tick()
    assert runs.after_import_run(None, did)["state"] == "done"


def test_an_edited_translation_text_stops_the_run_with_its_reason_and_changes_nothing(translate_calls, jobs):  # noqa: F811
    from sources import pipeline
    import os
    import novel_text
    did = _novel_with_lines()
    with open(os.path.join(db.drama_dir(did), novel_text.NOVEL_SOURCE_FILENAME), "a", encoding="utf-8") as f:
        f.write("\n\n我自己加的。")
    edited, rows = _text(did), _rows(did)
    pipeline.append_novel_chapter_once(did, "丙走了。", "第二章 继续", source="x")
    _start_append(did)
    _run_chain(jobs, did, lambda: runs.after_import_run(None, did)["state"] == "stopped")
    view = runs.after_import_run(None, did)
    assert view["step"] == "append" and "not a copy" in view["reason"]
    assert _text(did) == edited and _rows(did) == rows and translate_calls == []
    phone = runs.phone_progress(None, did)
    assert phone["state"] == "stopped" and phone["reason"].startswith("The translation text is not a copy")


def test_a_novel_with_lines_and_nothing_new_queues_nothing(translate_calls):
    did = _novel_with_lines()
    with pytest.raises(ConflictError):
        _start_append(did)
    assert _flow(did) == []
    # Without add_chapters (the PC's tick) a novel with lines is refused as before.
    from sources import pipeline
    pipeline.append_novel_chapter_once(did, "丙走了。", "第二章 继续", source="x")
    with pytest.raises(ConflictError):
        runs.start_after_import(None, did, "ollama", {}, at_pc=True, follow_import=False)
    assert _flow(did) == []


def test_retried_steps_after_the_chapters_went_in_add_nothing_and_end_cleanly(translate_calls, jobs):  # noqa: F811
    from sources import pipeline
    did = _novel_with_lines()
    pipeline.append_novel_chapter_once(did, "丙走了。", "第二章 继续", source="x")
    _start_append(did)
    _run_chain(jobs, did, lambda: bool(translate_calls))
    jobs.finish(f"translate_{did}")
    q.tick()
    lines = db.load_line_objects(did)
    for ln in lines:
        ln.en = ln.en or "Done"       # as the translation left them
    db.save_lines(did, lines, fields=("en",))
    text, rows = _text(did), _rows(did)
    drama = db.get_drama(did)
    assert runs.start_step(None, drama, q.APPEND, {}) is None
    assert runs.start_step(None, drama, q.APPEND, {"from_chapter": 2}) is None
    assert runs.start_step(None, drama, q.LINES, {"engine": "ollama", "one_narrator": True, "append": True}) is None
    assert runs.start_step(None, drama, q.RUN_TRANSLATE, {"engine": "ollama", "new_lines_only": True}) is None
    assert _text(did) == text and _rows(did) == rows and len(translate_calls) == 1


def test_a_retried_lines_step_whose_lines_were_saved_before_the_marker_cleared_adds_none(jobs):  # noqa: F811
    import novel_text
    from services import novel_attach_service
    from sources import pipeline
    did = _novel_with_lines()
    base = len(_rows(did))
    pipeline.append_novel_chapter_once(did, "丙走了。", "第二章 继续", source="x")
    waiting = novel_attach_service.add_new_chapters_from_sources(did)["pending_chars"]
    runs.start_step(None, db.get_drama(did), q.LINES, {"engine": "ollama", "one_narrator": True, "append": True})
    from jobs import job_runner
    assert job_runner.wait_for_job_threads(10, job_ids=[f"narration_{did}"])
    rows = _rows(did)
    # As if Baihe stopped after the lines were saved and before the marker was cleared.
    novel_text.write_pending_lines(db.drama_dir(did), waiting, len(_text(did)), base, len(rows) - base)
    assert runs.start_step(None, db.get_drama(did), q.LINES,
                           {"engine": "ollama", "one_narrator": True, "append": True}) is None
    assert _rows(did) == rows and novel_text.read_pending_lines(db.drama_dir(did)) is None


def test_the_phones_reason_has_no_link_or_key_and_is_one_short_line(translate_calls, jobs, monkeypatch):  # noqa: F811
    from services import novel_attach_service
    from sources import pipeline
    did = _novel_with_lines()
    pipeline.append_novel_chapter_once(did, "丙走了。", "第二章 继续", source="x")

    def refuse(drama_id, from_chapter=None):
        raise ConflictError("Refused at https://private.example/x?token=abc with key sk-ant-api03-"
                            + "A" * 40 + "\n" + "long " * 100)
    monkeypatch.setattr(novel_attach_service, "add_new_chapters_from_sources", refuse)
    _start_append(did)
    _run_chain(jobs, did, lambda: runs.after_import_run(None, did)["state"] == "stopped")
    reason = runs.phone_progress(None, did)["reason"]
    assert reason.startswith("Refused at") and len(reason) <= 200
    assert "private.example" not in reason and "sk-ant" not in reason and "\n" not in reason


def test_chapters_cut_off_the_end_of_the_text_are_not_put_back(translate_calls, jobs):  # noqa: F811
    import os
    import novel_text
    from services import narration_service
    from sources import pipeline
    from jobs import job_runner
    did = db.create_drama(title_en="Novel", title_zh="题", media_type="novel", translation_engine="ollama")
    pipeline.append_novel_chapter_once(did, "甲说了话。", "第一章 开始", source="x")
    pipeline.append_novel_chapter_once(did, "乙回答了。", "第二章 继续", source="x")
    from services import novel_attach_service
    novel_attach_service.attach_from_sources(did)
    path = os.path.join(db.drama_dir(did), novel_text.NOVEL_SOURCE_FILENAME)
    full = _text(did)
    with open(path, "w", encoding="utf-8") as f:
        f.write(full[:full.index("第二章")].strip())          # the owner cut chapter 2 off the end
    narration_service.start_narration_run(did, one_narrator=True)
    assert job_runner.wait_for_job_threads(10, job_ids=[f"narration_{did}"])
    cut, rows = _text(did), _rows(did)
    pipeline.append_novel_chapter_once(did, "丙走了。", "第三章 新", source="x")
    _start_append(did, new_chapters=1)
    _run_chain(jobs, did, lambda: runs.after_import_run(None, did)["state"] == "stopped")
    view = runs.after_import_run(None, did)
    assert view["step"] == "append" and "holds 1 saved chapters, not the 2 expected" in view["reason"]
    assert _text(did) == cut and _rows(did) == rows and translate_calls == []


def test_a_phone_without_the_new_chapter_count_is_asked_to_update(translate_calls):
    from sources import pipeline
    did = _novel_with_lines()
    pipeline.append_novel_chapter_once(did, "丙走了。", "第二章 继续", source="x")
    with pytest.raises(InvalidInputError, match="Update the Baihe app"):
        _start_append(did, new_chapters=None)
    assert _flow(did) == []


def test_losing_lines_edit_after_queuing_stops_the_append(translate_calls, jobs):  # noqa: F811
    from sources import pipeline
    u = auth_service.add_user("m@example.com")
    for p in ("jobs.start", "lines.edit"):
        auth_service.grant_permission(u["id"], p)
    did = _novel_with_lines(owner_user_id=u["id"], is_private=1)
    pipeline.append_novel_chapter_once(did, "丙走了。", "第二章 继续", source="x")
    text, rows = _text(did), _rows(did)
    _start_append(did, principal={"user_id": u["id"], "is_admin": False,
                                  "permissions": ["jobs.start", "lines.edit"]})
    auth_service.revoke_permission(u["id"], "lines.edit")
    _run_chain(jobs, did, lambda: _flow(did)[0][1] == "skipped")
    assert "can no longer do this step" in q._load()["items"][0]["reason"]
    assert _text(did) == text and _rows(did) == rows and translate_calls == []


def test_the_append_chains_translate_takes_every_untranslated_line_of_the_title(translate_calls, jobs):  # noqa: F811
    # Pins today's scope: an earlier chapter left untranslated is translated with the new one (bounded by the cap).
    from sources import pipeline
    did = _novel_with_lines(translated=False)
    old_ids = [i for i, _zh, _en in _rows(did)]
    pipeline.append_novel_chapter_once(did, "丙走了。", "第二章 继续", source="x")
    _start_append(did)
    _run_chain(jobs, did, lambda: bool(translate_calls))
    all_ids = [i for i, _zh, _en in _rows(did)]
    assert translate_calls[0]["line_ids"] == all_ids and set(old_ids) < set(all_ids)
