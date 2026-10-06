"""Re-time with the Qwen3 aligner: services/retime_service.py and its routes under
/api/transcribe/dramas/{id}/retime. The aligner is faked: no model, GPU or audio."""

import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

import background_jobs
import core
import db
import forced_align
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import (jobs_service, ownership_service, restructure_service, retime_service as svc,
                      settings_service, transcribe_service)
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    background_jobs.clear_all_jobs()
    monkeypatch.setattr(transcribe_service, "require_qwen3_packages", lambda feature: None)
    monkeypatch.setattr(core, "release_gpu_models", lambda: None)
    monkeypatch.setattr(settings_service, "get_use_gpu", lambda: True)
    calls = {"groups": [], "languages": [], "use_gpu": [], "shift": -0.4,
             "fail_text": None, "on_device": "GPU", "fallback": None}

    def fake_refine(audio_path, groups, language, use_gpu=False, cancel_check=None,
                    progress_cb=None, on_device=None, on_gpu_fallback=None):
        calls["groups"].append(groups)
        calls["languages"].append(language)
        calls["use_gpu"].append(use_gpu)
        if calls["fallback"] and use_gpu:
            on_gpu_fallback(RuntimeError("CUDA out of memory"))
        if on_device:
            on_device("CPU" if calls["fallback"] else calls["on_device"])
        out = []
        for seg in groups[0]:
            new = dict(seg)
            if seg["text"] == calls["fail_text"]:
                new["flag"], new["flag_note"] = "timing_uncertain", forced_align.TIMING_FALLBACK_NOTE
            else:
                new["start"] = max(0.0, seg["start"] + calls["shift"])
            out.append(new)
        return out

    monkeypatch.setattr(forced_align, "refine_segment_timing", fake_refine)
    yield calls
    background_jobs.clear_all_jobs()


def _drama(n=4, **kw):
    did = db.create_drama(title_en="D", audio_filename="audio.wav", source_language="zh", **kw)
    with open(os.path.join(db.drama_dir(did), "audio.wav"), "wb") as f:
        f.write(b"x")
    db.save_lines(did, [Line(idx=i, start=i * 5.0 + 1, end=i * 5.0 + 4, zh=f"原{i}", en=f"en{i}",
                             speaker="A", flag="check" if i == 1 else None) for i in range(n)])
    return did, [ln.id for ln in db.load_line_objects(did)]


def _wait(out):
    for _ in range(300):
        job = background_jobs.get_status(out["job_id"])
        if job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _run(did, ids):
    out = svc.start_retime(did, ids)
    job = _wait(out)
    assert job["status"] == "done"
    return out, job


def _item(p):
    return {"line_id": p["line_id"], "expected_new_start": p["new_start"],
            "expected_new_end": p["new_end"]}


class TestStart:
    def test_no_audio_mode_and_dependency_refusals(self, monkeypatch):
        did, ids = _drama()
        db.update_drama(did, audio_filename=None)
        with pytest.raises(UnsupportedOperationError, match="no stored audio"):
            svc.start_retime(did, ids)
        novel = db.create_drama(title_en="N", content_mode="novel_narration")
        with pytest.raises(UnsupportedOperationError):
            svc.start_retime(novel, [1])
        did, ids = _drama()

        def missing(feature):
            raise DependencyUnavailableError("needs qwen-asr")
        monkeypatch.setattr(transcribe_service, "require_qwen3_packages", missing)
        with pytest.raises(DependencyUnavailableError):
            svc.start_retime(did, ids)
        with pytest.raises(NotFoundError):
            svc.start_retime(99999, ids)

    def test_selection_and_cap(self, monkeypatch):
        did, ids = _drama()
        with pytest.raises(InvalidInputError):
            svc.start_retime(did, [])
        with pytest.raises(InvalidInputError):
            svc.start_retime(did, [987654])
        monkeypatch.setattr(svc.compare, "MAX_LINES", 2)
        with pytest.raises(InvalidInputError, match="limited to 2"):
            svc.start_retime(did, ids[:3])

    @pytest.mark.parametrize("prefix", ["transcribe_", "fixflag_", "resegment_", "narration_",
                                         "comparetx_"])
    def test_refused_while_a_lines_job_runs(self, prefix):
        did, ids = _drama()
        background_jobs.start_job(f"{prefix}{did}", lambda: time.sleep(0.5))
        with pytest.raises(ConflictError):
            svc.start_retime(did, ids)
        background_jobs.clear_all_jobs()


class TestJob:
    def test_proposes_times_only_and_windows_stay_off_neighbours(self, _env):
        did, ids = _drama()
        _, job = _run(did, [ids[1], ids[2]])
        group = _env["groups"][0][0]
        # Line 1 starts at 6.0; the previous line ends at 4.0, so the 3 s reach
        # back is cut to the gap (the fake aligner's 3.6 is clamped to it). Line 2 ends at 14.0; the next starts at 16.0.
        assert [g["text"] for g in group] == ["原1", "原2"]
        assert group[0]["start"] == 4.0 and group[-1]["end"] == 16.0
        res = svc.get_retime_result(did)
        assert [(p["number"], p["start"], p["new_start"]) for p in res["proposals"]] == [
            (2, 6.0, pytest.approx(4.0)), (3, 11.0, pytest.approx(10.6))]
        assert all(p["new_end"] > p["new_start"] and not p["uncertain"] for p in res["proposals"])
        assert res["device"] == "GPU"
        assert [(l.start, l.zh, l.en, l.flag) for l in db.load_line_objects(did)][1] == (
            6.0, "原1", "en1", "check")
        assert set(job["result"]) <= set(jobs_service.RESULT_ALLOWED_KEYS) | {"proposals"}

    def test_groups_split_on_gaps_and_language(self, _env):
        did, ids = _drama(5)
        db.update_line_fields_if(did, ids[1], {"lang": "ja"}, {})
        db.update_line_fields_if(did, ids[3], {"lang": "en"}, {})
        db.update_line_fields_if(did, ids[4], {"lang": "en"}, {})
        _run(did, ids)
        assert _env["languages"] == ["zh", "ja", "zh", "en"]
        assert [[s["text"] for s in g[0]] for g in _env["groups"]] == [
            ["原0"], ["原1"], ["原2"], ["原3", "原4"]]
        assert len(svc.get_retime_result(did)["proposals"]) == 5

    def test_language_the_aligner_lacks_is_skipped_per_line(self, _env):
        did, ids = _drama(3)
        db.update_drama(did, source_language="fr")
        db.update_line_fields_if(did, ids[1], {"lang": "ja"}, {})
        _run(did, ids)
        res = svc.get_retime_result(did)
        assert [p["number"] for p in res["proposals"]] == [2]
        assert sum("language (fr)" in e for e in res["errors"]) == 2

    def test_aligner_fallback_line_is_left_alone_and_reported(self, _env):
        did, ids = _drama()
        _env["fail_text"] = "原1"
        _run(did, ids)
        res = svc.get_retime_result(did)
        assert 2 not in [p["number"] for p in res["proposals"]]
        assert any("line 2" in e for e in res["errors"])

    def test_gpu_fallback_is_reported_and_not_retried(self, _env):
        did, ids = _drama(3)
        db.update_line_fields_if(did, ids[1], {"lang": "ja"}, {})
        _env["fallback"] = True
        _run(did, ids)
        assert _env["use_gpu"][0] is True and _env["use_gpu"][1:] == [False, False]
        res = svc.get_retime_result(did)
        assert res["device"] == "CPU" and "ran on the CPU" in res["device_notice"]

    def test_errors_are_scrubbed_and_one_group_failing_keeps_the_rest(self, monkeypatch, _env):
        did, ids = _drama(3)
        db.update_line_fields_if(did, ids[1], {"lang": "ja"}, {})
        real = forced_align.refine_segment_timing

        def flaky(audio, groups, language, **kw):
            if language == "ja":
                raise RuntimeError("boom https://secret.example/x?token=abc")
            return real(audio, groups, language, **kw)
        monkeypatch.setattr(forced_align, "refine_segment_timing", flaky)
        _run(did, ids)
        res = svc.get_retime_result(did)
        assert len(res["proposals"]) == 2
        assert "secret.example" not in " ".join(res["errors"])

    def test_missing_dependency_and_download_fail_the_job_cleanly(self, monkeypatch, _env):
        did, ids = _drama()
        monkeypatch.setattr(forced_align, "refine_segment_timing",
                            lambda *a, **k: (_ for _ in ()).throw(ImportError("/home/x/torch")))
        _, job = _run(did, ids)
        assert job["result"]["failed_reason"] == "dependency_missing"
        assert "/home" not in job["result"]["detail"]
        with pytest.raises(NotFoundError):
            svc.get_retime_result(did)

    def test_audio_cut_failure_leaks_no_windows_path(self, monkeypatch, _env):
        did, ids = _drama(2)
        boom = subprocess.CalledProcessError(1, ["ffmpeg", "-i", r"C:\Users\x\a.wav"])
        monkeypatch.setattr(forced_align, "refine_segment_timing",
                            lambda *a, **k: (_ for _ in ()).throw(boom))
        _, job = _run(did, ids)
        res = svc.get_retime_result(did)
        projected = str(jobs_service.project_result(job["result"]))
        for text in (str(res), str(job["result"]), projected):
            assert "Users" not in text and "a.wav" not in text
        assert res["errors"] == ["lines 1-2: couldn't cut this part of the audio"]

    def test_model_load_oserror_stops_after_the_first_group(self, monkeypatch, _env):
        did, ids = _drama(3)
        db.update_line_fields_if(did, ids[1], {"lang": "ja"}, {})
        tried = []

        def broken(audio, groups, language, **kw):
            tried.append(language)
            raise OSError(r"C:\Users\x\model.bin is corrupt")
        monkeypatch.setattr(forced_align, "refine_segment_timing", broken)
        _run(did, ids)
        assert len(tried) == 1
        res = svc.get_retime_result(did)
        assert res["errors"] == ["the aligner model couldn't be loaded"]

    def test_slice_oserror_after_load_only_skips_that_group(self, monkeypatch, _env):
        did, ids = _drama(3)
        db.update_line_fields_if(did, ids[1], {"lang": "ja"}, {})
        real = forced_align.refine_segment_timing

        def flaky(audio, groups, language, **kw):
            if language == "ja":
                kw["on_device"]("CPU")
                raise FileNotFoundError("ffmpeg")
            return real(audio, groups, language, **kw)
        monkeypatch.setattr(forced_align, "refine_segment_timing", flaky)
        _run(did, ids)
        res = svc.get_retime_result(did)
        assert len(res["proposals"]) == 2
        assert res["errors"] == ["lines 2-2: couldn't cut this part of the audio"]

    def test_cancel_inside_the_aligner_call_ends_partial_not_as_an_error(self, monkeypatch, _env):
        did, ids = _drama(3)
        db.update_line_fields_if(did, ids[2], {"lang": "ja"}, {})
        real = forced_align.refine_segment_timing

        def cancel_then_check(audio, groups, language, cancel_check=None, **kw):
            if language == "ja":
                background_jobs.request_cancel(f"retime_{did}")
                cancel_check()
            return real(audio, groups, language, cancel_check=cancel_check, **kw)
        monkeypatch.setattr(forced_align, "refine_segment_timing", cancel_then_check)
        _run(did, ids)
        res = svc.get_retime_result(did)
        assert res["partial"] is True and len(res["proposals"]) == 2
        assert res["errors"] == []

    def test_cancel_before_the_model_loads_proposes_nothing(self, monkeypatch, _env):
        did, ids = _drama(2)

        def cancelled(audio, groups, language, cancel_check=None, **kw):
            background_jobs.request_cancel(f"retime_{did}")
            cancel_check()
        monkeypatch.setattr(forced_align, "refine_segment_timing", cancelled)
        _, job = _run(did, ids)
        assert job["result"] == {"failed_reason": "cancelled"}

    def test_dropped_unmoved_neighbour_does_not_strand_the_line_before_it(self, monkeypatch, _env):
        did, ids = _drama(2)

        def shift(audio, groups, language, **kw):
            a, b = (dict(s) for s in groups[0])
            a["end"] = 6.005
            b["start"], b["end"] = 6.005, 9.5
            return [a, b]
        monkeypatch.setattr(transcribe_service, "_audio_duration_seconds", lambda path: 9.0)
        monkeypatch.setattr(forced_align, "refine_segment_timing", shift)
        _run(did, ids)
        (p,) = svc.get_retime_result(did)["proposals"]
        # Clamped to the media end, line 2 is back to its stored times and dropped; line 1 must end at its stored start.
        assert p["new_end"] == 6.0

    def test_device_notice_is_scrubbed(self, monkeypatch, _env):
        did, ids = _drama(2)
        _env["fallback"] = True
        monkeypatch.setattr(core, "gpu_fallback_notice",
                            lambda feature, why: f"{feature} fell back: C:\\Users\\x\\m.bin")
        _run(did, ids)
        assert "Users" not in svc.get_retime_result(did)["device_notice"]

    def test_line_longer_than_one_chunk_is_skipped(self, _env):
        did, ids = _drama(2)
        db.update_line_fields_if(did, ids[0], {"end": 1.0 + forced_align.MAX_CHUNK_SECONDS + 1}, {})
        _run(did, [ids[0]])
        res = svc.get_retime_result(did)
        assert _env["groups"] == [] and res["proposals"] == []
        assert any("line 1" in e and "too long" in e for e in res["errors"])

    def test_unplaced_line_does_not_leave_its_predecessor_overlapping(self, monkeypatch, _env):
        did, ids = _drama(2)

        def widen(audio, groups, language, **kw):
            a, b = (dict(s) for s in groups[0])
            a["end"] = 9.0
            b["flag"], b["flag_note"] = "timing_uncertain", forced_align.TIMING_FALLBACK_NOTE
            return [a, b]
        monkeypatch.setattr(forced_align, "refine_segment_timing", widen)
        _run(did, ids)
        (p,) = svc.get_retime_result(did)["proposals"]
        # Line 2 keeps its stored start of 6.0, so line 1 can't run past it.
        assert p["new_end"] == 6.0

    def test_groups_widening_into_one_gap_are_clamped(self, monkeypatch, _env):
        did, ids = _drama(2)
        db.update_line_fields_if(did, ids[1], {"lang": "ja"}, {})

        def meet(audio, groups, language, **kw):
            seg = dict(groups[0][0])
            if language == "zh":
                seg["end"] = 5.5
            else:
                seg["start"] = 4.5
            return [seg]
        monkeypatch.setattr(forced_align, "refine_segment_timing", meet)
        _run(did, ids)
        a, b = svc.get_retime_result(did)["proposals"]
        assert a["new_end"] <= b["new_start"]

    def test_last_line_is_clamped_to_media_duration(self, monkeypatch, _env):
        did, ids = _drama(2)
        monkeypatch.setattr(transcribe_service, "_audio_duration_seconds", lambda path: 10.0)
        monkeypatch.setattr(
            forced_align, "refine_segment_timing",
            lambda audio, groups, language, **kw: [{**groups[0][0], "end": 13.0}])
        _run(did, [ids[1]])
        (p,) = svc.get_retime_result(did)["proposals"]
        assert p["new_end"] == 10.0

    def test_unfixable_proposal_is_marked_uncertain(self, monkeypatch, _env):
        did, ids = _drama(2)
        monkeypatch.setattr(
            forced_align, "refine_segment_timing",
            lambda audio, groups, language, **kw: [{**groups[0][0], "start": 7.0, "end": 8.0}])
        _run(did, [ids[0]])
        (p,) = svc.get_retime_result(did)["proposals"]
        # Line 2 starts at 6.0, so no clamp leaves a usable span.
        assert p["uncertain"] is True


class TestApply:
    def test_writes_only_times_snapshots_and_undo_restores(self):
        did, ids = _drama()
        out, _ = _run(did, ids)
        res = svc.get_retime_result(did)
        before = [(l.start, l.end) for l in db.load_line_objects(did)]
        applied = svc.apply_retime(did, out["job_id"], [_item(p) for p in res["proposals"]])
        assert applied == {"applied": ids, "skipped": [], "overlapping": []}
        after = db.load_line_objects(did)
        assert [(l.zh, l.en, l.speaker, l.flag) for l in after] == [
            (f"原{i}", f"en{i}", "A", "check" if i == 1 else None) for i in range(4)]
        assert [l.start for l in after] != [b[0] for b in before]
        # The last line's end follows the widened window the fake aligner returns.
        assert [l.end for l in after][:3] == [b[1] for b in before][:3]
        history = db.list_line_history(did)
        assert [h["label"] for h in history] == ["before re-time apply"]
        restructure_service.restore_version(did, history[0]["id"], ids)
        assert [(l.start, l.end) for l in db.load_line_objects(did)] == before

    def test_edited_or_moved_line_is_skipped(self):
        did, ids = _drama()
        out, _ = _run(did, ids)
        res = svc.get_retime_result(did)
        db.update_line_fields_if(did, ids[0], {"zh": "我改的"}, {})
        db.update_line_fields_if(did, ids[1], {"end": 9.9}, {})
        applied = svc.apply_retime(did, out["job_id"], [_item(p) for p in res["proposals"]])
        assert applied["skipped"] == [ids[0], ids[1]] and applied["applied"] == ids[2:]
        assert db.load_line_objects(did)[0].start == 1.0

    def test_unknown_or_mismatched_item_writes_nothing(self):
        did, ids = _drama(2)
        out, _ = _run(did, ids)
        p = svc.get_retime_result(did)["proposals"]
        with pytest.raises(ConflictError):
            svc.apply_retime(did, out["job_id"],
                             [_item(p[0]), {**_item(p[1]), "expected_new_start": 0.123}])
        with pytest.raises(ConflictError):
            svc.apply_retime(did, out["job_id"], [{**_item(p[0]), "line_id": 12345}])
        assert db.list_line_history(did) == []
        assert db.load_line_objects(did)[0].start == 1.0

    def test_job_id_duplicates_and_other_title_are_refused(self):
        did, ids = _drama(2)
        out, _ = _run(did, ids)
        p = svc.get_retime_result(did)["proposals"][0]
        with pytest.raises(InvalidInputError):
            svc.apply_retime(did, "retime_999999", [_item(p)])
        with pytest.raises(InvalidInputError):
            svc.apply_retime(did, out["job_id"], [_item(p), _item(p)])
        other, _ = _drama(1)
        with pytest.raises(NotFoundError):
            svc.get_retime_result(other)

    def test_per_row_applies_share_one_snapshot(self):
        did, ids = _drama()
        out, _ = _run(did, ids)
        for p in svc.get_retime_result(did)["proposals"]:
            assert svc.apply_retime(did, out["job_id"], [_item(p)])["applied"] == [p["line_id"]]
        assert [h["label"] for h in db.list_line_history(did)] == ["before re-time apply"]


class TestApi:
    def test_round_trip_and_registries(self):
        did, ids = _drama(2)
        client = TestClient(create_app(ApiSettings()))
        base = f"/api/transcribe/dramas/{did}/retime"
        assert client.get(f"{base}/result").status_code == 404
        assert client.post(f"{base}/run", json={"line_ids": []}).status_code == 422
        assert client.post(f"{base}/run", json={"line_ids": ids, "x": 1}).status_code == 422
        started = client.post(f"{base}/run", json={"line_ids": ids})
        assert started.status_code == 200, started.text
        _wait(started.json())
        res = client.get(f"{base}/result").json()
        applied = client.post(f"{base}/apply", json={"job_id": res["job_id"], "items": [
            {k: res["proposals"][0][v] for k, v in (("line_id", "line_id"),
             ("expected_new_start", "new_start"), ("expected_new_end", "new_end"))}]})
        assert applied.json() == {"applied": [ids[0]], "skipped": [], "overlapping": []}
        job = client.get(f"/api/jobs/{started.json()['job_id']}").json()
        assert "proposals" not in (job.get("result") or {})
        assert ownership_service.drama_id_of_job(started.json()["job_id"]) == did

    def test_apply_refuses_a_row_that_would_overlap_a_neighbour_as_it_stands(self, monkeypatch, _env):
        did, ids = _drama(2)
        monkeypatch.setattr(
            forced_align, "refine_segment_timing",
            lambda audio, groups, language, **kw: [
                {**s, "start": s["start"] + 1.0, "end": s["end"] + 1.0} for s in groups[0]])
        out, _ = _run(did, ids)
        a, b = svc.get_retime_result(did)["proposals"]
        # A alone would end at 5.0 while B still starts at 6.0: fine. Shift B's stored start back.
        db.update_line_fields_if(did, ids[1], {"start": 4.5}, {})
        res = svc.apply_retime(did, out["job_id"], [_item(a)])
        assert res["applied"] == [] and res["skipped"] == [ids[0]] and res["overlapping"] == [ids[0]]
        assert db.load_line_objects(did)[0].start == 1.0

    def test_apply_together_checks_neighbours_at_their_new_times(self, monkeypatch, _env):
        did, ids = _drama(2)
        monkeypatch.setattr(
            forced_align, "refine_segment_timing",
            lambda audio, groups, language, **kw: [
                {**s, "start": s["start"] + 1.0, "end": s["end"] + 1.0} for s in groups[0]])
        out, _ = _run(did, ids)
        ps = svc.get_retime_result(did)["proposals"]
        res = svc.apply_retime(did, out["job_id"], [_item(p) for p in ps])
        assert res == {"applied": ids, "skipped": [], "overlapping": []}
