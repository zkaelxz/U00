"""
tests/test_cli.py -- coverage for cli.py's headless batch driver.

cli.py had zero test coverage before this file. It's focused narrowly
on the real, confirmed gaps this session's audit found and fixed:

  - cmd_translate silently skipped the series glossary, style
    guidelines, locale, and character names that the Workspace tab's
    own Translate button always sends for the same drama.
  - cmd_dub's Line reconstruction dropped flag/flag_note entirely, so
    db.save_lines() at the end of every dub run silently wiped every
    review-queue flag in the drama.
  - cmd_run reused cmd_translate's argparse Namespace but its own
    subparser (p_run) never defined --status/--force/--style-preset/
    --locale, so it crashed with AttributeError before reaching
    cmd_translate at all.

Not attempting full coverage of every cli.py command here -- just the
paths this audit touched.
"""

import argparse
import io
import contextlib
import os
import sys

import json

import pytest
import db
import diagnostics
import translate_engines
import dub as dub_module
from core import Line
import cli


def _translate_args(**overrides):
    defaults = dict(
        id=None, status=None, engine="claude", api_key="fake-key", model=None,
        style_note=None, style_preset="audio_drama", locale="en-US", force=False,
        ollama_num_ctx=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _dub_args(**overrides):
    defaults = dict(id=None, gpt_sovits_url=None, m4b=False)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestCmdTranslateParity:
    def test_passes_glossary_style_locale_and_character_names_through(self, isolated_db, monkeypatch):
        series_id = isolated_db.get_or_create_series("Test Series")
        did = isolated_db.create_drama(title_en="Test", series_id=series_id, status="aligned")
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Xiaoling")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", speaker="SPEAKER_00")])

        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())

        seen = {}
        def fake_translate(lines, engine, **kwargs):
            seen.update(kwargs)
            return lines, []
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

        args = _translate_args(id=did, style_preset="novel", locale="en-GB")
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(args)

        assert seen["locale"] == "en-GB"
        assert seen["character_names"] == {"SPEAKER_00": "Xiaoling"}
        # style_guidelines is built from the novel preset -- just check it
        # was actually computed and passed, not left as the default "".
        assert seen["style_guidelines"]

    @pytest.mark.parametrize("flags,female,genre", [
        ({}, False, True),
        ({"female_pronouns": True, "no_genre_notes": True}, True, False),
    ])
    def test_pronoun_and_genre_toggles_reach_the_style_guidelines(
            self, isolated_db, monkeypatch, flags, female, genre):
        """Parity with the Workspace checkboxes / API TranslateRunStart:
        she/her off and genre notes on unless a flag says otherwise."""
        did = isolated_db.create_drama(title_en="Test", status="aligned")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好")])
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine",
                            lambda lines, engine, **kw: (lines, []))
        seen = {}
        real = cli.tguide.build_style_guidelines
        def spy(*a, **k):
            seen.update(k)
            return real(*a, **k)
        monkeypatch.setattr(cli.tguide, "build_style_guidelines", spy)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(_translate_args(id=did, **flags))
        assert seen["default_female_pronouns"] is female
        assert seen["include_genre_notes"] is genre

    @pytest.mark.parametrize("command", ["translate", "run"])
    def test_real_parser_has_the_toggle_flags(self, monkeypatch, command):
        captured = {}
        monkeypatch.setattr(cli, "cmd_translate", lambda a: captured.setdefault("args", a))
        monkeypatch.setattr(cli, "cmd_run", lambda a: captured.setdefault("args", a))
        monkeypatch.setattr(sys, "argv", ["cli.py", command, "--id", "1", "--api-key", "k",
                                          "--female-pronouns", "--no-genre-notes"])
        cli.main()
        assert captured["args"].female_pronouns is True
        assert captured["args"].no_genre_notes is True

    def test_context_window_ahead_and_batch_size_default_to_the_same_values_as_before(
            self, isolated_db, monkeypatch):
        """Step 32: this command used to have no way to set any of these
        three -- confirms the new flags default to translate_lines_with_
        engine's own pre-existing defaults, so an old script calling this
        command with none of the new flags keeps behaving exactly as
        before."""
        did = isolated_db.create_drama(title_en="Test", status="aligned")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好")])
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
        seen = {}
        def fake_translate(lines, engine, **kwargs):
            seen.update(kwargs)
            return lines, []
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

        args = _translate_args(id=did)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(args)

        assert seen["context_window"] == 6
        assert seen["context_window_ahead"] == 3
        assert seen["batch_size"] == 20

    def test_context_window_ahead_and_batch_size_flags_reach_the_engine(
            self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="Test", status="aligned")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好")])
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
        seen = {}
        def fake_translate(lines, engine, **kwargs):
            seen.update(kwargs)
            return lines, []
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

        args = _translate_args(id=did, context_window=10, context_window_ahead=8, batch_size=30)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(args)

        assert seen["context_window"] == 10
        assert seen["context_window_ahead"] == 8
        assert seen["batch_size"] == 30

    def test_glossary_terms_only_looked_up_when_drama_has_a_series(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="Standalone", status="aligned")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好")])

        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())

        called = {"list_glossary_terms": False}
        real_list_glossary_terms = db.list_glossary_terms
        def spy(*a, **k):
            called["list_glossary_terms"] = True
            return real_list_glossary_terms(*a, **k)
        monkeypatch.setattr(db, "list_glossary_terms", spy)

        seen = {}
        def fake_translate(lines, engine, **kwargs):
            seen.update(kwargs)
            return lines, []
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

        args = _translate_args(id=did)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(args)

        assert called["list_glossary_terms"] is False
        assert seen["glossary_terms"] is None

    def test_omits_speakers_with_no_name_set(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="Test", status="aligned")
        isolated_db.upsert_character(did, "SPEAKER_00")  # no character_name
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", speaker="SPEAKER_00")])

        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())

        seen = {}
        def fake_translate(lines, engine, **kwargs):
            seen.update(kwargs)
            return lines, []
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

        args = _translate_args(id=did)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(args)

        assert seen["character_names"] == {}

    def test_sends_the_same_pronoun_hints_and_speaker_labels_as_the_ui(self, isolated_db, monkeypatch):
        """Step 1e: cmd_translate used to skip the pronoun hints block
        entirely. Checked against the UI's own run_translate_job for the
        same drama, not a restated expectation."""
        import background_jobs
        import translation_guide as tguide
        from services.workspace_job_service import run_translate_job

        series_id = isolated_db.get_or_create_series("Test Series")
        isolated_db.upsert_series_character(series_id, "Su Shan", gender="female")
        did = isolated_db.create_drama(title_en="Test", series_id=series_id, status="aligned")
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Su Shan", pronouns="they/them")
        isolated_db.upsert_character(did, "SPEAKER_01", character_name="Rin", pronouns="xe/xem")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", speaker="SPEAKER_00")])

        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
        seen = []
        def fake_translate(lines, engine, **kwargs):
            seen.append(kwargs)
            return lines, []
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(_translate_args(id=did))
        job_id = "test_cli_ui_pronoun_parity"
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        run_translate_job(job_id, did, [Line(idx=0, start=0.0, end=1.0, zh="你好", speaker="SPEAKER_00")],
                           object(), {"id": did}, "", None, False, "en-US", None, "", "claude",
                           "audio_drama")
        background_jobs._jobs.pop(job_id, None)
        cli_kwargs, ui_kwargs = seen

        assert cli_kwargs["character_names"] == ui_kwargs["character_names"] == {
            "SPEAKER_00": "Su Shan (they/them)", "SPEAKER_01": "Rin (xe/xem)"}
        # The same call the Workspace Translate button makes for its hints block.
        ui_hints = tguide.build_character_gender_hints(
            isolated_db.list_series_characters(series_id),
            isolated_db.list_characters_with_series_names(did))
        assert ui_hints and ui_hints in cli_kwargs["style_guidelines"]
        assert "Su Shan: they/them" in cli_kwargs["style_guidelines"]
        assert "Rin: xe/xem" in cli_kwargs["style_guidelines"]

    def test_flag_and_flag_note_survive_a_translate_run(self, isolated_db, monkeypatch):
        """save_cb below writes db.save_lines() on every batch -- if the
        Line reconstruction in cmd_translate dropped flag/flag_note, any
        review-queue flag set before this run would be silently wiped."""
        did = isolated_db.create_drama(title_en="Test", status="aligned")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=1.0, zh="你好", flag="review", flag_note="check this"),
        ])

        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())

        def fake_translate(lines, engine, **kwargs):
            for ln in lines:
                ln.en = "Hello"
            return lines, []
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

        args = _translate_args(id=did)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(args)

        rows = isolated_db.load_lines(did)
        assert rows[0]["flag"] == "review"
        assert rows[0]["flag_note"] == "check this"

    def test_reflect_flag_threads_through_and_saves_notes(self, isolated_db, monkeypatch):
        """CLI/UI parity (Step 7): --reflect must reach translate_lines_with_
        engine the same way the Workspace Translate button's checkbox does,
        and the reflection critique must actually get saved as a note."""
        did = isolated_db.create_drama(title_en="Test", status="aligned")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好")])

        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())

        seen = {}
        def fake_translate(lines, engine, notes_cb=None, **kwargs):
            seen.update(kwargs)
            if notes_cb:
                notes_cb([{"line_idx": 0, "term": "", "note_type": "reflection", "note": "a critique"}])
            return lines, []
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

        args = _translate_args(id=did, reflect=True)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(args)

        assert seen["reflect"] is True
        notes = isolated_db.list_translation_notes(did)
        assert [n["note"] for n in notes] == ["a critique"]

    def test_reflect_defaults_to_false_when_the_namespace_lacks_it(self, isolated_db, monkeypatch):
        # Defensive getattr, same pattern already used for ollama_url --
        # an older/hand-built Namespace without --reflect must not crash.
        did = isolated_db.create_drama(title_en="Test", status="aligned")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好")])
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
        seen = {}
        def fake_translate(lines, engine, **kwargs):
            seen.update(kwargs)
            return lines, []
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

        args = _translate_args(id=did)  # no "reflect" key at all
        assert not hasattr(args, "reflect")
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(args)
        assert seen["reflect"] is False


class TestCmdTranslateSpendingCaps:
    """Step 9 parity with the Workspace Translate job: the CLI logs usage
    (so the monthly cap sees CLI spend too) and honours both caps."""

    class _Engine:
        name = "claude"
        supports_reference = True
        model = "claude-sonnet-5"

        def __init__(self):
            self.calls = 0
            self.last_usage = {}

        def translate_batch(self, zh_lines, context):
            self.calls += 1
            self.last_usage = {"input_tokens": 1_000_000, "output_tokens": 0}
            return [f"EN:{z}" for z in zh_lines]

    def _drama(self, isolated_db, n=45):
        did = isolated_db.create_drama(title_en="Test", status="aligned")
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"句{i}") for i in range(n)])
        return did

    def test_cost_cap_stops_the_run_and_usage_is_logged(self, isolated_db, monkeypatch):
        engine = self._Engine()
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: engine)
        did = self._drama(isolated_db)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_translate(_translate_args(id=did, cost_cap=3.0, monthly_cap=None))
        assert engine.calls == 2
        assert sum(1 for r in isolated_db.load_lines(did) if r["en"]) == 40
        assert isolated_db.get_usage_summary(did)["estimated_cost_usd"] == pytest.approx(4.0)
        assert "stopped at the spending cap" in out.getvalue()

    def test_monthly_cap_used_up_refuses_to_start(self, isolated_db, monkeypatch):
        engine = self._Engine()
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: engine)
        did = self._drama(isolated_db, n=2)
        isolated_db.log_usage(did, "claude", "m", "translate", 1, 1, 50.0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                cli.cmd_translate(_translate_args(id=did, cost_cap=None, monthly_cap=20.0))
            except SystemExit:
                pass
        assert engine.calls == 0
        assert not any(r["en"] for r in isolated_db.load_lines(did))


class TestCmdTranslateRetryAndWorkspaceParity:
    """Step 25c item 4: the CLI marked a drama "translated" even after a
    batch failure or a cost-cap stop, so its own suggested re-run (default
    --status aligned) skipped it; and it skipped the post-translate steps
    Workspace does. Both now run bulk_translate.finish_translation_run."""

    class _Engine(TestCmdTranslateSpendingCaps._Engine):
        def translate_batch(self, zh_lines, context):
            super().translate_batch(zh_lines, context)
            return [f"Lin Mo says {z}" for z in zh_lines]

    def test_the_suggested_retry_after_a_cost_cap_stop_translates_the_rest(self, isolated_db, monkeypatch):
        engine = self._Engine()
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: engine)
        did = isolated_db.create_drama(title_en="Test", status="aligned")
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"句{i}") for i in range(45)])

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_translate(_translate_args(cost_cap=3.0, monthly_cap=None))
        assert "stopped at the spending cap" in out.getvalue()
        assert sum(1 for r in isolated_db.load_lines(did) if r["en"]) == 40
        assert isolated_db.get_drama(did)["status"] == "aligned"

        # The same command again, as the message suggests (a higher cap).
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(_translate_args(cost_cap=None, monthly_cap=None))
        assert all(r["en"] for r in isolated_db.load_lines(did))
        assert engine.calls == 3
        assert isolated_db.get_drama(did)["status"] == "translated"

    def test_batch_failures_are_persisted_and_the_drama_stays_retryable(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="Test", status="aligned")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="一"),
                                     Line(idx=1, start=1, end=2, zh="二")])
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())

        def half_translated(lines, engine, **kwargs):
            lines[0].en = "One"
            kwargs["save_cb"](lines)
            return lines, [{"lines": [1], "error": "boom"}]
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", half_translated)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_translate(_translate_args(id=did))

        d = isolated_db.get_drama(did)
        assert d["status"] == "aligned"
        assert json.loads(d["last_translate_errors"]) == [{"lines": [1], "error": "boom"}]
        assert "re-run this command" in out.getvalue()
        assert [x["id"] for x in isolated_db.list_dramas(status="aligned")] == [did]

    def test_enforces_exact_glossary_terms_and_saves_a_version_like_workspace(self, isolated_db, monkeypatch):
        import background_jobs
        from services.workspace_job_service import run_translate_job

        series_id = isolated_db.get_or_create_series("Test Series")
        isolated_db.upsert_glossary_term(series_id, "林默", "Lin Mo", notes="Lin Mo|Lim Mo",
                                         enforce_exact=True)
        engine = self._Engine()
        engine.translate_batch = lambda zh_lines, context: [f"Lim Mo says {z}" for z in zh_lines]
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: engine)

        def drama():
            did = isolated_db.create_drama(title_en="Test", series_id=series_id, status="aligned")
            isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="林默来了")])
            return did

        cli_did, ui_did = drama(), drama()
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(_translate_args(id=cli_did))
        job_id = "test_cli_ui_finish_parity"
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        run_translate_job(job_id, ui_did, isolated_db.load_line_objects(ui_did), engine,
                          isolated_db.get_drama(ui_did), "", None, False, "en-US",
                          isolated_db.list_glossary_terms(series_id), "", "claude", "audio_drama")
        background_jobs._jobs.pop(job_id, None)

        for did in (cli_did, ui_did):
            assert [r["en"] for r in isolated_db.load_lines(did)] == ["Lin Mo says 林默来了"]
            versions = isolated_db.list_translation_versions(did)
            assert [(v["label"], v["is_active"]) for v in versions] == [("claude · audio_drama", 1)]
            assert isolated_db.get_drama(did)["status"] == "translated"


    def test_a_cancelled_workspace_run_saves_no_version_and_stays_untranslated(self, isolated_db, monkeypatch):
        """Cancelling marked the drama "translated" and saved an active
        version, dropping it out of Library's untranslated selection."""
        import background_jobs
        from services.workspace_job_service import run_translate_job

        did = isolated_db.create_drama(title_en="Test", status="aligned")
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"句{i}") for i in range(45)])
        job_id = "test_cancelled_translate_run"
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        engine = self._Engine()

        def translate_then_cancel(zh_lines, context):
            background_jobs.request_cancel(job_id)  # the user clicks Cancel mid-run
            return self._Engine.translate_batch(engine, zh_lines, context)
        engine.translate_batch = translate_then_cancel

        run_translate_job(job_id, did, isolated_db.load_line_objects(did), engine,
                          isolated_db.get_drama(did), "", None, False, "en-US", None, "",
                          "claude", "audio_drama")
        background_jobs._jobs.pop(job_id, None)

        assert sum(1 for r in isolated_db.load_lines(did) if r["en"]) == 20
        assert isolated_db.get_drama(did)["status"] == "aligned"
        assert isolated_db.list_translation_versions(did) == []


class TestCmdAlignUsesDramaSettings:
    """Step 25d item 10: this command used to always use args.whisper_size
    (or its own hardcoded DEFAULT_WHISPER_SIZE), plain character-diff
    alignment, and no recognition priming at all -- ignoring the drama's
    own saved Whisper size / alignment method (Workspace's own "3.
    Recognition accuracy" section) and its series glossary."""

    def _drama_with_transcript(self, isolated_db, **kw):
        import os
        did = isolated_db.create_drama(title_en="Test", status="not started",
                                       audio_filename="audio.wav", **kw)
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(ddir, "transcript.txt"), "w", encoding="utf-8") as f:
            f.write("你好")
        return did

    def _args(self, **overrides):
        defaults = dict(id=None, whisper_size=None, fast=False)
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_uses_the_dramas_own_saved_whisper_size(self, isolated_db, monkeypatch):
        did = self._drama_with_transcript(isolated_db, whisper_size="large-v3")
        seen = {}

        def fake_transcribe(audio_path, model_size, **kw):
            seen["model_size"] = model_size
            return [{"start": 0.0, "end": 1.0, "text": "你好"}]
        monkeypatch.setattr(cli, "transcribe_for_timing", fake_transcribe)

        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_align(self._args(id=did))
        assert seen["model_size"] == "large-v3"

    def test_an_explicit_flag_still_overrides_the_dramas_saved_size(self, isolated_db, monkeypatch):
        did = self._drama_with_transcript(isolated_db, whisper_size="large-v3")
        seen = {}

        def fake_transcribe(audio_path, model_size, **kw):
            seen["model_size"] = model_size
            return [{"start": 0.0, "end": 1.0, "text": "你好"}]
        monkeypatch.setattr(cli, "transcribe_for_timing", fake_transcribe)

        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_align(self._args(id=did, whisper_size="small"))
        assert seen["model_size"] == "small"

    def test_glossary_terms_are_used_to_prime_recognition(self, isolated_db, monkeypatch):
        sid = isolated_db.get_or_create_series("Test Series")
        isolated_db.upsert_glossary_term(sid, "苏杉", "Su Shan")
        did = self._drama_with_transcript(isolated_db, series_id=sid)
        seen = {}

        def fake_transcribe(audio_path, model_size, **kw):
            seen["initial_prompt"] = kw.get("initial_prompt")
            return [{"start": 0.0, "end": 1.0, "text": "你好"}]
        monkeypatch.setattr(cli, "transcribe_for_timing", fake_transcribe)

        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_align(self._args(id=did))
        assert "苏杉" in (seen.get("initial_prompt") or "")

    def test_qwen3_forced_align_is_used_when_saved_on_the_drama(self, isolated_db, monkeypatch):
        did = self._drama_with_transcript(isolated_db, alignment_method="qwen3_forced_align")
        monkeypatch.setattr(cli, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "你好"}])
        import forced_align
        calls = []

        def fake_align_with_qwen3(audio_path, user_lines, segments, language, **kw):
            calls.append(language)
            return [Line(idx=0, start=0.0, end=1.0, zh="你好")]
        monkeypatch.setattr(forced_align, "align_with_qwen3", fake_align_with_qwen3)

        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_align(self._args(id=did))
        assert calls == ["zh"]

    def test_default_whisper_diff_alignment_is_unaffected(self, isolated_db, monkeypatch):
        """No alignment_method saved -- must still use the plain
        character-diff aligner, same as before this fix."""
        did = self._drama_with_transcript(isolated_db)
        monkeypatch.setattr(cli, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "你好"}])
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_align(self._args(id=did))
        assert isolated_db.load_lines(did)[0]["zh"] == "你好"
        assert isolated_db.get_drama(did)["status"] == "aligned"


class TestCmdDubFlagPreservation:
    def test_flag_and_flag_note_survive_a_dub_run(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="Test", status="translated")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello", speaker="SPEAKER_00",
                 flag="needs_review", flag_note="awkward phrasing"),
        ])

        calls = []

        def fake_build_dub_track(lines, drama_dir, voice_map, character_clone_map=None,
                                 progress_cb=None, emotion_map=None, **stretch):
            calls.append(lines)
            return "fake_dub.wav", []
        monkeypatch.setattr(dub_module, "build_dub_track", fake_build_dub_track)

        args = _dub_args(id=did)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_dub(args)

        assert len(calls) == 1  # the dub actually ran (a crash inside _run_batch is swallowed)
        rows = isolated_db.load_lines(did)
        assert rows[0]["flag"] == "needs_review"
        assert rows[0]["flag_note"] == "awkward phrasing"


class TestCmdDubVoiceFallback:
    def test_unvoiced_characters_get_different_voices(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="Test", status="translated")
        isolated_db.upsert_character(did, "SPEAKER_00", tts_voice="en-US-AvaNeural")
        isolated_db.save_lines(did, [
            Line(idx=i, start=float(i), end=i + 1.0, zh="你好", en="Hello", speaker=s)
            for i, s in enumerate(["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"])])
        seen = {}

        def fake_build_dub_track(lines, drama_dir, voice_map, offline_voice_map=None, **kw):
            seen.update(voice_map=voice_map, offline_voice_map=offline_voice_map)
            return "fake_dub.wav", []
        monkeypatch.setattr(dub_module, "build_dub_track", fake_build_dub_track)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_dub(_dub_args(id=did))

        voices = seen["voice_map"]
        assert voices["SPEAKER_00"] == "en-US-AvaNeural"
        assert len({voices[s] for s in ("SPEAKER_00", "SPEAKER_01", "SPEAKER_02")}) == 3
        offline = seen["offline_voice_map"]
        assert offline["SPEAKER_01"] != offline["SPEAKER_02"]


class TestCmdDubStretchLimits:
    """Step 11c: the time-stretch clamp is adjustable from the CLI, the
    same as from Workspace section 8 -- and defaults to dub.py's own."""

    def _run(self, isolated_db, monkeypatch, **overrides):
        did = isolated_db.create_drama(title_en="Test", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")])
        seen = {}
        monkeypatch.setattr(dub_module, "build_dub_track",
                            lambda lines, *a, **k: seen.update(k) or ("fake_dub.wav", []))
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_dub(_dub_args(id=did, **overrides))
        return seen

    def test_defaults(self, isolated_db, monkeypatch):
        seen = self._run(isolated_db, monkeypatch)
        assert (seen["max_speedup"], seen["max_slowdown"]) == (dub_module.DUB_MAX_SPEEDUP,
                                                               dub_module.DUB_MAX_SLOWDOWN)

    def test_flags_pass_through(self, isolated_db, monkeypatch):
        seen = self._run(isolated_db, monkeypatch, max_speedup=1.2, max_slowdown=1.0)
        assert (seen["max_speedup"], seen["max_slowdown"]) == (1.2, 1.0)


class TestCmdDubTtsEngineFlag:
    """Step 25d item 10: this command had no --tts-engine flag at all, so
    it could only ever use edge-tts from the command line, regardless of
    what's configured for the drama's characters -- Workspace's own
    "8. AI dub / narration" section always lets you pick edge_tts or
    offline/Piper as the fallback engine."""

    def _run(self, isolated_db, monkeypatch, **overrides):
        did = isolated_db.create_drama(title_en="Test", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")])
        seen = {}
        monkeypatch.setattr(dub_module, "build_dub_track",
                            lambda lines, *a, **k: seen.update(k) or ("fake_dub.wav", []))
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_dub(_dub_args(id=did, **overrides))
        return seen

    def test_defaults_to_edge_tts(self, isolated_db, monkeypatch):
        seen = self._run(isolated_db, monkeypatch)
        assert seen["tts_engine"] == "edge_tts"

    def test_offline_flag_passes_through(self, isolated_db, monkeypatch):
        seen = self._run(isolated_db, monkeypatch, tts_engine="offline")
        assert seen["tts_engine"] == "offline"


class TestCmdDubNarration:
    """Step 11b: cmd_dub routes characters through the same
    dub.clone_map_from_characters the Workspace tab uses, hands over the
    drama's saved emotion tags, and -- like the Workspace tab -- saves the
    new line timing a narration run produces."""

    def _narration_drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Novel", content_mode="novel_narration",
                                        status="translated", source_language="ja")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="一", en="One.", speaker="Hero"),
                                     Line(idx=1, start=1.0, end=2.0, zh="二", en="Two.", speaker="Hero")])
        isolated_db.upsert_character(did, "Hero", character_name="Hero", voice_design="male, low pitch")
        isolated_db.save_emotions(did, {1: {"emotion": "angry", "intensity": 0.9, "note": ""}})
        return did

    def test_saves_timing_and_passes_voices_and_emotions(self, isolated_db, monkeypatch):
        did = self._narration_drama(isolated_db)
        seen = {}

        def fake_build_narration_track(lines, drama_dir, voice_map, default_voice=None,
                                       character_clone_map=None, progress_cb=None, emotion_map=None,
                                       offline_voice_map=None, tts_engine=None,
                                       narrate_original=False, source_language=None):
            seen.update(clone_map=character_clone_map, emotion_map=emotion_map)
            for i, ln in enumerate(lines):
                ln.start, ln.end, ln.dub_filename = 10.0 + i, 10.5 + i, "dub_clips/line_0000-0001.wav"
            return "narration_track.wav", []
        monkeypatch.setattr(dub_module, "build_narration_track", fake_build_narration_track)

        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_dub(_dub_args(id=did))

        assert seen["clone_map"] == {"Hero": {"engine": "omnivoice", "instruct": "male, low pitch"}}
        assert seen["emotion_map"][1]["emotion"] == "angry"
        rows = isolated_db.load_lines(did)
        assert [(r["start"], r["end"]) for r in rows] == [(10.0, 10.5), (11.0, 11.5)]

    def test_m4b_flag_exports_the_audiobook(self, isolated_db, monkeypatch):
        did = self._narration_drama(isolated_db)
        monkeypatch.setattr(dub_module, "build_narration_track",
                            lambda lines, *a, **k: ("narration_track.wav", []))
        exported = []
        monkeypatch.setattr(dub_module, "export_narration_m4b",
                            lambda lines, ddir, title=None, **k: exported.append(title) or "x.m4b")
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_dub(_dub_args(id=did, m4b=True))
        assert exported == ["Novel"]

    def test_original_mode_passes_narrate_original_and_source_language_through(
            self, isolated_db, monkeypatch):
        """Step 26c: same CLI/UI parity the rest of cmd_dub already keeps --
        original-language narration works from the CLI, driven by the
        drama's own saved narration_language, with no separate CLI flag."""
        did = self._narration_drama(isolated_db)
        isolated_db.update_drama(did, narration_language="original")
        seen = {}

        def fake_build_narration_track(lines, drama_dir, voice_map, default_voice=None,
                                       character_clone_map=None, progress_cb=None, emotion_map=None,
                                       offline_voice_map=None, tts_engine=None,
                                       narrate_original=False, source_language=None):
            seen.update(narrate_original=narrate_original, source_language=source_language,
                       default_voice=default_voice)
            return "narration_track.wav", []
        monkeypatch.setattr(dub_module, "build_narration_track", fake_build_narration_track)

        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_dub(_dub_args(id=did))

        assert seen["narrate_original"] is True
        assert seen["source_language"] == "ja"
        assert seen["default_voice"] in dub_module.DEFAULT_VOICE_POOL_BY_LANGUAGE["ja"]

    def test_original_mode_still_narrates_a_drama_with_no_translation_at_all(
            self, isolated_db, monkeypatch):
        """Exit condition: narration works with no ln.en in original mode --
        the CLI's own "not translated yet" skip check would otherwise
        block this entirely, unlike the Workspace tab's dub button."""
        did = isolated_db.create_drama(title_en="Novel", content_mode="novel_narration",
                                        status="aligned", source_language="ja",
                                        narration_language="original")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="一", en="", speaker="Hero")])
        called = []
        monkeypatch.setattr(dub_module, "build_narration_track",
                            lambda lines, *a, **k: called.append(True) or ("narration_track.wav", []))

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_dub(_dub_args(id=did))

        assert called == [True]
        assert "skipped" not in out.getvalue()


class TestCmdRunArgparseParity:
    """cmd_run(args) calls cmd_translate(args) with the same Namespace it
    was given, but p_run never defined --status/--force/--style-preset/
    --locale -- meaning every `cli.py run ...` invocation crashed with
    AttributeError the moment it reached cmd_translate, regardless of
    anything this session touched in cmd_translate itself."""

    def _build_parser(self):
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="command", required=True)
        # Mirror cli.py's main() construction of p_run so this test fails
        # again if the parity fields are ever removed from p_run.
        import translation_guide as tguide
        p_run = sub.add_parser("run")
        p_run.add_argument("--id", type=int, required=True)
        p_run.add_argument("--whisper-size", default="medium")
        p_run.add_argument("--engine", default="claude", choices=list(translate_engines.ENGINES))
        p_run.add_argument("--api-key", required=True)
        p_run.add_argument("--model", default=None)
        p_run.add_argument("--style-note", default=None)
        p_run.add_argument("--status", default=None)
        p_run.add_argument("--style-preset", default="audio_drama", choices=list(tguide.STYLE_PRESETS))
        p_run.add_argument("--locale", default="en-US", choices=["en-US", "en-GB", "en-AU"])
        p_run.add_argument("--force", action="store_true")
        return p


    def test_run_namespace_has_every_attribute_cmd_translate_needs(self):
        p = self._build_parser()
        args = p.parse_args(["run", "--id", "1", "--api-key", "fake-key"])
        # cmd_translate reads all of these off args -- any missing one
        # raises AttributeError before this session's fix.
        for attr in ("status", "force", "style_preset", "locale", "style_note", "model"):
            assert hasattr(args, attr), f"p_run is missing --{attr.replace('_', '-')}"


class TestCliGpuLock:
    """Step 25w: cli.py never imported background_jobs.py at all, so its
    "one GPU job at a time" guard (Step 5c) never covered a CLI run --
    confirmed real: an overnight `cli.py` batch and a GPU-touching job
    started from the live UI could run concurrently, competing for the
    same VRAM. cli.py's own _gpu_lock() is the fix, sharing the gpu_lock
    table in the shared library.db with background_jobs.py's guard."""

    class _OllamaEngine:
        name = "ollama"
        supports_reference = True
        model = "qwen3:8b"

        def __init__(self):
            self.calls = 0
            self.last_usage = {}

        def translate_batch(self, zh_lines, context):
            self.calls += 1
            return [f"EN:{z}" for z in zh_lines]

    class _ClaudeEngine(_OllamaEngine):
        name = "claude"

    def _drama(self, isolated_db, n=2):
        did = isolated_db.create_drama(title_en="Test", status="aligned")
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"句{i}") for i in range(n)])
        return did

    def test_ollama_translate_acquires_and_releases_the_gpu_lock(self, isolated_db, monkeypatch):
        engine = self._OllamaEngine()
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: engine)
        did = self._drama(isolated_db)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(_translate_args(id=did, engine="ollama", cost_cap=None, monthly_cap=None))
        assert engine.calls == 1
        assert isolated_db.gpu_lock_status() == (None, None)  # released when done

    def test_ollama_translate_waits_for_an_externally_held_lock(self, isolated_db, monkeypatch):
        """Simulates the live UI already holding the GPU (as
        background_jobs.py's _gpu_slot_available_locked would record it)
        -- the CLI run must not proceed until it's released."""
        import threading
        import time as time_module

        import types
        _real_sleep = time_module.sleep
        # Stub only cli's `time`, not the global module (the job-heartbeat
        # thread would otherwise loop every 10 ms).
        monkeypatch.setattr(cli, "time", types.SimpleNamespace(
            time=time_module.time, sleep=lambda s: _real_sleep(0.01)))
        engine = self._OllamaEngine()
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: engine)
        did = self._drama(isolated_db)
        assert isolated_db.try_acquire_gpu_lock("ui:live_job", "Transcription") is True

        done = threading.Event()

        def run():
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_translate(_translate_args(id=did, engine="ollama",
                                                  cost_cap=None, monthly_cap=None))
            done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        _real_sleep(0.1)
        assert not done.is_set(), "must not translate while the UI holds the GPU lock"
        assert engine.calls == 0

        isolated_db.release_gpu_lock("ui:live_job")
        assert done.wait(timeout=2.0), "should proceed once the external lock is released"
        t.join(timeout=2.0)
        assert engine.calls == 1
        assert isolated_db.gpu_lock_status() == (None, None)

    def test_non_ollama_translate_never_touches_the_gpu_lock(self, isolated_db, monkeypatch):
        """Every other translate engine is a remote API call, not a
        GPU-touching one -- it must run immediately even while something
        else holds the GPU lock, and must not release someone else's."""
        engine = self._ClaudeEngine()
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: engine)
        did = self._drama(isolated_db)
        isolated_db.try_acquire_gpu_lock("ui:live_job", "Transcription")

        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_translate(_translate_args(id=did, engine="claude", cost_cap=None, monthly_cap=None))

        assert engine.calls == 1  # ran immediately, no waiting
        # Still the other holder's -- untouched by the non-GPU translate run.
        assert isolated_db.gpu_lock_status() == ("ui:live_job", "Transcription")

    def test_dub_only_locks_when_the_clone_map_uses_a_local_model(self, isolated_db, monkeypatch):
        """Same clone_map_uses_local_model check the Workspace tab's own
        Dub job uses to decide gpu_touching -- an edge-tts/cloud-only dub
        run doesn't need to wait on the GPU at all."""
        did = isolated_db.create_drama(title_en="Test", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")])
        monkeypatch.setattr(dub_module, "build_dub_track",
                            lambda lines, *a, **k: ("fake_dub.wav", []))

        monkeypatch.setattr(dub_module, "clone_map_uses_local_model", lambda clone_map: False)
        isolated_db.try_acquire_gpu_lock("ui:live_job", "Transcription")
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_dub(_dub_args(id=did))  # must not block despite the held lock
        assert isolated_db.gpu_lock_status() == ("ui:live_job", "Transcription")  # untouched
        isolated_db.release_gpu_lock("ui:live_job")

        monkeypatch.setattr(dub_module, "clone_map_uses_local_model", lambda clone_map: True)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_dub(_dub_args(id=did))
        assert isolated_db.gpu_lock_status() == (None, None)  # acquired, then released


class TestExportVideoClampsOverlappingCues:
    """Step 25d item 6: Workspace's own export already promises "never
    export an overlapping (invalid) cue" (subtitle_formats.clamp_overlaps)
    -- cli.py export-video used to skip that clamp entirely."""

    def test_overlapping_cues_are_clamped_before_burning(self, isolated_db, monkeypatch):
        import os
        did = isolated_db.create_drama(title_en="Test", status="translated",
                                       source_video_filename="source.mp4")
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "source.mp4"), "wb") as f:
            f.write(b"x")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="a", en="Hello"),
            Line(idx=1, start=1.5, end=3.0, zh="b", en="World"),
        ])

        import video_export
        captured = {}
        monkeypatch.setattr(video_export, "burn_subtitles",
                            lambda video_path, srt_text, out_path: captured.update(srt=srt_text))

        args = argparse.Namespace(id=did, style="hardsub", subs="english")
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_export_video(args)

        assert "00:00:00,000 --> 00:00:01,500" in captured["srt"]  # clamped
        assert "00:00:00,000 --> 00:00:02,000" not in captured["srt"]  # original, overlapping


class TestInspectLine:
    """Step 58 CLI parity: same real data as Workspace's own 🔍 What
    happened? button, headless."""

    def test_reports_real_flag_and_glossary_match_for_the_line(self, isolated_db):
        series_id = isolated_db.get_or_create_series("Test Series")
        did = isolated_db.create_drama(title_en="Test", series_id=series_id)
        isolated_db.upsert_glossary_term(series_id, "沈清疑", "Shen Qingyi")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=1.0, zh="沈清疑来了。", en="Shen Qingyi is here.",
                 flag="uncertain_translation", flag_note="pronoun unclear"),
        ])

        args = argparse.Namespace(id=did, line=1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_inspect_line(args)

        text = out.getvalue()
        assert "Shen Qingyi is here." in text
        assert "Possible mistranslation" in text
        assert "沈清疑 -> Shen Qingyi" in text

    def test_unknown_line_number_says_so_rather_than_crashing(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")])

        args = argparse.Namespace(id=did, line=5)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_inspect_line(args)

        assert "No line #5" in out.getvalue()


class TestCmdDoctor:
    """Step 97: pre-flight an engine's credentials/reachability with a
    real, minimal translate call, before committing a batch job to it."""

    def test_prints_ok_and_exits_cleanly_on_success(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_engine_reachable",
                            lambda *a, **k: {"engine": "test_offline", "ok": True, "error": None})
        args = argparse.Namespace(engine="test_offline", api_key=None, model=None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_doctor(args)
        assert "OK: test_offline is reachable" in out.getvalue()

    def test_prints_the_error_and_exits_nonzero_on_failure(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_engine_reachable",
                            lambda *a, **k: {"engine": "claude", "ok": False,
                                            "error": "invalid x-api-key"})
        args = argparse.Namespace(engine="claude", api_key="bad-key", model=None)
        out = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            with contextlib.redirect_stdout(out):
                cli.cmd_doctor(args)
        assert exc_info.value.code == 1
        assert "FAILED: claude -- invalid x-api-key" in out.getvalue()

    def test_real_argv_wires_doctor_to_cmd_doctor_with_the_right_namespace(self, monkeypatch):
        """Exercises the real parser built in cli.main() -- not a mirrored
        copy -- so this fails if the subparser's own wiring ever drifts,
        the same class of bug Step 67's own TestCmdRunArgparseParity was
        written to catch for `run`."""
        captured = {}
        monkeypatch.setattr(cli, "cmd_doctor", lambda args: captured.setdefault("args", args))
        monkeypatch.setattr(sys, "argv", ["cli.py", "doctor", "--engine", "claude",
                                          "--api-key", "sk-real", "--model", "claude-sonnet-5"])
        cli.main()
        assert captured["args"].engine == "claude"
        assert captured["args"].api_key == "sk-real"
        assert captured["args"].model == "claude-sonnet-5"

    def test_ollama_url_is_optional_and_defaults_to_none(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(cli, "cmd_doctor", lambda args: captured.setdefault("args", args))
        monkeypatch.setattr(sys, "argv", ["cli.py", "doctor", "--engine", "ollama"])
        cli.main()
        assert captured["args"].ollama_url is None


class _SeqEngine:
    """Claude-shaped fake returning canned raw responses, one per call."""
    supports_reference = True
    model = "fake-model"

    def __init__(self, responses):
        self.client = self
        self.messages = self
        self.responses = list(responses)

    def create(self, model, max_tokens, messages):
        text = self.responses.pop(0) if self.responses else "{}"
        block = type("B", (), {"type": "text", "text": text})()
        return type("Resp", (), {"content": [block]})()


class TestNarratePrepIdKeyed:
    """B-10: cmd_narrate_prep must attach speakers by id, not list position."""

    NOVEL = "他说：你好。\n\n夜很深了。\n\n风很大。"

    def _run(self, isolated_db, monkeypatch, engine):
        did = isolated_db.create_drama(title_en="N", content_mode="novel_narration")
        with open(os.path.join(isolated_db.drama_dir(did), dub_module.NOVEL_SOURCE_FILENAME),
                  "w", encoding="utf-8") as f:
            f.write(self.NOVEL)
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: engine)
        monkeypatch.setattr(cli, "chunk_novel_text", lambda text: text.split("\n\n"))
        cli.cmd_narrate_prep(argparse.Namespace(
            id=did, engine="claude", api_key="k", model=None, ollama_url=None))
        return did, [ln.speaker for ln in isolated_db.load_line_objects(did)]

    def test_reordered_and_extra_ids_land_on_the_right_chunks(self, isolated_db, monkeypatch):
        engine = _SeqEngine(['{"2": "Cara", "99": "Ghost", "0": "Ann", "1": "Narrator"}'])
        did, speakers = self._run(isolated_db, monkeypatch, engine)
        assert speakers == ["Ann", "Narrator", "Cara"]
        labels = {c["speaker_label"] for c in isolated_db.list_characters(did)}
        assert "Ghost" not in labels and {"Ann", "Cara"} <= labels

    def test_short_response_retries_missing_id_then_defaults_to_narrator(self, isolated_db, monkeypatch):
        engine = _SeqEngine(['{"0": "Ann"}', '{"2": "Cara"}'])  # id 1 never answered
        _, speakers = self._run(isolated_db, monkeypatch, engine)
        assert speakers == ["Ann", "Narrator", "Cara"]

    def test_partial_and_unknown_keys_only_touch_their_own_chunk(self, isolated_db, monkeypatch):
        monkeypatch.setattr(translate_engines, "tag_speakers_by_id",
                            lambda chunks, engine, known, **k: {2: "Cara", 42: "Ghost"})
        _, speakers = self._run(isolated_db, monkeypatch, _SeqEngine([]))
        assert speakers == ["Narrator", "Narrator", "Cara"]


class TestCliServiceParity:
    """Parity-audit fixes: cli align/narrate-prep use the service safeguards,
    translate defaults to the drama's saved engine and the service's
    per-drama context/batch defaults, and align reads the saved tuning."""

    def _align_drama(self, isolated_db, **kw):
        did = isolated_db.create_drama(title_en="A", status="aligned",
                                       audio_filename="audio.wav", **kw)
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(ddir, "transcript.txt"), "w", encoding="utf-8") as f:
            f.write("你好")
        return did

    def _align_args(self, did):
        return argparse.Namespace(id=did, whisper_size=None, fast=False)

    def test_align_snapshots_existing_lines_before_replacing(self, isolated_db, monkeypatch):
        did = self._align_drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="旧")])
        monkeypatch.setattr(cli, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "你好"}])
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_align(self._align_args(did))
        assert [h["label"] for h in isolated_db.list_line_history(did)] == ["before re-transcribe"]
        assert isolated_db.load_lines(did)[0]["zh"] == "你好"

    def test_align_empty_result_keeps_existing_lines(self, isolated_db, monkeypatch):
        did = self._align_drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="旧")])
        monkeypatch.setattr(cli, "transcribe_for_timing", lambda *a, **k: [])
        monkeypatch.setattr(cli, "align_transcript_to_timing", lambda *a, **k: [])
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_align(self._align_args(did))
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["旧"]
        assert isolated_db.list_line_history(did) == []

    def test_align_uses_the_dramas_saved_tuning(self, isolated_db, monkeypatch):
        did = self._align_drama(isolated_db)
        isolated_db.update_drama(did, beam_size=3, min_silence_ms=800, vad_threshold=0.3,
                                 whisper_fast_mode=1)
        seen = {}

        def fake(audio_path, model_size, **kw):
            seen.update(kw)
            return [{"start": 0.0, "end": 1.0, "text": "你好"}]
        monkeypatch.setattr(cli, "transcribe_for_timing", fake)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_align(self._align_args(did))
        assert (seen["beam_size"], seen["min_silence_duration_ms"], seen["vad_threshold"],
                seen["fast_mode"]) == (3, 800, 0.3, True)
        assert "use_gpu" in seen

    def test_narrate_prep_snapshots_existing_lines(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="N", content_mode="novel_narration")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="旧")])
        with open(os.path.join(isolated_db.drama_dir(did), "novel_narration_source.txt"),
                  "w", encoding="utf-8") as f:
            f.write("一。\n\n二。")
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
        monkeypatch.setattr(translate_engines, "tag_speakers_by_id", lambda *a, **k: {})
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_narrate_prep(argparse.Namespace(
                id=did, engine=None, api_key="k", model=None, ollama_url=None))
        assert [h["label"] for h in isolated_db.list_line_history(did)] == [
            "before chunk & tag speakers"]

    def test_narrate_prep_empty_result_changes_nothing(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="N", content_mode="novel_narration")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="旧")])
        with open(os.path.join(isolated_db.drama_dir(did), "novel_narration_source.txt"),
                  "w", encoding="utf-8") as f:
            f.write("")
        monkeypatch.setattr(cli, "chunk_novel_text", lambda text: [])
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
        monkeypatch.setattr(translate_engines, "tag_speakers_by_id", lambda *a, **k: {})
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_narrate_prep(argparse.Namespace(
                id=did, engine=None, api_key="k", model=None, ollama_url=None))
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["旧"]
        assert isolated_db.list_characters(did) == []
        assert isolated_db.list_line_history(did) == []

    def test_replacing_lines_flags_cross_process_line_jobs_for_cancel(self, isolated_db):
        did = isolated_db.create_drama(title_en="J")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="旧")])
        isolated_db.save_job_record(f"translate_{did}", "running")
        with contextlib.redirect_stdout(io.StringIO()):
            cli._replace_drama_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="新")], "x")
        assert isolated_db.is_job_record_cancel_requested(f"translate_{did}")

    def _translate(self, isolated_db, monkeypatch, drama_kw, **arg_overrides):
        did = isolated_db.create_drama(title_en="T", status="aligned", **drama_kw)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好")])
        engines, seen = [], {}
        # (name, api_key, model) for every translate engine built; the
        # summary engine (ollama, default) is filtered out.
        monkeypatch.setattr(
            translate_engines, "get_engine",
            lambda name, key=None, model=None, **k: engines.append((name, key, model)) or object())
        from services import translate_service
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, *a: f"saved-{name}")

        def fake_translate(lines, engine, **kwargs):
            seen.update(kwargs)
            return lines, []
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)
        overrides = dict(engine=None, style_preset=None)
        overrides.update(arg_overrides)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli.cmd_translate(_translate_args(id=did, **overrides))
        self.out = out.getvalue()
        return [e for e in engines if e[0] != "ollama"], seen

    def test_translate_uses_the_dramas_saved_engine_and_its_own_key(self, isolated_db, monkeypatch):
        engines, seen = self._translate(isolated_db, monkeypatch, {"translation_engine": "deepseek"},
                                        api_key=None, model="claude-x")
        assert engines == [("deepseek", "saved-deepseek", None)]
        assert seen  # it actually translated

    def test_a_bare_api_key_is_never_sent_to_a_different_saved_engine(self, isolated_db, monkeypatch):
        engines, seen = self._translate(isolated_db, monkeypatch, {"translation_engine": "deepseek"},
                                        api_key="sk-ant-secret", model="claude-x")
        assert engines == [] and not seen
        assert "saved engine deepseek" in self.out and "sk-ant" not in self.out

    def test_a_bare_api_key_still_works_for_claude_dramas(self, isolated_db, monkeypatch):
        engines, _ = self._translate(isolated_db, monkeypatch, {}, api_key="sk-ant", model="m")
        assert engines == [("claude", "sk-ant", "m")]

    def test_translate_without_api_key_uses_the_saved_claude_key(self, isolated_db, monkeypatch):
        engines, seen = self._translate(isolated_db, monkeypatch, {}, api_key=None, model=None)
        assert engines == [("claude", "saved-claude", None)]
        assert seen

    def test_monthly_cap_is_dropped_for_gemini_free_tier(self, isolated_db, monkeypatch):
        # A used-up monthly cap would refuse a paid run; Gemini's free tier
        # isn't billed, so --monthly-cap doesn't apply and the run goes ahead.
        monkeypatch.setattr(cli.settings_service, "get_gemini_free_tier", lambda: True)
        isolated_db.log_usage(None, "claude", "m", "translate", 1, 1, 50.0)
        engines, seen = self._translate(isolated_db, monkeypatch, {}, engine="gemini",
                                        api_key=None, model=None, monthly_cap=20.0)
        assert engines == [("gemini", "saved-gemini", None)]
        assert seen and seen.get("cost_cap_usd") is None

    def test_translate_explicit_engine_flag_still_wins(self, isolated_db, monkeypatch):
        engines, _ = self._translate(isolated_db, monkeypatch, {"translation_engine": "deepseek"},
                                     engine="gemini", api_key="g-key", model="gm")
        assert engines == [("gemini", "g-key", "gm")]

    def test_translate_novel_drama_gets_10_6_30(self, isolated_db, monkeypatch):
        _, seen = self._translate(isolated_db, monkeypatch, {"content_mode": "novel_narration"})
        assert (seen["context_window"], seen["context_window_ahead"], seen["batch_size"]) == (10, 6, 30)

    def test_translate_non_novel_drama_keeps_6_3_20(self, isolated_db, monkeypatch):
        _, seen = self._translate(isolated_db, monkeypatch, {})
        assert (seen["context_window"], seen["context_window_ahead"], seen["batch_size"]) == (6, 3, 20)

    def test_run_parser_leaves_engine_and_sizes_unset(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(sys, "argv", ["cli.py", "run", "--id", "1", "--api-key", "k"])
        monkeypatch.setattr(cli, "cmd_run", lambda a: captured.update(vars(a)))
        cli.main()
        assert captured["engine"] is None and captured["batch_size"] is None
        assert captured["whisper_size"] is None


class TestCliSavedSettingsFallbacks:
    """Parity audit (B2): the CLI falls back to saved Settings the way the
    services do -- keys, Ollama/GPT-SoVITS URLs, the monthly cap -- and
    narrate-prep errors instead of silently tagging everyone "Narrator"."""

    def _narration_drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="N", content_mode="novel_narration")
        with open(os.path.join(isolated_db.drama_dir(did), "novel_narration_source.txt"),
                  "w", encoding="utf-8") as f:
            f.write("一。")
        return did

    @pytest.mark.parametrize("command", ["translate", "run"])
    def test_api_key_is_optional(self, command):
        import sys as _sys
        seen = {}
        orig = _sys.argv
        try:
            _sys.argv = ["cli.py", command, "--id", "1"]
            import unittest.mock as mock
            with mock.patch.object(cli, "cmd_translate", lambda a: seen.update(ns=a)), \
                    mock.patch.object(cli, "cmd_run", lambda a: seen.update(ns=a)):
                cli.main()
            parser_args = seen["ns"]
        finally:
            _sys.argv = orig
        assert parser_args.api_key is None
        assert parser_args.monthly_cap is None

    def test_narrate_prep_without_any_key_exits_nonzero(self, isolated_db, monkeypatch):
        did = self._narration_drama(isolated_db)
        monkeypatch.setattr(cli.settings_service, "resolve_key", lambda *a, **k: None)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), pytest.raises(SystemExit) as exc:
            cli.cmd_narrate_prep(argparse.Namespace(
                id=did, engine="claude", api_key=None, model=None, ollama_url=None))
        assert exc.value.code != 0
        assert "No claude key is configured" in err.getvalue()
        assert isolated_db.load_lines(did) == []

    def test_narrate_prep_rejects_non_tag_engine(self, isolated_db):
        did = self._narration_drama(isolated_db)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), pytest.raises(SystemExit):
            cli.cmd_narrate_prep(argparse.Namespace(
                id=did, engine="deepl", api_key="k", model=None, ollama_url=None))
        assert "cannot tag speakers" in err.getvalue()

    def test_narrate_prep_uses_saved_key_ollama_url_free_tier_and_logs_usage(
            self, isolated_db, monkeypatch):
        did = self._narration_drama(isolated_db)
        saved = {"gemini": "saved-gemini", "ollama_url": "http://saved:11434"}
        monkeypatch.setattr(cli.settings_service, "resolve_key", lambda k, *a: saved.get(k))
        monkeypatch.setattr(cli.settings_service, "get_gemini_free_tier", lambda: True)
        built = []

        class _E:
            model = "m"
        monkeypatch.setattr(translate_engines, "get_engine",
                            lambda name, key=None, model=None, **k: built.append((name, key, k)) or _E())

        def fake_tag(id_to_zh, engine, known, usage_cb=None, **k):
            usage_cb(10, 5)
            return {i: "Lin" for i in id_to_zh}
        monkeypatch.setattr(translate_engines, "tag_speakers_by_id", fake_tag)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_narrate_prep(argparse.Namespace(
                id=did, engine="gemini", api_key=None, model=None, ollama_url=None))
        assert built == [("gemini", "saved-gemini", {"free_tier": True, "base_url": None})]
        assert [r["speaker"] for r in isolated_db.load_lines(did)] == ["Lin"]
        assert isolated_db.get_usage_summary(did)["call_count"] == 1

        built.clear()
        did2 = self._narration_drama(isolated_db)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_narrate_prep(argparse.Namespace(
                id=did2, engine="ollama", api_key=None, model=None, ollama_url=None))
        assert built[0][2]["base_url"] == "http://saved:11434"

    def test_ollama_url_flag_wins_over_saved(self, monkeypatch):
        monkeypatch.setattr(cli.settings_service, "resolve_key", lambda k, *a: "http://saved")
        assert cli._ollama_url(argparse.Namespace(ollama_url="http://flag")) == "http://flag"
        assert cli._ollama_url(argparse.Namespace(ollama_url=None)) == "http://saved"
        assert cli._ollama_url(argparse.Namespace()) == "http://saved"

    def test_monthly_cap_setting_parsing(self, monkeypatch):
        for raw, want in (("20", 20.0), ("0", None), ("", None), ("junk", None), (None, None)):
            monkeypatch.setattr(cli.settings_service, "resolve_key", lambda k, *a, r=raw: r)
            assert cli._monthly_cap_setting() == want

    def _cap_run(self, isolated_db, monkeypatch, engine_name):
        engine = TestCmdTranslateSpendingCaps._Engine()
        engine.name = engine_name
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: engine)
        monkeypatch.setattr(cli.settings_service, "resolve_key",
                            lambda k, *a: "20" if k == "monthly_cap_usd" else None)
        did = isolated_db.create_drama(title_en="T", status="aligned")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你")])
        isolated_db.log_usage(did, "claude", "m", "translate", 1, 1, 50.0)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                cli.cmd_translate(_translate_args(id=did, engine=engine_name,
                                                  cost_cap=None, monthly_cap=None))
            except SystemExit:
                pass
        return engine

    def test_saved_monthly_cap_applies_to_paid_engine(self, isolated_db, monkeypatch):
        assert self._cap_run(isolated_db, monkeypatch, "claude").calls == 0

    def test_monthly_cap_skips_non_cap_engine(self, isolated_db, monkeypatch):
        assert self._cap_run(isolated_db, monkeypatch, "test_offline").calls == 1

    def test_dub_falls_back_to_saved_gpt_sovits_url(self, isolated_db, monkeypatch):
        monkeypatch.setattr(cli.settings_service, "resolve_key",
                            lambda k, *a: "http://sovits" if k == "gpt_sovits_url" else None)
        seen = {}

        def fake_clone_map(chars, ddir, gpt_sovits_url=None, **k):
            seen["url"] = gpt_sovits_url
            raise RuntimeError("stop here")
        monkeypatch.setattr(dub_module, "clone_map_from_characters", fake_clone_map)
        did = isolated_db.create_drama(title_en="D", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你", en="hi")])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.cmd_dub(_dub_args(id=did))
        assert seen.get("url") == "http://sovits"
