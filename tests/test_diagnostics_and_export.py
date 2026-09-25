"""
tests/test_diagnostics_and_export.py -- tests for diagnostics.py,
export_package.py, and db.py's line history (undo) functions.
"""

import sys
import os
import json
import zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import diagnostics
import export_package
from core import Line, lines_to_srt, lines_to_bilingual_srt

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestDiagnostics:
    def test_python_version_check_returns_version_string(self):
        result = diagnostics.check_python_version()
        assert "version" in result
        assert isinstance(result["ok"], bool)

    def test_ffmpeg_check_returns_expected_shape(self):
        result = diagnostics.check_ffmpeg()
        assert "found" in result
        assert isinstance(result["found"], bool)

    def test_js_runtime_check_returns_expected_shape(self):
        result = diagnostics.check_js_runtime()
        assert "found" in result
        assert isinstance(result["found"], bool)

    def test_js_runtime_finds_deno_on_path(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which",
                             lambda name: "/usr/bin/deno" if name == "deno" else None)
        assert diagnostics.check_js_runtime() == {
            "found": True, "name": "deno", "path": "/usr/bin/deno"}

    def test_js_runtime_falls_through_to_a_later_candidate(self, monkeypatch):
        """Deno is checked first (yt-dlp's own default), but this app
        works with any of the supported runtimes -- confirms the check
        doesn't stop looking after the first miss."""
        monkeypatch.setattr(diagnostics.shutil, "which",
                             lambda name: "/usr/bin/node" if name == "node" else None)
        assert diagnostics.check_js_runtime() == {
            "found": True, "name": "node", "path": "/usr/bin/node"}

    def test_js_runtime_reports_missing_when_none_found(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
        assert diagnostics.check_js_runtime() == {"found": False, "name": None, "path": None}

    def test_check_dependency_true_for_stdlib_backed_package(self):
        # json is always importable
        assert diagnostics.check_dependency("json") is True

    def test_check_dependency_false_for_nonexistent(self):
        assert diagnostics.check_dependency("definitely_not_a_real_module_xyz123") is False

    def test_check_all_dependencies_covers_every_listed_package(self):
        results = diagnostics.check_all_dependencies()
        assert len(results) == len(diagnostics.OPTIONAL_DEPENDENCIES)
        for name, info in results.items():
            assert "installed" in info
            assert "powers" in info
            assert info["tier"] in ("required", "engine", "feature", "dev")

    def test_step_6_optional_dependencies_are_registered(self):
        """CLAUDE.md: every optional dependency must be listed here, or
        Diagnostics never reports it as missing."""
        deps = diagnostics.OPTIONAL_DEPENDENCIES
        assert deps["audio-separator"][0] == "audio_separator"
        assert deps["funasr"][0] == "funasr"
        assert deps["demucs"][0] == "demucs"
        assert deps["audio-separator"][2] == deps["funasr"][2] == deps["demucs"][2] == "feature"

    def test_file_completeness_detects_all_present_in_real_project(self):
        result = diagnostics.check_file_completeness(PROJECT_ROOT)
        assert result["all_present"] is True, \
            f"missing: {result['missing_top_level']} {result['missing_tabs']}"

    def test_file_completeness_reports_missing_in_empty_dir(self, tmp_path_str):
        result = diagnostics.check_file_completeness(tmp_path_str)
        assert result["all_present"] is False
        assert len(result["missing_top_level"]) > 0
        assert len(result["missing_tabs"]) > 0

    def test_library_writable_true_for_temp_dir(self, tmp_path_str):
        assert diagnostics.check_library_writable(os.path.join(tmp_path_str, "lib")) is True

    def test_run_full_diagnostics_returns_all_sections(self, tmp_path_str):
        result = diagnostics.run_full_diagnostics(
            PROJECT_ROOT, os.path.join(tmp_path_str, "lib"), {"claude": True})
        for section in ("python", "ffmpeg", "js_runtime", "dependencies", "files",
                        "library_writable", "api_keys"):
            assert section in result


class TestLineHistory:
    def test_snapshot_and_restore_roundtrip(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        original = [Line(idx=0, start=0, end=1, zh="原文", en="original", speaker="A")]
        isolated_db.save_lines(did, original)
        isolated_db.save_line_history_snapshot(did, original, "before change")

        # overwrite
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="原文", en="changed")])

        history = isolated_db.list_line_history(did)
        assert len(history) == 1
        snapshot = isolated_db.get_line_history_snapshot(history[0]["id"])
        assert snapshot[0]["en"] == "original"

    def test_snapshot_preserves_speaker_and_dub_filename(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        ln = Line(idx=0, start=0, end=1, zh="a", en="b", speaker="SPEAKER_00")
        ln.dub_filename = "clip.wav"
        isolated_db.save_line_history_snapshot(did, [ln], "test")
        history = isolated_db.list_line_history(did)
        snapshot = isolated_db.get_line_history_snapshot(history[0]["id"])
        assert snapshot[0]["speaker"] == "SPEAKER_00"
        assert snapshot[0]["dub_filename"] == "clip.wav"

    def test_pruning_keeps_only_most_recent(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        for i in range(15):
            isolated_db.save_line_history_snapshot(did, lines, f"snapshot {i}", keep_last=5)
        history = isolated_db.list_line_history(did)
        assert len(history) == 5

    def test_history_ordered_newest_first(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        isolated_db.save_line_history_snapshot(did, lines, "first")
        isolated_db.save_line_history_snapshot(did, lines, "second")
        history = isolated_db.list_line_history(did)
        assert history[0]["label"] == "second"

    def test_nonexistent_snapshot_returns_none(self, isolated_db):
        assert isolated_db.get_line_history_snapshot(99999) is None

    def test_history_deleted_with_drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_line_history_snapshot(
            did, [Line(idx=0, start=0, end=1, zh="a", en="b")], "test")
        isolated_db.delete_drama(did)
        assert isolated_db.list_line_history(did) == []


class TestExportPackage:
    def test_full_package_contains_all_components(self, isolated_db, tmp_path_str):
        did = isolated_db.create_drama(title_en="Export Test", author="Author")
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "source.mp3"), "wb") as f:
            f.write(b"audio")
        isolated_db.update_drama(did, audio_filename="source.mp3")
        with open(os.path.join(ddir, "dub_track.wav"), "wb") as f:
            f.write(b"dub")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1.5, zh="你好", en="Hello")])
        isolated_db.upsert_character(did, "A", character_name="Character A")

        out_zip = os.path.join(tmp_path_str, "pkg.zip")
        path, manifest = export_package.build_drama_export_package(
            isolated_db, did, out_zip, lines_to_srt, lines_to_bilingual_srt, Line)

        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            assert "metadata.json" in names
            assert "subtitles/english.srt" in names
            assert "subtitles/chinese.srt" in names
            assert "subtitles/bilingual.srt" in names
            assert "source/source.mp3" in names
            assert "audio/dub_track.wav" in names

    def test_metadata_includes_drama_and_characters(self, isolated_db, tmp_path_str):
        did = isolated_db.create_drama(title_en="Meta Test")
        isolated_db.upsert_character(did, "A", character_name="Someone")
        out_zip = os.path.join(tmp_path_str, "pkg.zip")
        export_package.build_drama_export_package(
            isolated_db, did, out_zip, lines_to_srt, lines_to_bilingual_srt, Line)
        with zipfile.ZipFile(out_zip) as zf:
            meta = json.loads(zf.read("metadata.json"))
            assert meta["drama"]["title_en"] == "Meta Test"
            assert meta["characters"][0]["character_name"] == "Someone"

    def test_incomplete_drama_still_produces_valid_zip(self, isolated_db, tmp_path_str):
        did = isolated_db.create_drama(title_en="Bare")
        out_zip = os.path.join(tmp_path_str, "bare.zip")
        path, manifest = export_package.build_drama_export_package(
            isolated_db, did, out_zip, lines_to_srt, lines_to_bilingual_srt, Line)
        assert os.path.exists(path)
        # should explain the gap rather than silently omitting it
        assert any("no subtitles" in m for m in manifest)

    def test_nonexistent_drama_raises_value_error(self, isolated_db, tmp_path_str):
        with pytest.raises(ValueError):
            export_package.build_drama_export_package(
                isolated_db, 99999, os.path.join(tmp_path_str, "x.zip"),
                lines_to_srt, lines_to_bilingual_srt, Line)

    def test_manifest_matches_zip_contents(self, isolated_db, tmp_path_str):
        did = isolated_db.create_drama(title_en="Manifest Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="b")])
        out_zip = os.path.join(tmp_path_str, "pkg.zip")
        _, manifest = export_package.build_drama_export_package(
            isolated_db, did, out_zip, lines_to_srt, lines_to_bilingual_srt, Line)
        with zipfile.ZipFile(out_zip) as zf:
            names = set(zf.namelist())
        # every real file in the manifest should exist in the zip
        # (explanatory entries in parentheses aren't files)
        for m in manifest:
            if not m.startswith("("):
                assert m in names


class TestDescribeJob:
    """tabs.diagnostics_tab._describe_job() -- turns a raw job_id like
    'emotion_42' into a human-readable line for the Running jobs panel,
    since a bare internal id string means nothing to look at."""

    def test_live_capture_has_a_fixed_label(self):
        from tabs.diagnostics_tab import _describe_job
        assert _describe_job("live_capture") == "🔴 Live capture"

    def test_known_prefix_includes_the_drama_title(self, isolated_db):
        from tabs.diagnostics_tab import _describe_job
        did = isolated_db.create_drama(title_en="Test Drama")
        result = _describe_job(f"emotion_{did}")
        assert "Detecting emotional register" in result
        assert "Test Drama" in result

    def test_deleted_drama_says_so_instead_of_crashing(self, isolated_db):
        from tabs.diagnostics_tab import _describe_job
        result = _describe_job("flag_999999")
        assert "deleted" in result

    def test_unrecognized_job_id_falls_back_to_the_raw_string(self):
        from tabs.diagnostics_tab import _describe_job
        assert _describe_job("some_custom_thing") == "some_custom_thing"
