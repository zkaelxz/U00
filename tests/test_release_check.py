"""tools/release_check.py: manifest refusals, DER and chrF scoring, regression flags, dry run, paid-engine
gate, one failed clip not stopping the run, report holds no paths. No models, no GPU, no network."""
import json

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
                 "error": "RuntimeError: failed to open C:\\Users\\Mary\\secret.wav"}])
    report = rc.format_report(now, prev)
    assert "WORSE" in report and "FAILED" in report and "1 failed, 1 worse" in report
    assert report.startswith(rc.REPORT_TITLE)
    assert "Mary" not in report


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
