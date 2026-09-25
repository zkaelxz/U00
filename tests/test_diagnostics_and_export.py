"""
tests/test_diagnostics_and_export.py -- tests for diagnostics.py,
export_package.py, and db.py's line history (undo) functions.
"""

import sys
import os
import json
import types
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


# ---------------------------------------------------------------------------
# Step 9b.2: HF model-cache panel, model/engine version panel, pyannote
# gated-access check, "copy diagnostics for support" redaction.
# ---------------------------------------------------------------------------

def _make_fake_hf_repo(cache_dir, repo_type_prefix, org, name, commit_hash="a" * 40,
                       files=None):
    """Builds a real, minimal-but-valid Hugging Face cache repo directory
    by hand (blobs/refs/snapshots), so scan_cache_dir()/delete_revisions()
    -- real huggingface_hub code, not mocked -- can parse and delete it,
    without touching the real ~/.cache/huggingface."""
    files = files or {"config.json": b"{}" * 200}
    repo_dir = os.path.join(cache_dir, f"{repo_type_prefix}--{org}--{name}")
    blobs_dir = os.path.join(repo_dir, "blobs")
    snapshot_dir = os.path.join(repo_dir, "snapshots", commit_hash)
    refs_dir = os.path.join(repo_dir, "refs")
    os.makedirs(blobs_dir, exist_ok=True)
    os.makedirs(snapshot_dir, exist_ok=True)
    os.makedirs(refs_dir, exist_ok=True)
    for i, (filename, content) in enumerate(files.items()):
        blob_hash = f"{'0' * 63}{i}"
        blob_path = os.path.join(blobs_dir, blob_hash)
        with open(blob_path, "wb") as f:
            f.write(content)
        os.symlink(blob_path, os.path.join(snapshot_dir, filename))
    with open(os.path.join(refs_dir, "main"), "w") as f:
        f.write(commit_hash)
    return repo_dir


class TestHfCacheScanAndDelete:
    def test_lists_entries_with_real_sizes(self, tmp_path_str):
        # huggingface_hub is in requirements.txt/requirements-optional.txt,
        # not requirements-core.txt -- CI's own core-only install (see
        # .github/workflows/tests.yml's comment) never has it, so this
        # (and the two tests below that also call scan_cache_dir() against
        # a real-shaped fake repo) must skip cleanly there instead of
        # failing hard, the same convention every other optional-dependency
        # test in this suite already follows (pytesseract, cv2, jieba).
        # scan_hf_cache() itself is unaffected either way -- confirmed
        # directly against a real download, not just this fixture -- this
        # was a missing-skip-guard gap in the test, not a bug in the
        # feature it's testing.
        pytest.importorskip("huggingface_hub")
        _make_fake_hf_repo(tmp_path_str, "models", "Systran", "faster-whisper-large-v3",
                           files={"model.bin": b"x" * 5000})
        entries = diagnostics.scan_hf_cache(tmp_path_str)
        assert len(entries) == 1
        assert entries[0]["repo_id"] == "Systran/faster-whisper-large-v3"
        assert entries[0]["repo_type"] == "model"
        assert entries[0]["size_bytes"] >= 5000

    def test_lists_multiple_repos_largest_first(self, tmp_path_str):
        pytest.importorskip("huggingface_hub")
        _make_fake_hf_repo(tmp_path_str, "models", "org", "small", commit_hash="a" * 40,
                           files={"f.bin": b"x" * 100})
        _make_fake_hf_repo(tmp_path_str, "models", "org", "big", commit_hash="b" * 40,
                           files={"f.bin": b"x" * 100000})
        entries = diagnostics.scan_hf_cache(tmp_path_str)
        assert [e["repo_id"] for e in entries] == ["org/big", "org/small"]

    def test_deleting_a_revision_actually_frees_the_space(self, tmp_path_str):
        pytest.importorskip("huggingface_hub")
        _make_fake_hf_repo(tmp_path_str, "models", "org", "model-a", commit_hash="c" * 40,
                           files={"f.bin": b"x" * 9000})
        before = diagnostics.scan_hf_cache(tmp_path_str)
        assert len(before) == 1
        ok = diagnostics.delete_hf_cache_revision(before[0]["revision"], tmp_path_str)
        assert ok is True
        after = diagnostics.scan_hf_cache(tmp_path_str)
        assert after == []

    def test_empty_or_missing_cache_dir_is_not_an_error(self, tmp_path_str):
        missing = os.path.join(tmp_path_str, "does_not_exist")
        assert diagnostics.scan_hf_cache(missing) == []

    def test_delete_of_an_unknown_revision_fails_cleanly(self, tmp_path_str):
        _make_fake_hf_repo(tmp_path_str, "models", "org", "model-a")
        assert diagnostics.delete_hf_cache_revision("f" * 40, tmp_path_str) is False

    def test_huggingface_hub_not_installed_returns_empty_not_raised(self, monkeypatch):
        # sys.modules[name] = None is the standard, thread-safe way to
        # simulate "not installed" -- Python's import system raises
        # ImportError for exactly that name and nothing else. Patching
        # builtins.__import__ globally would risk breaking an unrelated
        # module's import happening concurrently in another thread (this
        # app runs background_jobs threads throughout its own test suite).
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)
        assert diagnostics.scan_hf_cache() == []
        assert diagnostics.delete_hf_cache_revision("x") is False


class TestModelEngineVersions:
    def test_lists_every_registered_backend_with_a_version_or_repo_id(self):
        versions = diagnostics.get_model_engine_versions()
        names = {v["name"] for v in versions}
        assert "Whisper (faster-whisper)" in names
        assert "pyannote diarization model" in names
        for v in versions:
            assert v["version"]  # never blank -- "not installed" at worst
            assert v["url"].startswith("http")

    def test_repo_entry_shows_the_actual_diarization_model_ids(self):
        import diarize
        versions = diagnostics.get_model_engine_versions()
        row = next(v for v in versions if v["name"] == "pyannote diarization model")
        for model in diarize.DIARIZATION_MODELS:
            assert model in row["version"]

    def test_an_installed_package_shows_a_real_version_string(self):
        versions = diagnostics.get_model_engine_versions()
        row = next(v for v in versions if v["name"] == "pyannote.audio")
        # anthropic/requests/etc. are installed in this sandbox's test
        # env, but pyannote.audio may or may not be -- just check the
        # function distinguishes "not installed" from an actual version
        # rather than crashing either way.
        assert row["version"] == "not installed" or row["version"][0].isdigit()

    def test_ollama_tag_appended_only_when_given(self):
        assert not any(v["name"].startswith("Ollama") for v in diagnostics.get_model_engine_versions())
        versions = diagnostics.get_model_engine_versions("qwen3:8b")
        row = next(v for v in versions if v["name"].startswith("Ollama"))
        assert row["version"] == "qwen3:8b"

    def test_makes_no_network_call(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("should not touch the network")
        monkeypatch.setattr("requests.get", boom)
        monkeypatch.setattr("requests.post", boom)
        diagnostics.get_model_engine_versions("qwen3:8b")  # must not raise


class TestPyannoteGatedAccessCheck:
    class _FakeApi:
        def __init__(self, gated=()):
            self.gated = set(gated)
            self.calls = []

        def model_info(self, model, token=None):
            self.calls.append((model, token))
            if model in self.gated:
                raise RuntimeError(f"Access to model {model} is restricted. You must be "
                                   "authenticated to access it.")

    def test_accessible_and_gated_models_both_reported(self):
        import diarize
        gated_model = diarize.DIARIZATION_MODELS[1]
        api = self._FakeApi(gated=(gated_model,))
        results = diagnostics.check_pyannote_gated_access(api=api)
        assert len(results) == len(diarize.DIARIZATION_MODELS)
        by_model = {r["model"]: r for r in results}
        assert by_model[diarize.DIARIZATION_MODELS[0]]["accessible"] is True
        assert by_model[gated_model]["accessible"] is False
        assert "restricted" in by_model[gated_model]["error"]

    def test_hf_token_is_passed_through(self):
        api = self._FakeApi()
        diagnostics.check_pyannote_gated_access(hf_token="hf_faketoken", api=api)
        assert all(token == "hf_faketoken" for _, token in api.calls)

    def test_huggingface_hub_not_installed_returns_empty(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)
        assert diagnostics.check_pyannote_gated_access() == []


class TestRedactForSupport:
    def test_strips_api_keys(self):
        text = diagnostics.redact_for_support("key=sk-ant-api03-" + "X" * 40)
        assert "sk-ant-api03" not in text

    def test_strips_the_os_username(self, monkeypatch):
        monkeypatch.setattr("getpass.getuser", lambda: "bobsmith")
        text = diagnostics.redact_for_support("C:\\Users\\bobsmith\\project\\library\\foo.txt")
        assert "bobsmith" not in text

    def test_collapses_posix_paths_to_the_last_segment(self):
        text = diagnostics.redact_for_support("saved to /home/bob/U00/library/drama_3/audio.wav")
        assert "/home/bob" not in text
        assert "audio.wav" in text

    def test_collapses_windows_paths_to_the_last_segment(self):
        text = diagnostics.redact_for_support(r"saved to C:\Users\bob\U00\library\drama_3\audio.wav")
        assert "bob" not in text
        assert "audio.wav" in text

    def test_empty_text_is_safe(self):
        assert diagnostics.redact_for_support("") == ""
        assert diagnostics.redact_for_support(None) == ""


class TestFormatDiagnosticsReport:
    def _results(self, **overrides):
        base = {
            "python": {"version": "3.11.0", "ok": True},
            "ffmpeg": {"found": True, "version": "ffmpeg version 6.0"},
            "js_runtime": {"found": True, "name": "deno"},
            "library_writable": True,
            "api_keys": {"claude": True, "gemini": False},
            "dependencies": {"anthropic": {"installed": True, "tier": "engine"},
                             "torch": {"installed": False, "tier": "feature"}},
            "files": {"all_present": True, "missing_top_level": [], "missing_tabs": []},
        }
        base.update(overrides)
        return base

    def test_report_mentions_missing_dependency_and_key_state(self):
        report = diagnostics.format_diagnostics_report(self._results())
        assert "torch" in report
        assert "claude" in report and "gemini" not in report.split("API keys set:")[1].split("\n")[0]

    def test_includes_hf_cache_and_model_versions_when_given(self):
        hf_cache = [{"repo_id": "org/model", "repo_type": "model", "revision": "a" * 40,
                    "size_bytes": 1024 * 1024}]
        model_versions = [{"name": "Whisper (faster-whisper)", "version": "1.0.0",
                           "url": "https://x"}]
        report = diagnostics.format_diagnostics_report(self._results(), hf_cache, model_versions)
        assert "1 revision(s)" in report
        assert "Whisper (faster-whisper): 1.0.0" in report

    def test_omits_cache_and_versions_sections_when_not_given(self):
        report = diagnostics.format_diagnostics_report(self._results())
        assert "Hugging Face cache" not in report
        assert "Model/engine versions" not in report

    def test_report_content_survives_redaction_intact_apart_from_secrets(self):
        report = diagnostics.format_diagnostics_report(self._results())
        redacted = diagnostics.redact_for_support(report)
        assert "torch" in redacted
        assert "Python: 3.11.0" in redacted


class TestDiagnosticsTabSupportReport:
    """UI-level: the 'Copy diagnostics for support' button actually wires
    format_diagnostics_report + redact_for_support together, so a path/
    username that would appear in the raw diagnostics never reaches the
    copyable box."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def test_report_appears_with_no_path_or_username(self, isolated_db, monkeypatch):
        monkeypatch.setattr("getpass.getuser", lambda: "realuserabc")
        at = self._run()
        [b for b in at.button if b.key == "build_support_report"][0].click()
        at.run(timeout=30)
        # Find the specific code block holding the report (there's also
        # the log-tail code block on this page).
        codes = [c.value for c in at.code]
        report = next((c for c in codes if "Python:" in c), None)
        assert report is not None
        assert "realuserabc" not in report
        assert os.path.dirname(os.path.dirname(os.path.abspath(
            "tabs/diagnostics_tab.py"))) not in report
        assert "Model/engine versions" in report
        assert "Whisper (faster-whisper)" in report


class TestCheckCuda:
    """Step 10: the minimal "is a GPU actually usable" answer for
    start.bat's own launcher-time check -- deliberately not the fuller
    driver/CUDA-build version-mismatch detail a later step adds to the
    in-app GPU display."""

    def test_torch_not_installed(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_dependency", lambda name: False)
        assert diagnostics.check_cuda() == {"torch_installed": False, "cuda_available": None}

    def test_torch_installed_cuda_available(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
        fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True))
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        assert diagnostics.check_cuda() == {"torch_installed": True, "cuda_available": True}

    def test_torch_installed_no_cuda(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
        fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False))
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        assert diagnostics.check_cuda() == {"torch_installed": True, "cuda_available": False}

    def test_torch_installed_but_broken_import_does_not_crash(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
        monkeypatch.setitem(sys.modules, "torch", None)  # forces ImportError on `import torch`
        assert diagnostics.check_cuda() == {"torch_installed": True, "cuda_available": None}
