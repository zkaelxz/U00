"""Script counts for Chinese transcripts in the Benchmark Lab."""
import benchmark
import db
from services import benchmark_lab_service as svc
from services import benchmark_script_service as scripts
from tests.test_benchmark_lab import _run

import pytest

pytestmark = pytest.mark.usefixtures("whisper_present")


def test_table_is_one_to_one_and_disjoint():
    assert len(scripts.SIMPLIFIED_ONLY) == len(scripts.TRADITIONAL_ONLY) == len(scripts._PAIRS)
    assert not scripts.SIMPLIFIED_ONLY & scripts.TRADITIONAL_ONLY


def test_pure_traditional():
    c = scripts.count_scripts("這是我們的國家\n你好")
    assert (c["traditional_lines"], c["neutral_lines"], c["simplified_lines"]) == (1, 1, 0)
    assert c["traditional_chars"] == 3 and c["simplified_chars"] == 0


def test_pure_simplified():
    c = scripts.count_scripts("这是我们的国家")
    assert (c["simplified_lines"], c["traditional_lines"], c["mixed_lines"]) == (1, 0, 0)
    assert c["simplified_chars"] == 3 and c["neutral_chars"] == 4


def test_mixed_line_counts_both_scripts():
    c = scripts.count_scripts("这是我們")
    assert (c["mixed_lines"], c["traditional_lines"], c["simplified_lines"]) == (1, 0, 0)
    assert (c["simplified_chars"], c["traditional_chars"]) == (1, 1)


def test_punctuation_latin_digits_and_blank_lines_ignored():
    c = scripts.count_scripts("Hello 123, OK!\n\n。。。\n你好，world")
    assert c == dict(scripts.empty_counts(), neutral_lines=1, neutral_chars=2)


def test_empty():
    assert scripts.count_scripts("") == scripts.count_scripts(None) == scripts.empty_counts()


def _transcribe(monkeypatch, outputs, language="zh"):
    for i in range(len(outputs)):
        db.create_benchmark_case(f"clip{i}", "transcription", "audio_drama", input_filename="c.wav",
                                 reference_text="你好", source_language=language)
    texts = iter(outputs)
    monkeypatch.setattr(benchmark, "run_transcription_case",
                        lambda case, whisper_size="medium", use_gpu=False, **kw:
                        {"output_text": next(texts), "duration_seconds": 0.1, "error": None, "score": None})
    return _run(stage="transcription", configs=[{"engine": "whisper", "model": "small"}])["session_ids"][0]


def test_run_carries_counts_per_case_and_per_arm(isolated_db, monkeypatch):
    sid = _transcribe(monkeypatch, ["我們的國家", "这是国家"])
    detail = svc.get_run(sid)
    assert [r["script_counts"]["traditional_lines"] for r in detail["results"]] == [1, 0]
    assert [r["script_counts"]["simplified_lines"] for r in detail["results"]] == [0, 1]
    assert detail["run"]["script_counts"]["traditional_chars"] == 2
    assert detail["run"]["script_counts"]["simplified_chars"] == 2


def test_arena_carries_counts(isolated_db, monkeypatch):
    sid = _transcribe(monkeypatch, ["我們的國家"])
    texts = iter(["我们的国家"])
    monkeypatch.setattr(benchmark, "run_transcription_case",
                        lambda case, whisper_size="medium", use_gpu=False, **kw:
                        {"output_text": next(texts), "duration_seconds": 0.1, "error": None, "score": None})
    sid2 = _run(stage="transcription", configs=[{"engine": "whisper", "model": "small"}])["session_ids"][0]
    arena = svc.arena([sid, sid2])
    (row,) = arena["rows"]
    assert row["results"][0]["script_counts"]["traditional_lines"] == 1
    assert row["results"][1]["script_counts"]["simplified_lines"] == 1
    assert [r["script_counts"]["traditional_chars"] for r in arena["runs"]] == [2, 0]


def test_non_chinese_run_has_no_counts(isolated_db, monkeypatch):
    sid = _transcribe(monkeypatch, ["こんにちは"], language="ja")
    detail = svc.get_run(sid)
    assert detail["results"][0]["script_counts"] is None
    assert detail["run"]["script_counts"] is None
