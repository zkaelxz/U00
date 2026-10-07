"""Auto-tune (transcribe_service) and glossary-from-novel (glossary_service)
service jobs. Fully mocked: no audio models, no LLM, no network."""
import os
import time

import pytest

import background_jobs
import core
import translation_guide as tguide
from services import glossary_service as gs
from services import settings_service, transcribe_service as ts, translate_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, UnsupportedOperationError)

SECRET = "sk-ant-api03-SECRETSECRETSECRETSECRET"


class _Queue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def _audio_drama(db, **fields):
    did = db.create_drama(title_en="D", audio_filename="audio.wav", **fields)
    ddir = db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, "audio.wav"), "wb").close()
    return did


def _fake_done(monkeypatch, job_id, result):
    real = background_jobs.get_status
    monkeypatch.setattr(background_jobs, "get_status", lambda jid: (
        {"status": "done", "result": result} if jid == job_id else real(jid)))


# ----- auto-tune -------------------------------------------------------------

class TestAutotune:
    def test_score_matches_tab_rule(self):
        segs = [{"start": 0, "end": 30, "text": "长" * 5}, {"start": 30, "end": 31, "text": " "},
                {"start": 31, "end": 32, "text": "好"}]
        r = ts.score_autotune_segments(800, segs)
        assert r == {"candidate_ms": 800, "total_lines": 2,
                     "long_lines": len(core.diagnose_line_coverage(
                         [core.Line(idx=0, start=0, end=30, zh="长" * 5),
                          core.Line(idx=2, start=31, end=32, zh="好")])["long_lines"])}
        assert r["long_lines"] == 1

    def test_start_passes_drama_settings_and_worker_scores(self, isolated_db, monkeypatch):
        did = _audio_drama(isolated_db, whisper_size="small", beam_size=7)
        monkeypatch.setattr(settings_service, "resolve_key",
                            lambda k, *a: SECRET if k == "hf_token" else None)
        captured = {}
        monkeypatch.setattr(background_jobs, "start_process_job",
                            lambda job_id, target, args=(), **kw: captured.update(
                                job_id=job_id, target=target, args=args) or True)
        out = ts.start_autotune_run(did, candidates=[300, 1500])
        assert out == {"job_id": f"autotune_{did}", "candidates": [300, 1500]}
        assert captured["args"][1] == "small" and captured["args"][6] == 7

        calls = []

        def fake_transcribe(path, size, **kw):
            calls.append(kw["min_silence_duration_ms"])
            if kw["min_silence_duration_ms"] == 300:
                return [{"start": 0, "end": 2, "text": "a"}, {"start": 2, "end": 4, "text": "b"}]
            return [{"start": 0, "end": 40, "text": "ab"}]
        monkeypatch.setattr(core, "transcribe_for_timing", fake_transcribe)
        q = _Queue()
        captured["target"](*captured["args"], q)
        assert calls == [300, 1500]
        final = q.items[-1]
        assert final[0] == "ok"
        assert final[1]["best_candidate_ms"] == 300
        assert [r["candidate_ms"] for r in final[1]["results"]] == [300, 1500]
        assert SECRET not in repr(final)

    def test_worker_scores_with_the_titles_repeat_guard(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(core, "transcribe_for_timing",
                            lambda *a, **kw: seen.update(kw) or [])
        ts._autotune_all_worker("a", "small", "zh", False, None, "", 5, [300], 0.5, False,
                                "normal", True, _Queue())
        assert seen["repeat_guard"] is True

    def test_start_passes_the_titles_repeat_guard(self, isolated_db, monkeypatch):
        did = _audio_drama(isolated_db, whisper_repeat_guard=1)
        captured = {}
        monkeypatch.setattr(background_jobs, "start_process_job",
                            lambda job_id, target, args=(), **kw: captured.update(args=args) or True)
        ts.start_autotune_run(did, candidates=[300])
        assert captured["args"][-1] is True

    def test_refused_while_lines_split_by_sentences(self, isolated_db, monkeypatch):
        # That run uses a fixed silence, so a tuned min_silence would change nothing.
        did = _audio_drama(isolated_db, split_by_sentences=1, asr_backend_choice="whisper")
        with pytest.raises(UnsupportedOperationError, match="Split lines by sentences"):
            ts.start_autotune_run(did, candidates=[300])

    def test_worker_error_is_redacted(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError(f"bad token {SECRET}")
        monkeypatch.setattr(core, "transcribe_for_timing", boom)
        q = _Queue()
        ts._autotune_all_worker("a", "small", "zh", False, SECRET, "", 5, [300], 0.5, False, "normal", False, q)
        assert q.items[-1][0] == "error" and SECRET not in repr(q.items[-1])

    def test_bad_input(self, isolated_db):
        did = _audio_drama(isolated_db)
        for bad in ([], [99], [300, 300], [True], list(range(300, 1000, 100))):
            with pytest.raises(InvalidInputError):
                ts.start_autotune_run(did, candidates=bad)
        nod = isolated_db.create_drama(title_en="none")
        with pytest.raises(UnsupportedOperationError):
            ts.start_autotune_run(nod)

    def test_conflict(self, isolated_db, monkeypatch):
        did = _audio_drama(isolated_db)
        monkeypatch.setattr(background_jobs, "start_process_job", lambda *a, **k: False)
        with pytest.raises(ConflictError):
            ts.start_autotune_run(did)

    def test_apply_is_field_scoped_and_per_drama(self, isolated_db, monkeypatch):
        did = _audio_drama(isolated_db, beam_size=8, vad_threshold=0.6)
        other = _audio_drama(isolated_db)
        isolated_db.update_drama(other, min_silence_ms=500)
        with pytest.raises(UnsupportedOperationError):
            ts.apply_autotune_candidate(did, 800)
        _fake_done(monkeypatch, f"autotune_{did}", {"results": [
            {"candidate_ms": 300, "long_lines": 2, "total_lines": 9},
            {"candidate_ms": 800, "long_lines": 0, "total_lines": 12}]})
        with pytest.raises(InvalidInputError):
            ts.apply_autotune_candidate(did, 1500)
        before = isolated_db.get_drama(did)
        cfg = ts.apply_autotune_candidate(did, 800)
        after = isolated_db.get_drama(did)
        assert cfg["min_silence_ms"] == 800
        assert {k for k in after if after[k] != before[k]} <= {"min_silence_ms", "updated_at"}
        assert isolated_db.get_drama(other)["min_silence_ms"] == 500
        with pytest.raises(UnsupportedOperationError):  # other drama has no results
            ts.apply_autotune_candidate(other, 800)

    def test_no_paid_engine(self):
        assert ts.PAID_ENGINE_FUNCTIONS == ()


# ----- glossary from novel ---------------------------------------------------

class _Engine:
    supports_reference = True
    model = "fake-model"


def _novel_drama(db, orig="原文", novel="English", engine="claude"):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="N", series_id=sid, translation_engine=engine,
                          novel_reference_filename="novel.txt" if novel else None)
    ddir = db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    if orig:
        with open(os.path.join(ddir, "raw_novel_context.txt"), "w", encoding="utf-8") as f:
            f.write(orig)
    if novel:
        with open(os.path.join(ddir, "novel.txt"), "w", encoding="utf-8") as f:
            f.write(novel)
    return did, sid


def _wait(job_id):
    for _ in range(200):
        st = background_jobs.get_status(job_id)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.fixture
def fake_engine(monkeypatch):
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, *a: SECRET)
    built = {}
    monkeypatch.setattr(gs.translate_engines, "get_engine",
                        lambda name, key, **kw: built.update(name=name, key=key) or _Engine())
    return built


class TestNovelGlossary:
    def test_paid_exposure(self, isolated_db):
        assert gs.PAID_ENGINE_FUNCTIONS == ("start_novel_glossary_run", "start_lines_glossary_run")
        did, _ = _novel_drama(isolated_db, engine="deepseek")
        assert gs.novel_glossary_engine(did) == "deepseek"

    def test_run_pairs_sources_and_hides_key(self, isolated_db, monkeypatch, fake_engine):
        did, sid = _novel_drama(isolated_db)
        isolated_db.upsert_glossary_term(sid, "师尊", "Master")
        seen = {}

        def fake_extract(src, engine, **kw):
            seen.update(src=src, en=kw["english_translation"],
                        known=[t["term_original"] for t in kw["known_terms"]])
            kw["usage_cb"](10, 5)
            # reordered, duplicated, malformed, and a term already present
            return [{"term": "青云宗", "suggested_translation": "Qingyun Sect",
                     "category": "sect", "policy": "hybrid", "reason": "sect"},
                    {"term": 5}, "junk", {"suggested_translation": "no term"},
                    {"term": "师尊", "suggested_translation": "Teacher", "category": "title",
                     "policy": "translate", "reason": ""},
                    {"term": "沈清", "suggested_translation": "Shen Qing", "category": "bogus",
                     "policy": "bogus", "reason": ""}]
        monkeypatch.setattr(tguide, "extract_glossary_from_novel", fake_extract)
        out = gs.start_novel_glossary_run(did)
        assert out == {"job_id": f"novel_glossary_{did}", "engine": "claude", "paired": True}
        assert fake_engine["key"] == SECRET and SECRET not in repr(out)
        st = _wait(out["job_id"])
        assert st["status"] == "done", st.get("error")
        assert seen == {"src": "原文", "en": "English", "known": ["师尊"]}
        props = {p["term"]: p for p in st["result"]["proposals"]}
        assert set(props) == {"青云宗", "师尊", "沈清"}
        assert props["师尊"]["already_in_glossary"] and not props["青云宗"]["already_in_glossary"]
        assert props["沈清"]["category"] is None and props["沈清"]["policy"] is None
        assert SECRET not in repr(st)

    def test_novel_only_is_source(self, isolated_db, monkeypatch, fake_engine):
        did, _ = _novel_drama(isolated_db, orig=None)
        seen = {}
        monkeypatch.setattr(tguide, "extract_glossary_from_novel",
                            lambda src, e, **kw: seen.update(src=src, en=kw["english_translation"]) or [])
        out = gs.start_novel_glossary_run(did)
        assert out["paired"] is False
        _wait(out["job_id"])
        assert seen == {"src": "English", "en": ""}

    def test_engine_error_redacted(self, isolated_db, monkeypatch, fake_engine):
        did, _ = _novel_drama(isolated_db)

        def boom(*a, **kw):
            raise RuntimeError(f"401 for key {SECRET}")
        monkeypatch.setattr(tguide, "extract_glossary_from_novel", boom)
        st = _wait(gs.start_novel_glossary_run(did)["job_id"])
        assert st["status"] == "error" and SECRET not in repr(st)

    def test_start_refusals(self, isolated_db, monkeypatch):
        nos = isolated_db.create_drama(title_en="x")
        with pytest.raises(UnsupportedOperationError):
            gs.start_novel_glossary_run(nos)
        did, _ = _novel_drama(isolated_db, orig=None, novel=None)
        with pytest.raises(UnsupportedOperationError):
            gs.start_novel_glossary_run(did)
        did, _ = _novel_drama(isolated_db)
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda *a: None)
        with pytest.raises(DependencyUnavailableError):
            gs.start_novel_glossary_run(did)
        did2, _ = _novel_drama(isolated_db, engine="nllb")
        with pytest.raises(UnsupportedOperationError):
            gs.start_novel_glossary_run(did2)

    def test_apply_by_term_preserves_user_edits(self, isolated_db, monkeypatch):
        did, sid = _novel_drama(isolated_db)
        isolated_db.upsert_glossary_term(sid, "师尊", "Shizun (user)", notes="mine",
                                         enforce_exact=True)
        _fake_done(monkeypatch, f"novel_glossary_{did}", {"proposals": [
            {"term": "师尊", "suggested_translation": "Teacher", "category": "title",
             "policy": "translate", "reason": "r1", "already_in_glossary": True},
            {"term": "青云宗", "suggested_translation": "Qingyun Sect", "category": "sect",
             "policy": "hybrid", "reason": "r2", "already_in_glossary": False},
            {"term": "空", "suggested_translation": "", "category": None, "policy": None,
             "reason": "", "already_in_glossary": False}]})
        # caller's order differs from the proposal order; includes unknowns
        rep = gs.apply_novel_glossary(did, ["青云宗", "不存在", "师尊", "空", "青云宗"])
        assert rep == {"added": ["青云宗"], "overwritten": [],
                       "skipped_existing": ["师尊"], "unknown": ["不存在", "空"]}
        terms = {t["term_original"]: t for t in isolated_db.list_glossary_terms(sid)}
        assert terms["师尊"]["term_translation"] == "Shizun (user)"
        assert terms["青云宗"]["term_translation"] == "Qingyun Sect"
        assert terms["青云宗"]["notes"] == "r2"

        rep = gs.apply_novel_glossary(did, ["师尊"], overwrite_existing=True)
        assert rep["overwritten"] == ["师尊"]
        t = {t["term_original"]: t for t in isolated_db.list_glossary_terms(sid)}["师尊"]
        assert t["term_translation"] == "Teacher" and t["enforce_exact"]

    def test_run_id_names_each_run_and_a_stale_apply_writes_nothing(
            self, isolated_db, monkeypatch, fake_engine):
        """Another tab re-ran the extraction: an apply of the run the user
        reviewed is refused (ConflictError) and nothing is written."""
        did, sid = _novel_drama(isolated_db)
        answers = iter([
            [{"term": "青云宗", "suggested_translation": "Qingyun Sect", "category": "sect",
              "policy": "hybrid", "reason": "first"}],
            [{"term": "青云宗", "suggested_translation": "Blue Cloud Sect", "category": "place",
              "policy": "translate", "reason": "second"}]])
        monkeypatch.setattr(tguide, "extract_glossary_from_novel", lambda *a, **kw: next(answers))
        _wait(gs.start_novel_glossary_run(did)["job_id"])
        first = gs.get_novel_glossary_status(did)["run_id"]
        assert first and isinstance(first, str)
        _wait(gs.start_novel_glossary_run(did)["job_id"])
        second = gs.get_novel_glossary_status(did)["run_id"]
        assert second and second != first
        with pytest.raises(ConflictError, match="review again"):
            gs.apply_novel_glossary(did, ["青云宗"], run_id=first,
                                    overrides={"青云宗": {"translation": "Qingyun Sect"}})
        assert isolated_db.list_glossary_terms(sid) == []
        rep = gs.apply_novel_glossary(did, ["青云宗"], run_id=second)
        assert rep["added"] == ["青云宗"]
        t = isolated_db.list_glossary_terms(sid)[0]
        assert (t["term_translation"], t["category"]) == ("Blue Cloud Sect", "place")

    def test_apply_refusals(self, isolated_db):
        did, _ = _novel_drama(isolated_db)
        with pytest.raises(UnsupportedOperationError):
            gs.apply_novel_glossary(did, ["x"])
        # a token for a run no longer held (app restarted) is a changed run
        with pytest.raises(ConflictError):
            gs.apply_novel_glossary(did, ["x"], run_id="gone")
        for bad in ("", 5, "x" * 65):
            with pytest.raises(InvalidInputError):
                gs.apply_novel_glossary(did, ["x"], run_id=bad)
        for bad in ([], "x", [1]):
            with pytest.raises(InvalidInputError):
                gs.apply_novel_glossary(did, bad)
        with pytest.raises(InvalidInputError):
            gs.apply_novel_glossary(did, ["x"], overwrite_existing="yes")


def test_novel_glossary_job_blocks_drama_delete():
    """A running paid glossary extraction must make a drama delete refuse."""
    import background_jobs
    from services import glossary_service

    assert "novel_glossary_" in background_jobs.DRAMA_JOB_PREFIXES
    job_id = glossary_service.novel_glossary_job_id(987654)
    with background_jobs._lock:
        background_jobs._jobs[job_id] = {"status": "running"}
    try:
        assert background_jobs.any_job_running_for_drama(987654)
    finally:
        with background_jobs._lock:
            background_jobs._jobs.pop(job_id, None)
