"""The opt-in whisper-ja 1.5B model: pinned download with a file-set refusal, the
cache check, and the Japanese-only rule on the server, the Lab and the CLI.

Fakes only: no network, no weights.
"""

import os
import sys
import types

import pytest

import memory_headroom
import whisper_models as wm
from lib import ct2_repo
from lib.errors import InvalidInputError
from services import benchmark_lab_service as lab
from services import compare_transcription_service, transcribe_pipeline, transcribe_service

KEY = "ja-1.5b"
REPO, REVISION = wm.PINNED_CT2_REPOS[KEY]
GOOD = ["config.json", "model.bin", "tokenizer.json", "vocabulary.json",
        "preprocessor_config.json", "README.md", ".gitattributes"]


class TestFileSet:
    def test_accepts_the_expected_set_and_skips_text_files(self):
        assert ct2_repo.select_ct2_files(GOOD) == [
            "config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"]

    @pytest.mark.parametrize("bad", ["modeling.py", "w.pt", "w.pth", "d.pkl", "pytorch_model.bin",
                                     "model.ckpt", "extra.safetensors", "sub/model.bin"])
    def test_refuses_anything_else(self, bad):
        with pytest.raises(ct2_repo.RepoFilesRefused):
            ct2_repo.select_ct2_files(GOOD + [bad])

    def test_refusal_names_only_the_file_not_its_folder(self):
        with pytest.raises(ct2_repo.RepoFilesRefused) as err:
            ct2_repo.select_ct2_files(GOOD + ["secret/dir/evil.py"])
        assert "evil.py" in str(err.value) and "secret" not in str(err.value)

    @pytest.mark.parametrize("missing", ["config.json", "model.bin", "tokenizer.json", "preprocessor_config.json"])
    def test_refuses_a_listing_without_a_required_file(self, missing):
        with pytest.raises(ct2_repo.RepoFilesRefused, match=missing):
            ct2_repo.select_ct2_files([n for n in GOOD if n != missing])

    def test_needs_a_vocabulary_file(self):
        with pytest.raises(ct2_repo.RepoFilesRefused, match="vocabulary"):
            ct2_repo.select_ct2_files([n for n in GOOD if n != "vocabulary.json"])


class FakeHub:
    """Stands in for huggingface_hub: a listing and a download that records its arguments."""

    def __init__(self, monkeypatch, tmp_path, names=GOOD):
        self.names, self.downloads, self.lists = names, [], 0
        hub = self

        class HfApi:
            def model_info(self, repo, revision=None, token=None, timeout=None):
                assert timeout, "every outbound call needs a timeout"
                hub.lists += 1
                return types.SimpleNamespace(
                    siblings=[types.SimpleNamespace(rfilename=n) for n in hub.names])

        def snapshot_download(repo, **kw):
            hub.downloads.append((repo, kw))
            return str(tmp_path / "downloaded")

        monkeypatch.setitem(sys.modules, "huggingface_hub",
                            types.SimpleNamespace(HfApi=HfApi, snapshot_download=snapshot_download))


@pytest.fixture
def hf_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    return tmp_path / "hf" / "hub"


def make_snapshot(hf_home, revision=REVISION, weights=True):
    folder = hf_home / ("models--" + REPO.replace("/", "--")) / "snapshots" / revision
    folder.mkdir(parents=True)
    if weights:
        for name in ct2_repo.CT2_REQUIRED:
            (folder / name).write_bytes(b"x")
    return folder


class TestCacheLocation:
    def test_hf_hub_cache_wins_over_hf_home(self, monkeypatch, tmp_path, hf_home):
        monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hubcache"))
        assert wm._hub_dir() == str(tmp_path / "hubcache")
        monkeypatch.delenv("HF_HUB_CACHE")
        assert wm._hub_dir() == str(hf_home)

    def test_default_is_under_the_home_cache(self, monkeypatch):
        monkeypatch.delenv("HF_HOME", raising=False)
        monkeypatch.delenv("HF_HUB_CACHE", raising=False)
        assert wm._hub_dir() == os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")

    def test_pinned_and_generic_checks_both_follow_hf_hub_cache(self, monkeypatch, tmp_path, hf_home):
        cache = tmp_path / "hubcache"
        monkeypatch.setenv("HF_HUB_CACHE", str(cache))
        make_snapshot(cache)
        generic = cache / "models--Systran--faster-whisper-small" / "snapshots" / "r1"
        generic.mkdir(parents=True)
        (generic / "model.bin").write_bytes(b"x")
        assert wm.is_whisper_model_cached(KEY) and wm.is_whisper_model_cached("small")


class TestPinnedDownload:
    def test_a_snapshot_missing_a_required_file_is_not_cached(self, hf_home):
        folder = make_snapshot(hf_home)
        (folder / "tokenizer.json").unlink()
        assert wm._pinned_snapshot(KEY) is None
        assert not wm.is_whisper_model_cached(KEY)

    def test_downloads_only_allowed_files_at_the_pinned_revision(self, monkeypatch, tmp_path, hf_home):
        hub = FakeHub(monkeypatch, tmp_path)
        path = wm._fetch_pinned_model(KEY, "tok")
        assert path == str(tmp_path / "downloaded")
        (repo, kw), = hub.downloads
        assert repo == REPO and kw["revision"] == REVISION
        assert sorted(kw["allow_patterns"]) == [
            "config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"]

    def test_a_stray_file_refuses_before_anything_is_downloaded(self, monkeypatch, tmp_path, hf_home):
        hub = FakeHub(monkeypatch, tmp_path, GOOD + ["loader.py"])
        with pytest.raises(wm.ModelDownloadError, match="loader.py") as err:
            wm._fetch_pinned_model(KEY, None)
        assert hub.downloads == []
        assert str(tmp_path) not in str(err.value) and "http" not in str(err.value)

    def test_a_cached_pinned_snapshot_needs_no_network(self, monkeypatch, tmp_path, hf_home):
        folder = make_snapshot(hf_home)
        hub = FakeHub(monkeypatch, tmp_path)
        assert wm._fetch_pinned_model(KEY, None) == str(folder)
        assert hub.lists == 0 and hub.downloads == []

    def test_a_download_error_is_a_plain_model_download_error(self, monkeypatch, tmp_path, hf_home):
        FakeHub(monkeypatch, tmp_path)
        sys.modules["huggingface_hub"].snapshot_download = lambda *a, **k: (_ for _ in ()).throw(
            ValueError("403 for https://hf.co/x?token=hf_SECRETSECRET1234 at C:\\Users\\me"))
        with pytest.raises(wm.ModelDownloadError) as err:
            wm._fetch_pinned_model(KEY, None)
        text = str(err.value)
        assert "hf_SECRET" not in text and "C:\\" not in text and "http" not in text

    def test_load_uses_the_pinned_folder_and_ignores_a_client_repo_id(self, monkeypatch, tmp_path, hf_home):
        FakeHub(monkeypatch, tmp_path)
        loaded = []

        class WhisperModel:
            def __init__(self, path, device=None, compute_type=None):
                loaded.append(path)

        monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WhisperModel))
        monkeypatch.setattr(memory_headroom, "before_load", lambda *a, **k: None)
        monkeypatch.setattr(wm, "_whisper_model_cache", {})
        wm.load_whisper_model(KEY)
        assert loaded == [str(tmp_path / "downloaded")]


class TestCacheAndEstimates:
    def test_cached_only_at_the_pinned_revision_with_weights(self, hf_home):
        assert not wm.is_whisper_model_cached(KEY)
        make_snapshot(hf_home, revision="a" * 40)
        assert not wm.is_whisper_model_cached(KEY)
        make_snapshot(hf_home, weights=False)
        assert not wm.is_whisper_model_cached(KEY)
        folder = hf_home / ("models--" + REPO.replace("/", "--")) / "snapshots" / REVISION
        for name in ct2_repo.CT2_REQUIRED:
            (folder / name).write_bytes(b"x")
        assert wm.is_whisper_model_cached(KEY)

    def test_other_models_are_still_matched_by_exact_name(self, hf_home):
        folder = hf_home / "models--Systran--faster-whisper-small" / "snapshots" / "r1"
        folder.mkdir(parents=True)
        (folder / "model.bin").write_bytes(b"x")
        assert wm.is_whisper_model_cached("small") and not wm.is_whisper_model_cached(KEY)

    def test_download_size_and_memory_estimate_are_known(self):
        assert transcribe_pipeline._MODEL_DOWNLOAD_SIZES[KEY] == "~3.1 GB"
        assert memory_headroom.estimate_mb("whisper", KEY)
        assert memory_headroom.whisper_key("D:\\models\\whisper-ja-1.5B") == KEY

    def test_never_the_default(self):
        assert wm.DEFAULT_WHISPER_SIZE != KEY and wm.WHISPER_LANGUAGE_RULES[KEY].only_for == ("ja",)
        assert transcribe_service.default_whisper_size() != KEY


class TestJapaneseOnly:
    def test_update_config_accepts_it_for_a_japanese_title_only(self, isolated_db):
        ja = isolated_db.create_drama(title_en="ja", source_language="ja")
        zh = isolated_db.create_drama(title_en="zh", source_language="zh")
        assert transcribe_service.update_transcribe_config(ja, whisper_size=KEY)["whisper_size"] == KEY
        with pytest.raises(InvalidInputError, match="Japanese"):
            transcribe_service.update_transcribe_config(zh, whisper_size=KEY)
        assert isolated_db.get_drama(zh)["whisper_size"] != KEY

    def test_a_saved_choice_falls_back_when_the_language_changes(self):
        assert transcribe_service.stored_whisper_size(
            {"whisper_size": KEY, "source_language": "ja"}) == KEY
        for lang in ("zh", "ko", None):
            assert transcribe_service.stored_whisper_size(
                {"whisper_size": KEY, "source_language": lang}) == transcribe_service.default_whisper_size()

    def test_a_run_naming_another_language_is_refused(self):
        with pytest.raises(InvalidInputError):
            transcribe_service.check_whisper_size_for_language(KEY, "ko")
        transcribe_service.check_whisper_size_for_language(KEY, "ja")
        transcribe_service.check_whisper_size_for_language("large-v3", "ko")

    def test_compare_offers_and_accepts_it_only_for_japanese(self, isolated_db, monkeypatch):
        ja = {"id": 1, "source_language": "ja"}
        zh = {"id": 2, "source_language": "zh"}
        monkeypatch.setattr(compare_transcription_service, "_backend_problem", lambda *a: None)
        assert compare_transcription_service._validate_candidate(ja, KEY, "whisper")[0] == KEY
        with pytest.raises(InvalidInputError):
            compare_transcription_service._validate_candidate(zh, KEY, "whisper")

    def test_lab_refuses_it_for_non_japanese_cases(self, monkeypatch):
        assert KEY in lab.get_options()["whisper_sizes"]
        cfg = [{"engine": "whisper", "model": KEY}]
        cases = {"ja": [{"id": 1, "source_language": "ja"}],
                 "mixed": [{"id": 1, "source_language": "ja"}, {"id": 2, "source_language": "zh"}]}
        which = []
        monkeypatch.setattr(lab, "_select_cases", lambda *a, **k: cases[which[0]])
        which[:] = ["ja"]
        assert lab._check_selection("transcription", cfg, None, None, None)[0][0]["model"] == KEY
        which[:] = ["mixed"]
        with pytest.raises(InvalidInputError, match="Japanese"):
            lab._check_selection("transcription", cfg, None, None, None)

