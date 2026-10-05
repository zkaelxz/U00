"""
tests/test_diagnostics_and_export.py -- tests for diagnostics.py
and db.py's line history (undo) functions.
"""

import sys
import os
import json
import shutil
import types
import zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import db
import diagnostics
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
            assert info["tier"] in ("required", "engine", "feature", "dev", "experimental")

    def test_step_6_optional_dependencies_are_registered(self):
        """CLAUDE.md: every optional dependency must be listed here, or
        Diagnostics never reports it as missing."""
        deps = diagnostics.OPTIONAL_DEPENDENCIES
        assert deps["audio-separator"][0] == "audio_separator"
        assert deps["funasr"][0] == "funasr"
        assert deps["demucs"][0] == "demucs"
        assert deps["audio-separator"][2] == deps["funasr"][2] == deps["demucs"][2] == "feature"

    def test_requirements_optional_extras_are_registered(self):
        """CLAUDE.md: every pip-installable extra in requirements-optional.txt
        is registered under its pip name with the right import name."""
        deps = diagnostics.OPTIONAL_DEPENDENCIES
        expected = {"yt-dlp": "yt_dlp", "torchaudio": "torchaudio", "uroman": "uroman",
                    "sentencepiece": "sentencepiece",
                    "opencc-python-reimplemented": "opencc",
                    "sudachidict_core": "sudachidict_core", "piper-tts": "piper"}
        for pip_name, import_name in expected.items():
            assert pip_name in deps, pip_name
            assert deps[pip_name][0] == import_name
            assert deps[pip_name][2] == "feature"

    def test_lazy_optional_imports_in_asr_modules_are_registered(self):
        """CLAUDE.md: every optional import must be in OPTIONAL_DEPENDENCIES,
        or Diagnostics never reports it missing. asr_backend/forced_align
        import their optional packages inside functions (qwen_asr, torch)."""
        import ast
        registered = {imp.split(".")[0] for imp, _f, _t in diagnostics.OPTIONAL_DEPENDENCIES.values()}
        for module in ("asr_backend.py", "forced_align.py"):
            with open(os.path.join(PROJECT_ROOT, module), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(fn):
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        names = [node.module]
                    else:
                        continue
                    for name in names:
                        top = name.split(".")[0]
                        if top in sys.stdlib_module_names or os.path.exists(os.path.join(PROJECT_ROOT, f"{top}.py")):
                            continue
                        assert top in registered, f"{module}: {top}"
        assert diagnostics.OPTIONAL_DEPENDENCIES["qwen-asr"][0] == "qwen_asr"

    def test_upload_and_numpy_deps_are_registered(self):
        """python-multipart (requirements-core; the upload routes) and numpy
        (imported directly by dub_service/scanlate/hardsub_ocr)."""
        deps = diagnostics.OPTIONAL_DEPENDENCIES
        assert deps["python-multipart"][0] == "multipart"
        assert deps["python-multipart"][2] == "required"
        assert deps["numpy"][0] == "numpy" and deps["numpy"][2] == "feature"

    def test_media_only_deps_are_not_tagged_required(self):
        """Step 83: faster_whisper, cv2, and PIL are only in
        requirements-media.txt, not requirements-core.txt, so the Core
        panel (which shows "required" as "needed for the app to run at
        all") must not list them -- they belong in "feature", same tier
        as edge_tts (also media-only)."""
        deps = diagnostics.OPTIONAL_DEPENDENCIES
        assert deps["faster_whisper"][2] == "feature"
        assert deps["cv2"][2] == "feature"
        assert deps["PIL"][2] == "feature"

    def test_file_completeness_detects_all_present_in_real_project(self):
        result = diagnostics.check_file_completeness(PROJECT_ROOT)
        assert result["all_present"] is True, \
            f"missing: {result['missing_top_level']} {result['missing_tabs']}"

    def test_expected_top_level_files_list_is_not_stale(self):
        """Step 25d item 9: this list is hand-maintained on purpose (so a
        missing file shows up as "missing" instead of silently not being
        checked at all) -- pin that every real top-level .py file is
        actually in it, so a newly added module can't again silently fall
        outside what this health check covers."""
        real_files = {f for f in os.listdir(PROJECT_ROOT)
                     if f.endswith(".py") and os.path.isfile(os.path.join(PROJECT_ROOT, f))
                     and f not in ("__init__.py", "conftest.py")}
        missing_from_list = real_files - set(diagnostics.EXPECTED_TOP_LEVEL_FILES)
        assert missing_from_list == set(), \
            f"real top-level .py files missing from EXPECTED_TOP_LEVEL_FILES: {missing_from_list}"

    def test_file_completeness_reports_missing_in_empty_dir(self, tmp_path_str):
        result = diagnostics.check_file_completeness(tmp_path_str)
        assert result["all_present"] is False
        assert len(result["missing_top_level"]) > 0

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
        snapshot_path = os.path.join(snapshot_dir, filename)
        try:
            os.symlink(blob_path, snapshot_path)
        except OSError:
            # Real huggingface_hub cache downloads fall back to a plain
            # copy on Windows when the user lacks
            # SeCreateSymbolicLinkPrivilege (no Developer Mode) --
            # scan_cache_dir() already handles a non-symlinked snapshot
            # file the same way (treats it as its own blob), so this
            # fixture should too rather than failing outright for any
            # Windows contributor without that privilege.
            shutil.copy2(blob_path, snapshot_path)
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


class TestPiperVoiceScanAndDelete:
    """Step 25d item 14: this disk-management panel only ever scanned the
    Hugging Face model cache -- Piper voices (Step 25c item 1's
    offline-voice picker) download to library/piper_voices instead, so
    they were invisible to it and to whatever cleanup/disk-usage view
    relies on it."""

    def _make_voice(self, voices_dir, name, onnx_bytes=b"x" * 5000, with_json=True):
        os.makedirs(voices_dir, exist_ok=True)
        with open(os.path.join(voices_dir, f"{name}.onnx"), "wb") as f:
            f.write(onnx_bytes)
        if with_json:
            with open(os.path.join(voices_dir, f"{name}.onnx.json"), "wb") as f:
                f.write(b"{}")

    def test_lists_voices_with_real_sizes_largest_first(self, tmp_path_str):
        self._make_voice(tmp_path_str, "en_US-amy-medium", b"x" * 5000)
        self._make_voice(tmp_path_str, "en_US-ryan-low", b"x" * 1000)
        entries = diagnostics.scan_piper_voices(tmp_path_str)
        assert [e["voice"] for e in entries] == ["en_US-amy-medium", "en_US-ryan-low"]
        assert entries[0]["size_bytes"] >= 5000
        assert entries[1]["size_bytes"] >= 1000

    def test_empty_when_the_directory_does_not_exist_yet(self, tmp_path_str):
        missing = os.path.join(tmp_path_str, "does_not_exist")
        assert diagnostics.scan_piper_voices(missing) == []

    def test_only_onnx_files_are_counted_as_voices(self, tmp_path_str):
        self._make_voice(tmp_path_str, "en_US-amy-medium")
        with open(os.path.join(tmp_path_str, "README.txt"), "w") as f:
            f.write("not a voice")
        entries = diagnostics.scan_piper_voices(tmp_path_str)
        assert [e["voice"] for e in entries] == ["en_US-amy-medium"]

    def test_delete_removes_both_the_model_and_its_config(self, tmp_path_str):
        self._make_voice(tmp_path_str, "en_US-amy-medium")
        assert diagnostics.delete_piper_voice("en_US-amy-medium", tmp_path_str) is True
        assert not os.path.exists(os.path.join(tmp_path_str, "en_US-amy-medium.onnx"))
        assert not os.path.exists(os.path.join(tmp_path_str, "en_US-amy-medium.onnx.json"))

    def test_delete_of_an_unknown_voice_fails_cleanly(self, tmp_path_str):
        assert diagnostics.delete_piper_voice("does-not-exist", tmp_path_str) is False


class TestModelFolders:
    """torch.hub checkpoints (TORCH_HOME) and the audio-separator models
    live outside the Hugging Face cache; the model panel lists and deletes
    their entries, never anything outside those folders."""

    def test_folders_follow_their_env_vars(self, monkeypatch, tmp_path_str):
        import audio_preprocess
        monkeypatch.setenv("TORCH_HOME", tmp_path_str)
        assert diagnostics.model_folder("torch") == os.path.join(tmp_path_str, "hub", "checkpoints")
        monkeypatch.delenv("TORCH_HOME")
        monkeypatch.setenv("XDG_CACHE_HOME", tmp_path_str)
        assert diagnostics.model_folder("torch") == os.path.join(
            tmp_path_str, "torch", "hub", "checkpoints")
        monkeypatch.setattr(audio_preprocess, "MODEL_DIR", tmp_path_str)
        assert diagnostics.model_folder("audio_separator") == tmp_path_str

    def test_lists_files_and_folders_largest_first_skipping_symlinks(self, tmp_path_str):
        folder = os.path.join(tmp_path_str, "models")
        os.makedirs(os.path.join(folder, "repo"))
        with open(os.path.join(folder, "htdemucs.th"), "wb") as f:
            f.write(b"x" * 300)
        with open(os.path.join(folder, "repo", "w.bin"), "wb") as f:
            f.write(b"x" * 500)
        outside = os.path.join(tmp_path_str, "outside.bin")
        with open(outside, "wb") as f:
            f.write(b"x" * 900)
        try:
            os.symlink(outside, os.path.join(folder, "link.bin"))
        except (OSError, NotImplementedError):
            pass
        assert diagnostics.scan_model_folder("torch", folder) == [
            {"name": "repo", "size_bytes": 500}, {"name": "htdemucs.th", "size_bytes": 300}]
        assert diagnostics.scan_model_folder("torch", os.path.join(tmp_path_str, "nope")) == []

    def test_delete_stays_inside_the_folder(self, tmp_path_str):
        folder = os.path.join(tmp_path_str, "models")
        os.makedirs(os.path.join(folder, "repo"))
        with open(os.path.join(folder, "m.ckpt"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(tmp_path_str, "keep.txt"), "wb") as f:
            f.write(b"x")
        for bad in ("../keep.txt", "..", ".", "", "repo/../../keep.txt", "missing.ckpt"):
            assert diagnostics.delete_model_folder_entry("torch", bad, folder) is False
        assert os.path.exists(os.path.join(tmp_path_str, "keep.txt"))
        assert diagnostics.delete_model_folder_entry("audio_separator", "m.ckpt", folder) is True
        assert diagnostics.delete_model_folder_entry("audio_separator", "repo", folder) is True
        assert os.listdir(folder) == []


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

    def test_step_11b_voice_engines_have_rows(self):
        rows = {v["name"]: v for v in diagnostics.get_model_engine_versions()}
        for name in ("OmniVoice", "GPT-SoVITS", "Chatterbox", "TADA"):
            assert name in rows
        # a separate server, not a pip package -- says so rather than "not installed"
        assert rows["GPT-SoVITS"]["version"] == "Separate local server (not pip-installed)"

    def test_step_11b_pip_engines_are_registered_dependencies(self):
        # keyed by the real pip name, since the Install button runs `pip install <key>`
        deps = diagnostics.OPTIONAL_DEPENDENCIES
        assert deps["omnivoice"][0] == "omnivoice"
        assert deps["chatterbox-tts"][0] == "chatterbox"
        assert deps["hume-tada"][0] == "tada"

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

    def test_installed_is_a_real_boolean_not_a_string_match(self):
        """Step 18 item 2: the render step branches on a real boolean this
        function computes, not a fragile `version == "not installed"`
        string match in the caller."""
        rows = {v["name"]: v for v in diagnostics.get_model_engine_versions()}
        # a "package" kind: installed iff importlib.metadata found a version
        pkg_row = rows["pyannote.audio"]
        assert pkg_row["installed"] == (pkg_row["version"] != "not installed")
        # a "repo" kind has no real "not installed" state -- always installed
        assert rows["pyannote diarization model"]["installed"] is True
        # a "service" kind (a separate server, not pip-installed) likewise
        assert rows["GPT-SoVITS"]["installed"] is True

    def test_ollama_row_is_installed(self):
        rows = {v["name"]: v for v in diagnostics.get_model_engine_versions("qwen3:8b")}
        assert rows["Ollama (active tag)"]["installed"] is True


class TestGpuStatus:
    """Step 18 item 3: a live GPU/VRAM readout for Diagnostics' routine
    view -- always a plain dict, never an exception, whether or not torch
    or a GPU is actually present."""

    def test_unavailable_when_torch_not_installed(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_dependency", lambda name: False)
        status = diagnostics.get_gpu_status()
        assert status["available"] is False
        assert "PyTorch isn't installed" in status["message"]

    def test_unavailable_no_error_when_no_gpu_present(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
        fake_torch = types.SimpleNamespace(
            version=types.SimpleNamespace(cuda=None),
            cuda=types.SimpleNamespace(is_available=lambda: False))
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        status = diagnostics.get_gpu_status()
        assert status["available"] is False
        assert "unavailable" in status["message"]
        assert "GPU" not in status["message"] or "no CUDA-capable GPU" in status["message"]

    def test_names_the_real_mismatch_when_gpu_present_but_torch_is_cpu_only(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
        monkeypatch.setattr(diagnostics.shutil, "which",
                            lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
        fake_torch = types.SimpleNamespace(
            version=types.SimpleNamespace(cuda=None),
            cuda=types.SimpleNamespace(is_available=lambda: False))
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        status = diagnostics.get_gpu_status()
        assert status["available"] is False
        assert "CPU-only" in status["message"]

    def test_available_reports_real_name_and_vram(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
        props = types.SimpleNamespace(name="NVIDIA GeForce RTX 3080 Ti", total_memory=12 * 1024 ** 3)
        fake_torch = types.SimpleNamespace(
            version=types.SimpleNamespace(cuda="12.8"),
            cuda=types.SimpleNamespace(
                is_available=lambda: True,
                current_device=lambda: 0,
                get_device_properties=lambda idx: props,
                memory_allocated=lambda idx: 2 * 1024 ** 3))
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        status = diagnostics.get_gpu_status()
        assert status["available"] is True
        assert status["name"] == "NVIDIA GeForce RTX 3080 Ti"
        assert status["vram_used_gb"] == pytest.approx(2.0)
        assert status["vram_total_gb"] == pytest.approx(12.0)
        assert status["torch_cuda_version"] == "12.8"

    def test_broken_torch_import_does_not_crash(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
        monkeypatch.setitem(sys.modules, "torch", None)  # forces ImportError on `import torch`
        status = diagnostics.get_gpu_status()
        assert status["available"] is False
        assert status["message"]


class TestStreamDependencyInstall:
    """Step 18 item 7: the generic per-dependency Install button must not
    reproduce the CPU-only-torch footgun the dedicated GPU-PyTorch button
    already exists to fix."""

    def test_torch_with_gpu_present_uses_the_gpu_aware_reinstall(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which",
                            lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
        called = {}

        def fake_gpu_reinstall(python_executable=None, project_root=None):
            called["used"] = True
            yield {"done": True, "ok": True, "returncode": 0}
        monkeypatch.setattr(diagnostics, "stream_gpu_torch_reinstall", fake_gpu_reinstall)

        def boom(*a, **k):
            raise AssertionError("should not fall back to a bare pip install")
        monkeypatch.setattr(diagnostics, "stream_pip_install", boom)

        list(diagnostics.stream_dependency_install("torch"))
        assert called.get("used") is True

    def test_torch_with_no_gpu_uses_a_plain_install(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
        captured = {}

        def fake_plain_install(pip_args, python_executable=None):
            captured["pip_args"] = pip_args
            yield {"done": True, "ok": True, "returncode": 0}
        monkeypatch.setattr(diagnostics, "stream_pip_install", fake_plain_install)

        list(diagnostics.stream_dependency_install("torch"))
        assert captured["pip_args"] == ["torch"]

    def test_other_dependencies_always_use_a_plain_install(self, monkeypatch):
        monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
        captured = {}

        def fake_plain_install(pip_args, python_executable=None):
            captured["pip_args"] = pip_args
            yield {"done": True, "ok": True, "returncode": 0}
        monkeypatch.setattr(diagnostics, "stream_pip_install", fake_plain_install)

        list(diagnostics.stream_dependency_install("audio-separator"))
        assert captured["pip_args"] == ["audio-separator"]


class TestPyannoteGatedAccessCheck:
    class _FakeApi:
        def __init__(self, gated=()):
            self.gated = set(gated)
            self.calls = []
            self.timeouts = []

        def model_info(self, model, token=None, timeout=None):
            self.calls.append((model, token))
            self.timeouts.append(timeout)
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

    def test_every_hub_call_has_a_timeout(self):
        api = self._FakeApi()
        diagnostics.check_pyannote_gated_access(api=api)
        assert api.timeouts and all(t == 10 for t in api.timeouts)

    def test_real_hfapi_model_info_accepts_timeout(self):
        hub = pytest.importorskip("huggingface_hub")
        import inspect
        assert "timeout" in inspect.signature(hub.HfApi.model_info).parameters

    def test_huggingface_hub_not_installed_returns_empty(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)
        assert diagnostics.check_pyannote_gated_access() == []


class TestCheckEngineReachable:
    """Step 97: pre-flight a translation engine's credentials/
    reachability with a real, minimal (zh -> en) call, before a batch
    job commits to it. TestOfflineEngine needs no network/key, so it
    exercises the real success path end to end; the failure paths are
    mocked, same as every other network-reaching diagnostics check."""

    def test_a_working_engine_reports_ok(self):
        result = diagnostics.check_engine_reachable("fake")
        assert result == {"engine": "fake", "ok": True, "error": None}

    def test_unknown_engine_name_reports_failure_not_a_crash(self):
        result = diagnostics.check_engine_reachable("not-a-real-engine")
        assert result["ok"] is False
        assert result["engine"] == "not-a-real-engine"
        assert result["error"]

    def test_a_translate_call_that_raises_is_reported_as_failure(self, monkeypatch):
        import translate_engines

        def boom(*a, **k):
            raise RuntimeError("invalid key: sk-fake1234567890")
        monkeypatch.setattr(translate_engines, "standalone_translate", boom)
        result = diagnostics.check_engine_reachable("claude", api_key="sk-fake1234567890")
        assert result["ok"] is False
        assert "sk-fake1234567890" not in result["error"]     # redacted, like every other engine error

    def test_an_empty_translation_is_reported_as_failure_not_silently_ok(self, monkeypatch):
        import translate_engines
        monkeypatch.setattr(translate_engines, "standalone_translate", lambda *a, **k: "")
        result = diagnostics.check_engine_reachable("claude", api_key="sk-x")
        assert result["ok"] is False
        assert "empty" in result["error"].lower()


class TestDependencyVersionCheck:
    """Step 27: 'is this outdated' + Upgrade. Like the pyannote check
    above, this reaches the network -- every test here mocks
    requests.get so no test ever makes a real call to PyPI."""

    def test_get_installed_version_returns_none_for_unknown_distribution(self):
        assert diagnostics.get_installed_version("definitely-not-a-real-package-xyz") is None

    def test_get_installed_version_returns_a_real_version_for_something_installed(self):
        # pytest is a real dev dependency of this project's own test env.
        version = diagnostics.get_installed_version("pytest")
        assert version and version[0].isdigit()

    def test_get_latest_pypi_version_parses_a_successful_response(self, monkeypatch):
        class FakeResp:
            status_code = 200
            headers = {}
            def iter_content(self, size):
                yield b'{"info": {"version": "9.9.9"}}'
            def close(self):
                pass
        captured = {}
        def fake_get(url, timeout=None, stream=False, allow_redirects=True):
            captured["url"], captured["timeout"] = url, timeout
            return FakeResp()
        monkeypatch.setattr("requests.get", fake_get)
        assert diagnostics.get_latest_pypi_version("somepkg", timeout=3.5) == "9.9.9"
        assert captured["url"] == "https://pypi.org/pypi/somepkg/json"
        assert captured["timeout"] == 3.5

    def test_get_latest_pypi_version_returns_none_on_404(self, monkeypatch):
        class FakeResp:
            status_code = 404
            def close(self):
                pass
        monkeypatch.setattr("requests.get", lambda url, timeout=None, **kw: FakeResp())
        assert diagnostics.get_latest_pypi_version("no-such-package") is None

    def test_get_latest_pypi_version_gives_up_on_an_oversized_body(self, monkeypatch):
        closed = []

        class FakeResp:
            status_code = 200
            headers = {}
            def iter_content(self, size):
                while True:             # a server that never stops
                    yield b"x" * size
            def close(self):
                closed.append(True)
        monkeypatch.setattr(diagnostics, "PYPI_JSON_MAX_BYTES", 1000)
        monkeypatch.setattr("requests.get", lambda url, timeout=None, **kw: FakeResp())
        assert diagnostics.get_latest_pypi_version("somepkg") is None
        assert closed

    def test_get_latest_pypi_version_returns_none_on_network_error(self, monkeypatch):
        def boom(url, timeout=None, **kw):
            raise ConnectionError("no network")
        monkeypatch.setattr("requests.get", boom)
        assert diagnostics.get_latest_pypi_version("somepkg") is None

    def test_check_dependency_versions_flags_an_outdated_package(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "get_installed_version", lambda name: "1.0.0")
        monkeypatch.setattr(diagnostics, "get_latest_pypi_version",
                            lambda name, timeout=10.0: "2.0.0")
        deps = {"somepkg": {"installed": True, "powers": "x", "tier": "feature"}}
        results = diagnostics.check_dependency_versions(deps)
        assert results == {"somepkg": {"installed_version": "1.0.0",
                                       "latest_version": "2.0.0", "outdated": True}}

    def test_check_dependency_versions_flags_an_up_to_date_package(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "get_installed_version", lambda name: "2.0.0")
        monkeypatch.setattr(diagnostics, "get_latest_pypi_version",
                            lambda name, timeout=10.0: "2.0.0")
        deps = {"somepkg": {"installed": True, "powers": "x", "tier": "feature"}}
        results = diagnostics.check_dependency_versions(deps)
        assert results["somepkg"]["outdated"] is False

    def test_check_dependency_versions_compares_numerically_not_lexically(self, monkeypatch):
        # a plain string compare gets "1.10.0" < "1.9.0" backwards
        monkeypatch.setattr(diagnostics, "get_installed_version", lambda name: "1.9.0")
        monkeypatch.setattr(diagnostics, "get_latest_pypi_version",
                            lambda name, timeout=10.0: "1.10.0")
        deps = {"somepkg": {"installed": True, "powers": "x", "tier": "feature"}}
        assert diagnostics.check_dependency_versions(deps)["somepkg"]["outdated"] is True

    def test_check_dependency_versions_skips_packages_that_arent_installed(self, monkeypatch):
        def boom(name):
            raise AssertionError("should not be called for an uninstalled package")
        monkeypatch.setattr(diagnostics, "get_installed_version", boom)
        deps = {"somepkg": {"installed": False, "powers": "x", "tier": "feature"}}
        assert diagnostics.check_dependency_versions(deps) == {}

    def test_check_dependency_versions_outdated_is_none_when_latest_cant_be_determined(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "get_installed_version", lambda name: "1.0.0")
        monkeypatch.setattr(diagnostics, "get_latest_pypi_version", lambda name, timeout=10.0: None)
        deps = {"somepkg": {"installed": True, "powers": "x", "tier": "feature"}}
        assert diagnostics.check_dependency_versions(deps)["somepkg"]["outdated"] is None

    def test_no_network_call_unless_the_check_is_explicitly_run(self, monkeypatch, tmp_path):
        def boom(*a, **k):
            raise AssertionError("should not touch the network")
        monkeypatch.setattr("requests.get", boom)
        monkeypatch.setattr("requests.post", boom)
        # Everything else on this page must stay network-free by default.
        diagnostics.check_all_dependencies()
        diagnostics.run_full_diagnostics(PROJECT_ROOT, str(tmp_path), {})


class TestUpgradePipArgs:
    def test_adds_the_upgrade_flag_and_package_name(self, tmp_path):
        args = diagnostics.upgrade_pip_args("somepkg", project_root=str(tmp_path))
        assert args[:2] == ["--upgrade", "somepkg"]

    def test_adds_constraints_when_the_file_exists(self, tmp_path):
        (tmp_path / "constraints.txt").write_text("torch<3\n")
        args = diagnostics.upgrade_pip_args("torch", project_root=str(tmp_path))
        assert "-c" in args
        assert str(tmp_path / "constraints.txt") in args

    def test_no_constraints_flag_when_the_file_is_missing(self, tmp_path):
        args = diagnostics.upgrade_pip_args("somepkg", project_root=str(tmp_path))
        assert "-c" not in args


class TestUpgradeBlockedReason:
    """Step 47 item 5: an "Upgrade" action that can't actually reach the
    latest release for a real reason must say why instead of silently
    offering nothing, or a doomed-to-fail upgrade."""

    def test_none_for_an_ordinary_package_with_no_known_limitation(self, tmp_path):
        assert diagnostics.upgrade_blocked_reason(
            "somepkg", "9.9.9", project_root=str(tmp_path)) is None

    def test_known_python_314_limitation_only_applies_on_python_314(self, monkeypatch, tmp_path):
        monkeypatch.setattr(diagnostics.sys, "version_info", (3, 14, 0, "final", 0))
        reason = diagnostics.upgrade_blocked_reason(
            "audio-separator", "0.3.0", project_root=str(tmp_path))
        assert reason is not None
        assert "3.14" in reason
        assert "diffq-fixed" in reason

    def test_known_limitation_does_not_apply_on_a_different_python_version(self, monkeypatch, tmp_path):
        monkeypatch.setattr(diagnostics.sys, "version_info", (3, 11, 0, "final", 0))
        assert diagnostics.upgrade_blocked_reason(
            "audio-separator", "0.3.0", project_root=str(tmp_path)) is None

    def test_constraints_cap_explains_why_when_latest_exceeds_it(self, tmp_path):
        (tmp_path / "constraints.txt").write_text("torch<3  # some real reason\n")
        reason = diagnostics.upgrade_blocked_reason(
            "torch", "3.1.0", project_root=str(tmp_path))
        assert reason is not None
        assert "torch<3" in reason
        assert "constraints.txt" in reason

    def test_no_reason_when_latest_is_still_within_the_cap(self, tmp_path):
        (tmp_path / "constraints.txt").write_text("torch<3\n")
        assert diagnostics.upgrade_blocked_reason(
            "torch", "2.9.0", project_root=str(tmp_path)) is None

    def test_no_reason_when_constraints_file_is_missing(self, tmp_path):
        assert diagnostics.upgrade_blocked_reason(
            "torch", "3.1.0", project_root=str(tmp_path)) is None


class TestKnownInstallLimitationReason:
    """Step 61: the same known, Python-version-specific limitation
    upgrade_blocked_reason shows for an already-installed package should
    also be visible for one that isn't installed yet at all -- a "not
    installed" row shouldn't leave that as the only signal until the
    user clicks Install and hits a raw pip/Cython traceback."""

    def test_none_for_an_ordinary_package(self):
        assert diagnostics.known_install_limitation_reason("somepkg") is None

    def test_known_limitation_on_the_affected_python_version(self, monkeypatch):
        monkeypatch.setattr(diagnostics.sys, "version_info", (3, 14, 0, "final", 0))
        reason = diagnostics.known_install_limitation_reason("audio-separator")
        assert reason is not None
        assert "3.14" in reason and "diffq-fixed" in reason

    def test_underscore_and_hyphen_names_are_equivalent(self, monkeypatch):
        monkeypatch.setattr(diagnostics.sys, "version_info", (3, 14, 0, "final", 0))
        assert diagnostics.known_install_limitation_reason("audio_separator") == \
            diagnostics.known_install_limitation_reason("audio-separator")

    def test_does_not_apply_on_a_different_python_version(self, monkeypatch):
        monkeypatch.setattr(diagnostics.sys, "version_info", (3, 11, 0, "final", 0))
        assert diagnostics.known_install_limitation_reason("audio-separator") is None


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

    def test_strips_ansi_colour_codes(self):
        raw = "\x1b[0;31mERROR:\x1b[0m [youtube] abc123: Sign in to confirm \x1b[1mnow\x1b[0m\x1b[K"
        text = diagnostics.redact_for_support(raw)
        assert text == "ERROR: [youtube] abc123: Sign in to confirm now"
        assert "\x1b" not in text


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
