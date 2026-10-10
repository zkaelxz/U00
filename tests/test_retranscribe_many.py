"""Review's "Re-transcribe selected": services/retranscribe_many_service.py,
services/retranscribe_worker.py's many-line worker and its routes under
/api/transcribe/dramas/{id}/retranscribe-lines. ffmpeg and Whisper are faked:
no audio model, no network."""

import os
import queue
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

import background_jobs
import bulk_translate
import core
import whisper_models
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import (auth_service, jobs_service, ownership_service, retranscribe_many_service as svc,
                      retranscribe_worker, transcribe_service)
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)

SECRET = "sk-ant-api03-SECRETSECRETSECRETSECRET"
_PREFIXES = ("retranscribe_", "transcribe_", "fixflag_", "resegment_", "narration_", "translate_")


@pytest.fixture(autouse=True)
def _no_leftover_jobs():
    def clear():
        with background_jobs._lock:
            for jid in [j for j in background_jobs._jobs if j.startswith(_PREFIXES)]:
                background_jobs._jobs.pop(jid, None)
    clear()
    yield
    clear()


def _put_job(job_id, status="running"):
    with background_jobs._lock:
        background_jobs._jobs[job_id] = {
            "status": status, "progress": 0.0, "message": "", "result": None, "error": None,
            "started_at": time.time(), "finished_at": None, "cancel_requested": False}


def _drama(isolated_db, audio=True, content_mode=None, n=4):
    sid = isolated_db.get_or_create_series("S")
    kw = {"content_mode": content_mode} if content_mode else {}
    did = isolated_db.create_drama(title_en="D", series_id=sid, audio_filename="audio.wav",
                                   whisper_size="small", beam_size=7, source_language="zh",
                                   status="translated", **kw)
    if audio:
        with open(os.path.join(isolated_db.drama_dir(did), "audio.wav"), "wb") as f:
            f.write(b"x")
    isolated_db.save_lines(did, [
        Line(idx=i, start=float(i * 2), end=float(i * 2 + 1.5), zh=f"旧{i}", en=f"Old {i}",
             speaker="A" if i % 2 else "B", flag="unsure" if i == 0 else None,
             flag_note="check" if i == 0 else "")
        for i in range(n)])
    return did, [ln.id for ln in isolated_db.load_line_objects(did)]


def _run_worker_inline(target, args):
    q = queue.Queue()
    target(*args, q)
    items = []
    while not q.empty():
        items.append(q.get())
    return [i for i in items if i[0] in ("ok", "error")][-1]


@pytest.fixture
def inline_process_jobs(monkeypatch):
    """Runs start_process_job's worker and on_done/on_finish on a thread job,
    so the fake Whisper can be patched in (a spawned child could not)."""
    monkeypatch.setattr(background_jobs, "start_own_process_group", lambda: None)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    calls = {"started": 0}

    def fake_start(job_id, target, args=(), gpu_touching=False, description=None,
                   on_done=None, on_finish=None, initial_result=None, **kw):
        calls["started"] += 1
        calls["kwargs"] = {**kw, "gpu_touching": gpu_touching}
        calls["description"] = description

        def body():
            background_jobs.set_result(job_id, initial_result, mirror=True)
            try:
                kind, *payload = _run_worker_inline(target, args)
                if kind == "ok":
                    background_jobs.set_result(job_id, on_done(job_id, payload[0]))
                else:
                    raise RuntimeError(payload[1])
            finally:
                if on_finish:
                    on_finish(job_id)
        return background_jobs.start_job(job_id, body, gpu_touching=gpu_touching,
                                         description=description)
    monkeypatch.setattr(background_jobs, "start_process_job", fake_start)
    return calls


@pytest.fixture
def fake_asr(monkeypatch, inline_process_jobs):
    """Fakes ffmpeg + Whisper. `heard` maps a window start to what Whisper
    hears there (default: 新<start>)."""
    calls = {"heard": {}, "slices": [], "transcribe": [], "slice_fail": set(), "started":
             inline_process_jobs}

    def fake_slice(audio_path, start, end, out_path, timeout=None):
        calls["slices"].append((start, end))
        if start in calls["slice_fail"]:
            raise RuntimeError(f"ffmpeg failed on {audio_path}")
        calls["current"] = start
        with open(out_path, "wb") as f:
            f.write(b"slice")

    def fake_transcribe(path, model_size="medium", **kw):
        calls["transcribe"].append({"model_size": model_size, **kw})
        text = calls["heard"].get(calls["current"], f"新{int(calls['current'])}")
        return [{"start": 0.0, "end": 1.0, "text": text}] if text else []

    monkeypatch.setattr(core, "extract_audio_slice", fake_slice)
    monkeypatch.setattr(core, "transcribe_for_timing", fake_transcribe)
    monkeypatch.setattr(whisper_models, "is_whisper_model_cached", lambda size: True)
    return calls


def _wait(job_id, timeout=30):
    deadline = time.time() + timeout
    while background_jobs.get_status(job_id)["status"] in ("running", "queued"):
        assert time.time() < deadline, "the job never ended"
        time.sleep(0.02)
    return background_jobs.get_status(job_id)


def _run(did, ids, **kw):
    out = svc.start_retranscribe_many(did, ids, **kw)
    return out, _wait(out["job_id"])


def _items(did, ids=None):
    res = svc.get_retranscribe_many_result(did)
    return [{"line_id": p["line_id"], "expected_zh": p["base_zh"],
             "expected_proposed": p["proposed_zh"]}
            for p in res["proposals"] if ids is None or p["line_id"] in ids]


class TestStart:
    def test_one_gpu_process_job_in_title_order_with_the_one_line_settings(
            self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        out, job = _run(did, [ids[2], ids[0]], extra_names="沈清疑")
        assert out == {"job_id": f"retranscribe_{did}", "drama_id": did, "line_count": 2}
        assert job["status"] == "done"
        kw = fake_asr["started"]["kwargs"]
        assert (kw["kill_whole_tree"], kw["start_method"], kw["gpu_touching"]) == (True, "spawn", True)
        # Title order, not the order the ids were sent in; one process for all.
        assert fake_asr["slices"] == [(0.0, 1.5), (4.0, 5.5)]
        assert fake_asr["started"]["started"] == 1
        # The one-line job's settings, and Fast mode never on.
        assert len(fake_asr["transcribe"]) == 2
        for t in fake_asr["transcribe"]:
            assert t["fast_mode"] is False
            assert t["model_size"] == "small" and t["beam_size"] == 7
            assert t["language"] == "zh"
            assert t["initial_prompt"] == "沈清疑。"

    def test_timeout_counts_every_window_because_whisper_pads_each_clip(
            self, isolated_db, fake_asr, monkeypatch):
        did, ids = _drama(isolated_db)
        seen = []
        real = svc.retranscribe_timeout_s
        monkeypatch.setattr(svc, "retranscribe_timeout_s",
                            lambda seconds, count=1: seen.append(count) or real(seconds, count))
        _run(did, [ids[2], ids[0]])
        assert seen == [2]

    def test_settings_match_the_one_line_job(self, isolated_db, monkeypatch):
        did, ids = _drama(isolated_db)
        isolated_db.update_drama(did, whisper_fast_mode=1, min_silence_ms=420,
                                 whisper_repeat_guard=1)
        seen = {}

        def capture(tag):
            def fake(job_id, target, args=(), **kw):
                seen[tag] = (target.__name__, args)
                return True
            return fake
        monkeypatch.setattr(background_jobs, "start_process_job", capture("one"))
        transcribe_service.start_retranscribe_line(did, ids[0])
        monkeypatch.setattr(background_jobs, "start_process_job", capture("many"))
        svc.start_retranscribe_many(did, [ids[0]])
        one_name, one = seen["one"]
        many_name, many = seen["many"]
        assert (one_name, many_name) == ("retranscribe_worker", "retranscribe_many_worker")
        # one: audio, start, end, language, <settings...>; many: audio, windows, <settings...>
        assert one[0] == many[0]
        assert one[3] == many[1][0][3]
        assert one[4:-3] == many[2:-3]
        assert one[-3] != 0 and many[-3] != 0

    def test_cap_and_bad_selection(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        for bad in ([], None, "x", [True], [ids[0], "a"]):
            with pytest.raises(InvalidInputError):
                svc.start_retranscribe_many(did, bad)
        with pytest.raises(InvalidInputError):
            svc.start_retranscribe_many(did, list(range(1, svc.MAX_LINES + 2)))
        assert fake_asr["started"]["started"] == 0

    def test_unknown_and_foreign_lines_404(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _other, other_ids = _drama(isolated_db)
        with pytest.raises(NotFoundError):
            svc.start_retranscribe_many(999999, ids)
        with pytest.raises(NotFoundError):
            svc.start_retranscribe_many(did, [ids[0], other_ids[0]])
        with pytest.raises(NotFoundError):
            svc.start_retranscribe_many(did, [ids[0], 999999])
        assert fake_asr["started"]["started"] == 0

    def test_no_audio_pipeline_or_window(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db, audio=False)
        with pytest.raises(UnsupportedOperationError):
            svc.start_retranscribe_many(did, ids)
        did2, ids2 = _drama(isolated_db, content_mode="novel_narration")
        with pytest.raises(UnsupportedOperationError):
            svc.start_retranscribe_many(did2, ids2)
        did3, _ = _drama(isolated_db)
        isolated_db.save_lines(did3, [Line(idx=9, start=2.0, end=2.0, zh="x")])
        zero = [ln.id for ln in isolated_db.load_line_objects(did3) if ln.idx == 9][0]
        with pytest.raises(UnsupportedOperationError):
            svc.start_retranscribe_many(did3, [zero])
        assert fake_asr["started"]["started"] == 0

    @pytest.mark.parametrize("prefix", ["transcribe_", "fixflag_", "resegment_", "narration_"])
    @pytest.mark.parametrize("status", ["running", "queued"])
    def test_409_while_a_line_writing_job_runs(self, isolated_db, fake_asr, prefix, status):
        did, ids = _drama(isolated_db)
        _put_job(f"{prefix}{did}", status)
        with pytest.raises(ConflictError):
            svc.start_retranscribe_many(did, ids)
        assert fake_asr["started"]["started"] == 0

    def test_en_only_jobs_do_not_block(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _put_job(f"translate_{did}")
        svc.start_retranscribe_many(did, ids)
        assert fake_asr["started"]["started"] == 1
        _wait(f"retranscribe_{did}")

    def test_second_run_for_the_same_drama_is_refused_in_both_directions(
            self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _put_job(f"retranscribe_{did}")
        with pytest.raises(ConflictError):
            svc.start_retranscribe_many(did, ids)
        with pytest.raises(ConflictError):
            transcribe_service.start_retranscribe_line(did, ids[0])

    def test_another_drama_is_not_blocked(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        other, other_ids = _drama(isolated_db)
        _put_job(f"retranscribe_{other}")
        svc.start_retranscribe_many(did, ids)
        _wait(f"retranscribe_{did}")

    def test_real_start_refusal_removes_the_scratch_folder(self, isolated_db, monkeypatch):
        import storage
        did, ids = _drama(isolated_db)
        monkeypatch.setattr(background_jobs, "start_process_job", lambda *a, **k: False)
        before = set(os.listdir(storage.temp_root()))
        with pytest.raises(ConflictError):
            svc.start_retranscribe_many(did, ids)
        assert set(os.listdir(storage.temp_root())) == before


class TestJob:
    def test_proposes_and_writes_nothing(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        before = [(ln.id, ln.zh, ln.en, ln.start, ln.end, ln.flag, ln.speaker)
                  for ln in isolated_db.load_line_objects(did)]
        _out, job = _run(did, ids)
        assert job["status"] == "done"
        result = svc.get_retranscribe_many_result(did)
        assert [(p["line_id"], p["number"], p["base_zh"], p["proposed_zh"], p["had_english"])
                for p in result["proposals"]] == [
            (ids[i], i + 1, f"旧{i}", f"新{i * 2}", True) for i in range(4)]
        assert result["job_id"] == f"retranscribe_{did}"
        assert [(ln.id, ln.zh, ln.en, ln.start, ln.end, ln.flag, ln.speaker)
                for ln in isolated_db.load_line_objects(did)] == before
        assert isolated_db.list_line_history(did) == []

    def test_job_record_exposes_counts_only(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        fake_asr["heard"][2.0] = ""
        fake_asr["heard"][4.0] = "旧2"
        _out, job = _run(did, ids)
        projected = jobs_service.project_result(job["result"])
        assert projected == {"line_count": 4, "candidate_count": 2, "failed_count": 1,
                             "skipped_count": 1}
        text = str(projected)
        assert "新" not in text and "旧" not in text and "Old" not in text

    def test_jobs_route_never_shows_proposal_text(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _run(did, ids)
        client = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                            headers={"X-Baihe-Local": "1"})
        body = client.get(f"/api/jobs/retranscribe_{did}")
        assert body.status_code == 200, body.text
        for secret_text in ("新", "旧", "Old"):
            assert secret_text not in body.text
        assert isolated_db.drama_dir(did) not in body.text
        assert ownership_service.drama_id_of_job(f"retranscribe_{did}") == did

    def test_unchanged_empty_and_unreadable_lines_have_no_proposal(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        fake_asr["heard"][2.0] = "旧1"      # same as now
        fake_asr["heard"][4.0] = " "        # nothing heard
        fake_asr["slice_fail"].add(6.0)     # the cut fails; the run goes on
        _out, job = _run(did, ids)
        assert job["status"] == "done"
        res = svc.get_retranscribe_many_result(did)
        assert [p["line_id"] for p in res["proposals"]] == [ids[0]]
        assert res["unchanged_count"] == 1
        assert res["failures"] == [{"line_id": ids[2], "number": 3, "reason": "empty"},
                                   {"line_id": ids[3], "number": 4, "reason": "audio_slice"}]
        assert isolated_db.drama_dir(did) not in str(res)

    def test_model_download_failure_stops_the_run_and_is_redacted(
            self, isolated_db, fake_asr, monkeypatch):
        def boom(*a, **k):
            raise whisper_models.ModelDownloadError(f"download failed token={SECRET}")
        monkeypatch.setattr(core, "transcribe_for_timing", boom)
        did, ids = _drama(isolated_db)
        _out, job = _run(did, ids)
        assert job["result"]["failed_reason"] == "model_download"
        assert SECRET not in str(job["result"])
        assert len(fake_asr["slices"]) == 1
        with pytest.raises(NotFoundError):
            svc.get_retranscribe_many_result(did)

    def test_line_deleted_while_it_ran_is_reported_not_proposed(self, isolated_db, fake_asr,
                                                                 monkeypatch):
        did, ids = _drama(isolated_db)
        real = core.transcribe_for_timing

        def delete_then_hear(*a, **k):
            if not fake_asr.get("deleted"):
                fake_asr["deleted"] = True
                isolated_db.save_lines(did, [ln for ln in isolated_db.load_line_objects(did)
                                             if ln.id != ids[3]])
            return real(*a, **k)
        monkeypatch.setattr(core, "transcribe_for_timing", delete_then_hear)
        _run(did, ids)
        res = svc.get_retranscribe_many_result(did)
        assert ids[3] not in [p["line_id"] for p in res["proposals"]]
        assert {"line_id": ids[3], "number": 4, "reason": "line_gone"} in res["failures"]

    def test_memory_ceiling_drops_extra_proposals(self, isolated_db, fake_asr, monkeypatch):
        monkeypatch.setattr(retranscribe_worker, "MAX_HELD_CHARS", 30)
        did, ids = _drama(isolated_db)
        fake_asr["heard"] = {float(i * 2): "长" * 10 for i in range(4)}
        _run(did, ids)
        res = svc.get_retranscribe_many_result(did)
        assert 0 < len(res["proposals"]) < 4 and res["truncated"] is True

    def test_proposal_text_is_capped_per_line(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        fake_asr["heard"][0.0] = "字" * 5000
        _run(did, [ids[0]])
        assert len(svc.get_retranscribe_many_result(did)["proposals"][0]["proposed_zh"]) == 2000

    def test_a_one_line_result_is_not_a_many_line_result(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        transcribe_service.start_retranscribe_line(did, ids[0])
        _wait(f"retranscribe_{did}")
        assert transcribe_service.get_retranscribe_result(did, ids[0])["proposed_zh"] == "新0"
        with pytest.raises(NotFoundError):
            svc.get_retranscribe_many_result(did)
        _run(did, [ids[1]])
        with pytest.raises(NotFoundError):
            transcribe_service.get_retranscribe_result(did, ids[0])


class TestApply:
    def test_writes_zh_clears_english_and_keeps_the_rest(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        assert bulk_translate.untranslated_line_count(did) == 0
        _run(did, ids)
        out = svc.apply_retranscribe_many(did, f"retranscribe_{did}", _items(did, {ids[0], ids[1]}))
        assert out == {"applied": [ids[0], ids[1]], "skipped": [], "untranslated_count": 2}
        lines = {ln.id: ln for ln in isolated_db.load_line_objects(did)}
        assert (lines[ids[0]].zh, lines[ids[0]].en) == ("新0", "")
        assert (lines[ids[1]].zh, lines[ids[1]].en) == ("新2", "")
        # speaker, flag and timing stay
        assert (lines[ids[0]].speaker, lines[ids[0]].flag, lines[ids[0]].flag_note) == (
            "B", "unsure", "check")
        assert (lines[ids[1]].speaker, lines[ids[1]].start, lines[ids[1]].end) == ("A", 2.0, 3.5)
        # Lines not applied keep text and English.
        assert (lines[ids[2]].zh, lines[ids[2]].en) == ("旧2", "Old 2")
        assert (lines[ids[3]].zh, lines[ids[3]].en) == ("旧3", "Old 3")

    def test_history_snapshot_first_and_only_one_per_run(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _run(did, ids)
        svc.apply_retranscribe_many(did, f"retranscribe_{did}", _items(did, {ids[0]}))
        svc.apply_retranscribe_many(did, f"retranscribe_{did}", _items(did, {ids[1]}))
        history = isolated_db.list_line_history(did)
        assert len(history) == 1
        snap = isolated_db.load_line_history_lines(did, history[0]["id"]) \
            if hasattr(isolated_db, "load_line_history_lines") else None
        if snap is not None:
            assert [ln["zh"] if isinstance(ln, dict) else ln.zh for ln in snap][0] == "旧0"

    def test_an_edited_line_wins_and_keeps_its_english(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _run(did, ids)
        items = _items(did)
        lines = isolated_db.load_line_objects(did)
        lines[0].zh = "我改的"                     # source edited
        lines[1].en = "My translation"             # translation edited
        lines[2].start = 4.2                       # re-timed
        isolated_db.save_lines(did, lines)
        out = svc.apply_retranscribe_many(did, f"retranscribe_{did}", items)
        assert out["applied"] == [ids[3]] and out["skipped"] == [ids[0], ids[1], ids[2]]
        got = {ln.id: ln for ln in isolated_db.load_line_objects(did)}
        assert (got[ids[0]].zh, got[ids[0]].en) == ("我改的", "Old 0")
        assert (got[ids[1]].zh, got[ids[1]].en) == ("旧1", "My translation")
        assert (got[ids[2]].zh, got[ids[2]].en, got[ids[2]].start) == ("旧2", "Old 2", 4.2)
        assert (got[ids[3]].zh, got[ids[3]].en) == ("新6", "")

    def test_a_deleted_line_is_skipped(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _run(did, ids)
        items = _items(did)
        isolated_db.save_lines(did, [ln for ln in isolated_db.load_line_objects(did)
                                     if ln.id != ids[1]])
        out = svc.apply_retranscribe_many(did, f"retranscribe_{did}", items)
        assert out["skipped"] == [ids[1]] and ids[1] not in isolated_db.load_line_ids(did)

    def test_second_apply_of_the_same_line_is_skipped(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _run(did, ids)
        items = _items(did, {ids[0]})
        assert svc.apply_retranscribe_many(did, f"retranscribe_{did}", items)["applied"] == [ids[0]]
        again = svc.apply_retranscribe_many(did, f"retranscribe_{did}", items)
        assert again["applied"] == [] and again["skipped"] == [ids[0]]

    def test_reopens_a_translated_title_and_counts_refresh(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        assert isolated_db.get_drama(did)["status"] == "translated"
        _run(did, ids)
        out = svc.apply_retranscribe_many(did, f"retranscribe_{did}", _items(did, {ids[2]}))
        assert out["untranslated_count"] == 1 == bulk_translate.untranslated_line_count(did)
        assert isolated_db.get_drama(did)["status"] == "aligned"
        # Translating the line again completes the title.
        lines = isolated_db.load_line_objects(did)
        lines[2].en = "New 2"
        isolated_db.save_lines(did, lines, fields=("en",))
        bulk_translate.mark_translated_if_complete(did)
        assert isolated_db.get_drama(did)["status"] == "translated"

    def test_a_dubbed_title_keeps_its_status(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        isolated_db.update_drama(did, status="dubbed")
        _run(did, ids)
        svc.apply_retranscribe_many(did, f"retranscribe_{did}", _items(did, {ids[2]}))
        assert isolated_db.get_drama(did)["status"] == "dubbed"

    def test_items_must_be_this_runs_proposals(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _run(did, ids)
        good = _items(did, {ids[1]})[0]
        base = _items(did, {ids[0]})[0]
        for bad in ({**base, "expected_zh": "别的"}, {**base, "expected_proposed": "别的"},
                    {**base, "line_id": 999999}):
            with pytest.raises(ConflictError):
                svc.apply_retranscribe_many(did, f"retranscribe_{did}", [good, bad])
        assert isolated_db.load_line_objects(did)[0].zh == "旧0"
        assert isolated_db.list_line_history(did) == []

    def test_malformed_requests(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _run(did, ids)
        item = _items(did, {ids[0]})[0]
        job_id = f"retranscribe_{did}"
        for bad_job in (None, "translate_1", f"retranscribe_{did + 1}"):
            with pytest.raises(InvalidInputError):
                svc.apply_retranscribe_many(did, bad_job, [item])
        for bad_items in (None, [], "x", [item] * 2, [item] * (svc.MAX_LINES + 1)):
            with pytest.raises(InvalidInputError):
                svc.apply_retranscribe_many(did, job_id, bad_items)
        with pytest.raises(ConflictError):
            svc.apply_retranscribe_many(did, job_id, ["x"])
        assert isolated_db.load_line_objects(did)[0].zh == "旧0"

    def test_no_finished_run_404(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        with pytest.raises(NotFoundError):
            svc.apply_retranscribe_many(
                did, f"retranscribe_{did}",
                [{"line_id": ids[0], "expected_zh": "旧0", "expected_proposed": "x"}])
        _put_job(f"retranscribe_{did}")
        with pytest.raises(NotFoundError):
            svc.get_retranscribe_many_result(did)

    def test_a_newer_run_replaces_the_proposals_shown(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _run(did, ids)
        stale = _items(did, {ids[0]})
        fake_asr["heard"][0.0] = "又一次"
        _run(did, ids)
        with pytest.raises(ConflictError):
            svc.apply_retranscribe_many(did, f"retranscribe_{did}", stale)


def _hung_many_worker(*args):
    """Process-job target for the real-process test: the real many-line worker
    with a Whisper that never returns (installed in the spawned child)."""
    import core as c
    from services import retranscribe_worker as rw

    def cut(audio_path, start, end, out_path, timeout=None):
        with open(out_path, "wb") as f:
            f.write(b"slice")
        marker = os.environ.get("RETRANSCRIBE_TEST_MARKER")
        if marker:
            with open(marker, "w") as f:
                f.write("1")

    def hang(*a, **k):
        time.sleep(600)
    c.extract_audio_slice = cut
    c.transcribe_for_timing = hang
    whisper_models.is_whisper_model_cached = lambda size: True
    rw.retranscribe_many_worker(*args)


class TestRealProcess:
    def _start(self, did, ids, marker=None, timeout=None):
        mp = pytest.MonkeyPatch()
        mp.setattr(svc, "retranscribe_many_worker", _hung_many_worker)
        if timeout is not None:
            mp.setattr(svc, "retranscribe_timeout_s", lambda window, count=1: timeout)
        if marker:
            mp.setenv("RETRANSCRIBE_TEST_MARKER", marker)
        try:
            return svc.start_retranscribe_many(did, ids)
        finally:
            mp.undo()

    def test_cancel_kills_the_process_tree_and_frees_the_slot(self, isolated_db):
        did, ids = _drama(isolated_db)
        marker = os.path.join(isolated_db.drama_dir(did), "worker-started")
        job_id = self._start(did, ids, marker=marker)["job_id"]
        deadline = time.time() + 60
        while not os.path.exists(marker):
            assert time.time() < deadline, "the worker never started"
            time.sleep(0.05)
        proc = background_jobs.get_status(job_id)["process"]
        with db.get_conn() as conn:
            assert conn.execute("SELECT 1 FROM gpu_lock WHERE holder = ?",
                                (f"ui:{job_id}",)).fetchone() is not None
        background_jobs.request_cancel(job_id)
        job = _wait(job_id, timeout=90)
        assert job["status"] == "cancelled"
        assert not proc.is_alive()
        assert not background_jobs.is_running(job_id)
        import storage
        deadline = time.time() + 10
        while [d for d in os.listdir(storage.temp_root()) if d.startswith("retranscribe_")]:
            assert time.time() < deadline, "scratch folder left behind"
            time.sleep(0.05)
        with db.get_conn() as conn:
            assert conn.execute("SELECT 1 FROM gpu_lock WHERE holder = ?",
                                (f"ui:{job_id}",)).fetchone() is None
        # Nothing was proposed or written, and a new run may start.
        with pytest.raises(NotFoundError):
            svc.get_retranscribe_many_result(did)
        assert [ln.zh for ln in isolated_db.load_line_objects(did)] == [f"旧{i}" for i in range(4)]
        assert background_jobs.get_status(job_id)["status"] == "cancelled"

    def test_whole_run_gives_up_on_one_timeout(self, isolated_db):
        did, ids = _drama(isolated_db)
        job_id = self._start(did, ids, timeout=1)["job_id"]
        job = _wait(job_id, timeout=90)
        assert job["status"] == "done"
        assert job["result"] == {"failed_reason": "timeout"}
        job["process"].join(10)
        assert not job["process"].is_alive()


# ----- routes ----------------------------------------------------------------

@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _path(did, tail=""):
    return f"/api/transcribe/dramas/{did}/retranscribe-lines{tail}"


class TestRoutes:
    def test_start_get_apply(self, client, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        r = client.post(_path(did), json={"line_ids": ids[:2]})
        assert r.status_code == 200, r.text
        assert r.json() == {"job_id": f"retranscribe_{did}", "drama_id": did, "line_count": 2}
        assert isolated_db.drama_dir(did) not in r.text
        _wait(f"retranscribe_{did}")
        got = client.get(_path(did))
        assert got.status_code == 200, got.text
        body = got.json()
        assert set(body) == {"job_id", "line_count", "proposals", "failures", "unchanged_count",
                             "truncated", "device_notice"}
        assert "audio.wav" not in got.text and isolated_db.drama_dir(did) not in got.text
        assert "Old" not in got.text            # the English never leaves
        items = [{"line_id": p["line_id"], "expected_zh": p["base_zh"],
                  "expected_proposed": p["proposed_zh"]} for p in body["proposals"]]
        applied = client.post(_path(did, "/apply"), json={"job_id": body["job_id"], "items": items})
        assert applied.status_code == 200, applied.text
        assert applied.json() == {"applied": ids[:2], "skipped": [], "untranslated_count": 2}

    def test_errors(self, client, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        assert client.get(_path(did)).status_code == 404
        assert client.post(_path(999999), json={"line_ids": ids}).status_code == 404
        assert client.post(_path(did), json={"line_ids": [999999]}).status_code == 404
        assert client.post(_path(0), json={"line_ids": ids}).status_code == 422
        for bad in ({}, {"line_ids": []}, {"line_ids": ids, "x": 1}, {"line_ids": ["a"]},
                    {"line_ids": [True]}, {"line_ids": list(range(1, 202))},
                    {"line_ids": ids, "initial_prompt": "x" * 1001}):
            assert client.post(_path(did), json=bad).status_code == 422, bad
        _put_job(f"transcribe_{did}")
        assert client.post(_path(did), json={"line_ids": ids}).status_code == 409
        _wait_clear = [j for j in list(background_jobs._jobs) if j.startswith("transcribe_")]
        for j in _wait_clear:
            background_jobs._jobs.pop(j, None)
        no_audio, no_ids = _drama(isolated_db, audio=False)
        assert client.post(_path(no_audio), json={"line_ids": no_ids}).status_code == 400
        apply_body = {"job_id": f"retranscribe_{did}", "items": [
            {"line_id": ids[0], "expected_zh": "旧0", "expected_proposed": "x"}]}
        assert client.post(_path(did, "/apply"), json=apply_body).status_code == 404
        for bad in ({}, {"job_id": "j"}, {**apply_body, "items": []},
                    {**apply_body, "x": 1},
                    {**apply_body, "items": [{"line_id": ids[0], "expected_zh": "a",
                                              "expected_proposed": ""}]}):
            assert client.post(_path(did, "/apply"), json=bad).status_code == 422, bad

    def test_apply_conflict_is_409_and_writes_nothing(self, client, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        client.post(_path(did), json={"line_ids": ids})
        _wait(f"retranscribe_{did}")
        r = client.post(_path(did, "/apply"), json={"job_id": f"retranscribe_{did}", "items": [
            {"line_id": ids[0], "expected_zh": "旧0", "expected_proposed": "不是这个"}]})
        assert r.status_code == 409
        assert isolated_db.load_line_objects(did)[0].zh == "旧0"


REMOTE = "https://baihe.example.com"


def _remote():
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                      raise_server_exceptions=False)


def _bare_user(email, *grants):
    u = auth_service.add_user(email)
    for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
        auth_service.revoke_permission(u["id"], p)
    for g in grants:
        auth_service.grant_permission(u["id"], g)
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


class TestPermissions:
    def test_start_needs_jobs_start(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        c = _remote()
        assert c.post(_path(did), json={"line_ids": ids}).status_code == 401
        h = _bare_user("a@example.com", "lines.read", "lines.edit")
        assert c.post(_path(did), json={"line_ids": ids}, headers=h).status_code == 403
        assert fake_asr["started"]["started"] == 0
        h = _bare_user("b@example.com", "jobs.start")
        assert c.post(_path(did), json={"line_ids": ids}, headers=h).status_code == 200
        _wait(f"retranscribe_{did}")

    def test_read_needs_lines_read_and_apply_needs_lines_edit(self, isolated_db, fake_asr):
        did, ids = _drama(isolated_db)
        _run(did, ids)
        c = _remote()
        item = _items(did, {ids[0]})
        body = {"job_id": f"retranscribe_{did}", "items": item}
        assert c.get(_path(did)).status_code == 401
        assert c.post(_path(did, "/apply"), json=body).status_code == 401
        h = _bare_user("c@example.com", "jobs.start", "lines.edit")
        assert c.get(_path(did), headers=h).status_code == 403
        h = _bare_user("d@example.com", "jobs.start", "lines.read")
        assert c.get(_path(did), headers=h).status_code == 200
        assert c.post(_path(did, "/apply"), json=body, headers=h).status_code == 403
        assert isolated_db.load_line_objects(did)[0].zh == "旧0"
        h = _bare_user("e@example.com", "lines.edit")
        assert c.post(_path(did, "/apply"), json=body, headers=h).status_code == 200


class TestGapLinesFlow:
    def test_gap_lines_are_heard_in_one_job_and_filled_on_apply(self, isolated_db, fake_asr,
                                                                  monkeypatch):
        from services import transcribe_gap_service as gaps
        monkeypatch.setattr(gaps, "_pauses", lambda *a: None)
        did, ids = _drama(isolated_db, n=2)
        isolated_db.save_lines(did, isolated_db.load_line_objects(did)
                               + [Line(idx=2, start=100.0, end=102.0, zh="尾", en="Tail")])
        ids = [ln.id for ln in isolated_db.load_line_objects(did)]
        added = gaps.add_gap_lines(did, ids, start=3.5, end=99.5, after_line_id=ids[1])
        new_ids = added["new_line_ids"]
        assert len(new_ids) == 4
        _out, job = _run(did, new_ids)
        assert job["result"]["candidate_count"] == 4
        # One process for all four pieces, in time order.
        assert fake_asr["started"]["started"] == 1
        assert [s for s, _e in fake_asr["slices"]] == sorted(s for s, _e in fake_asr["slices"])
        res = svc.get_retranscribe_many_result(did)
        assert [p["had_english"] for p in res["proposals"]] == [False] * 4
        out = svc.apply_retranscribe_many(did, f"retranscribe_{did}", _items(did))
        assert out["applied"] == new_ids
        filled = [ln for ln in isolated_db.load_line_objects(did) if ln.id in new_ids]
        assert all(ln.zh and not ln.en and ln.flag == gaps.GAP_FLAG for ln in filled)
        # Existing lines kept their English.
        assert [ln.en for ln in isolated_db.load_line_objects(did) if ln.id in ids] == [
            "Old 0", "Old 1", "Tail"]
