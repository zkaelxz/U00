"""tools/asr_bench.py: recipe parsing and refusals, window extraction, dry run, nothing saved,
overwrite refusal, report metrics, GPU release between recipes, error redaction. No models, no GPU."""
import dataclasses
import inspect
import os
import time

import pytest

import db
from core import Line
from tests.saved_settings import patch_setting
from tools import asr_bench as bench
from tools import asr_bench_recipes as recipes

FAKE_KEY = "sk-ant-api03-FAKEKEYVALUE1234567890abcdef"
WRITER_PREFIXES = ("save_", "update_", "create_", "delete_", "add_", "set_", "insert_", "remove_",
                   "replace_", "upsert_", "import_", "clear_", "reset_", "mark_", "record_", "rename_",
                   "merge_", "restore_", "log_")

# Title lines (and the reference when none is given): three lines at 2-4 s, 6-8 s, 10-12 s.
TITLE_LINES = [("こんにちは", 2.0, 4.0), ("元気ですか", 6.0, 8.0), ("はい元気です", 10.0, 12.0)]


@pytest.fixture
def title(isolated_db, monkeypatch):
    did = db.create_drama(title_zh="D", source_language="ja", audio_filename="a.wav")
    with open(os.path.join(db.drama_dir(did), "a.wav"), "wb") as fh:
        fh.write(b"RIFF")
    db.save_lines(did, [Line(idx=i, start=s, end=e, zh=zh, en="") for i, (zh, s, e) in enumerate(TITLE_LINES)])
    patch_setting(monkeypatch, "use_gpu", False)
    monkeypatch.setattr(recipes, "availability_problem", lambda recipe, language: None)
    monkeypatch.setattr(recipes.transcribe_service, "measured_transcribe_speed", lambda *a: None)
    return did


@pytest.fixture
def engine(monkeypatch):
    """Fake runners per backend plus counters for the window cut and the model release."""
    state = type("State", (), {})()
    state.events, state.cuts, state.contexts = [], [], []
    state.outputs = {}      # recipe label -> segments, or an Exception to raise

    def fake_run(ctx, recipe):
        state.events.append(("run", recipe.label))
        state.contexts.append(ctx)
        out = state.outputs.get(recipe.label, [])
        if isinstance(out, Exception):
            raise out
        return out

    for name, spec in list(bench.BACKENDS.items()):
        monkeypatch.setitem(bench.BACKENDS, name, dataclasses.replace(spec, run=fake_run))

    def fake_cut(audio, start, end, out_path, timeout=None):
        state.cuts.append((os.path.basename(audio), start, end))
        open(out_path, "wb").write(b"RIFF")
        return out_path

    monkeypatch.setattr(bench.core, "extract_audio_slice", fake_cut)
    monkeypatch.setattr(bench.transcribe_pipeline, "_audio_duration_seconds", lambda path: 20.0)
    monkeypatch.setattr(bench, "speech_spans", lambda wav, offset: [(offset + 2, offset + 4),
                                                                    (offset + 6, offset + 8)])
    monkeypatch.setattr(bench.whisper_models, "release_gpu_models",
                        lambda: state.events.append(("release",)))
    return state


@pytest.fixture
def writes(monkeypatch):
    calls = []
    for name, fn in list(vars(db).items()):
        if inspect.isfunction(fn) and fn.__module__.startswith("db") and name.startswith(WRITER_PREFIXES):
            def spy(*a, _name=name, **kw):
                calls.append(_name)
                raise AssertionError(f"the bench called db.{_name}")
            monkeypatch.setattr(db, name, spy)
    return calls


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


def good_output():
    return [seg(2.0, 4.0, "こんにちは"), seg(6.0, 8.0, "元気ですか"), seg(10.0, 12.0, "はい元気です")]


# ---------------------------------------------------------------------------
# Recipe grammar
# ---------------------------------------------------------------------------

def parse(text, language="ja", default="large-v3-turbo"):
    return recipes.parse_recipe(text, language, default)


def test_recipe_forms_parse():
    r = parse("whisper:large-v3-turbo,vad=sensitive,beam=1")
    assert (r.backend, r.model, r.vad, r.beam, r.prompt, r.align) == (
        "whisper", "large-v3-turbo", "sensitive", 1, True, False)
    r = parse("whisper:kotoba-v2,align=on")
    assert (r.model, r.align) == ("kotoba-v2", True)
    assert parse("whisper").model == "large-v3-turbo"
    assert parse("whisper:small,prompt=off").prompt is False
    r = parse("qwen3_asr_vad,detector=asmr")
    assert (r.backend, r.model, r.detector) == ("qwen3_asr_vad", None, "asmr")
    assert parse("qwen3_asr_long").label == "qwen3_asr_long"
    assert parse(" whisper : ja-1.5b , BEAM=3 ").label == "whisper:ja-1.5b,beam=3"


@pytest.mark.parametrize("text,language,message", [
    ("nope", "ja", "not a recipe backend"),
    ("whisper:huge", "ja", "not a Whisper model"),
    ("whisper:kotoba-v2", "zh", "only for Japanese"),
    ("whisper:ja-1.5b", "ko", "only for Japanese"),
    ("whisper:medium", "ja", "isn't recommended for Japanese"),
    ("qwen3_asr_vad:small", "ja", "does not use a Whisper model"),
    ("whisper,speed=2", "ja", "not a recipe key"),
    ("whisper,beam", "ja", "not KEY=VALUE"),
    ("whisper,beam=0", "ja", "beam= must be"),
    ("whisper,beam=x", "ja", "beam= must be"),
    ("whisper,beam=2,beam=3", "ja", "given twice"),
    ("whisper,vad=loud", "ja", "vad= must be one of"),
    ("whisper,prompt=maybe", "ja", "prompt= must be on or off"),
    ("whisper,detector=asmr", "ja", "whisper does not use detector="),
    ("qwen3_asr_vad,beam=2", "ja", "qwen3_asr_vad does not use beam="),
    ("qwen3_asr_long,align=on", "ja", "does not use align="),
    ("qwen3_asr_vad,detector=silero", "ja", "detector= must be one of"),
])
def test_bad_recipes_are_refused_with_a_reason(text, language, message):
    with pytest.raises(recipes.RecipeError, match=message):
        parse(text, language)


def test_duplicate_recipes_get_distinct_labels():
    labels = [r.label for r in recipes.unique_labels([parse("whisper:small"), parse("whisper:small")])]
    assert labels == ["whisper:small", "whisper:small #2"]


def test_help_documents_the_grammar(capsys):
    with pytest.raises(SystemExit):
        bench.main(["--help"])
    text = capsys.readouterr().out
    for word in ("BACKEND[:MODEL]", "vad=normal|sensitive", "beam=1..20", "prompt=on|off", "align=on|off",
                 "detector=standard|asmr", "kotoba-v2", "ja-1.5b", "qwen3_asr_long", "baseline"):
        assert word in text


def test_future_backend_is_one_registry_entry(monkeypatch):
    spec = dataclasses.replace(recipes.BACKENDS["whisper"], name="anime_whisper", keys=frozenset({"beam"}))
    monkeypatch.setitem(recipes.BACKENDS, "anime_whisper", spec)
    assert parse("anime_whisper:small,beam=2").backend == "anime_whisper"
    with pytest.raises(recipes.RecipeError, match="does not use vad="):
        parse("anime_whisper,vad=sensitive")


# ---------------------------------------------------------------------------
# Refusals, dry run, nothing saved
# ---------------------------------------------------------------------------

def _lines(did):
    return [(r["id"], r["idx"], r["start"], r["end"], r["zh"], r["flag"]) for r in db.load_lines(did)]


def test_dry_run_prints_the_plan_loads_nothing_and_writes_nothing(title, engine, writes, capsys, tmp_path):
    out = tmp_path / "r.md"
    before = _lines(title)
    rc = bench.main([str(title), "--config", "whisper:large-v3-turbo", "--config", "whisper:kotoba-v2,align=on",
                     "--config", "qwen3_asr_long", "--out", str(out)])
    text = capsys.readouterr().out
    assert rc == 0 and "Dry run only" in text
    assert not out.exists() and engine.events == [] and engine.cuts == [] and writes == []
    assert _lines(title) == before
    for expected in ("baseline", "whisper:large-v3-turbo", "whisper:kotoba-v2,align=on", "qwen3_asr_long",
                     "Whisper kotoba-v2 + Qwen3 forced aligner", "Qwen3-ASR", "Rough total", "3 lines"):
        assert expected in text
    assert not os.path.exists(bench.default_report_path())


def test_zh_title_refuses_a_japanese_model_up_front(isolated_db, monkeypatch, engine, capsys):
    did = db.create_drama(title_zh="D", source_language="zh", audio_filename="a.wav")
    open(os.path.join(db.drama_dir(did), "a.wav"), "wb").write(b"x")
    patch_setting(monkeypatch, "use_gpu", False)
    assert bench.main([str(did), "--config", "whisper:kotoba-v2", "--yes"]) == 2
    out = capsys.readouterr().out
    assert "Refused" in out and "only for Japanese" in out
    assert engine.events == [] and engine.cuts == []


def test_no_config_unknown_title_no_audio_and_bad_window_are_refused(title, isolated_db, engine, capsys):
    assert bench.main([str(title)]) == 2
    assert "at least one --config" in capsys.readouterr().out
    assert bench.main(["9999", "--config", "whisper"]) == 2
    assert "not found" in capsys.readouterr().out
    bare = db.create_drama(title_zh="No audio", source_language="ja")
    assert bench.main([str(bare), "--config", "whisper"]) == 2
    assert "no stored audio" in capsys.readouterr().out
    for window in (["--from", "5", "--to", "5"], ["--from", "-1"], ["--from", "0", "--to", "99999"],
                   ["--from", "nan"]):
        assert bench.main([str(title), "--config", "whisper", *window]) == 2
        capsys.readouterr()
    assert engine.events == []


def test_a_window_without_reference_lines_is_refused(title, engine, capsys):
    assert bench.main([str(title), "--config", "whisper", "--from", "100", "--to", "200"]) == 2
    assert "No reference lines" in capsys.readouterr().out


def test_an_unavailable_recipe_is_refused_before_anything_runs(title, engine, monkeypatch, capsys):
    monkeypatch.setattr(recipes, "availability_problem", lambda r, language: "Qwen3 needs transformers 5.")
    assert bench.main([str(title), "--config", "qwen3_asr_vad", "--yes"]) == 2
    out = capsys.readouterr().out
    assert "qwen3_asr_vad can't run on this PC" in out and engine.events == []


# ---------------------------------------------------------------------------
# A real run
# ---------------------------------------------------------------------------

def run_two(title, tmp_path, engine, *extra):
    engine.outputs = {"whisper:large-v3-turbo": good_output(),
                      "whisper:small": [seg(2.0, 4.0, "こんにちは"), seg(6.0, 12.0, "元気ですかはい元気です")]}
    out = tmp_path / "r.md"
    rc = bench.main([str(title), "--yes", "--config", "whisper:large-v3-turbo", "--config", "whisper:small",
                     "--out", str(out), "--from", "0", "--to", "20", *extra])
    return rc, out


def test_live_run_cuts_the_window_with_the_apps_helper_and_saves_nothing(title, engine, writes, tmp_path):
    before = _lines(title)
    rc, out = run_two(title, tmp_path, engine)
    assert rc == 0 and out.is_file()
    assert engine.cuts == [("a.wav", 0.0, 20.0)]
    assert writes == [] and _lines(title) == before
    assert {c.title_id for c in engine.contexts} == {title}
    assert all(c.wav.endswith("window.wav") for c in engine.contexts)
    assert out.read_text(encoding="utf-8").splitlines()[0] == bench.REPORT_TITLE


def test_models_are_released_after_every_recipe(title, engine, tmp_path):
    run_two(title, tmp_path, engine)
    assert engine.events == [("run", "whisper:large-v3-turbo"), ("release",),
                             ("run", "whisper:small"), ("release",), ("release",)]


def test_report_metrics_for_merged_missed_and_timing(title, engine, tmp_path):
    reference = tmp_path / "ref.srt"
    reference.write_text(
        "1\n00:00:02,000 --> 00:00:04,000\nこんにちは\n\n"
        "2\n00:00:06,000 --> 00:00:08,000\n元気ですか\n\n"
        "3\n00:00:10,000 --> 00:00:12,000\nはい元気です\n\n"
        "4\n00:00:14,000 --> 00:00:16,000\nまたね\n", encoding="utf-8")
    engine.outputs = {
        "whisper:large-v3-turbo": [seg(2.0, 4.0, "こんにちは"), seg(6.0, 8.0, "元気ですか"),
                                   seg(10.0, 12.0, "はい元気です"), seg(14.0, 16.0, "またね")],
        # Merges lines 2 and 3, hears line 1 half a second late, misses line 4, and loops.
        "whisper:small": [seg(2.5, 4.5, "こんにちは"), seg(6.0, 12.0, "元気ですかはい元気です"),
                          seg(17.0, 18.0, "ご視聴ありがとうございました"),
                          seg(18.0, 19.0, "ああ"), seg(19.0, 19.5, "ああ"), seg(19.5, 20.0, "ああ")],
    }
    out = tmp_path / "r.md"
    assert bench.main([str(title), "--yes", "--config", "whisper:large-v3-turbo", "--config", "whisper:small",
                       "--reference", str(reference), "--out", str(out), "--from", "0", "--to", "20"]) == 0
    report = out.read_text(encoding="utf-8")
    quality = {row.split("|")[1].strip(): [c.strip() for c in row.strip().strip("|").split("|")[1:]]
               for row in report.splitlines() if row.startswith("| whisper:")}
    turbo, small = quality["whisper:large-v3-turbo"], quality["whisper:small"]
    # lines, merged, missed, start median/p95, end median/p95, score, loops, stock phrases, in silence
    assert turbo == ["4", "0", "0", "0.00", "0.00", "0.00", "0.00", "1.000", "0", "0", "2"]
    assert small[:7] == ["6", "1", "1", "0.50", "4.00", "0.50", "4.00"]
    assert small[8:] == ["1", "1", "4"]
    assert float(small[7]) < 1.0
    assert "the hand-corrected file ref.srt" in report
    assert "Baseline: whisper:large-v3-turbo" in report
    assert "(merged)" in report                      # the side-by-side shows the merge
    assert "| 6.0 | 元気ですか |" in report
    for term in ("**wall s**", "**merged**", "**missed**", "**1-CER**", "**loops**", "**stock phrases**",
                 "**in silence**", "**peak VRAM MB**", "**start / end error**"):
        assert term in report
    assert str(tmp_path) not in report


def test_without_a_reference_the_titles_lines_are_the_baseline(title, engine, tmp_path):
    rc, out = run_two(title, tmp_path, engine)
    report = out.read_text(encoding="utf-8")
    assert rc == 0 and "the title's current lines (not hand-corrected" in report
    assert "3 lines fully inside the window" in report


def test_scores_use_the_benchmark_labs_scorer(title, engine, tmp_path, monkeypatch):
    calls = []
    real = bench.benchmark_scoring_service.score_output
    monkeypatch.setattr(bench.benchmark_scoring_service, "score_output",
                        lambda *a, **k: calls.append(a) or real(*a, **k))
    run_two(title, tmp_path, engine)
    assert calls and calls[0][0] == "transcription" and calls[0][3] == "ja"


def test_a_failed_recipe_is_a_row_not_the_end_of_the_run(title, engine, tmp_path, capsys):
    engine.outputs = {"whisper:large-v3-turbo": RuntimeError(f"401 bad key {FAKE_KEY} at C:\\Users\\bob\\x.py"),
                      "whisper:small": good_output()}
    out = tmp_path / "r.md"
    rc = bench.main([str(title), "--yes", "--config", "whisper:large-v3-turbo", "--config", "whisper:small",
                     "--out", str(out)])
    report, shown = out.read_text(encoding="utf-8"), capsys.readouterr().out
    assert rc == 0 and "FAILED: RuntimeError" in report
    assert FAKE_KEY not in report + shown and "bob" not in report + shown
    assert "REDACTED" in report
    assert "| whisper:small | 3 |" in report


def test_urls_in_heard_text_stay_out_of_the_report(title, engine, tmp_path):
    engine.outputs = {"whisper:small": [seg(2.0, 4.0, "see https://example.com/a?k=1 | now")]}
    out = tmp_path / "r.md"
    assert bench.main([str(title), "--yes", "--config", "whisper:small", "--config", "whisper:small",
                       "--out", str(out)]) == 0
    report = out.read_text(encoding="utf-8")
    assert "http" not in report and "whisper:small #2" in report


# ---------------------------------------------------------------------------
# Output file
# ---------------------------------------------------------------------------

def test_out_refuses_a_file_that_is_not_a_previous_report(title, engine, tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("CLAUDE_API_KEY=keep-me\n", encoding="utf-8")
    assert bench.main([str(title), "--yes", "--config", "whisper", "--out", str(env)]) == 2
    assert env.read_text(encoding="utf-8") == "CLAUDE_API_KEY=keep-me\n"
    assert engine.events == [] and engine.cuts == []
    old = tmp_path / "old.md"
    old.write_text(bench.REPORT_TITLE + "\nold\n", encoding="utf-8")
    engine.outputs = {"whisper:large-v3-turbo": good_output()}
    assert bench.main([str(title), "--yes", "--config", "whisper:large-v3-turbo", "--out", str(old)]) == 0
    assert "old" not in old.read_text(encoding="utf-8").splitlines()[1:2]


def test_report_is_not_written_over_a_file_swapped_in_during_the_run(title, engine, tmp_path, capsys,
                                                                     monkeypatch):
    out = tmp_path / "r.md"

    def swap(ctx, recipe):
        out.write_text("CLAUDE_API_KEY=keep-me\n", encoding="utf-8")  # appears after the first check
        return good_output()

    for name, spec in list(bench.BACKENDS.items()):
        monkeypatch.setitem(bench.BACKENDS, name, dataclasses.replace(spec, run=swap))
    bench.main([str(title), "--yes", "--config", "whisper", "--out", str(out)])
    assert out.read_text(encoding="utf-8") == "CLAUDE_API_KEY=keep-me\n"
    assert "Could not write" in capsys.readouterr().out


def test_default_report_goes_to_the_data_folder(title, engine, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.portable, "data_dir", lambda: str(tmp_path))
    engine.outputs = {"whisper:large-v3-turbo": good_output()}
    assert bench.main([str(title), "--yes", "--config", "whisper:large-v3-turbo"]) == 0
    assert (tmp_path / bench.REPORT_NAME).is_file()
    assert str(tmp_path) not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------

@pytest.fixture
def gpu(monkeypatch, title):
    patch_setting(monkeypatch, "use_gpu", True)
    state = type("Gpu", (), {})()
    state.acquired, state.released, state.busy = [], [], 0
    state.status = ("ui:transcribe_1", "Transcription (title #1)")

    def acquire(holder, description=None, **kw):
        if state.busy:
            state.busy -= 1
            return False
        state.acquired.append((holder, kw.get("check_external_load")))
        return True

    monkeypatch.setattr(bench.gpu_slots, "acquire", acquire)
    monkeypatch.setattr(bench.gpu_slots, "release", state.released.append)
    monkeypatch.setattr(bench.gpu_slots, "status", lambda: state.status)
    monkeypatch.setattr(bench.gpu_slots, "heartbeat", lambda holders: None)
    monkeypatch.setattr(bench, "GPU_POLL_S", 0.0)
    return state


def _speed_row(report, label):
    return next(r for r in report.splitlines() if r.startswith(f"| {label} |") and r.count("|") == 6)


def test_gpu_run_takes_and_frees_the_slot_and_reports_peak_vram(title, engine, gpu, tmp_path, monkeypatch):
    # Whole-GPU (total, free) MB: 2000 in use before the recipe, 5000 while it runs.
    readings = iter([(8000.0, 6000.0)])
    monkeypatch.setattr(bench.memory_headroom, "read_memory_mb",
                        lambda memory: next(readings, (8000.0, 3000.0)))
    monkeypatch.setattr(bench, "MONITOR_TICK_S", 0.005)

    def slow(ctx, recipe):
        time.sleep(0.1)
        return good_output()

    for name, spec in list(bench.BACKENDS.items()):
        monkeypatch.setitem(bench.BACKENDS, name, dataclasses.replace(spec, run=slow))
    out = tmp_path / "r.md"
    assert bench.main([str(title), "--yes", "--config", "whisper:large-v3-turbo", "--out", str(out)]) == 0
    report = out.read_text(encoding="utf-8")
    assert gpu.acquired == [(gpu.acquired[0][0], True)] and gpu.released == [gpu.acquired[0][0]]
    assert "Device: GPU" in report
    assert float(_speed_row(report, "whisper:large-v3-turbo").split("|")[4]) == 3000.0


def test_gpu_run_waits_for_a_busy_gpu_then_runs(title, engine, gpu, tmp_path, capsys):
    gpu.busy = 2
    engine.outputs = {"whisper:large-v3-turbo": good_output()}
    assert bench.main([str(title), "--yes", "--config", "whisper:large-v3-turbo",
                       "--out", str(tmp_path / "r.md")]) == 0
    assert "Waiting for the GPU -- busy with: Transcription (title #1)" in capsys.readouterr().out
    assert len(gpu.acquired) == 1


def test_a_gpu_that_stays_busy_refuses_without_loading_anything(title, engine, gpu, tmp_path, capsys, monkeypatch):
    gpu.busy = 10 ** 6
    monkeypatch.setattr(bench, "GPU_WAIT_S", 0.0)
    assert bench.main([str(title), "--yes", "--config", "whisper", "--out", str(tmp_path / "r.md")]) == 2
    assert "GPU stayed busy" in capsys.readouterr().out
    assert engine.events == [] and engine.cuts == [] and gpu.acquired == []


def test_the_slot_is_freed_when_a_recipe_is_interrupted(title, engine, gpu, tmp_path, capsys, monkeypatch):
    def interrupt(ctx, recipe):
        raise KeyboardInterrupt

    for name, spec in list(bench.BACKENDS.items()):
        monkeypatch.setitem(bench.BACKENDS, name, dataclasses.replace(spec, run=interrupt))
    out = tmp_path / "r.md"
    assert bench.main([str(title), "--yes", "--config", "whisper", "--out", str(out)]) == 130
    assert gpu.released == [gpu.acquired[0][0]] and not out.exists()
    assert engine.events[-2:] == [("release",), ("release",)]


def test_a_cpu_run_takes_no_gpu_slot(title, engine, monkeypatch, tmp_path):
    taken = []
    monkeypatch.setattr(bench.gpu_slots, "acquire", lambda *a, **k: taken.append(a) or True)
    engine.outputs = {"whisper:large-v3-turbo": good_output()}
    assert bench.main([str(title), "--yes", "--config", "whisper:large-v3-turbo",
                       "--out", str(tmp_path / "r.md")]) == 0
    assert taken == []
    assert "n/a (CPU)" in (tmp_path / "r.md").read_text(encoding="utf-8")


def test_gpu_fallback_is_reported_in_the_row(title, engine, tmp_path, monkeypatch):
    def fall_back(ctx, recipe):
        ctx.note_gpu_fallback(RuntimeError(f"CUDA failed, key {FAKE_KEY}"))
        return good_output()

    for name, spec in list(bench.BACKENDS.items()):
        monkeypatch.setitem(bench.BACKENDS, name, dataclasses.replace(spec, run=fall_back))
    out = tmp_path / "r.md"
    assert bench.main([str(title), "--yes", "--config", "whisper", "--out", str(out)]) == 0
    report = out.read_text(encoding="utf-8")
    assert "GPU unavailable, ran on CPU" in report and FAKE_KEY not in report


# ---------------------------------------------------------------------------
# Small pieces
# ---------------------------------------------------------------------------

def test_percentile_is_nearest_rank():
    assert recipes.percentile([], 0.95) is None
    assert recipes.percentile([5.0], 0.95) == 5.0
    assert recipes.percentile(list(map(float, range(1, 21))), 0.95) == 19.0


def test_loops_and_stock_phrases_are_counted():
    lines = [seg(0, 1, "はいはい"), seg(1, 2, "ああ"), seg(2, 3, "ああ"), seg(3, 4, "ああ"),
             seg(4, 5, "ありがとう" * 6), seg(5, 6, "Thanks for watching!"), seg(6, 7, "ご視聴ありがとうございました")]
    assert bench.count_loops(lines) == 2
    assert bench.count_stock_phrases(lines) == 2


def test_lines_in_silence_need_no_speech_under_them():
    lines = [seg(0, 2, "a"), seg(10, 12, "b"), seg(4, 6, "c")]
    assert bench.count_in_silence(lines, [(0.0, 2.0), (4.5, 5.5)]) == 1
    assert bench.count_in_silence(lines, None) is None


# ---------------------------------------------------------------------------
# The runners pass each recipe's options to the app's own transcription code
# ---------------------------------------------------------------------------

@pytest.fixture
def app_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(recipes.core, "transcribe_for_timing",
                        lambda wav, model, **kw: calls.append(("whisper", model, kw)) or [seg(0, 1, "x")])
    monkeypatch.setattr(recipes.forced_align, "refine_whisper_segments",
                        lambda wav, segments, language, **kw: calls.append(("align", language, kw)) or segments)
    monkeypatch.setattr(recipes.whisper_models, "release_gpu_models", lambda: calls.append(("release",)))
    monkeypatch.setattr(recipes.whisper_models, "get_whisper_device_info", lambda *a: {})
    monkeypatch.setattr(recipes.transcribe_service, "build_auto_initial_prompt", lambda did: "NAMES。")
    monkeypatch.setattr(recipes.asr_options_service, "get_qwen_asr_batch_size", lambda: 3)
    return calls


def ctx_for(drama=None):
    return recipes.RunContext(1, drama or {"sensitivity_preset": "normal", "beam_size": 7, "min_silence_ms": 400},
                              "ja", "w.wav", False, None)


def test_whisper_runner_uses_the_recipes_options_over_the_titles(app_calls):
    recipes.run_whisper(ctx_for(), parse("whisper:kotoba-v2,vad=sensitive,beam=1,prompt=off"))
    _, model, kw = app_calls[0]
    assert model == "kotoba-v2" and kw["beam_size"] == 1 and kw["initial_prompt"] == ""
    assert kw["sensitivity_preset"] == "sensitive" and kw["vad_threshold"] == 0.35
    assert kw["min_silence_duration_ms"] == 400 and kw["language"] == "ja"
    assert [c[0] for c in app_calls] == ["whisper"]


def test_whisper_runner_falls_back_to_the_titles_settings(app_calls):
    recipes.run_whisper(ctx_for(), parse("whisper:small"))
    _, _, kw = app_calls[0]
    assert kw["beam_size"] == 7 and kw["initial_prompt"] == "NAMES。" and kw["sensitivity_preset"] == "normal"


def test_align_refines_after_whisper_has_released_its_model(app_calls):
    recipes.run_whisper(ctx_for(), parse("whisper:kotoba-v2,align=on"))
    assert [c[0] for c in app_calls] == ["whisper", "release", "align"]
    assert app_calls[2][1] == "ja"


def test_qwen3_asr_hears_whispers_segments_after_whisper_is_released(app_calls, monkeypatch):
    heard = []

    class FakeQwen:
        def transcribe(self, wav, language, segments, **kw):
            heard.append((segments, kw))
            return [seg(0, 1, "qwen")]

    monkeypatch.setattr(recipes.asr_backend, "get_backend", lambda name: FakeQwen())
    out = recipes.run_qwen3_asr(ctx_for(), parse("qwen3_asr:small"))
    assert out == [seg(0, 1, "qwen")] and heard[0][0] == [seg(0, 1, "x")] and heard[0][1]["batch_size"] == 3
    assert [c[0] for c in app_calls] == ["whisper", "release"]


def test_vad_backends_get_the_recipes_detector_and_alignment(monkeypatch):
    seen = []

    class FakeVad:
        def transcribe(self, wav, language, **kw):
            seen.append(kw)
            return []

    monkeypatch.setattr(recipes.asr_backend, "get_backend", lambda name: FakeVad())
    monkeypatch.setattr(recipes.asr_options_service, "resolve_voice_detector", lambda d, lang: "auto_asmr")
    monkeypatch.setattr(recipes.asr_options_service, "get_qwen_asr_batch_size", lambda: 1)
    recipes.run_qwen3_vad(ctx_for(), parse("qwen3_asr_vad,detector=asmr,align=on"))
    recipes.run_qwen3_vad(ctx_for(), parse("qwen3_asr_vad"))
    assert (seen[0]["detector"], seen[0]["refine_timing"]) == ("asmr", True)
    assert (seen[1]["detector"], seen[1]["refine_timing"]) == ("auto_asmr", False)


def test_dry_run_facts_come_from_the_apps_tables():
    r = parse("whisper:kotoba-v2,align=on")
    assert recipes.describe_loads(r) == "Whisper kotoba-v2 + Qwen3 forced aligner"
    assert recipes.peak_vram_estimate_mb(r, True) == 2600.0
    assert recipes.estimate_seconds(r, 100, True) > recipes.estimate_seconds(parse("whisper:kotoba-v2"), 100, True)
