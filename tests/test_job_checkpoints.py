"""Step 41: checkpoints and resume, the result cache, per-stage timing,
per-line provenance, the VRAM fit check and GPU queue position. Fakes
only: no model, GPU or network."""
import json
import sys
import threading
import time
import types

import pytest

import background_jobs
import debug_view
import translate_engines
import translation_guide as tguide
from core import Line
from services import (glossary_service, job_checkpoint_service as cp, job_timing_service,
                      line_provenance_service, narration_service, vram_service)


@pytest.fixture(autouse=True)
def _reset_timing():
    job_timing_service.reset_for_tests()
    yield
    job_timing_service.reset_for_tests()


# --- result cache (item 1) ------------------------------------------------

def test_cache_hits_only_for_the_same_input_model_and_settings(isolated_db):
    h = cp.hash_text("你好")
    cp.cache_put("demo", h, "claude:x", {"a": 1, "b": 2}, {"out": "hello"})
    assert cp.cache_get("demo", h, "claude:x", {"b": 2, "a": 1}) == {"out": "hello"}
    assert cp.cache_has("demo", h, "claude:x", {"a": 1, "b": 2})
    assert cp.cache_get("demo", cp.hash_text("再见"), "claude:x", {"a": 1, "b": 2}) is None
    assert cp.cache_get("demo", h, "claude:y", {"a": 1, "b": 2}) is None
    assert cp.cache_get("demo", h, "claude:x", {"a": 1, "b": 3}) is None
    assert cp.cache_get("other", h, "claude:x", {"a": 1, "b": 2}) is None
    assert cp.cache_get("demo", h, "claude:y", None, default="miss") == "miss"


def test_cache_keeps_a_bounded_number_of_rows_per_kind(isolated_db, monkeypatch):
    monkeypatch.setattr(cp, "CACHE_MAX_PER_KIND", 3)
    for i in range(5):
        cp.cache_put("demo", str(i), "m", None, i)
        time.sleep(0.001)
    cp.cache_put("keep", "x", "m", None, "other kind")
    assert [cp.cache_get("demo", str(i), "m") for i in range(5)] == [None, None, 2, 3, 4]
    assert cp.cache_get("keep", "x", "m") == "other kind"
    assert cp.cache_clear("demo") == 3


# --- checkpoints (item 2) ---------------------------------------------------

def test_scope_changes_with_input_model_or_settings():
    base = cp.checkpoint_scope("k", 1, "h", "m", {"s": 1})
    assert base == cp.checkpoint_scope("k", 1, "h", "m", {"s": 1})
    assert len({base, cp.checkpoint_scope("k", 1, "h2", "m", {"s": 1}),
                cp.checkpoint_scope("k", 1, "h", "m2", {"s": 1}),
                cp.checkpoint_scope("k", 1, "h", "m", {"s": 2}),
                cp.checkpoint_scope("k", 2, "h", "m", {"s": 1})}) == 5


def test_record_done_and_clear(isolated_db):
    scope = cp.checkpoint_scope("k", 7, "h", "m")
    other = cp.checkpoint_scope("k", 8, "h", "m")
    cp.record_unit(scope, 1, "A")
    cp.record_unit(scope, 2, {"x": 1})
    cp.record_unit(other, 1, "B")
    assert cp.done_units(scope) == {"1": "A", "2": {"x": 1}}
    cp.clear_prefix("k", 7)
    assert cp.done_units(scope) == {} and cp.done_units(other) == {"1": "B"}


class _Engine:
    supports_reference = True
    model = "fake-model"


def _narration_setup(isolated_db, monkeypatch, chunks=40):
    did = isolated_db.create_drama(title_en="N", content_mode="novel_narration")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: _Engine())
    monkeypatch.setattr(narration_service.core_module, "chunk_novel_text",
                        lambda text, n: [f"段落{i}" for i in range(chunks)])
    return did


def test_interrupted_narration_tagging_resumes_from_the_last_finished_batch(isolated_db,
                                                                          monkeypatch):
    did = _narration_setup(isolated_db, monkeypatch)
    calls = []
    fail_on = {"call": 2}

    def fake_batch(batch_ids, build, call_model):
        calls.append(list(batch_ids))
        if len(calls) == fail_on["call"]:
            raise RuntimeError("process killed")
        return {str(i): f"S{i % 3}" for i in batch_ids}

    monkeypatch.setattr(translate_engines, "_id_keyed_batch_request", fake_batch)
    job_id = f"narration_{did}"
    with pytest.raises(RuntimeError):
        narration_service._run_narration_job(job_id, did, "novel", "claude", "k", None)
    assert calls == [list(range(0, 15)), list(range(15, 30))]
    assert isolated_db.load_line_objects(did) == []   # nothing saved yet

    calls.clear()
    fail_on["call"] = -1
    narration_service._run_narration_job(job_id, did, "novel", "claude", "k", None)
    # Batch 1 was paid for already: the re-run starts at chunk 15, not 0.
    assert calls == [list(range(15, 30)), list(range(30, 40))]
    lines = isolated_db.load_line_objects(did)
    assert [ln.speaker for ln in lines] == [f"S{i % 3}" for i in range(40)]
    # Finished: the checkpoints are gone, so a later run starts fresh.
    scope = cp.checkpoint_scope("narration_tag", did, cp.hash_text("novel"),
                                "claude:fake-model",
                                {"max_chunk_chars": narration_service.MAX_CHUNK_CHARS})
    assert cp.done_units(scope) == {}


def test_changed_text_or_engine_never_reuses_checkpoints(isolated_db, monkeypatch):
    did = _narration_setup(isolated_db, monkeypatch, chunks=20)
    calls = []
    state = {"fail": True}

    def fake_batch(batch_ids, build, call_model):
        calls.append(list(batch_ids))
        if state["fail"] and len(calls) == 2:
            raise RuntimeError("stop")
        return {str(i): "A" for i in batch_ids}

    monkeypatch.setattr(translate_engines, "_id_keyed_batch_request", fake_batch)
    with pytest.raises(RuntimeError):
        narration_service._run_narration_job("j", did, "novel v1", "claude", "k", None)
    calls.clear()
    state["fail"] = False
    narration_service._run_narration_job("j", did, "novel v2", "claude", "k", None)
    assert calls[0] == list(range(0, 15))


def test_novel_glossary_resumes_through_the_cache(isolated_db, monkeypatch):
    novel = "".join(f"第{i}章。" + "林" * 5000 for i in range(8))
    prompts = []
    state = {"fail_at": 3}
    seen = {}

    def fake_llm(engine, prompt, **kw):
        prompts.append(prompt)
        if len(prompts) == state["fail_at"]:
            raise RuntimeError("killed")
        return json.dumps([{"term": f"T{seen.setdefault(prompt, len(seen))}", "suggested_translation": "x",
                            "category": "character", "policy": "keep_pinyin", "reason": ""}])

    monkeypatch.setattr(tguide, "call_llm_json", fake_llm)
    cache = glossary_service._novel_glossary_cache(_Engine(), "claude")
    with pytest.raises(RuntimeError):
        tguide.extract_glossary_from_novel(novel, _Engine(), response_cache=cache)
    first = list(prompts)
    total = len(tguide._sample_across_text(novel))
    assert len(first) == 3 and total > 3
    prompts.clear()
    state["fail_at"] = -1
    terms = tguide.extract_glossary_from_novel(novel, _Engine(), response_cache=cache)
    # The two passages that finished before the crash come from the cache.
    assert prompts[0] == first[2] and len(prompts) == total - 2
    assert len(terms) == total


def test_a_failed_or_empty_reply_is_not_cached(isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(tguide, "call_llm_json", lambda *a, **k: calls.append(1) or "[]")
    cache = glossary_service._novel_glossary_cache(_Engine(), "claude")
    tguide.extract_glossary_from_novel("林" * 2000, _Engine(), response_cache=cache)
    n = len(calls)
    tguide.extract_glossary_from_novel("林" * 2000, _Engine(), response_cache=cache)
    assert len(calls) == 2 * n


# --- provenance (item 4) ----------------------------------------------------

def test_per_line_provenance_is_recorded_and_retrievable(isolated_db):
    did = isolated_db.create_drama(title_en="P")
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en=""),
                                 Line(idx=1, start=1, end=2, zh="再见", en="bye")])
    lines = isolated_db.load_line_objects(did)
    glossary = [{"term_original": "林", "term_translation": "Lin", "policy": "keep_pinyin"}]
    on_save = line_provenance_service.tracker(did, lines, "claude", "claude-x", "1", glossary,
                                              settings={"locale": "en-US"})
    lines[0].en = "hello"
    on_save(lines)   # line 1's text is unchanged: no record for it
    prov = line_provenance_service.get(did, lines[0].id)
    assert prov["engine"] == "claude" and prov["model"] == "claude-x"
    assert prov["prompt_version"] == "1"
    assert prov["glossary_hash"] == line_provenance_service.glossary_hash(glossary) != ""
    assert prov["software_version"]
    assert line_provenance_service.get(did, lines[1].id) is None

    info = debug_view.explain_line(did, lines[0], lines)
    assert info["provenance"]["model"] == "claude-x" and info["model"] == "claude-x"
    assert "prompt version 1" in info["prompt_version_note"]
    assert "claude-x" in info["prompt_version_note"]


def test_glossary_hash_ignores_order_and_changes_with_content():
    a = {"term_original": "林", "term_translation": "Lin"}
    b = {"term_original": "沈", "term_translation": "Shen"}
    h = line_provenance_service.glossary_hash
    assert h([a, b]) == h([b, a]) and h([a]) != h([a, b]) and h([]) == ""
    assert h([a]) != h([{**a, "term_translation": "Lynn"}])


def test_translate_job_records_provenance_for_translated_lines(isolated_db, monkeypatch):
    from services import workspace_job_service as wjs
    did = isolated_db.create_drama(title_en="T")
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    lines = isolated_db.load_line_objects(did)

    def fake_translate(ls, engine, **kw):
        ls[0].en = "hello"
        kw["save_cb"](ls)
        return ls, []

    monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)
    monkeypatch.setattr(wjs.bulk_translate, "finish_translation_run", lambda *a, **k: False)
    wjs.run_translate_job("translate_x", did, lines, _Engine(), {}, "", "", False, "en-US",
                          [], "", "claude", "default")
    prov = line_provenance_service.get(did, lines[0].id)
    assert prov["engine"] == "claude" and prov["model"] == "fake-model"
    assert prov["prompt_version"] == translate_engines.TRANSLATE_PROMPT_VERSION
    assert isolated_db.load_line_objects(did)[0].en == "hello"


# --- per-stage timing (item 5) ------------------------------------------------

def _wait(job_id):
    for _ in range(300):
        job = background_jobs.get_status(job_id)
        if job and job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def _wait_runs(job_id):
    for _ in range(300):
        runs = job_timing_service.list_runs(job_id)
        if runs and not runs[0]["running"]:
            return runs
        time.sleep(0.01)
    raise AssertionError("timing not recorded")


def test_stages_and_spend_are_recorded_for_a_real_job(isolated_db):
    did = isolated_db.create_drama(title_en="S")

    def work():
        job_timing_service.mark_stage("stage_job", "Transcribe")
        time.sleep(0.02)
        job_timing_service.mark_stage("stage_job", "Translate")
        isolated_db.log_usage(did, "claude", "m", "translate", 10, 10, 0.25)
        isolated_db.log_usage(did, "claude", "m", "translate", 10, 10, 0.5)

    background_jobs.start_job("stage_job", work, description="Stages")
    assert _wait("stage_job")["status"] == "done"
    run = _wait_runs("stage_job")[0]
    assert [s["stage"] for s in run["stages"]] == ["Transcribe", "Translate"]
    assert run["stages"][0]["duration_seconds"] >= 0.02
    assert run["stages"][0]["cost_usd"] == 0 and run["stages"][1]["cost_usd"] == 0.75
    assert run["cost_usd"] == 0.75
    info = debug_view.explain_job("stage_job")
    assert [s["stage"] for s in info["per_stage_breakdown"]] == ["Transcribe", "Translate"]
    background_jobs.clear_job("stage_job")


def test_a_job_without_stages_gets_one_whole_job_row_even_when_it_fails(isolated_db):
    def boom():
        raise RuntimeError("x")

    background_jobs.start_job("plain_job", boom, description="Plain")
    assert _wait("plain_job")["status"] == "error"
    run = _wait_runs("plain_job")[0]
    assert [s["stage"] for s in run["stages"]] == [job_timing_service.WHOLE_JOB]
    background_jobs.clear_job("plain_job")


def test_spend_outside_a_job_is_not_attributed(isolated_db):
    job_timing_service.start_run("idle_job")
    isolated_db.log_usage(None, "claude", "m", "translate", 1, 1, 9.0)   # main thread, no job
    job_timing_service.finish_run("idle_job")
    assert job_timing_service.list_runs("idle_job")[0]["cost_usd"] == 0


def test_only_the_latest_runs_are_kept(isolated_db, monkeypatch):
    monkeypatch.setattr(job_timing_service, "RUNS_KEPT_PER_JOB", 2)
    for t in (100.0, 200.0, 300.0):
        job_timing_service.start_run("r", now=t)
        job_timing_service.finish_run("r", now=t + 1)
    assert [r["run_started_at"] for r in job_timing_service.list_runs("r")] == [300.0, 200.0]


def test_stages_route_follows_job_visibility(isolated_db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.server import ApiSettings, create_app
    job_timing_service.start_run("translate_1", now=10.0)
    job_timing_service.mark_stage("translate_1", "Translate", now=11.0)
    job_timing_service.finish_run("translate_1", now=13.0)
    isolated_db.save_job_record("translate_1", status="done", progress=1.0, message="",
                                error=None, description="t", gpu_touching=False,
                                started_at=10.0, finished_at=13.0)
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    r = c.get("/api/jobs/translate_1/stages")
    assert r.status_code == 200
    body = r.json()
    assert [s["stage"] for s in body["runs"][0]["stages"]] == ["Preparing", "Translate"]
    assert body["runs"][0]["total_seconds"] == 3.0
    assert c.get("/api/jobs/nope/stages").status_code == 404


# --- GPU queue position (item 6) -----------------------------------------------

def test_queued_gpu_jobs_show_their_place_in_line(monkeypatch):
    monkeypatch.setattr(background_jobs, "get_gpu_limit_enabled", lambda: True)
    monkeypatch.setattr(background_jobs, "_gpu_slot_available_locked", lambda *a: False)
    ids = ["q_a", "q_b", "q_c"]
    try:
        for j in ids:
            assert background_jobs.start_job(j, lambda: None, gpu_touching=True, description=j)
        messages = [background_jobs.get_status(j)["message"] for j in ids]
        assert messages[0] == background_jobs.GPU_WAIT_MESSAGE
        assert messages[1].endswith("number 2 in line") and messages[2].endswith("number 3 in line")
        assert [background_jobs.queue_position(j) for j in ids] == [1, 2, 3]
        assert background_jobs.cancel_queued("q_a")
        assert background_jobs.get_status("q_b")["message"] == background_jobs.GPU_WAIT_MESSAGE
        assert background_jobs.get_status("q_c")["message"].endswith("number 2 in line")
        assert background_jobs.queue_position("q_a") is None
    finally:
        for j in ids:
            background_jobs.clear_job(j)


# --- VRAM fit check (item 7) -------------------------------------------------------

def test_fit_check_refuses_an_oversized_load_with_a_clear_message():
    with pytest.raises(vram_service.InsufficientVramError) as ei:
        vram_service.check_fits("TADA 3B", free_mb=3000)
    msg = str(ei.value)
    assert "TADA 3B" in msg and "6.3 GB" in msg and "2.9 GB is free" in msg
    assert "CUDA" not in msg and "out of memory" not in msg.lower()
    vram_service.check_fits("TADA 3B", free_mb=8000)          # fits
    vram_service.check_fits("Unknown model", free_mb=1)        # no estimate: allowed


def test_unknown_free_memory_never_blocks(monkeypatch):
    monkeypatch.setattr(vram_service, "free_vram_mb", lambda: None)
    vram_service.check_fits("TADA 3B")


def test_free_memory_comes_from_torch_when_loaded(monkeypatch):
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: True,
                                       mem_get_info=lambda: (512 * 1024 * 1024, 12 * 1024 ** 3))
    monkeypatch.setitem(sys.modules, "torch", torch)
    assert vram_service.free_vram_mb() == 512


def test_free_memory_falls_back_to_nvidia_smi(monkeypatch):
    import diagnostics
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setattr(diagnostics, "external_gpu_load",
                        lambda: {"memory_free_mb": 1024.0, "memory_used_mb": 0,
                                 "memory_total_mb": 1024.0, "utilization_percent": 0})
    assert vram_service.free_vram_mb() == 1024.0


@pytest.mark.parametrize("loader,args", [("_get_chatterbox", ()), ("_get_omnivoice_model", ()),
                                         ("_get_tada", (None,))])
def test_dub_loaders_check_vram_before_loading(monkeypatch, loader, args):
    import dub
    monkeypatch.setattr(dub, "_cuda_available", lambda: True)
    monkeypatch.setattr(vram_service, "free_vram_mb", lambda: 100.0)
    monkeypatch.setattr(dub, "_chatterbox", None)
    monkeypatch.setattr(dub, "_omnivoice_model", None)
    monkeypatch.setitem(dub._tada, "model", None)
    with pytest.raises(vram_service.InsufficientVramError):
        getattr(dub, loader)(*args)
