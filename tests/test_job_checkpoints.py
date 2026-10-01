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
    on_save = line_provenance_service.tracker(did, lines, lambda: ("claude", "claude-x"), "1",
                                              glossary, settings={"locale": "en-US"})
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

def test_queued_gpu_jobs_show_their_place_in_line(isolated_db, monkeypatch):
    monkeypatch.setattr(background_jobs, "get_gpu_limit_enabled", lambda: True)
    monkeypatch.setattr(background_jobs, "_gpu_slot_available_locked", lambda *a: False)
    ids = ["q_a", "q_b", "q_c"]
    try:
        for j in ids:
            assert background_jobs.start_job(j, lambda: None, gpu_touching=True, description=j)
        messages = [background_jobs.get_status(j)["message"] for j in ids]
        assert messages[0] == background_jobs.GPU_WAIT_MESSAGE
        assert messages[1].endswith("number 2 in line") and messages[2].endswith("number 3 in line")
        assert background_jobs.cancel_queued("q_a")
        assert background_jobs.get_status("q_b")["message"] == background_jobs.GPU_WAIT_MESSAGE
        assert background_jobs.get_status("q_c")["message"].endswith("number 2 in line")
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


def test_provenance_is_ignored_once_the_translation_changes(isolated_db):
    did = isolated_db.create_drama(title_en="P")
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    lines = isolated_db.load_line_objects(did)
    on_save = line_provenance_service.tracker(did, lines, lambda: ("claude", "claude-x"), "1", [])
    lines[0].en = "hello"
    on_save(lines)
    assert line_provenance_service.get(did, lines[0].id, current_en="hello")["engine"] == "claude"
    # An edit or an activated alternate version rewrote the line since.
    lines[0].en = "hi there"
    assert line_provenance_service.get(did, lines[0].id, current_en="hi there") is None
    info = debug_view.explain_line(did, lines[0], lines)
    assert info["provenance"] is None and info["model"] != "claude-x"
    assert "No per-line record" in info["prompt_version_note"]


def test_provenance_follows_a_mid_run_engine_fallback(isolated_db, monkeypatch):
    from services import workspace_job_service as wjs

    class Claude:
        model = "claude-x"

    class DeepSeek:
        model = "deepseek-chat"

    engine = translate_engines.FallbackEngine([Claude(), DeepSeek()], ["claude", "deepseek"])
    did = isolated_db.create_drama(title_en="F")
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="一", en=""),
                                 Line(idx=1, start=1, end=2, zh="二", en="")])
    lines = isolated_db.load_line_objects(did)

    def fake_translate(ls, eng, **kw):
        ls[0].en = "one"
        kw["save_cb"](ls)
        eng.active = 1   # quota hit on Claude: the run fell back to DeepSeek
        ls[1].en = "two"
        kw["save_cb"](ls)
        return ls, []

    monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)
    monkeypatch.setattr(wjs.bulk_translate, "finish_translation_run", lambda *a, **k: False)
    wjs.run_translate_job("translate_f", did, lines, engine, {}, "", "", False, "en-US",
                          [], "", "claude>deepseek", "default")
    assert line_provenance_service.get(did, lines[0].id)["engine"] == "claude"
    second = line_provenance_service.get(did, lines[1].id)
    assert (second["engine"], second["model"]) == ("deepseek", "deepseek-chat")


def test_an_old_runs_finish_does_not_close_a_new_run_of_the_same_job(isolated_db):
    old = job_timing_service.start_run("same", now=10.0)
    new = job_timing_service.start_run("same", now=20.0)    # restarted at once
    job_timing_service.finish_run("same", now=21.0, token=old)
    job_timing_service.mark_stage("same", "Translate", now=22.0)
    job_timing_service.finish_run("same", now=25.0, token=new)
    runs = job_timing_service.list_runs("same")
    assert [r["run_started_at"] for r in runs] == [20.0]
    assert [s["stage"] for s in runs[0]["stages"]] == ["Preparing", "Translate"]


def test_process_jobs_get_timing_too(isolated_db):
    background_jobs.start_process_job("proc_timing", _proc_ok, description="Proc")
    assert _wait("proc_timing")["status"] == "done"
    run = _wait_runs("proc_timing")[0]
    assert [s["stage"] for s in run["stages"]] == [job_timing_service.WHOLE_JOB]
    background_jobs.clear_job("proc_timing")


def test_a_reply_without_usable_terms_is_not_cached(isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(tguide, "call_llm_json",
                        lambda *a, **k: calls.append(1) or '["林", {"error": "x"}]')
    cache = glossary_service._novel_glossary_cache(_Engine(), "claude")
    tguide.extract_glossary_from_novel("林" * 2000, _Engine(), response_cache=cache)
    n = len(calls)
    tguide.extract_glossary_from_novel("林" * 2000, _Engine(), response_cache=cache)
    assert n and len(calls) == 2 * n


def test_a_broken_cache_never_fails_the_run(isolated_db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(cp, "cache_get", boom)
    monkeypatch.setattr(cp, "cache_put", boom)
    monkeypatch.setattr(tguide, "call_llm_json", lambda *a, **k: json.dumps(
        [{"term": "林", "suggested_translation": "Lin"}]))
    cache = glossary_service._novel_glossary_cache(_Engine(), "claude")
    assert tguide.extract_glossary_from_novel("林" * 2000, _Engine(), response_cache=cache)


def _proc_ok(result_queue):
    result_queue.put(("ok", None))


# --- lead review fixes ------------------------------------------------------------

def test_fresh_glossary_run_ignores_the_cache_but_refreshes_it(isolated_db, monkeypatch):
    replies = iter(["A", "B", "C"])
    calls = []

    def fake_llm(engine, prompt, **kw):
        calls.append(1)
        return json.dumps([{"term": next(replies), "suggested_translation": "x"}])

    monkeypatch.setattr(tguide, "call_llm_json", fake_llm)
    text = "林" * 2000
    first = tguide.extract_glossary_from_novel(
        text, _Engine(), response_cache=glossary_service._novel_glossary_cache(_Engine(), "c"))
    n = len(calls)
    assert n == 1 and [t["term"] for t in first] == ["A"]
    again = tguide.extract_glossary_from_novel(
        text, _Engine(), response_cache=glossary_service._novel_glossary_cache(_Engine(), "c"))
    assert len(calls) == 1 and [t["term"] for t in again] == ["A"]        # cached
    fresh = tguide.extract_glossary_from_novel(
        text, _Engine(),
        response_cache=glossary_service._novel_glossary_cache(_Engine(), "c", fresh=True))
    assert len(calls) == 2 and [t["term"] for t in fresh] == ["B"]        # asked again
    later = tguide.extract_glossary_from_novel(
        text, _Engine(), response_cache=glossary_service._novel_glossary_cache(_Engine(), "c"))
    assert [t["term"] for t in later] == ["B"]                            # new reply cached


def test_fresh_route_parameter_reaches_the_services(isolated_db, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.server import ApiSettings, create_app
    seen = {}
    monkeypatch.setattr(glossary_service, "novel_glossary_engine", lambda d: "ollama")
    monkeypatch.setattr(glossary_service, "start_novel_glossary_run",
                        lambda d, engine_name=None, fresh=False: seen.update(g=fresh) or
                        {"job_id": "novel_glossary_1", "engine": "ollama", "paired": False})
    monkeypatch.setattr(narration_service, "start_narration_run",
                        lambda d, engine_name=None, model=None, fresh=False:
                        seen.update(n=fresh) or {"job_id": "narration_1"})
    did = isolated_db.create_drama(title_en="F")
    c = TestClient(create_app(ApiSettings()), base_url="http://127.0.0.1:8600",
                   client=("127.0.0.1", 50000), raise_server_exceptions=False)
    r = c.post(f"/api/glossary/dramas/{did}/from-novel?fresh=true", json={})
    assert r.status_code == 200, r.text
    assert c.post(f"/api/narration/dramas/{did}/run?fresh=true",
                  json={"engine": "ollama"}).status_code == 200
    assert seen == {"g": True, "n": True}


def test_narration_start_over_drops_the_saved_batches(isolated_db, monkeypatch):
    did = _narration_setup(isolated_db, monkeypatch)
    calls, state = [], {"fail": True}

    def fake_batch(batch_ids, build, call_model):
        calls.append(batch_ids[0])
        if state["fail"] and len(calls) == 2:
            raise RuntimeError("killed")
        return {str(i): "A" for i in batch_ids}

    monkeypatch.setattr(translate_engines, "_id_keyed_batch_request", fake_batch)
    with pytest.raises(RuntimeError):
        narration_service._run_narration_job("j", did, "novel", "claude", "k", None)
    calls.clear()
    state["fail"] = False
    narration_service._run_narration_job("j", did, "novel", "claude", "k", None, fresh=True)
    assert calls == [0, 15, 30]


def test_changed_characters_or_prompt_version_invalidate_narration_checkpoints(
        isolated_db, monkeypatch):
    done, on_batch = narration_service.tagging_checkpoint(1, "t", "claude", "m", ["Ann"])
    on_batch({0: "Ann"})
    assert narration_service.tagging_checkpoint(1, "t", "claude", "m", ["Ann"])[0] == {0: "Ann"}
    assert narration_service.tagging_checkpoint(1, "t", "claude", "m", ["Ann", "Bo"])[0] == {}
    monkeypatch.setattr(translate_engines, "TAG_SPEAKERS_PROMPT_VERSION", "2")
    assert narration_service.tagging_checkpoint(1, "t", "claude", "m", ["Ann"])[0] == {}


def test_deleting_a_drama_drops_its_checkpoints_and_provenance(isolated_db):
    keep = isolated_db.create_drama(title_en="Keep")
    gone = isolated_db.create_drama(title_en="Gone")
    for did in (keep, gone):
        narration_service.tagging_checkpoint(did, "t", "c", "m", [])[1]({0: "A"})
        line_provenance_service.record(did, {7: ("zh", "en")}, "claude", "m", "1", "")
    isolated_db.delete_drama(gone)
    assert narration_service.tagging_checkpoint(gone, "t", "c", "m", [])[0] == {}
    assert line_provenance_service.get(gone, 7) is None
    assert narration_service.tagging_checkpoint(keep, "t", "c", "m", [])[0] == {0: "A"}
    assert line_provenance_service.get(keep, 7)["engine"] == "claude"


def test_checkpoints_older_than_30_days_are_swept(isolated_db):
    scope = cp.checkpoint_scope("k", 1, "h", "m")
    cp.record_unit(scope, 1, "old")
    assert cp.sweep_old(now=time.time() + 29 * 86400) == 0
    assert cp.sweep_old(now=time.time() + 31 * 86400) == 1
    assert cp.done_units(scope) == {}


def test_provenance_sentence_leaves_out_the_baihe_commit(isolated_db):
    line_provenance_service.record(1, {3: ("zh", "en")}, "claude", "m", "1", "")
    prov = line_provenance_service.get(1, 3)
    assert prov["software_version"]                                  # kept in the row
    assert prov["software_version"] not in line_provenance_service.describe(prov)
    assert "Baihe" not in line_provenance_service.describe(prov)


def test_nllb_pipeline_survives_a_release_between_check_and_read(monkeypatch):
    engine = translate_engines.NLLBEngine.__new__(translate_engines.NLLBEngine)
    engine.model_name = "fake-nllb"
    made = []
    fake = types.ModuleType("transformers")
    fake.pipeline = lambda *a, **k: made.append(1) or "PIPE"
    monkeypatch.setitem(sys.modules, "transformers", fake)

    class Vanishing(dict):   # release_gpu_models() clears it right after the check
        def get(self, key, default=None):
            value = dict.get(self, key, default)
            self.clear()
            return value

    cache = Vanishing({("fake-nllb", "zh", "en"): "OLD"})
    monkeypatch.setattr(translate_engines, "_nllb_pipeline_cache", cache)
    assert engine._get_pipeline("zh") == "OLD" and made == []


def test_cli_narrate_prep_resumes_and_can_start_over(isolated_db, monkeypatch):
    import argparse
    import os
    import cli
    import dub
    did = _narration_setup(isolated_db, monkeypatch)
    with open(os.path.join(isolated_db.drama_dir(did), dub.NOVEL_SOURCE_FILENAME), "w",
              encoding="utf-8") as f:
        f.write("novel")
    monkeypatch.setattr(cli, "chunk_novel_text", lambda text: [f"段落{i}" for i in range(40)])
    calls, state = [], {"fail": True}

    def fake_batch(batch_ids, build, call_model):
        calls.append(batch_ids[0])
        if state["fail"] and len(calls) == 2:
            raise RuntimeError("killed")
        return {str(i): "A" for i in batch_ids}

    monkeypatch.setattr(translate_engines, "_id_keyed_batch_request", fake_batch)
    args = argparse.Namespace(id=did, engine="claude", api_key="k", model=None,
                              ollama_url=None, fresh=False)
    try:
        cli.cmd_narrate_prep(args)
    except (RuntimeError, SystemExit):
        pass
    calls.clear()
    state["fail"] = False
    cli.cmd_narrate_prep(args)
    assert calls == [15, 30]                       # resumed, like the API job
    assert len(isolated_db.load_line_objects(did)) == 40


def test_cli_translate_records_provenance(isolated_db, monkeypatch):
    import argparse
    import contextlib
    import io
    import cli
    from services import workspace_job_service as wjs
    did = isolated_db.create_drama(title_en="C", status="aligned")
    isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="")])
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: _Engine())

    def fake_translate(lines, engine, **kw):
        lines[0].en = "hello"
        kw["save_cb"](lines)
        return lines, []

    monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)
    args = argparse.Namespace(id=did, status=None, engine="claude", api_key="k", model=None,
                              style_note="Keep it short", style_preset="novel",
                              locale="en-GB", force=False, ollama_num_ctx=None)
    with contextlib.redirect_stdout(io.StringIO()):
        cli.cmd_translate(args)
    line = isolated_db.load_line_objects(did)[0]
    prov = line_provenance_service.get(did, line.id, current_en=line.en)
    assert line.en == "hello" and prov is not None
    assert (prov["engine"], prov["model"]) == ("claude", "fake-model")
    assert prov["prompt_version"] == translate_engines.TRANSLATE_PROMPT_VERSION
    # The same settings the Workspace job records: a run with only the style
    # preset changed hashes differently, so the preset is in the record.
    captured = {}
    real = line_provenance_service.translate_run_tracker
    monkeypatch.setattr(line_provenance_service, "translate_run_tracker",
                        lambda *a, **k: captured.update(k) or real(*a, **k))
    isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="")])
    with contextlib.redirect_stdout(io.StringIO()):
        cli.cmd_translate(args)
    assert captured["style_preset"] == "novel"
    assert captured["style_note"] == "Keep it short"
    assert captured["style_guidelines"]
    assert captured["locale"] == "en-GB"
    workspace_keys = {"locale", "style_preset", "reflect", "context_window",
                      "context_window_ahead", "batch_size", "style_note", "style_guidelines"}
    assert set(captured) == workspace_keys
    import inspect
    assert all(k in inspect.getsource(wjs.run_translate_job) for k in workspace_keys)


# --- stages route with auth on (security review) --------------------------------

def _session(auth_service, email, *perms):
    u = auth_service.add_user(email)
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    return u["id"], auth_service.create_session(u["id"], "pytest", "203.0.113.9")


def test_stages_route_with_auth_on_hides_other_users_jobs_and_runs(isolated_db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api import auth as api_auth
    from api.server import ApiSettings, create_app
    from services import auth_service
    kid_id, kid = _session(auth_service, "kid@example.com", "library.read")
    other_id, _ = _session(auth_service, "other@example.com", "library.read")
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={kid['session_token']}",
         api_auth.CSRF_HEADER: kid["csrf_token"]}

    def run(job_id, start, owner):
        job_timing_service.start_run(job_id, now=start)
        job_timing_service.finish_run(job_id, now=start + 2)
        # As background_jobs mirrors a run: running (takes the owner), then done.
        isolated_db.save_job_record(job_id, status="running", description="j",
                                    started_at=start, owner_user_id=owner)
        isolated_db.save_job_record(job_id, status="done", progress=1.0, description="j",
                                    started_at=start, finished_at=start + 2,
                                    owner_user_id=owner)

    run("discover_other", 100.0, other_id)                 # another user's non-drama job
    private = isolated_db.create_drama(title_en="P", owner_user_id=other_id, is_private=1)
    run(f"translate_{private}", 100.0, other_id)           # a drama the kid can't see
    run("sources_search", 100.0, other_id)                 # shared id: the other user's run...
    run("sources_search", 200.0, kid_id)                   # ...then the kid's own
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                   raise_server_exceptions=False)
    assert c.get("/api/jobs/discover_other/stages", headers=h).status_code == 404
    assert c.get(f"/api/jobs/translate_{private}/stages", headers=h).status_code == 404
    r = c.get("/api/jobs/sources_search/stages", headers=h)
    assert r.status_code == 200
    assert [x["run_started_at"] for x in r.json()["runs"]] == [200.0]
    assert c.get("/api/jobs/sources_search/stages").status_code == 401


def test_provenance_record_failure_is_logged(isolated_db, monkeypatch):
    import applog
    from core import Line
    seen = []

    class Log:
        def warning(self, msg, *args):
            seen.append(msg % args)
    monkeypatch.setattr(applog, "get_logger", lambda: Log())

    def boom(*a, **k):
        raise RuntimeError("db locked")
    monkeypatch.setattr(line_provenance_service, "record", boom)
    lines = [Line(id=1, idx=0, start=0, end=1, zh="a", en="")]
    on_save = line_provenance_service.tracker(1, lines, lambda: ("claude", "m"), "1", [])
    lines[0].en = "hello"
    on_save(lines)
    assert len(seen) == 1 and "db locked" in seen[0]
