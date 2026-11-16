"""tools/release_check.py: manifest refusals, DER and chrF scoring, regression flags, dry run, paid-engine
gate, one failed clip not stopping the run, report holds no paths. No models, no GPU, no network."""
import json
import os

import pytest

from tools import release_check as rc

SRT = "1\n00:00:01,000 --> 00:00:02,000\nhello\n\n2\n00:00:03,000 --> 00:00:04,000\nworld\n"


def _write(tmp_path, clips):
    (tmp_path / "a.wav").write_bytes(b"RIFF")
    (tmp_path / "r.srt").write_text(SRT, encoding="utf-8")
    (tmp_path / "m.json").write_text(json.dumps({"clips": clips}), encoding="utf-8")
    return str(tmp_path / "m.json")


def test_manifest_needs_a_reference_and_unique_ids(tmp_path):
    with pytest.raises(rc.Refusal):
        rc.load_manifest(_write(tmp_path, [{"id": "a", "kind": "transcription", "audio": "a.wav", "language": "zh"}]))
    clip = {"id": "a", "kind": "transcription", "audio": "a.wav", "language": "zh", "reference_srt": "r.srt"}
    with pytest.raises(rc.Refusal):
        rc.load_manifest(_write(tmp_path, [clip, dict(clip)]))
    loaded = rc.load_manifest(_write(tmp_path, [clip]))
    assert loaded["clips"][0]["audio"] == str(tmp_path / "a.wav")


def test_manifest_missing_file_is_refused(tmp_path):
    clip = {"id": "a", "kind": "transcription", "audio": "gone.wav", "language": "zh", "reference_text": "x"}
    with pytest.raises(rc.Refusal):
        rc.load_manifest(_write(tmp_path, [clip]))


def test_der_perfect_after_speaker_rename():
    ref = [{"start": 0, "end": 2, "speaker": "A"}, {"start": 2, "end": 4, "speaker": "B"}]
    hyp = [{"start": 0, "end": 2, "speaker": "S1"}, {"start": 2, "end": 4, "speaker": "S0"}]
    der, miss, fa, conf = rc.diarization_error_rate(ref, hyp)
    assert (der, miss, fa, conf) == (0, 0, 0, 0)


def test_der_counts_miss_false_alarm_and_confusion():
    ref = [{"start": 0, "end": 4, "speaker": "A"}, {"start": 4, "end": 8, "speaker": "B"}]
    hyp = [{"start": 0, "end": 3, "speaker": "x"},           # 1 s of A missed
           {"start": 4, "end": 6, "speaker": "x"},           # 2 s of B heard as the same speaker as A
           {"start": 6, "end": 8, "speaker": "y"},
           {"start": 8, "end": 9, "speaker": "y"}]           # 1 s false alarm
    der, miss, fa, conf = rc.diarization_error_rate(ref, hyp)
    assert miss == pytest.approx(1 / 8, abs=0.01)
    assert fa == pytest.approx(1 / 8, abs=0.01)
    assert conf == pytest.approx(2 / 8, abs=0.01)
    assert der == pytest.approx(miss + fa + conf)


def test_der_with_many_speakers_uses_greedy_without_error():
    ref = [{"start": i, "end": i + 1, "speaker": f"r{i}"} for i in range(10)]
    hyp = [{"start": i, "end": i + 1, "speaker": f"h{i}"} for i in range(10)]
    assert rc.diarization_error_rate(ref, hyp)[0] == 0


def test_parse_rttm():
    text = "SPEAKER f 1 1.50 2.00 <NA> <NA> alice <NA> <NA>\nSPEAKER f 1 4.00 1.00 <NA> <NA> bob <NA> <NA>\n"
    assert rc.parse_rttm(text) == [{"start": 1.5, "end": 3.5, "speaker": "alice"},
                                   {"start": 4.0, "end": 5.0, "speaker": "bob"}]


def test_chrf_pads_short_replies_as_missing():
    full, _ = rc.chrf_score(["hello there", "good night"], ["hello there", "good night"])
    short, _ = rc.chrf_score(["hello there", ""], ["hello there", "good night"])
    assert full > short


def test_worse_direction_depends_on_metric():
    assert rc.worse("der", 0.30, 0.20) and not rc.worse("der", 0.10, 0.20)
    assert rc.worse("cer", 0.80, 0.90) and not rc.worse("cer", 0.95, 0.90)
    assert not rc.worse("cer", 0.89, 0.90)  # inside the noise margin
    assert not rc.worse("cer", None, 0.90)


def _run(results):
    return {"when": "2026-11-09 10:00", "machine": "PC", "gpu": "3080 Ti", "whisper_model": "large-v3",
            "results": results}


def test_report_flags_regression_and_failure_without_paths():
    prev = _run([{"id": "t1", "kind": "transcription", "metric": "cer", "score": 0.9}])
    now = _run([{"id": "t1", "kind": "transcription", "metric": "cer", "score": 0.8, "speed_x": 12.0,
                 "vram_mb": 4000, "detail": "ok"},
                {"id": "d1", "kind": "diarization", "score": None, "metric": None,
                 "error": "RuntimeError: failed to open C:\\Users\\Example\\secret.wav"}])
    report = rc.format_report(now, prev)
    assert "WORSE" in report and "FAILED" in report and "1 failed, 1 worse" in report
    assert report.startswith(rc.REPORT_TITLE)
    assert "Example" not in report


def test_dry_run_loads_and_writes_nothing(tmp_path, monkeypatch, capsys):
    clip = {"id": "a", "kind": "transcription", "audio": "a.wav", "language": "zh", "reference_text": "x"}
    monkeypatch.setattr(rc, "run_all", lambda *a: pytest.fail("a dry run must not run clips"))
    out = tmp_path / "out"
    assert rc.main([_write(tmp_path, [clip]), "--out", str(out)]) == 0
    assert not out.exists()


def test_failed_clip_does_not_stop_the_rest(tmp_path, monkeypatch):
    clips = [{"id": "a", "kind": "transcription", "audio": "a.wav", "language": "zh", "reference_text": "x"},
             {"id": "b", "kind": "transcription", "audio": "a.wav", "language": "zh", "reference_text": "x"}]
    manifest = rc.load_manifest(_write(tmp_path, clips))
    calls = []

    def fake(clip, opts):
        calls.append(clip["id"])
        if clip["id"] == "a":
            raise RuntimeError("model missing")
        return {"score": 0.9, "metric": "cer"}

    monkeypatch.setattr(rc, "run_transcription", fake)
    monkeypatch.setattr(rc, "gpu_name", lambda: None)
    opts = rc.argparse.Namespace(use_gpu=False, max_usd=1.0, whisper_model="large-v3")
    result = rc.run_all(manifest, opts)
    assert calls == ["a", "b"]
    assert result["results"][0]["error"].startswith("RuntimeError") and result["results"][1]["score"] == 0.9


def test_paid_engine_needs_allow_paid(tmp_path):
    clip = {"id": "t", "kind": "translation", "source_srt": "r.srt", "reference_srt": "r.srt", "language": "zh",
            "engine": "claude"}
    manifest = rc.load_manifest(_write(tmp_path, [clip]))
    opts = rc.argparse.Namespace(engine="ollama", allow_paid=False, max_usd=1.0)
    with pytest.raises(RuntimeError, match="allow-paid"):
        rc.run_translation(manifest["clips"][0], opts, rc.Spend(1.0))


def test_translation_scores_with_a_fake_engine(tmp_path, monkeypatch):
    clip = {"id": "t", "kind": "translation", "source_srt": "r.srt", "reference_srt": "r.srt", "language": "zh"}
    manifest = rc.load_manifest(_write(tmp_path, [clip]))

    class Engine:
        model = "fake"

        def translate_batch(self, lines, context):
            return list(lines)

    monkeypatch.setattr(rc.translate_service, "resolve_api_key", lambda name: "local")
    monkeypatch.setattr(rc.engine_registry, "get_engine", lambda *a, **k: Engine())
    opts = rc.argparse.Namespace(engine="ollama", allow_paid=False, max_usd=1.0)
    row = rc.run_translation(manifest["clips"][0], opts, rc.Spend(1.0))
    assert row["score"] == pytest.approx(1.0)


def test_manifest_refuses_an_unknown_title_setting_or_backend(tmp_path):
    clip = {"id": "a", "kind": "transcription", "audio": "a.wav", "language": "zh", "reference_srt": "r.srt",
            "title_settings": {"min_silence_ms": 300, "min_silense_ms": 300}}
    with pytest.raises(rc.Refusal, match="title_settings"):
        rc.load_manifest(_write(tmp_path, [clip]))
    (tmp_path / "r.rttm").write_text("", encoding="utf-8")
    speakers = {"id": "d", "kind": "diarization", "audio": "a.wav", "reference_rttm": "r.rttm", "backend": "other"}
    with pytest.raises(rc.Refusal, match="backend"):
        rc.load_manifest(_write(tmp_path, [speakers]))


def _app_clip(tmp_path):
    clip = {"id": "a", "kind": "transcription", "audio": "a.wav", "language": "zh", "reference_srt": "r.srt",
            "title_settings": {"transcript_mode": "whisper", "min_silence_ms": 300}, "initial_prompt": "名字。"}
    return rc.load_manifest(_write(tmp_path, [clip]))["clips"][0]


def test_title_settings_run_the_app_pipeline_and_score_its_lines(tmp_path, monkeypatch):
    import core
    clip = _app_clip(tmp_path)
    built = {}

    def fake_args(settings, audio, scratch, use_gpu, prompt):
        built.update(settings=settings, audio=audio, use_gpu=use_gpu, prompt=prompt, scratch=scratch)
        return ("args",)

    monkeypatch.setattr(rc.transcribe_service, "worker_args_for_settings", fake_args)
    monkeypatch.setattr(rc, "_transcribe_in_child", lambda args, fixed=False: {
        "lines": [core.Line(idx=0, start=1, end=2, zh="hello"), core.Line(idx=1, start=3, end=4, zh="world")],
        "raw_backend": "whisper", "raw_model": "large-v3"})
    row = rc.run_transcription(clip, rc.argparse.Namespace(use_gpu=False, whisper_model="large-v3"))
    assert row["score"] == pytest.approx(1.0)
    assert built["settings"] == {"transcript_mode": "whisper", "min_silence_ms": 300, "source_language": "zh"}
    assert built["prompt"] == "名字。" and built["audio"] == clip["audio"] and built["use_gpu"] is False
    assert not os.path.exists(built["scratch"])
    assert "2 lines, app pipeline" in row["detail"]


def test_fixed_decoding_is_checked_and_reaches_the_child(tmp_path, monkeypatch):
    base = {"id": "a", "kind": "transcription", "audio": "a.wav", "language": "zh", "reference_srt": "r.srt"}
    for bad in ({**base, "fixed_decoding": True},
                {**base, "title_settings": {"min_silence_ms": 300}, "fixed_decoding": "yes"}):
        with pytest.raises(rc.Refusal, match="fixed_decoding"):
            rc.load_manifest(_write(tmp_path, [bad]))
    clip = rc.load_manifest(_write(tmp_path, [{**base, "title_settings": {"min_silence_ms": 300},
                                               "fixed_decoding": True}]))["clips"][0]
    seen = []
    monkeypatch.setattr(rc.transcribe_service, "worker_args_for_settings", lambda *a: ())
    monkeypatch.setattr(rc, "_transcribe_in_child", lambda args, fixed=False: seen.append(fixed) or {
        "lines": [], "raw_backend": "whisper", "raw_model": "large-v3"})
    rc.run_transcription(clip, rc.argparse.Namespace(use_gpu=False, whisper_model="large-v3"))
    assert seen == [True]


def test_pin_temperature_overrides_what_the_caller_passes():
    from tools import release_check_worker

    class Model:
        def transcribe(self, audio, **kwargs):
            return kwargs

    release_check_worker.pin_temperature(Model)
    assert Model().transcribe("a.wav", beam_size=5, temperature=[0.0, 0.2, 0.4]) == {
        "beam_size": 5, "temperature": 0.0}
    assert Model().transcribe("a.wav") == {"temperature": 0.0}


def test_a_stopped_app_run_is_a_failure_not_a_zero(tmp_path, monkeypatch):
    clip = _app_clip(tmp_path)
    monkeypatch.setattr(rc.transcribe_service, "worker_args_for_settings", lambda *a: ())
    monkeypatch.setattr(rc, "_transcribe_in_child", lambda args, fixed=False: {"failed_reason": "no_lines"})
    with pytest.raises(RuntimeError, match="no_lines"):
        rc.run_transcription(clip, rc.argparse.Namespace(use_gpu=False, whisper_model="large-v3"))


def test_repeats_score_the_median_and_keep_every_run():
    scores = iter([0.5, 0.9, 0.7])
    row = rc.run_repeated(lambda clip, opts: {"score": next(scores), "metric": "cer", "speed_x": 2.0,
                                              "vram_mb": None, "detail": "x"}, {}, None, 3)
    assert row["score"] == 0.7 and row["scores"] == [0.5, 0.9, 0.7]
    assert "3 runs, scores 0.500 to 0.900" in row["detail"]


def test_a_repeated_previous_run_is_compared_by_its_worst_score():
    prev = _run([{"id": "t1", "kind": "transcription", "metric": "cer", "score": 0.7, "scores": [0.6, 0.7, 0.8]},
                 {"id": "d1", "kind": "diarization", "metric": "der", "score": 0.2, "scores": [0.1, 0.2, 0.3]}])

    def report(cer, der):
        return rc.format_report(_run([{"id": "t1", "kind": "transcription", "metric": "cer", "score": cer},
                                      {"id": "d1", "kind": "diarization", "metric": "der", "score": der}]), prev)

    assert "0 worse" in report(0.59, 0.31)
    assert "2 worse" in report(0.57, 0.33)
    assert "| 0.600 |" in report(0.59, 0.31)  # Previous shows the value the flag compared with


def test_nemotron_speakers_need_no_hugging_face_token(tmp_path, monkeypatch):
    import diarize_nemotron
    (tmp_path / "r.rttm").write_text("SPEAKER f 1 0.00 2.00 <NA> <NA> A <NA> <NA>\n", encoding="utf-8")
    clip = rc.load_manifest(_write(tmp_path, [{"id": "d", "kind": "diarization", "audio": "a.wav",
                                               "reference_rttm": "r.rttm", "backend": "nemotron"}]))["clips"][0]
    monkeypatch.setattr(rc.settings_service, "resolve_key", lambda name: pytest.fail("no key is needed"))
    monkeypatch.setattr(diarize_nemotron, "diarize",
                        lambda audio, use_gpu=False: ([{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"}], {}))
    row = rc.run_diarization(clip, rc.argparse.Namespace(use_gpu=False))
    assert row["score"] == 0 and "nemotron" in row["detail"]


def test_a_speaker_count_runs_pyannote_as_the_app_does(tmp_path, monkeypatch):
    import diarize_nemotron
    (tmp_path / "r.rttm").write_text("SPEAKER f 1 0.00 2.00 <NA> <NA> A <NA> <NA>\n", encoding="utf-8")
    clip = rc.load_manifest(_write(tmp_path, [{"id": "d", "kind": "diarization", "audio": "a.wav", "num_speakers": 3,
                                               "reference_rttm": "r.rttm", "backend": "nemotron"}]))["clips"][0]
    monkeypatch.setattr(rc.settings_service, "resolve_key", lambda name: "hf-token")
    monkeypatch.setattr(diarize_nemotron, "diarize", lambda *a, **k: pytest.fail("Nemotron takes no speaker count"))
    hints = {}

    def fake_pyannote(audio, token, num_speakers, use_gpu=False, run_info=None):
        hints["num_speakers"] = num_speakers
        return [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"}]

    monkeypatch.setattr(rc.diarize, "diarize", fake_pyannote)
    row = rc.run_diarization(clip, rc.argparse.Namespace(use_gpu=False))
    assert hints == {"num_speakers": 3} and "pyannote" in row["detail"]
