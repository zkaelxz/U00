"""The opt-in anime_whisper backend (Japanese only, personal non-commercial use): decode
settings, word placement, the pinned download's file-set and checksum refusals, and the
language rule on the server. Fakes only: no network, no weights, no real audio."""
import hashlib
import json
import os
import sys
import types

import numpy as np
import pytest

import asr_backend as ab
import whisper_models as wm
from lib.errors import InvalidInputError
from services import asr_options_service, compare_transcription_service, timing_check_service
from services import transcribe_pipeline, transcribe_service

GOOD = ["config.json", "model.bin", "tokenizer.json", "vocabulary.json",
        "preprocessor_config.json", "README.md", ".gitattributes"]
PIN = wm.ANIME_WHISPER_PIN
WEIGHTS = b"fake weights"


class FakeModel:
    """Records the transcribe() call; one segment per clip, starting where the clip does."""
    def __init__(self, texts):
        self.texts = list(texts)
        self.kwargs = None

    def transcribe(self, audio, **kw):
        self.kwargs = kw
        clips = kw["clip_timestamps"]
        segs = [types.SimpleNamespace(start=clips[2 * n], text=text, tokens=[n + 1])
                for n, text in enumerate(self.texts)]
        return iter(segs), types.SimpleNamespace()


def vad(spans):
    return lambda audio, sr: spans


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(ab, "load_audio_16k", lambda path: np.zeros(16000 * 90, dtype="float32"))
    holder = {}

    def use(texts, words_per_window):
        holder["model"] = FakeModel(texts)
        loads = []
        monkeypatch.setattr(wm, "load_whisper_model",
                            lambda name, use_gpu=False, **kw: loads.append(name) or holder["model"])
        monkeypatch.setattr(wm, "get_whisper_device_info",
                            lambda name, use_gpu=False, **kw: {"device": "cpu"})
        queue = list(words_per_window)
        monkeypatch.setattr(ab.AnimeWhisperBackend, "_align",
                            staticmethod(lambda model, audio, sr, window, tokens: queue.pop(0)))
        holder["loads"] = loads
        return holder
    return use


def word(text, start, end):
    return {"word": text, "start": start, "end": end}


def run(spans, **kw):
    return ab.AnimeWhisperBackend().transcribe("/a.wav", "ja", vad_fn=vad(spans), **kw)


class TestDecode:
    def test_decode_settings_and_no_prompt(self, backend):
        h = backend(["こんにちは"], [[word("こんにちは", 1.0, 2.0)]])
        run([(1.0, 2.0)], prompt="Vocabulary: 白鶴", batch_size=4, refine_timing=True)
        kw = h["model"].kwargs
        assert "initial_prompt" not in kw and "hotwords" not in kw
        assert kw["without_timestamps"] is True and kw["word_timestamps"] is False
        assert kw["condition_on_previous_text"] is False and kw["vad_filter"] is False
        assert kw["language"] == "ja" and kw["beam_size"] == 5
        assert "no_repeat_ngram_size" not in kw
        assert h["loads"] == [wm.ANIME_WHISPER_KEY]

    def test_one_clip_pair_per_window_capped_at_30_seconds(self, backend):
        h = backend(["あ", "い"], [[word("あ", 1.0, 2.0)], [word("い", 50.0, 51.0)]])
        run([(1.0, 2.0), (50.0, 51.0)])
        clips = h["model"].kwargs["clip_timestamps"]
        assert len(clips) == 4 and clips[2] > clips[1] + 3.0
        assert all(clips[i + 1] - clips[i] <= 30.0 for i in (0, 2))

    def test_repeat_guard_only_when_the_title_turned_it_on(self, backend):
        h = backend(["あ"], [[word("あ", 1.0, 2.0)]])
        run([(1.0, 2.0)], repeat_guard=True)
        assert h["model"].kwargs["no_repeat_ngram_size"] == 3

    @pytest.mark.parametrize("language", ["zh", "ko", "en", None])
    def test_japanese_only(self, language, backend):
        backend([], [])
        with pytest.raises(ValueError, match="Japanese"):
            ab.AnimeWhisperBackend().transcribe("/a.wav", language, vad_fn=vad([(1.0, 2.0)]))

    def test_registered_as_a_backend(self):
        assert ab.get_backend("anime_whisper").name == "anime_whisper"


class TestPlacement:
    def test_words_go_to_the_speech_span_holding_their_midpoint(self, backend):
        words = [word("おはよう", 1.2, 2.0), word("ね", 2.0, 2.3),
                 word("うん", 4.5, 5.0), word("。", 5.0, 5.1)]
        backend(["おはようねうん。"], [words])
        out = run([(1.0, 2.5), (4.0, 5.5)])
        assert [l["text"] for l in out] == ["おはようね", "うん。"]
        assert out[0]["start"] == pytest.approx(1.2) and out[0]["end"] == pytest.approx(2.3)
        assert out[1]["start"] == pytest.approx(4.5)
        for line in out:
            assert "".join(w["word"] for w in line["words"]) == line["text"]

    def test_a_line_is_never_wider_than_its_span(self):
        lines = ab.place_words_in_spans([word("あ", 1.5, 3.5)], [(2.0, 3.0)])
        assert lines[0]["start"] >= 2.0 and lines[0]["end"] <= 3.0

    def test_a_word_in_a_gap_joins_the_nearest_span(self):
        lines = ab.place_words_in_spans([word("あ", 2.6, 2.8)], [(1.0, 2.0), (3.0, 4.0)])
        assert len(lines) == 1 and lines[0]["start"] == pytest.approx(3.0)

    def test_punctuation_only_lines_go_but_kana_breaths_stay(self, backend):
        words = [word("…", 1.0, 1.5), word("はぁ", 4.0, 4.8)]
        backend(["…はぁ"], [words])
        out = run([(0.9, 1.6), (3.9, 5.0)])
        assert [l["text"] for l in out] == ["はぁ"]

    def test_failed_alignment_keeps_the_text_as_a_flagged_window_line(self, backend, monkeypatch):
        backend(["こんにちは"], [])

        def boom(model, audio, sr, window, tokens):
            raise RuntimeError("align failed")
        monkeypatch.setattr(ab.AnimeWhisperBackend, "_align", staticmethod(boom))
        out = run([(1.0, 3.0)])
        assert out[0]["text"] == "こんにちは" and out[0]["flag"] == "timing_uncertain"


class FakeHub:
    def __init__(self, monkeypatch, names=GOOD, weights=WEIGHTS, extra_files=(), sha=None):
        self.names, self.downloads, self.lists = names, [], 0
        hub = self
        pinned = sha or PIN["revision"]

        class HfApi:
            def model_info(self, repo, revision=None, token=None, timeout=None):
                assert timeout, "every outbound call needs a timeout"
                hub.lists += 1
                return types.SimpleNamespace(
                    sha=pinned, siblings=[types.SimpleNamespace(rfilename=n) for n in hub.names])

        def snapshot_download(repo, **kw):
            hub.downloads.append((repo, kw))
            dest = kw["local_dir"]
            os.makedirs(os.path.join(dest, ".cache"), exist_ok=True)
            for name in kw["allow_patterns"]:
                data = weights if name == "model.bin" else b'{"alignment_heads": [[7, 0]]}' \
                    if name == "config.json" else b"x"
                with open(os.path.join(dest, name), "wb") as f:
                    f.write(data)
            for name in extra_files:
                os.symlink(os.path.join(dest, "model.bin"), os.path.join(dest, name))
            return dest

        monkeypatch.setitem(sys.modules, "huggingface_hub",
                            types.SimpleNamespace(HfApi=HfApi, snapshot_download=snapshot_download))


@pytest.fixture
def model_home(monkeypatch, tmp_path):
    monkeypatch.setattr(wm, "anime_whisper_folder", lambda: str(tmp_path / "anime_whisper"))
    monkeypatch.setitem(PIN, "model_bin_sha256", hashlib.sha256(WEIGHTS).hexdigest())
    return tmp_path / "anime_whisper"


class TestPinnedDownload:
    def test_pin_is_a_full_sha_of_the_ct2_build(self):
        assert PIN["repo"] == "quantumcookie/anime-whisper-ct2-fp16"
        assert PIN["revision"] == "8ad0b0053dd0af6e108eea290e6f50a3aea49008"
        assert PIN["alignment_heads"] == [[1, 14], [1, 8], [1, 12]]
        assert len(PIN["model_bin_sha256"]) == 64

    def test_downloads_the_pinned_revision_allowed_files_only(self, monkeypatch, model_home):
        hub = FakeHub(monkeypatch)
        assert wm._fetch_anime_whisper("tok") == str(model_home)
        (repo, kw), = hub.downloads
        assert repo == PIN["repo"] and kw["revision"] == PIN["revision"]
        assert sorted(kw["allow_patterns"]) == [
            "config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"]

    def test_installs_calibrated_heads_keeps_the_original_config_and_needs_no_network_after(
            self, monkeypatch, model_home):
        hub = FakeHub(monkeypatch)
        wm._fetch_anime_whisper(None)
        assert json.loads((model_home / "config.json").read_text())["alignment_heads"] == PIN["alignment_heads"]
        assert json.loads((model_home / "config.json.orig").read_text())["alignment_heads"] == [[7, 0]]
        assert not (model_home / ".cache").exists() and not os.path.exists(str(model_home) + ".partial")
        assert wm.is_whisper_model_cached(wm.ANIME_WHISPER_KEY)
        wm._fetch_anime_whisper(None)
        assert len(hub.downloads) == 1

    @pytest.mark.parametrize("stray", ["loader.py", "weights.pt", "pytorch_model.bin", "sub/model.bin"])
    def test_a_stray_file_refuses_before_anything_is_downloaded(self, monkeypatch, model_home, stray):
        hub = FakeHub(monkeypatch, GOOD + [stray])
        with pytest.raises(wm.ModelDownloadError, match="allowlist") as err:
            wm._fetch_anime_whisper(None)
        assert hub.downloads == [] and not model_home.exists()
        assert "http" not in str(err.value)

    def test_a_listing_missing_a_required_file_is_refused(self, monkeypatch, model_home):
        hub = FakeHub(monkeypatch, [n for n in GOOD if n != "tokenizer.json"])
        with pytest.raises(wm.ModelDownloadError, match="tokenizer.json"):
            wm._fetch_anime_whisper(None)
        assert hub.downloads == []

    def test_a_checksum_mismatch_discards_the_download(self, monkeypatch, model_home):
        FakeHub(monkeypatch, weights=b"tampered")
        with pytest.raises(wm.ModelDownloadError, match="checksum"):
            wm._fetch_anime_whisper(None)
        assert not model_home.exists() and not os.path.exists(str(model_home) + ".partial")
        assert not wm.is_whisper_model_cached(wm.ANIME_WHISPER_KEY)

    def test_a_repository_that_does_not_return_the_pinned_revision_is_refused(self, monkeypatch, model_home):
        hub = FakeHub(monkeypatch, sha="0" * 40)
        with pytest.raises(wm.ModelDownloadError, match="pinned revision"):
            wm._fetch_anime_whisper(None)
        assert hub.downloads == []

    def test_a_link_among_the_downloaded_files_is_refused(self, monkeypatch, model_home):
        FakeHub(monkeypatch, extra_files=["tokenizer_alias.json"])
        with pytest.raises(wm.ModelDownloadError, match="outside"):
            wm._fetch_anime_whisper(None)
        assert not model_home.exists()

    def test_a_tampered_cached_model_is_not_trusted(self, monkeypatch, model_home):
        FakeHub(monkeypatch)
        wm._fetch_anime_whisper(None)
        (model_home / "model.bin").write_bytes(WEIGHTS + b"x")
        assert not wm.is_whisper_model_cached(wm.ANIME_WHISPER_KEY)

    def test_a_download_failure_is_a_model_download_error_without_the_raw_text(self, monkeypatch, model_home):
        FakeHub(monkeypatch)

        def broken(repo, **kw):
            raise OSError("disk /secret/path full")
        sys.modules["huggingface_hub"].snapshot_download = broken
        with pytest.raises(wm.ModelDownloadError) as err:
            wm._fetch_anime_whisper(None)
        assert "/secret" not in str(err.value)


class TestNotASize:
    def test_not_a_selectable_size_or_pinned_size_or_default(self):
        key = wm.ANIME_WHISPER_KEY
        assert key not in wm.WHISPER_MODELS and key not in wm.PINNED_CT2_REPOS
        assert key not in wm.LIVE_WHISPER_SIZES
        assert key not in transcribe_service._allowed_whisper_sizes()
        assert wm.DEFAULT_WHISPER_SIZE != key

    def test_memory_headroom_knows_it(self):
        import memory_headroom
        assert memory_headroom.estimate_mb("whisper", wm.ANIME_WHISPER_KEY)


class TestServerRules:
    def test_offered_and_never_default(self):
        assert "anime_whisper" in asr_options_service.ASR_BACKEND_CHOICES
        assert asr_options_service.stored_asr_backend({}) == "whisper"
        assert "anime_whisper" in transcribe_pipeline.VAD_BACKENDS
        assert "anime_whisper" in timing_check_service.NO_WHISPER_PASS_BACKENDS

    def test_the_server_accepts_it_for_a_japanese_title_only(self, isolated_db):
        ja = isolated_db.create_drama(title_en="ja", source_language="ja")
        zh = isolated_db.create_drama(title_en="zh", source_language="zh")
        saved = transcribe_service.update_transcribe_config(ja, asr_backend_choice="anime_whisper")
        assert saved["asr_backend_choice"] == "anime_whisper"
        with pytest.raises(InvalidInputError, match="Japanese"):
            transcribe_service.update_transcribe_config(zh, asr_backend_choice="anime_whisper")

    def test_a_saved_choice_falls_back_with_a_notice_when_the_language_changes(self):
        drama = {"asr_backend_choice": "anime_whisper", "source_language": "zh"}
        assert asr_options_service.stored_asr_backend(drama) == "whisper"
        assert "Japanese" in asr_options_service.removed_asr_backend_notice(drama)
        assert asr_options_service.removed_asr_backend_notice(
            {"asr_backend_choice": "anime_whisper", "source_language": "ja"}) is None

    def test_a_run_for_another_language_is_refused(self):
        with pytest.raises(InvalidInputError, match="Japanese"):
            transcribe_service._check_run_choices("whisper", "anime_whisper", "whisper_diff", "ko")

    def test_compare_refuses_it_for_other_languages(self):
        assert "Japanese" in compare_transcription_service._backend_problem("anime_whisper", "zh")

    def test_the_cli_does_not_offer_it_but_still_offers_the_others(self, monkeypatch):
        import cli
        captured = {}
        monkeypatch.setattr(cli, "cmd_transcribe", lambda a: captured.setdefault("args", a))
        monkeypatch.setattr(sys, "argv", ["cli.py", "transcribe", "--id", "1",
                                          "--asr-backend", "qwen3_asr_long"])
        cli.main()
        assert captured["args"].asr_backend == "qwen3_asr_long"
        monkeypatch.setattr(sys, "argv", ["cli.py", "transcribe", "--id", "1",
                                          "--asr-backend", "anime_whisper"])
        with pytest.raises(SystemExit):
            cli.main()
