"""The Queue card's "Saved settings for this step" panel: read-only, and built
from the same values the queue's own run resolves, so it can't drift from what
the queued run really uses."""
import pytest

import db
import translate_engines
from core import Line
from lib.errors import InvalidInputError, NotFoundError
from services import auth_service, queue_service as q, settings_service, translate_run_service


def _drama(engine="ollama", **fields):
    fields.setdefault("transcript_mode", "whisper")
    did = db.create_drama(title_en="Signal", title_zh="信号", translation_engine=engine, **fields)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    return did


def _rows(preview):
    return {r["label"]: r["value"] for r in preview["rows"]}


@pytest.fixture
def seen_run(monkeypatch):
    """What the queue hands the translate run for a title."""
    seen = {}

    def start(drama_id, *args, **kw):
        seen.update(kw, args=args)
        return {"job_id": f"translate_{drama_id}", "engine": "ollama"}
    monkeypatch.setattr(translate_run_service, "start_translate_run", start)
    return seen


def test_translate_panel_shows_what_the_queued_run_resolves(isolated_db, seen_run, monkeypatch):
    real = settings_service.get
    monkeypatch.setattr(settings_service, "get", lambda k: {
        "default_locale": "en-GB", "default_style_note": "Keep honorifics."}.get(k) or real(k))
    did = _drama(translate_thinking=1)

    rows = _rows(q.step_settings(None, did, "translate"))
    q._start_translate(None, db.get_drama(did))

    assert seen_run["locale"] == "en-GB" and rows["English variant"] == seen_run["locale"]
    assert bool(seen_run["style_note"]) and rows["Style note"] == "Set"
    assert seen_run["thinking"] is True and rows["Thinking"] == "On"
    options = translate_run_service.get_translate_config_defaults(False, "ollama")
    assert rows["Batch size"] == str(options["batch_size"])
    assert "Audio drama" in rows["Tone and character voice"]


def test_translate_panel_reads_the_title_engine_the_run_uses(isolated_db, seen_run, monkeypatch):
    monkeypatch.setattr(translate_run_service.translate_service, "resolve_api_key", lambda name: "k")
    did = _drama(engine="claude")
    assert q._translate_engine(db.get_drama(did)) == "claude"
    rows = _rows(q.step_settings(None, did, "translate"))
    row = next(r for r in q.step_settings(None, did, "translate")["rows"] if r["label"] == "Engine and model")
    assert row["engine"] == "claude" and row["value"]
    assert rows["Cost"].startswith(("About $", "No estimate yet")) and "API key" in rows["Cost"]


def test_thinking_reads_off_for_an_engine_without_the_switch(isolated_db, seen_run):
    did = _drama(engine="claude", translate_thinking=1)
    assert _rows(q.step_settings(None, did, "translate"))["Thinking"] == "Off"


def test_translate_panel_lists_each_toggle_the_run_applies(isolated_db, seen_run):
    did = _drama()
    rows = _rows(q.step_settings(None, did, "translate"))
    for label in ("Glossary", "Language packs", "VTuber names", "Reply check", "Off-peak start",
                  "Source language", "Monthly cap left"):
        assert label in rows, label
    assert rows["Off-peak start"].startswith("Off")
    assert rows["Reply check"] in ("On", "Off")
    assert rows["VTuber names"] in ("On", "Off")


def test_reply_check_follows_the_saved_mode(isolated_db, seen_run, monkeypatch):
    did = _drama(engine="ollama")
    real = settings_service.get
    for mode, want in (("on", "On"), ("off", "Off")):
        monkeypatch.setattr(settings_service, "get",
                            lambda k, mode=mode: mode if k == "translate_reply_guard" else real(k))
        assert _rows(q.step_settings(None, did, "translate"))["Reply check"] == want


def test_monthly_cap_left_is_the_cap_minus_this_months_spend(isolated_db, seen_run, monkeypatch):
    did = _drama()
    real = settings_service.resolve_key
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda k, *a, **kw: "5" if k == "monthly_cap_usd" else real(k, *a, **kw))
    monkeypatch.setattr(db, "get_month_spend", lambda *a, **kw: 1.25)
    assert _rows(q.step_settings(None, did, "translate"))["Monthly cap left"] == "$3.75 of $5.00"
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda k, *a, **kw: "0" if k == "monthly_cap_usd" else real(k, *a, **kw))
    assert _rows(q.step_settings(None, did, "translate"))["Monthly cap left"] == "No cap set"


def test_transcribe_panel_reads_the_config_the_queue_run_reads(isolated_db, monkeypatch):
    did = _drama(whisper_size="small", beam_size=7, use_groq=1)
    from services import transcribe_service
    config = transcribe_service.get_transcribe_config(did)
    rows = _rows(q.step_settings(None, did, "transcribe"))
    assert rows["Beam size"] == str(config["beam_size"]) == "7"
    assert rows["Speech model"] == "Whisper small"
    assert rows["Groq (paid)"] == "On"
    assert "Source language" in rows


def test_export_panel_names_the_format_and_where_it_goes(isolated_db):
    rows = _rows(q.step_settings(None, _drama(), "export"))
    assert rows["Format"] == "SRT" and rows["Language"] == "English"


def test_the_panel_changes_nothing(isolated_db, seen_run):
    did = _drama()
    before = (db.get_drama(did), q.get_queue(None))
    for step in ("transcribe", "translate", "export"):
        q.step_settings(None, did, step)
    assert (db.get_drama(did), q.get_queue(None)) == before


def test_a_member_cannot_read_another_titles_settings(isolated_db, seen_run):
    owner = auth_service.add_user("o@example.com")
    other = auth_service.add_user("p@example.com")
    did = _drama(owner_user_id=owner["id"], is_private=1)
    member = {"user_id": other["id"], "is_admin": False, "permissions": ["jobs.start"]}
    with pytest.raises(NotFoundError):
        q.step_settings(member, did, "translate")


def test_unknown_title_or_step_is_refused(isolated_db):
    with pytest.raises(NotFoundError):
        q.step_settings(None, 999, "translate")
    with pytest.raises(InvalidInputError):
        q.step_settings(None, _drama(), "dub")


# --- editing the settings of one queued item ------------------------------------

def _fields(preview):
    return {f["key"]: f for f in preview["fields"]}


@pytest.fixture
def started(monkeypatch):
    """What the queue hands start_transcribe_run, with the model check passing."""
    from services import model_readiness_service, transcribe_service
    seen = {}
    monkeypatch.setattr(model_readiness_service, "for_transcribe", lambda *a, **kw: [])
    monkeypatch.setattr(transcribe_service, "start_transcribe_run",
                        lambda drama_id, **kw: seen.update(kw) or {"job_id": f"transcribe_{drama_id}"})
    return seen


def test_transcribe_fields_start_at_the_titles_saved_values(isolated_db):
    did = _drama(whisper_size="small", beam_size=7, source_language="ja", separate_vocals_first=1)
    fields = _fields(q.step_settings(None, did, "transcribe", holds_paid=True))
    assert fields["beam_size"]["value"] == 7 and (fields["beam_size"]["min"], fields["beam_size"]["max"]) == (1, 10)
    assert fields["whisper_size"]["value"] == "small"
    assert fields["source_language"]["value"] == "ja"
    assert fields["separate_vocals_first"]["value"] is True
    assert fields["use_groq"]["locked"] is None
    assert [o["value"] for o in fields["transcript_mode"]["options"]][:2] == ["whisper", "hardsub_ocr"]


def test_groq_is_locked_without_paid_permission_and_other_steps_have_no_fields(isolated_db):
    did = _drama()
    assert _fields(q.step_settings(None, did, "transcribe", holds_paid=False))["use_groq"]["locked"]
    assert q.step_settings(None, did, "translate")["fields"] == []
    assert q.step_settings(None, did, "export")["fields"] == []


def test_item_settings_reach_the_run_and_leave_the_title_alone(isolated_db, started):
    did = _drama(whisper_size="small", beam_size=5)
    before = db.get_drama(did)
    q.add_item(None, did, "transcribe", True, at_pc=True,
               settings={"beam_size": 8, "separate_vocals_first": True})
    q.start(None)
    q.tick()
    assert started == {"overrides": {"beam_size": 8, "separate_vocals_first": True}}
    assert db.get_drama(did) == before
    item = q.get_queue(None)["items"][0]
    assert {r["label"]: r["value"] for r in item["changed_settings"]} == {
        "Beam size": "8", "Separate vocals first": "On"}


def test_an_item_without_settings_starts_as_before(isolated_db, started):
    did = _drama()
    q.add_item(None, did, "transcribe", True, at_pc=True)
    q.start(None)
    q.tick()
    assert started == {}


@pytest.mark.parametrize("settings", [
    {"beam_size": 0}, {"beam_size": 11}, {"beam_size": True}, {"beam_size": "5"},
    {"whisper_size": "../../evil"}, {"asr_backend_choice": "nope"}, {"sensitivity_preset": "loud"},
    {"alignment_method": "x"}, {"source_language": "fr"}, {"transcript_mode": "have_transcript"},
    {"use_groq": "yes"}, {"initial_prompt": "hi"}, {"drama_id": 2},
])
def test_bad_item_settings_are_refused_before_anything_is_queued(isolated_db, started, settings):
    did = _drama()
    with pytest.raises(InvalidInputError):
        q.add_item(None, did, "transcribe", True, at_pc=True, settings=settings)
    assert q.get_queue(None)["items"] == []


def test_item_settings_must_fit_the_language(isolated_db, started):
    did = _drama(source_language="zh")
    with pytest.raises(InvalidInputError):
        q.add_item(None, did, "transcribe", True, at_pc=True, settings={"asr_backend_choice": "anime_whisper"})
    q.add_item(None, did, "transcribe", True, at_pc=True,
               settings={"asr_backend_choice": "anime_whisper", "source_language": "ja"})


def test_screen_subtitle_reading_needs_a_video(isolated_db, started):
    from lib.errors import UnsupportedOperationError
    did = _drama()
    with pytest.raises(UnsupportedOperationError):
        q.add_item(None, did, "transcribe", True, at_pc=True, settings={"transcript_mode": "hardsub_ocr"})


def test_settings_only_belong_to_a_transcribe_item(isolated_db):
    with pytest.raises(InvalidInputError):
        q.add_item(None, _drama(), "translate", True, at_pc=True, settings={"beam_size": 3})


def test_groq_on_an_item_needs_paid_permission_at_add_and_at_start(isolated_db, started):
    from lib.errors import ForbiddenError
    did = _drama()
    with pytest.raises(ForbiddenError):
        q.add_item(None, did, "transcribe", False, at_pc=True, settings={"use_groq": True})
    q.add_item(None, did, "transcribe", True, at_pc=True, settings={"use_groq": True})
    # The adder lost the permission before the item's turn.
    principal = {"user_id": 1, "permissions": ["jobs.start"]}
    with pytest.raises(ForbiddenError):
        q._start_transcribe(principal, db.get_drama(did), {"use_groq": True})
    assert started == {}


def test_a_title_with_groq_saved_still_needs_permission_but_an_item_can_switch_it_off(isolated_db, started):
    from lib.errors import ForbiddenError
    did = _drama(use_groq=1)
    principal = {"user_id": 1, "permissions": ["jobs.start"]}
    with pytest.raises(ForbiddenError):
        q._start_transcribe(principal, db.get_drama(did), {})
    q._start_transcribe(principal, db.get_drama(did), {"use_groq": False})
    assert started == {"overrides": {"use_groq": False}}


def test_a_hand_edited_queue_document_cannot_smuggle_settings(isolated_db, started):
    did = _drama()
    q.add_item(None, did, "transcribe", True, at_pc=True, settings={"beam_size": 8})
    doc = db.get_app_setting(q.QUEUE_KEY, None)
    doc["items"][0]["settings"] = {"beam_size": 8, "initial_prompt": "x", "api_key": "k", "use_groq": [1]}
    db.set_app_setting(q.QUEUE_KEY, doc)
    with q._lock:
        assert q._load()["items"][0]["settings"] == {"beam_size": 8}


def test_the_overlay_is_what_the_run_and_the_model_check_see(isolated_db):
    from services import transcribe_options_service as opts
    did = _drama(whisper_size="small", beam_size=5, source_language="zh")
    drama = db.get_drama(did)
    merged = opts.apply_run_overrides(drama, {"whisper_size": "medium", "use_groq": True,
                                              "separate_vocals_first": False})
    assert (merged["whisper_size"], merged["use_groq"], merged["separate_vocals_first"]) == ("medium", 1, 0)
    assert merged["beam_size"] == 5 and drama["whisper_size"] == "small"
