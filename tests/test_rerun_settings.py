"""A line heard again (Review's re-transcribe, one or several lines, and fix-flagged)
hears it with what the title's last full run used: Difficult audio's detector and the
vocals a "Separate vocals first" run left. The worker itself is not run."""

import os

import pytest

import sensitivity_preset as presets
from jobs import job_launch
from core import Line
from services import (fixflag_transcribe, retranscribe_line_service, retranscribe_many_service,
                      timing_settings_service)


def _title(isolated_db, **options):
    did = isolated_db.create_drama(title_en="D", audio_filename="audio.wav", whisper_size="small",
                                   source_language="zh", status="translated", **options)
    audio = os.path.join(isolated_db.drama_dir(did), "audio.wav")
    with open(audio, "wb") as f:
        f.write(b"x")
    isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.5, zh="旧", en="Old")])
    return did, audio, isolated_db.load_line_objects(did)[0].id


def _vocals_beside(audio, newer=True):
    vocals = os.path.join(os.path.dirname(audio), "vocals.wav")
    with open(vocals, "wb") as f:
        f.write(b"v")
    stamp = os.path.getmtime(audio) + (10 if newer else -10)
    os.utime(vocals, (stamp, stamp))
    return vocals


@pytest.fixture
def started(monkeypatch):
    """The args a re-transcribe job would be started with."""
    seen = {}

    def fake_start(job_id, target, args=(), **kw):
        seen["args"] = args
        return True

    monkeypatch.setattr(job_launch, "start_process_job", fake_start)
    return seen


def _one_line(did, line_id):
    retranscribe_line_service.start_retranscribe_line(did, line_id)


def _many(did, line_id):
    retranscribe_many_service.start_retranscribe_many(did, [line_id])


# (audio, vad_threshold, preset) positions in each job's args
_ONE = (0, 7, 12)
_MANY = (0, 5, 10)


@pytest.mark.parametrize("start,where", [(_one_line, _ONE), (_many, _MANY)])
def test_difficult_audio_title_is_heard_with_the_sensitive_preset(isolated_db, started, start, where):
    did, audio, line_id = _title(isolated_db, difficult_audio=1)
    start(did, line_id)
    _, vad, preset = (started["args"][i] for i in where)
    assert preset == presets.SENSITIVE
    assert vad == timing_settings_service.vad_threshold_for({"sensitivity_preset": presets.SENSITIVE})


@pytest.mark.parametrize("start,where", [(_one_line, _ONE), (_many, _MANY)])
def test_title_without_difficult_audio_keeps_its_stored_preset(isolated_db, started, start, where):
    did, audio, line_id = _title(isolated_db)
    start(did, line_id)
    _, _, preset = (started["args"][i] for i in where)
    assert preset == presets.NORMAL


@pytest.mark.parametrize("start,where", [(_one_line, _ONE), (_many, _MANY)])
def test_separated_title_is_heard_from_its_vocals(isolated_db, started, start, where):
    did, audio, line_id = _title(isolated_db, separate_vocals_first=1)
    vocals = _vocals_beside(audio)
    start(did, line_id)
    assert started["args"][where[0]] == vocals


@pytest.mark.parametrize("start,where", [(_one_line, _ONE), (_many, _MANY)])
def test_separated_title_without_current_vocals_is_heard_from_its_audio(isolated_db, started, start, where):
    did, audio, line_id = _title(isolated_db, separate_vocals_first=1)
    start(did, line_id)    # no vocals file yet
    assert started["args"][where[0]] == audio
    _vocals_beside(audio, newer=False)    # left over from media since replaced
    start(did, line_id)
    assert started["args"][where[0]] == audio


@pytest.mark.parametrize("start,where", [(_one_line, _ONE), (_many, _MANY)])
def test_title_that_did_not_separate_ignores_a_vocals_file(isolated_db, started, start, where):
    did, audio, line_id = _title(isolated_db)
    _vocals_beside(audio)
    start(did, line_id)
    assert started["args"][where[0]] == audio


def test_fixflag_hears_a_difficult_audio_title_with_the_sensitive_preset(isolated_db):
    did, audio, line_id = _title(isolated_db, difficult_audio=1)
    drama = isolated_db.get_drama(did)
    settings = fixflag_transcribe.hearing_settings(did, drama, "small", "zh", False)
    assert settings["sensitivity_preset"] == presets.SENSITIVE
    assert settings["vad_threshold"] == timing_settings_service.vad_threshold_for(
        {"sensitivity_preset": presets.SENSITIVE})


# ---- translate: a run repeats what the title's last run used ----------------

import db  # noqa: E402
from services import profile_service, queue_service, translate_run_service, workspace_job_service  # noqa: E402


def _seeded(**options):
    did = db.create_drama(title_zh="D", translation_engine="fake", **options)
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en=""),
                        Line(idx=1, start=1.0, end=2.0, zh="再见", en="")])
    return did


@pytest.fixture
def translate_job(monkeypatch):
    """What start_job is handed for a translation: (args, kwargs)."""
    seen = {}

    def fake_start(job_id, target, *args, **kw):
        seen.update(args=args, kw=kw)
        return True

    monkeypatch.setattr(job_launch, "start_job", fake_start)
    return seen


# run_translate_job's positional args after the job id
_NOTE, _LOCALE, _STYLE, _WINDOW = 5, 8, 12, 13


def test_rerun_without_options_repeats_the_last_run(isolated_db, translate_job):
    did = _seeded()
    translate_run_service.start_translate_run(
        did, engine_name="fake", style_preset="novel", style_note="Keep honorifics.",
        locale="en-GB", context_window=3, context_window_ahead=2, batch_size=7)
    translate_run_service.start_translate_run(did, engine_name="fake")

    args, kw = translate_job["args"], translate_job["kw"]
    assert (args[_NOTE], args[_LOCALE], args[_STYLE]) == ("Keep honorifics.", "en-GB", "novel")
    assert (args[_WINDOW], kw["context_window_ahead"], kw["batch_size"]) == (3, 2, 7)


def test_values_given_to_a_rerun_win_and_become_the_next_default(isolated_db, translate_job):
    did = _seeded()
    translate_run_service.start_translate_run(did, engine_name="fake", locale="en-GB", batch_size=7)
    translate_run_service.start_translate_run(did, engine_name="fake", locale="en-AU")
    assert translate_job["args"][_LOCALE] == "en-AU"
    assert translate_job["kw"]["batch_size"] == 7
    translate_run_service.start_translate_run(did, engine_name="fake")
    assert translate_job["args"][_LOCALE] == "en-AU"


def test_a_title_that_never_ran_uses_the_defaults(isolated_db, translate_job):
    did = _seeded()
    translate_run_service.start_translate_run(did, engine_name="fake")
    assert translate_job["args"][_LOCALE] == "en-US"
    assert translate_job["args"][_NOTE] == ""


def test_the_form_starts_from_the_last_run(isolated_db, translate_job):
    did = _seeded()
    translate_run_service.start_translate_run(did, engine_name="fake", locale="en-GB",
                                              style_note="Formal.", batch_size=9)
    start = translate_run_service.get_translate_config(did)["translate_start"]
    assert (start["locale"], start["style_note"], start["batch_size"]) == ("en-GB", "Formal.", 9)


def test_a_paid_mode_is_not_remembered(isolated_db, translate_job):
    did = _seeded()
    translate_run_service.start_translate_run(did, engine_name="fake", reflect=True,
                                              job_cost_cap_usd=5.0)
    start = profile_service.translate_start(db.get_drama(did))
    assert not {"reflect", "bulk", "cost_cap", "fallback_engines"} & set(start)


def test_model_applies_only_to_a_run_on_the_titles_engine(isolated_db):
    did = _seeded()
    profile_service.remember_run(
        did, db.get_drama(did), on_title_engine=True, model="model-a", style_preset="audio_drama",
        style_note="", locale="en-US", batch_size=None, context_window=1, context_window_ahead=1)
    drama = db.get_drama(did)
    assert profile_service.title_run_defaults(drama, "fake", "fake")["model"] == "model-a"
    assert "model" not in profile_service.title_run_defaults(drama, "other", "fake")
    profile_service.remember_run(
        did, drama, on_title_engine=False, model="model-b", style_preset="audio_drama",
        style_note="", locale="en-US", batch_size=None, context_window=1, context_window_ahead=1)
    assert profile_service.title_run_defaults(db.get_drama(did), "fake", "fake")["model"] == "model-a"


def test_a_model_no_longer_offered_falls_back_to_the_engines_default(isolated_db, translate_job):
    did = _seeded()
    profile_service.remember_run(
        did, db.get_drama(did), on_title_engine=True, model="gone-model", style_preset="audio_drama",
        style_note="", locale="en-US", batch_size=None, context_window=1, context_window_ahead=1)
    out = translate_run_service.start_translate_run(did, engine_name="fake")
    assert out["job_id"] == f"translate_{did}"


def test_queued_run_and_its_settings_panel_repeat_the_last_run(isolated_db, translate_job):
    did = _seeded()
    translate_run_service.start_translate_run(did, engine_name="fake", locale="en-GB",
                                              style_note="Formal.", style_preset="novel", batch_size=9)
    drama = db.get_drama(did)
    args = queue_service._translate_run_args(drama)
    assert (args["locale"], args["style_note"]) == ("en-GB", "Formal.")
    rows = {r["label"]: r for r in queue_service._translate_rows(drama)}
    assert rows["English variant"]["value"] == "en-GB"
    assert rows["Batch size"]["value"] == "9"


def test_library_batch_translates_each_title_as_its_last_run_did(isolated_db, translate_job):
    did = _seeded()
    translate_run_service.start_translate_run(did, engine_name="fake", locale="en-GB",
                                              style_note="Formal.", style_preset="novel", batch_size=9)
    workspace_job_service.run_bulk_series_translate_job(
        "bulk", [did], {"fake": "k"}, default_locale="en-US", locale_is_explicit=False)
    args, kw = translate_job["args"], translate_job["kw"]
    assert (args[_NOTE], args[_LOCALE], args[_STYLE]) == ("Formal.", "en-GB", "novel")
    assert kw["batch_size"] == 9


def test_library_batch_with_a_chosen_variant_uses_it_for_every_title(isolated_db, translate_job):
    did = _seeded()
    translate_run_service.start_translate_run(did, engine_name="fake", locale="en-GB")
    workspace_job_service.run_bulk_series_translate_job(
        "bulk", [did], {"fake": "k"}, default_locale="en-AU", locale_is_explicit=True)
    assert translate_job["args"][_LOCALE] == "en-AU"


# ---- a remembered cloud tag is never picked up by a run that named no model ----

CLOUD = "gpt-oss:120b-cloud"


def _ollama_title_that_last_ran_on_cloud():
    did = db.create_drama(title_zh="D", translation_engine="ollama")
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="")])
    profile_service.remember_run(
        did, db.get_drama(did), on_title_engine=True, model=CLOUD, style_preset="audio_drama",
        style_note="", locale="en-GB", batch_size=None, context_window=1, context_window_ahead=1)
    return did


def test_cloud_tag_stays_a_form_start_but_is_not_applied_by_the_server(isolated_db):
    did = _ollama_title_that_last_ran_on_cloud()
    drama = db.get_drama(did)
    assert profile_service.translate_start(drama)["engine_model"] == CLOUD
    assert "model" not in profile_service.title_run_defaults(drama, "ollama", "ollama")


def test_run_without_a_model_does_not_use_the_remembered_cloud_tag(isolated_db, translate_job):
    did = _ollama_title_that_last_ran_on_cloud()
    translate_run_service.start_translate_run(did)
    assert translate_job["args"][3].model != CLOUD
    assert translate_job["args"][_LOCALE] == "en-GB"   # the rest is still repeated


def test_library_batch_does_not_use_the_remembered_cloud_tag(isolated_db, translate_job):
    did = _ollama_title_that_last_ran_on_cloud()
    workspace_job_service.run_bulk_series_translate_job(
        "bulk", [did], {}, locale_is_explicit=False)
    assert translate_job["args"][3].model != CLOUD


def test_queue_does_not_use_or_price_the_remembered_cloud_tag(isolated_db):
    did = _ollama_title_that_last_ran_on_cloud()
    assert queue_service._queued_model_and_batch(db.get_drama(did), "ollama") == (None, None)


# ---- the queue prices the model and batch size it will run with ----

def test_queue_cost_is_estimated_for_the_remembered_model_and_batch(isolated_db, monkeypatch):
    did = _seeded()
    profile_service.remember_run(
        did, db.get_drama(did), on_title_engine=True, model="model-a", style_preset="audio_drama",
        style_note="", locale="en-US", batch_size=9, context_window=1, context_window_ahead=1)
    monkeypatch.setattr(translate_run_service, "_require_offered_model", lambda e, m: None)
    seen = {}

    def estimate(drama_id, **kw):
        seen.update(kw)
        raise queue_service.ServiceError("stop")
    monkeypatch.setattr(translate_run_service, "estimate_translate_cost", estimate)
    model, batch = queue_service._queued_model_and_batch(db.get_drama(did), "fake")
    queue_service._translate_cost(did, "claude", model, batch)
    assert seen == {"model": "model-a", "batch_size": 9}


def test_queue_shows_the_default_when_the_remembered_model_is_gone(isolated_db):
    did = _seeded()
    profile_service.remember_run(
        did, db.get_drama(did), on_title_engine=True, model="gone-model", style_preset="audio_drama",
        style_note="", locale="en-US", batch_size=None, context_window=1, context_window_ahead=1)
    assert queue_service._queued_model_and_batch(db.get_drama(did), "fake")[0] is None


# ---- remembering does not undo a profile applied while the run started ----

def test_a_profile_applied_during_the_start_is_kept(isolated_db):
    did = _seeded()
    stale = db.get_drama(did)
    db.update_drama(did, translate_start_json='{"thinking": true}')
    profile_service.remember_run(
        did, stale, on_title_engine=True, model=None, style_preset="audio_drama",
        style_note="", locale="en-US", batch_size=None, context_window=1, context_window_ahead=1)
    assert profile_service.translate_start(db.get_drama(did))["thinking"] is True


# ---- vocals from media since replaced ----

def test_vocals_of_a_replaced_file_that_kept_its_old_modified_time_are_not_used(isolated_db, started):
    did, audio, line_id = _title(isolated_db, separate_vocals_first=1)
    vocals = _vocals_beside(audio)
    old = os.path.getmtime(vocals) - 100
    os.utime(vocals, (old, old))                      # the vocals pre-date the file now on disk
    os.utime(audio, (old - 50, old - 50))             # a copy that kept an older modified time
    _one_line(did, line_id)
    assert started["args"][0] == audio
