"""The two Translate toggles ("Include baihe/GL genre guidance" and "Default
ambiguous pronouns to she/her") must reach the prompt on every path that
sends source lines to an engine, and the owner's choice must be stored per
title so a path that is not handed the toggles reuses it. Fully mocked: the
fake engine captures the system prompt, no network."""
import time

import pytest

import background_jobs
import db
import translate_engines
import translation_guide as tguide
from core import Line
from services import (line_ai_service, review_jobs_service,
                      translate_run_service, workspace_job_service)

FEMALE = tguide.FEMALE_PRONOUN_DEFAULT_GUIDANCE.strip().splitlines()[0]
GENRE = tguide.BAIHE_SPECIFIC_GUIDANCE.strip().splitlines()[0]


@pytest.fixture(autouse=True)
def _clean_jobs():
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


@pytest.fixture
def prompts(monkeypatch):
    """Every system prompt the fake engine is asked to translate with."""
    from tests import fake_engine
    seen = []
    real = fake_engine.FakeEngine.translate_batch

    def capture(self, zh_lines, context):
        seen.append(translate_engines.build_stable_system_text(dict(context)))
        return real(self, zh_lines, context)
    monkeypatch.setattr(fake_engine.FakeEngine, "translate_batch", capture)
    return seen


@pytest.fixture
def builder_calls(monkeypatch):
    """The (include_genre_notes, default_female_pronouns) every call of the
    style builder received."""
    calls = []
    real = tguide.build_style_guidelines

    def spy(*a, **k):
        calls.append((k.get("include_genre_notes"), k.get("default_female_pronouns")))
        return real(*a, **k)
    monkeypatch.setattr(tguide, "build_style_guidelines", spy)
    return calls


def _wait(job_id):
    for _ in range(400):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _drama(isolated_db, saved=None, **kw):
    did = db.create_drama(title_zh="D", **kw)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="他来了", en=""),
                        Line(idx=1, start=1, end=2, zh="你好", en="")])
    if saved:
        db.update_drama(did, **saved)
    return did


SAVED_FEMALE_NO_GENRE = {"default_female_pronouns": 1, "include_genre_notes": 0}


def test_a_new_title_starts_from_the_api_defaults(isolated_db, prompts):
    did = _drama(isolated_db)
    _wait(translate_run_service.start_translate_run(did, engine_name="fake")["job_id"])
    assert GENRE in prompts[0] and FEMALE not in prompts[0]


def test_single_run_without_toggles_reuses_the_saved_choice(isolated_db, prompts):
    did = _drama(isolated_db, SAVED_FEMALE_NO_GENRE)
    _wait(translate_run_service.start_translate_run(did, engine_name="fake")["job_id"])
    assert FEMALE in prompts[0] and GENRE not in prompts[0]


def test_explicit_toggles_are_saved_for_later_runs(isolated_db, prompts):
    did = _drama(isolated_db)
    _wait(translate_run_service.start_translate_run(
        did, engine_name="fake", default_female_pronouns=True, include_genre_notes=False)["job_id"])
    assert FEMALE in prompts[0] and GENRE not in prompts[0]
    drama = db.get_drama(did)
    assert (drama["default_female_pronouns"], drama["include_genre_notes"]) == (1, 0)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="他来了", en=""),
                        Line(idx=1, start=1, end=2, zh="你好", en="")])
    prompts.clear()
    _wait(translate_run_service.start_translate_run(did, engine_name="fake")["job_id"])
    assert FEMALE in prompts[0] and GENRE not in prompts[0]


def test_a_later_explicit_off_wins_over_the_saved_choice(isolated_db, prompts):
    did = _drama(isolated_db, SAVED_FEMALE_NO_GENRE)
    _wait(translate_run_service.start_translate_run(
        did, engine_name="fake", default_female_pronouns=False,
        include_genre_notes=True)["job_id"])
    assert FEMALE not in prompts[0] and GENRE in prompts[0]


def test_fix_flagged_without_toggles_reuses_the_saved_choice(isolated_db, builder_calls,
                                                             monkeypatch):
    did = _drama(isolated_db, SAVED_FEMALE_NO_GENRE, status="translated")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="他来了", en="x", flag="bad")])
    _wait(review_jobs_service.start_fix_flagged(did, engine_name="fake")["job_id"])
    assert builder_calls and builder_calls[-1] == (False, True)


def test_library_bulk_translate_without_toggles_reuses_each_titles_choice(isolated_db,
                                                                         builder_calls,
                                                                         monkeypatch):
    did = _drama(isolated_db, SAVED_FEMALE_NO_GENRE, status="aligned",
                 translation_engine="claude")
    wjs = workspace_job_service
    monkeypatch.setattr(wjs.translate_engines, "get_engine", lambda *a, **k: object())
    monkeypatch.setattr(wjs.background_jobs, "start_job", lambda *a, **k: False)
    monkeypatch.setattr(wjs.db, "get_month_spend", lambda: 0.0)
    wjs.run_bulk_series_translate_job("bulk_toggles", [did], {"claude": "k"},
                                      include_genre_notes=None, default_female_pronouns=None)
    assert builder_calls == [(False, True)]


def test_line_ai_improve_reuses_the_saved_choice(isolated_db, monkeypatch, builder_calls):
    from services import translate_service
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    from tests import fake_engine
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: fake_engine.FakeEngine())
    did = _drama(isolated_db, SAVED_FEMALE_NO_GENRE)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="他来了", en="He came.")])
    line_id = db.load_lines(did)[0]["id"]
    monkeypatch.setattr(line_ai_service.line_tools, "improve_line",
                        lambda *a, **k: "better")
    line_ai_service.improve_line(did, line_id, engine_name="fake")
    assert builder_calls == [(False, True)]


def test_resolver_reads_the_saved_choice_only_when_not_passed(isolated_db, builder_calls):
    did = _drama(isolated_db, SAVED_FEMALE_NO_GENRE)
    drama = db.get_drama(did)
    lines = []
    workspace_job_service.build_run_style_context(did, drama, lines, "audio_drama")
    workspace_job_service.build_run_style_context(
        did, drama, lines, "audio_drama", include_genre_notes=True, default_female_pronouns=False)
    assert builder_calls == [(False, True), (True, False)]


def test_unsaved_title_resolves_to_genre_on_she_her_off(isolated_db, builder_calls):
    did = _drama(isolated_db)
    workspace_job_service.build_run_style_context(did, db.get_drama(did), [], "audio_drama")
    assert builder_calls == [(True, False)]


def test_the_chosen_toggles_are_baked_into_the_persisted_bulk_args(isolated_db, monkeypatch):
    """A resumed off-peak job re-reads translate_args, so the guidance text it
    sends must be the one the run was started with, whatever is saved later."""
    import bulk_translate
    did = _drama(isolated_db)
    drama = db.get_drama(did)
    _glossary, guidelines, _names = workspace_job_service.build_run_style_context(
        did, drama, [], "audio_drama", include_genre_notes=False, default_female_pronouns=True)
    captured = {}
    monkeypatch.setattr(bulk_translate, "schedule_offpeak_translation",
                        lambda drama_id, lines, name, model, args, **kw: captured.update(args) or 1)
    submit = translate_run_service._bulk_submitter(
        did, drama, type("E", (), {"model": "m"})(), "deepseek", False, None, None, guidelines,
        "", "en-US", "audio_drama", 6, 3, 20, False, None, None, True)
    submit()
    assert FEMALE in captured["style_guidelines"] and GENRE not in captured["style_guidelines"]
    assert captured["thinking"] is True
