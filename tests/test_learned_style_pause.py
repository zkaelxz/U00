"""
The learned-style pause (parity for the Streamlit `apply_style_profile`
checkbox) reaches every path that puts the learned style in a prompt.

The switch itself is the stored profile's `apply: false`, set by
`POST /api/review-extras/dramas/{id}/style/apply` (React: Review > AI
extras > Learn my style > "Use in future translations"; its API tests are
in test_api_review_extras.py). adaptive_style.profile_to_prompt_block
returns "" for a paused profile; these tests pin that each caller goes
through it: the Translate run, build_run_style_context (fix-flagged,
series bulk translate, stronger engine), line Improve and the CLI.
Fully mocked: no network.
"""
import argparse
import contextlib
import io
import time

import pytest

import background_jobs
import cli_translate
import db
import line_tools
import translate_engines
from core import Line
from services import (line_ai_service, translate_run_service, translate_service,
                      workspace_job_service)

PREF = "Always use contractions in dialogue"


def _drama(apply):
    sid = db.get_or_create_series("Pause Series")
    did = db.create_drama(title_zh="D", series_id=sid, status="aligned")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en=""),
                        Line(idx=1, start=1, end=2, zh="再见", en="bye")])
    profile = {"preferences": [PREF]}
    if apply is False:
        profile["apply"] = False
    db.save_style_profile(f"series:{sid}", profile, 8)
    return did


def _wait(job_id):
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.mark.parametrize("apply", [True, False])
def test_translate_run(isolated_db, monkeypatch, apply):
    seen = []
    real = translate_run_service.translation_guide.build_style_guidelines

    def spy(*a, **k):
        out = real(*a, **k)
        seen.append(out)
        return out
    monkeypatch.setattr(translate_run_service.translation_guide, "build_style_guidelines", spy)
    did = _drama(apply)
    _wait(translate_run_service.start_translate_run(did, engine_name="fake")["job_id"])
    assert seen and (PREF in seen[0]) is apply


@pytest.mark.parametrize("apply", [True, False])
def test_run_style_context(isolated_db, apply):
    # Fix-flagged, the series bulk translate and "Try with the stronger
    # engine" all build their prompt here.
    did = _drama(apply)
    _g, guidelines, _n = workspace_job_service.build_run_style_context(
        did, db.get_drama(did), [Line(idx=0, start=0, end=1, zh="你好")], "audio_drama")
    assert (PREF in guidelines) is apply


@pytest.mark.parametrize("apply", [True, False])
def test_line_improve(isolated_db, monkeypatch, apply):
    class FakeEngine:
        model = "fake-model"
        supports_reference = True
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())
    seen = {}

    def fake(zh, en, engine, issue="", source_language="zh", style_guidelines=""):
        seen["style"] = style_guidelines
        return "hi"
    monkeypatch.setattr(line_tools, "improve_line", fake)
    did = _drama(apply)
    line_id = db.load_lines(did)[1]["id"]
    line_ai_service.improve_line(did, line_id, engine_name="claude")
    assert (PREF in seen["style"]) is apply


@pytest.mark.parametrize("apply", [True, False])
def test_cli_translate(isolated_db, monkeypatch, apply):
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
    seen = {}

    def fake_translate(lines, engine, **kw):
        seen.update(kw)
        return lines, []
    monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)
    did = _drama(apply)
    args = argparse.Namespace(
        id=did, status=None, engine="claude", api_key="fake-key", model=None,
        style_note=None, style_preset="audio_drama", locale="en-US", force=False,
        ollama_num_ctx=None)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_translate.cmd_translate(args)
    assert (PREF in seen["style_guidelines"]) is apply
