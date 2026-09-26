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

import pytest
import db
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
        from tabs.workspace_tab import run_translate_job

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
        monkeypatch.setattr(dub_module, "assign_voices_to_characters", lambda speakers: {})

        args = _dub_args(id=did)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_dub(args)

        assert len(calls) == 1  # the dub actually ran (a crash inside _run_batch is swallowed)
        rows = isolated_db.load_lines(did)
        assert rows[0]["flag"] == "needs_review"
        assert rows[0]["flag_note"] == "awkward phrasing"


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

        def fake_build_narration_track(lines, drama_dir, voice_map, character_clone_map=None,
                                       progress_cb=None, emotion_map=None, offline_voice_map=None):
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
                            lambda lines, ddir, title=None: exported.append(title) or "x.m4b")
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_dub(_dub_args(id=did, m4b=True))
        assert exported == ["Novel"]


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
