"""Translate and Export items carry their own settings, like a Transcribe item: checked when
added and again at start, laid over the title for that one run, never saved to it."""
import pytest

import db
import translate_engines
from core import Line
from lib.errors import ForbiddenError, InvalidInputError
from services import artifact_service, queue_service as q, translate_run_service


def _drama(lines=(("你好", ""),), **fields):
    fields.setdefault("transcript_mode", "whisper")
    did = db.create_drama(title_en="Signal", title_zh="信号", translation_engine="ollama", **fields)
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z, en=en) for i, (z, en) in enumerate(lines)])
    return did


@pytest.fixture
def seen_run(monkeypatch):
    """What the queue hands the translate run for a title."""
    seen = {}

    def start(drama_id, *args, **kw):
        seen.update(kw, args=args)
        return {"job_id": f"translate_{drama_id}", "engine": "ollama"}
    monkeypatch.setattr(translate_run_service, "start_translate_run", start)
    return seen


def _changed(item):
    return {r["label"]: r["value"] for r in item["changed_settings"]}


def test_translate_item_settings_reach_the_run_and_leave_the_title_alone(isolated_db, seen_run):
    did = _drama(translate_start_json='{"batch_size": 12, "locale": "en-GB"}')
    before = db.get_drama(did)
    q.add_item(None, did, "translate", True, at_pc=True,
               settings={"batch_size": 20, "style_preset": "novel", "locale": "en-AU"})
    assert _changed(q.get_queue(None)["items"][0]) == {
        "Batch size": "20", "Tone and character voice": "Novel / prose", "English variant": "en-AU"}
    q._start_translate(None, db.get_drama(did), q._load()["items"][0]["settings"])
    assert seen_run["batch_size"] == 20 and seen_run["style_preset"] == "novel" and seen_run["locale"] == "en-AU"
    assert seen_run["record_run"] is False
    assert db.get_drama(did) == before


def test_a_translate_item_without_settings_still_records_its_run(isolated_db, seen_run):
    q._start_translate(None, db.get_drama(_drama()))
    assert seen_run["record_run"] is True and "batch_size" not in seen_run


@pytest.mark.parametrize("settings", [
    {"batch_size": 0}, {"batch_size": 61}, {"batch_size": True}, {"batch_size": "5"}, {"locale": "fr"},
    {"style_preset": "nope"}, {"model": "not-a-model/../x"}, {"model": ""}, {"engine": "claude"}, {"beam_size": 5},
])
def test_bad_translate_settings_are_refused_before_anything_is_queued(isolated_db, seen_run, settings):
    with pytest.raises(InvalidInputError):
        q.add_item(None, _drama(), "translate", True, at_pc=True, settings=settings)
    assert q.get_queue(None)["items"] == []


def test_a_hosted_ollama_model_needs_paid_permission(isolated_db, seen_run, monkeypatch):
    monkeypatch.setattr(translate_engines, "is_ollama_cloud_model", lambda m: m.endswith("-cloud"))
    with pytest.raises(ForbiddenError):
        q.add_item(None, _drama(), "translate", False, at_pc=True, settings={"model": "gpt-oss:120b-cloud"})


def test_changing_a_translate_or_export_step_needs_lines_edit(isolated_db, seen_run):
    did = _drama()
    with pytest.raises(ForbiddenError):
        q.add_item(None, did, "translate", True, at_pc=True, settings={"batch_size": 5}, holds_edit=False)
    with pytest.raises(ForbiddenError):
        q.add_item(None, did, "export", True, at_pc=True, settings={"format": "vtt"}, holds_edit=False)
    assert q.get_queue(None)["items"] == []


def test_a_translate_item_that_lost_lines_edit_is_not_started(isolated_db, seen_run):
    member = {"user_id": 5, "is_admin": False, "permissions": ["jobs.start", "engines.paid"]}
    with pytest.raises(ForbiddenError):
        q._start_translate(member, db.get_drama(_drama()), {"batch_size": 5})
    assert seen_run == {}


@pytest.mark.parametrize("settings", [{"format": "ass"}, {"language": "fr"}, {"format": ""}, {"beam_size": 5}, {"batch_size": 5}])
def test_bad_export_settings_are_refused(isolated_db, settings):
    with pytest.raises(InvalidInputError):
        q.add_item(None, _drama(), "export", True, at_pc=True, settings=settings)


def test_export_item_writes_the_chosen_format_and_language(isolated_db):
    did = _drama(lines=[("你好", "Hello")])
    q.add_item(None, did, "export", True, at_pc=True, settings={"format": "vtt", "language": "bilingual"})
    assert _changed(q.get_queue(None)["items"][0]) == {"Format": "VTT", "Language": "Both"}
    q.run_export(db.get_drama(did), q._load()["items"][0]["settings"])
    art = artifact_service.get_artifact(did, "subtitle")
    assert art["path"].endswith(".vtt")
    with open(art["path"], encoding="utf-8") as f:
        text = f.read()
    assert text.startswith("WEBVTT") and "Hello" in text and "你好" in text


def test_stored_settings_keep_only_their_own_steps_keys(isolated_db):
    export = {"id": 1, "drama_id": 1, "step": "export", "status": "waiting",
              "settings": {"format": "lrc", "beam_size": 9, "language": ["x"]}}
    translate = {"id": 2, "drama_id": 1, "step": "translate", "status": "waiting",
                 "settings": {"batch_size": 7, "format": "lrc"}}
    assert q._clean_item(export)["settings"] == {"format": "lrc"}
    assert q._clean_item(translate)["settings"] == {"batch_size": 7}


def _cost_row(preview):
    return next(r["value"] for r in preview["rows"] if r["label"] == "Cost")


def test_the_cost_line_prices_the_items_own_model_and_batch_size(isolated_db, monkeypatch):
    seen = []

    def estimate(drama_id, engine_name=None, model=None, batch_size=None, **kw):
        seen.append((model, batch_size))
        return {"estimated_usd": 0.5 if model == "claude-haiku-4-5" else 2.0, **kw}
    monkeypatch.setattr(translate_run_service, "estimate_translate_cost", estimate)
    did = _drama()
    db.update_drama(did, translation_engine="claude")
    monkeypatch.setattr(translate_run_service, "_require_offered_model", lambda engine, model: None)

    saved = _cost_row(q.step_settings(None, did, "translate"))
    edited = _cost_row(q.step_settings(None, did, "translate", settings={"model": "claude-haiku-4-5", "batch_size": 7}))

    assert saved.startswith("About $2.00") and edited.startswith("About $0.50")
    assert seen[-1] == ("claude-haiku-4-5", 7)


def test_the_cost_line_and_the_start_check_estimate_the_same_run(isolated_db, monkeypatch):
    seen = []

    def estimate(drama_id, engine_name=None, model=None, batch_size=None, **kw):
        seen.append((model, batch_size))
        return {"estimated_usd": 1.0, "cap_applies": False}
    monkeypatch.setattr(translate_run_service, "estimate_translate_cost", estimate)
    monkeypatch.setattr(translate_run_service, "_require_offered_model", lambda engine, model: None)
    did = _drama()
    db.update_drama(did, translation_engine="claude")
    own = {"model": "claude-haiku-4-5", "batch_size": 9}

    q.step_settings(None, did, "translate", settings=own)
    q._check_translate_spend(did, own["model"], own["batch_size"])

    assert seen[0] == seen[1] == ("claude-haiku-4-5", 9)


def test_a_bad_setting_for_the_cost_line_is_refused(isolated_db):
    with pytest.raises(InvalidInputError):
        q.step_settings(None, _drama(), "translate", settings={"batch_size": 0})
    with pytest.raises(InvalidInputError):
        q.step_settings(None, _drama(), "translate", settings={"locale": "fr"})


def test_the_cost_read_refuses_a_hosted_ollama_tag_without_paid_permission(isolated_db):
    from types import SimpleNamespace

    from api.routers import queue_routes
    from lib.errors import ForbiddenError

    member = SimpleNamespace(state=SimpleNamespace(principal={"user_id": 7, "permissions": ["jobs.start"]}))
    with pytest.raises(ForbiddenError):
        queue_routes.step_settings(member, drama_id=1, step="translate", model="gemma4:31b-cloud", batch_size=None)
