"""The opt-in Breeze-ASR-25 model: registered like whisper-ja 1.5B (pinned CTranslate2 repo) but
for Chinese titles only. Fakes only: no network, no weights."""

import re

import pytest

import memory_headroom
import whisper_models as wm
from lib.errors import InvalidInputError
from services import compare_transcription_service, transcribe_pipeline, transcribe_service, transcribe_options_service
from tests.test_whisper_ja_model import FakeHub, hf_home  # noqa: F401  (fixture)

KEY = "breeze-asr-25"
REPO, REVISION = wm.PINNED_CT2_REPOS[KEY]
# Everything this repo ships; the app refuses a listing with anything outside the CTranslate2 set.
REPO_FILES = [".gitattributes", "README.md", "config.json", "model.bin", "preprocessor_config.json",
              "tokenizer.json", "vocabulary.json"]


class TestRegistry:
    def test_pinned_to_a_full_commit_sha(self):
        assert REPO == "phate334/Breeze-ASR-25-ct2"
        assert re.fullmatch(r"[0-9a-f]{40}", REVISION)

    def test_registered_everywhere_a_model_key_must_be(self):
        assert KEY in wm.WHISPER_MODELS
        assert KEY in transcribe_options_service._allowed_whisper_sizes()
        assert transcribe_pipeline._MODEL_DOWNLOAD_SIZES[KEY] == "~3.1 GB"
        assert memory_headroom.estimate_mb("whisper", KEY)
        assert KEY in wm.LIVE_WHISPER_SIZES

    def test_global_default_stays_large_v3(self):
        assert wm.DEFAULT_WHISPER_SIZE != KEY
        assert transcribe_options_service.default_whisper_size() != KEY

    @pytest.mark.parametrize("language,cached,expected", [
        ("zh", True, KEY), ("zh", False, "large-v3"), ("ja", True, "large-v3"), ("ko", True, "large-v3")])
    def test_default_for_a_language_is_breeze_only_for_downloaded_chinese(
            self, monkeypatch, language, cached, expected):
        monkeypatch.setattr(wm, "is_whisper_model_cached", lambda size: cached and size == KEY)
        assert wm.default_whisper_size_for(language) == expected

    def test_a_title_with_no_saved_model_uses_it_but_a_saved_one_is_kept(self, monkeypatch):
        monkeypatch.setattr(wm, "is_whisper_model_cached", lambda size: True)
        assert transcribe_options_service.stored_whisper_size({"source_language": "zh"}) == KEY
        assert transcribe_options_service.stored_whisper_size(
            {"source_language": "zh", "whisper_size": "large-v3"}) == "large-v3"
        assert transcribe_options_service.stored_whisper_size({"source_language": "ko"}) == "large-v3"

    @pytest.mark.parametrize("drama,gpu,sep_ready,expected", [
        ({"separate_vocals_first": None}, True, True, True),
        ({"separate_vocals_first": 0}, True, True, False),
        ({"separate_vocals_first": 1, "whisper_size": "large-v3"}, True, True, True),
        ({"separate_vocals_first": None, "whisper_size": "large-v3"}, True, True, False),
        ({"separate_vocals_first": None}, False, True, False),
        ({"separate_vocals_first": None}, True, False, False),
        ({"separate_vocals_first": None, "use_groq": 1}, True, True, False),
        ({"separate_vocals_first": None, "asr_backend_choice": "qwen3_asr"}, True, True, False),
    ])
    def test_vocal_separation_defaults_on_for_breeze_only(self, monkeypatch, drama, gpu, sep_ready, expected):
        from services import model_readiness_service, settings_service
        monkeypatch.setattr(wm, "is_whisper_model_cached", lambda size: True)
        monkeypatch.setattr(settings_service, "get", lambda key, *a: gpu if key == "use_gpu" else None)
        monkeypatch.setattr(model_readiness_service, "separation_ready", lambda: sep_ready)
        assert transcribe_options_service.effective_separate_vocals({"source_language": "zh", **drama}) is expected

    def test_a_new_title_has_the_switch_unset(self, isolated_db):
        did = isolated_db.create_drama(title_zh="t")
        assert isolated_db.get_drama(did)["separate_vocals_first"] is None

    def test_live_starts_chinese_on_it_only_when_downloaded(self, monkeypatch):
        from services import live_service
        monkeypatch.setattr(wm, "is_whisper_model_cached", lambda size: size == KEY)
        monkeypatch.setattr(live_service.diagnostics_torch, "nvidia_driver_info", lambda: None)
        assert live_service.form_options()["language_whisper_defaults"] == {"zh": KEY}
        monkeypatch.setattr(wm, "is_whisper_model_cached", lambda size: False)
        assert live_service.form_options()["language_whisper_defaults"] == {}

    @pytest.mark.parametrize("language,allowed", [("zh", True), ("ja", False), ("ko", False)])
    def test_selectable_only_for_chinese(self, language, allowed):
        assert wm.whisper_model_fits_language(KEY, language) is allowed

    def test_the_picker_payload_carries_the_reason_for_japanese_and_korean(self):
        payload = wm.whisper_language_exclusions()[KEY]
        assert set(payload) == {"ja", "ko"} and "Chinese" in payload["ja"]

    def test_the_server_accepts_it_for_a_chinese_title_only(self, isolated_db):
        zh = isolated_db.create_drama(title_en="zh", source_language="zh")
        ja = isolated_db.create_drama(title_en="ja", source_language="ja")
        assert transcribe_service.update_transcribe_config(zh, whisper_size=KEY)["whisper_size"] == KEY
        with pytest.raises(InvalidInputError, match="Chinese"):
            transcribe_service.update_transcribe_config(ja, whisper_size=KEY)

    def test_a_saved_choice_falls_back_when_the_language_changes(self):
        assert transcribe_options_service.stored_whisper_size({"whisper_size": KEY, "source_language": "zh"}) == KEY
        assert transcribe_options_service.stored_whisper_size(
            {"whisper_size": KEY, "source_language": "ja"}) == transcribe_options_service.default_whisper_size()

    def test_compare_refuses_it_for_japanese(self, isolated_db, monkeypatch):
        monkeypatch.setattr(compare_transcription_service, "_backend_problem", lambda *a: None)
        zh = {"id": 1, "source_language": "zh"}
        ja = {"id": 2, "source_language": "ja"}
        assert compare_transcription_service._validate_candidate(zh, KEY, "whisper")[0] == KEY
        with pytest.raises(InvalidInputError):
            compare_transcription_service._validate_candidate(ja, KEY, "whisper")

    def test_it_keeps_word_timestamps(self):
        # Its config carries large-v2's alignment heads on a full 32-layer decoder, so unlike kotoba it can be asked.
        assert wm.supports_word_timestamps(KEY)


class TestPinnedDownload:
    def test_downloads_the_pinned_revision_through_the_shared_file_check(self, monkeypatch, tmp_path, hf_home):
        hub = FakeHub(monkeypatch, tmp_path, REPO_FILES)
        wm._fetch_pinned_model(KEY, None)
        (repo, kw), = hub.downloads
        assert repo == REPO and kw["revision"] == REVISION
        assert "model.bin" in kw["allow_patterns"]

    def test_a_stray_file_refuses_before_anything_is_downloaded(self, monkeypatch, tmp_path, hf_home):
        hub = FakeHub(monkeypatch, tmp_path, [*REPO_FILES, "run.py"])
        with pytest.raises(wm.ModelDownloadError):
            wm._fetch_pinned_model(KEY, None)
        assert hub.downloads == []

    def test_not_cached_until_the_pinned_snapshot_is_complete(self, hf_home):
        assert not wm.is_whisper_model_cached(KEY)
        folder = hf_home / ("models--" + REPO.replace("/", "--")) / "snapshots" / REVISION
        folder.mkdir(parents=True)
        for name in ("config.json", "model.bin", "tokenizer.json", "preprocessor_config.json"):
            (folder / name).write_bytes(b"x")
        assert wm.is_whisper_model_cached(KEY)
