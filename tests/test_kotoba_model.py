"""The opt-in kotoba-whisper v2.0 model: registered like whisper-ja 1.5B (pinned, Japanese
only) but never asked for word timestamps, because its CTranslate2 config carries
large-v3's alignment heads on a 2-layer decoder. Fakes only: no network, no weights."""

import re
import sys
import types

import pytest

import whisper_transcribe
import forced_align
import memory_headroom
import mixed_language
import qwen3_native
import whisper_models as wm
from lib.errors import InvalidInputError
from services import compare_transcription_service, transcribe_pipeline, transcribe_service, transcribe_options_service
from tests.test_whisper_ja_model import FakeHub, hf_home  # noqa: F401  (fixture)

KEY = "kotoba-v2"
REPO, REVISION = wm.PINNED_CT2_REPOS[KEY]


class TestRegistry:
    def test_pinned_to_a_full_commit_sha_of_the_faster_repo(self):
        assert REPO == "kotoba-tech/kotoba-whisper-v2.0-faster"
        assert re.fullmatch(r"[0-9a-f]{40}", REVISION)

    def test_registered_everywhere_a_model_key_must_be(self):
        assert KEY in wm.WHISPER_MODELS
        assert KEY in transcribe_options_service._allowed_whisper_sizes()
        assert transcribe_pipeline._MODEL_DOWNLOAD_SIZES[KEY] == "~1.5 GB"
        assert memory_headroom.estimate_mb("whisper", KEY)
        assert KEY not in wm.LIVE_WHISPER_SIZES

    def test_never_the_default(self):
        assert wm.DEFAULT_WHISPER_SIZE != KEY
        assert transcribe_options_service.default_whisper_size() != KEY

    @pytest.mark.parametrize("language,allowed", [("ja", True), ("zh", False), ("ko", False)])
    def test_selectable_only_for_japanese(self, language, allowed):
        assert wm.whisper_model_fits_language(KEY, language) is allowed
        assert (wm.whisper_model_language_problem(KEY, language) is None) is allowed

    def test_the_picker_payload_carries_the_reason_for_chinese_and_korean(self):
        payload = wm.whisper_language_exclusions()[KEY]
        assert set(payload) == {"zh", "ko"} and "Japanese" in payload["zh"]

    def test_the_server_accepts_it_for_a_japanese_title_only(self, isolated_db):
        ja = isolated_db.create_drama(title_en="ja", source_language="ja")
        zh = isolated_db.create_drama(title_en="zh", source_language="zh")
        assert transcribe_service.update_transcribe_config(ja, whisper_size=KEY)["whisper_size"] == KEY
        with pytest.raises(InvalidInputError, match="Japanese"):
            transcribe_service.update_transcribe_config(zh, whisper_size=KEY)

    def test_a_saved_choice_falls_back_when_the_language_changes(self):
        assert transcribe_options_service.stored_whisper_size({"whisper_size": KEY, "source_language": "ja"}) == KEY
        assert transcribe_options_service.stored_whisper_size(
            {"whisper_size": KEY, "source_language": "zh"}) == transcribe_options_service.default_whisper_size()

    def test_compare_lists_it_and_refuses_it_for_chinese(self, isolated_db, monkeypatch):
        monkeypatch.setattr(compare_transcription_service, "_backend_problem", lambda *a: None)
        ja = {"id": 1, "source_language": "ja"}
        zh = {"id": 2, "source_language": "zh"}
        zh_id = isolated_db.create_drama(title_en="zh", source_language="zh")
        options = compare_transcription_service.get_options(zh_id)
        assert KEY in options["whisper_sizes"] and "zh" in options["whisper_exclusions"][KEY]
        assert compare_transcription_service._validate_candidate(ja, KEY, "whisper")[0] == KEY
        with pytest.raises(InvalidInputError):
            compare_transcription_service._validate_candidate(zh, KEY, "whisper")

    def test_only_kotoba_lacks_word_timestamps(self):
        assert not wm.supports_word_timestamps(KEY)
        for other in set(wm.WHISPER_MODELS) - {KEY}:
            assert wm.supports_word_timestamps(other)


class TestPinnedDownload:
    def test_downloads_the_pinned_revision_through_the_shared_file_check(self, monkeypatch, tmp_path, hf_home):
        hub = FakeHub(monkeypatch, tmp_path)
        wm._fetch_pinned_model(KEY, None)
        (repo, kw), = hub.downloads
        assert repo == REPO and kw["revision"] == REVISION
        assert "model.bin" in kw["allow_patterns"]

    def test_a_stray_file_refuses_before_anything_is_downloaded(self, monkeypatch, tmp_path, hf_home):
        hub = FakeHub(monkeypatch, tmp_path, ["config.json", "model.bin", "tokenizer.json",
                                              "vocabulary.json", "preprocessor_config.json", "run.py"])
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


class Segment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class NoWordTimestampsModel:
    """Fails the way kotoba's stock config does when word timestamps are requested."""
    seen = []

    def transcribe(self, audio, **kwargs):
        if kwargs.get("word_timestamps"):
            raise IndexError("alignment_heads point past the 2-layer decoder")
        NoWordTimestampsModel.seen.append(kwargs)
        return iter([Segment(0.0, 2.0, "こんにちは"), Segment(3.0, 5.0, "さようなら")]), None


@pytest.fixture
def fake_whisper(monkeypatch):
    monkeypatch.setattr(wm, "_whisper_model_cache", {})
    monkeypatch.setattr(memory_headroom, "before_load", lambda *a, **k: None)
    monkeypatch.setattr(wm, "_fetch_pinned_model", lambda size, token: "/pinned/" + size)
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(
        WhisperModel=lambda *a, **k: NoWordTimestampsModel()))
    NoWordTimestampsModel.seen = []


class TestSegmentTimingOnly:
    def test_transcribe_never_requests_word_timestamps(self, fake_whisper):
        result = whisper_transcribe.transcribe_for_timing("/a.wav", KEY, language="ja")
        assert [s["text"] for s in result] == ["こんにちは", "さようなら"]
        assert NoWordTimestampsModel.seen[0]["word_timestamps"] is False
        assert all("words" not in s for s in result)

    def test_other_models_still_ask_for_them(self, fake_whisper, monkeypatch):
        asked = []

        class Model:
            def transcribe(self, audio, **kwargs):
                asked.append(kwargs["word_timestamps"])
                return iter([]), None
        monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(
            WhisperModel=lambda *a, **k: Model()))
        whisper_transcribe.transcribe_for_timing("/a.wav", "large-v3-turbo", language="ja")
        assert asked == [True]

    def test_the_mixed_language_runner_does_not_request_them_either(self):
        calls = []

        class Model:
            def transcribe(self, wave, **kwargs):
                calls.append(kwargs["word_timestamps"])
                return iter([]), None
        import numpy as np
        run_span, _retry = mixed_language._span_runners(
            Model(), np.zeros(16000, dtype="float32"), 16000, "ja", 5, "", word_timestamps=False)
        span = types.SimpleNamespace(start_s=0.0, end_s=1.0)
        Model.detect_language = lambda self, wave: ("ja", 1.0, [("ja", 1.0)])
        run_span(span)
        assert calls == [False]


class _Rep:
    job_id = None

    def __init__(self):
        self.stages = []

    def progress(self, frac, message=""):
        pass

    def stage(self, message, frac=0.0):
        self.stages.append(message)

        class Ticker:
            def start(self):
                return self

            def stop(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        return Ticker()

    def cancelled(self):
        return False

    def raise_if_cancelled(self):
        pass


SEGMENTS = [{"start": 0.0, "end": 2.0, "text": "a"}, {"start": 3.0, "end": 5.0, "text": "b"}]


class TestRefinement:
    def run(self, monkeypatch, *, problem=None, refine=None, mixed=False):
        monkeypatch.setattr(qwen3_native, "aligner_language_problem", lambda lang: problem)
        monkeypatch.setattr(qwen3_native, "transformers_problem", lambda feature="": None)
        if refine:
            monkeypatch.setattr(forced_align, "refine_whisper_segments", refine)
        notices = []
        out = transcribe_pipeline._refine_without_word_timestamps(
            SEGMENTS, "/a.wav", "ja", False, _Rep(), {}, lambda *a: None, notices, mixed, 0.99)
        return out, notices

    def test_the_aligner_times_replace_the_segment_times(self, monkeypatch):
        out, notices = self.run(monkeypatch, refine=lambda path, segs, lang, **kw: [
            {**s, "start": s["start"] + 0.25} for s in segs])
        assert [s["start"] for s in out] == [0.25, 3.25] and not notices
        assert all("flag" not in s for s in out)

    def test_without_the_aligner_segment_times_are_kept_and_the_reason_is_reported(self, monkeypatch):
        out, notices = self.run(
            monkeypatch, problem="Japanese forced alignment needs the nagisa package.",
            refine=lambda *a, **k: pytest.fail("must not load the aligner"))
        assert [(s["start"], s["end"]) for s in out] == [(0.0, 2.0), (3.0, 5.0)]
        assert not any("flag" in s for s in out)
        assert "Line timing was not refined" in notices[0] and "nagisa" in notices[0]

    @pytest.mark.parametrize("error", [
        qwen3_native.TransformersUnavailableError("transformers is too old"),
        wm.ModelDownloadError("Couldn't download the aligner"), ValueError("line too long")])
    def test_an_aligner_failure_never_loses_the_transcription(self, monkeypatch, error):
        def refine(*a, **k):
            raise error
        out, notices = self.run(monkeypatch, refine=refine)
        assert [s["text"] for s in out] == ["a", "b"] and notices
        assert not any("flag" in s for s in out)

    def test_a_mixed_language_run_is_not_sent_to_the_aligner(self, monkeypatch):
        out, notices = self.run(monkeypatch, mixed=True,
                                refine=lambda *a, **k: pytest.fail("one language per run"))
        assert not any("flag" in s for s in out) and "Mixed" in notices[0]

    def test_a_cancel_propagates_and_frees_the_models(self, monkeypatch):
        import background_jobs
        freed = []
        monkeypatch.setattr(wm, "release_gpu_models", lambda: freed.append(1))

        def refine(*a, **k):
            raise background_jobs.JobCancelled("x")
        with pytest.raises(background_jobs.JobCancelled):
            self.run(monkeypatch, refine=refine)
        assert freed


class TestGrouping:
    def test_segments_are_grouped_to_the_chunk_size_the_aligner_keeps_in_sync(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(forced_align, "refine_segment_timing",
                            lambda path, groups, lang, **kw: seen.update(groups=groups) or [])
        segs = [{"start": t, "end": t + 10, "text": "x"} for t in range(0, 150, 15)]
        forced_align.refine_whisper_segments("/a.wav", segs, "ja")
        assert [len(g) for g in seen["groups"]] == [4, 4, 2]
        assert all(g[-1]["end"] - g[0]["start"] <= forced_align.MAX_CHUNK_SECONDS for g in seen["groups"])


class TestPipelineWiring:
    def run(self, monkeypatch, tmp_path, size, aligner_problem):
        transcribe_calls = []
        monkeypatch.setattr(wm, "release_gpu_models", lambda: None)
        monkeypatch.setattr(wm, "is_whisper_model_cached", lambda s: True)
        monkeypatch.setattr(wm, "load_whisper_model", lambda *a, **k: None)
        monkeypatch.setattr(wm, "get_whisper_device_info", lambda *a, **k: {})
        monkeypatch.setattr(transcribe_pipeline, "_audio_duration_seconds", lambda p: 6.0)
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: transcribe_calls.append(a[1]) or [dict(s) for s in SEGMENTS])
        monkeypatch.setattr(qwen3_native, "aligner_language_problem", lambda lang: aligner_problem)
        monkeypatch.setattr(qwen3_native, "transformers_problem", lambda feature="": None)
        monkeypatch.setattr(forced_align, "refine_whisper_segments",
                            lambda path, segs, lang, **kw: [{**s, "end": s["end"] + 0.5} for s in segs])
        out = transcribe_pipeline._transcribe_pipeline(
            _Rep(), str(tmp_path / "a.wav"), "whisper", None, "ja", "simplified",
            size, 5, 300, 0.5, False, "auto", False, False, False, None, "", False,
            "whisper", "whisper_diff")
        return out, transcribe_calls

    def test_kotoba_lines_are_timed_by_the_aligner_when_installed(self, monkeypatch, tmp_path):
        out, calls = self.run(monkeypatch, tmp_path, KEY, None)
        assert calls == [KEY]
        assert [ln.end for ln in out["lines"]] == [2.5, 5.5]
        assert not any(ln.flag for ln in out["lines"]) and not out["coverage_warning"]

    def test_kotoba_without_the_aligner_keeps_segment_times_and_reports_why(self, monkeypatch, tmp_path):
        out, _ = self.run(monkeypatch, tmp_path, KEY, "Japanese forced alignment needs nagisa.")
        assert [ln.end for ln in out["lines"]] == [2.0, 5.0]
        assert not any(ln.flag for ln in out["lines"])
        assert "Line timing was not refined" in out["coverage_warning"] and "nagisa" in out["coverage_warning"]

    def test_other_models_never_touch_the_aligner(self, monkeypatch, tmp_path):
        out, _ = self.run(monkeypatch, tmp_path, "large-v3-turbo", "should not matter")
        assert [ln.end for ln in out["lines"]] == [2.0, 5.0] and not out["coverage_warning"]


class TestRefinementNeverLosesTheTranscription:
    """The aligner is optional: whatever it raises, Whisper's finished segments are kept."""

    def refine(self, monkeypatch, *, load=None, align=None, extract=None):
        monkeypatch.setattr(qwen3_native, "aligner_language_problem", lambda lang: None)
        monkeypatch.setattr(qwen3_native, "transformers_problem", lambda feature="": None)
        monkeypatch.setattr(wm, "release_gpu_models", lambda: None)

        class Aligner:
            def align(self, audio, text, language):
                if align:
                    raise align
                return []

        def load_aligner(**kw):
            if load:
                raise load
            return Aligner()
        monkeypatch.setattr(forced_align, "load_qwen3_aligner", load_aligner)

        def extract_slice(*a):
            if extract:
                raise extract
            open(a[3], "wb").close()
        monkeypatch.setattr(forced_align, "_extract_audio_slice", extract_slice)
        notices = []
        out = transcribe_pipeline._refine_without_word_timestamps(
            SEGMENTS, "/a.wav", "ja", True, _Rep(), {}, lambda *a: None, notices, False, 0.99)
        return out, notices

    @pytest.mark.parametrize("fault", [
        {"load": memory_headroom.HeadroomError("Not enough graphics memory to load the aligner.")},
        {"align": RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")},
        {"extract": __import__("subprocess").CalledProcessError(1, ["ffmpeg", "-i", "C:\\Users\\me\\a.wav"])},
    ])
    def test_any_failure_keeps_the_segments_and_reports_the_notice(self, monkeypatch, fault):
        out, notices = self.refine(monkeypatch, **fault)
        assert out == SEGMENTS
        assert notices and notices[0].startswith("Line timing was not refined (")
        assert "C:\\" not in notices[0] and "ffmpeg" not in notices[0]

    def test_a_cancel_still_propagates(self, monkeypatch):
        import background_jobs
        with pytest.raises(background_jobs.JobCancelled):
            self.refine(monkeypatch, load=background_jobs.JobCancelled("x"))

    def test_whisper_is_unloaded_before_the_aligner_loads(self, monkeypatch):
        events = []
        monkeypatch.setattr(qwen3_native, "aligner_language_problem", lambda lang: None)
        monkeypatch.setattr(qwen3_native, "transformers_problem", lambda feature="": None)
        monkeypatch.setattr(wm, "release_gpu_models", lambda: events.append("release"))
        monkeypatch.setattr(forced_align, "refine_whisper_segments",
                            lambda *a, **k: events.append("refine") or list(SEGMENTS))
        transcribe_pipeline._refine_without_word_timestamps(
            SEGMENTS, "/a.wav", "ja", True, _Rep(), {}, lambda *a: None, [], False, 0.99)
        assert events == ["release", "refine"]

    def test_an_import_error_path_never_reaches_the_notice(self, monkeypatch):
        def refine(*a, **k):
            raise ImportError("cannot import name x from /usr/lib/python3/site-packages/transformers/y.py")
        monkeypatch.setattr(qwen3_native, "aligner_language_problem", lambda lang: None)
        monkeypatch.setattr(qwen3_native, "transformers_problem", lambda feature="": None)
        monkeypatch.setattr(wm, "release_gpu_models", lambda: None)
        monkeypatch.setattr(forced_align, "refine_whisper_segments", refine)
        notices = []
        transcribe_pipeline._refine_without_word_timestamps(
            SEGMENTS, "/a.wav", "ja", True, _Rep(), {}, lambda *a: None, notices, False, 0.99)
        assert notices and "site-packages" not in notices[0] and "/usr" not in notices[0]


class TestOnlyForWhisperTranscripts:
    def test_a_supplied_transcript_is_not_refined(self, monkeypatch, tmp_path):
        refined = []
        monkeypatch.setattr(wm, "release_gpu_models", lambda: None)
        monkeypatch.setattr(wm, "is_whisper_model_cached", lambda s: True)
        monkeypatch.setattr(wm, "load_whisper_model", lambda *a, **k: None)
        monkeypatch.setattr(wm, "get_whisper_device_info", lambda *a, **k: {})
        monkeypatch.setattr(transcribe_pipeline, "_audio_duration_seconds", lambda p: 6.0)
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [dict(s) for s in SEGMENTS])
        monkeypatch.setattr(transcribe_pipeline, "_refine_without_word_timestamps",
                            lambda *a, **k: refined.append(1) or a[0])
        out = transcribe_pipeline._transcribe_pipeline(
            _Rep(), str(tmp_path / "a.wav"), "have_transcript", "line one\nline two", "ja",
            "simplified", KEY, 5, 300, 0.5, False, "auto", False, False, False, None, "", False,
            "whisper", "whisper_diff")
        assert out["lines"] and refined == []
