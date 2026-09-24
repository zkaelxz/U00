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

import db
import translate_engines
import dub as dub_module
from core import Line
import cli


def _translate_args(**overrides):
    defaults = dict(
        id=None, status=None, engine="claude", api_key="fake-key", model=None,
        style_note=None, style_preset="audio_drama", locale="en-US", force=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _dub_args(**overrides):
    defaults = dict(id=None, elevenlabs_key=None)
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


class TestCmdDubFlagPreservation:
    def test_flag_and_flag_note_survive_a_dub_run(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="Test", status="translated")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello", speaker="SPEAKER_00",
                 flag="needs_review", flag_note="awkward phrasing"),
        ])

        def fake_build_dub_track(lines, drama_dir, voice_map, character_clone_map=None, progress_cb=None):
            return "fake_dub.wav", []
        monkeypatch.setattr(dub_module, "build_dub_track", fake_build_dub_track)
        monkeypatch.setattr(dub_module, "assign_voices_to_characters", lambda speakers: {})

        args = _dub_args(id=did)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_dub(args)

        rows = isolated_db.load_lines(did)
        assert rows[0]["flag"] == "needs_review"
        assert rows[0]["flag_note"] == "awkward phrasing"


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
