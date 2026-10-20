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
