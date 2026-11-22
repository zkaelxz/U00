"""Tests that paid bulk results are applied exactly once, and that failed stages, short result
streams, cancels, races and junk replies are visible, never silent."""

import datetime
import json
import os
import re
import sqlite3
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs
from jobs import job_launch
from bulk import bulk_providers as bp
from bulk import bulk_apply
from bulk import bulk_poll
from bulk import bulk_reflect
from bulk import bulk_translate
import translation_finish
from engine_backends import translate_pipeline, prompts, reflect_prompts
from engine_backends import gemini as engine_gemini
from engine_backends import reflect_pass as engine_reflect_pass
from engine_backends import shared as engine_shared
from core import Line
from tests.test_bulk_translate import (_Resp, _answer_every_request, _claude_engine, _drama, _en,
                                        _patch_http, _request_text, _submit, _succeeded, _wait_for)


# ---------------------------------------------------------------------------
# Paid results are applied exactly once; a failed stage submit, a short
# result stream and a cancelled off-peak run are all visible, never silent.
# ---------------------------------------------------------------------------

def _usage_ops(isolated_db, did):
    import contextlib
    with contextlib.closing(isolated_db.get_conn()) as conn:
        return [r[0] for r in conn.execute(
            "SELECT operation FROM usage_log WHERE drama_id = ? ORDER BY id", (did,)).fetchall()]


def _fail_first(monkeypatch, module, name, when=lambda *a, **k: True):
    real = getattr(module, name)
    state = {"failed": False}

    def wrapper(*a, **k):
        if not state["failed"] and when(*a, **k):
            state["failed"] = True
            raise sqlite3.OperationalError("database is locked")
        return real(*a, **k)
    monkeypatch.setattr(module, name, wrapper)


class TestApplyOnce:
    def test_an_apply_that_raises_is_not_applied_and_billed_again(self, isolated_db, monkeypatch):
        engine = _claude_engine()
        did = _drama(isolated_db, n=4)
        bulk_id = _submit(isolated_db, did, engine)
        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"
        _fail_first(monkeypatch, isolated_db, "save_translation_version")

        bulk_poll.run_bulk_poller("t_once", bulk_id, provider=bp.make_provider("claude", engine),
                           sleep=lambda s: None, interval=0.01)

        assert _usage_ops(isolated_db, did) == ["translate_bulk", "translate_bulk"]
        job = isolated_db.get_bulk_job(bulk_id)
        assert job["status"] == "failed" and "locked" in job["last_error"]
        background_jobs.clear_job("t_once")

    def test_a_reflect_stage_whose_status_write_fails_submits_the_next_stage_once(
            self, isolated_db, monkeypatch):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        jid = bulk_reflect.submit_reflect_pipeline(did, isolated_db.load_line_objects(did), engine, "claude",
                                         {"locale": "en-US"})
        batches = engine.client.messages.batches
        ids = [r["line_id"] for r in isolated_db.list_bulk_job_lines(jid)]
        batches.results_list = [_succeeded(batches.created[0]["custom_id"],
                                           {str(i): f"draft-{i}" for i in ids})]
        batches.status = "ended"
        _fail_first(monkeypatch, isolated_db, "update_bulk_job",
                    when=lambda job_id, **k: job_id == jid and k.get("status") == "applied")

        bulk_poll.run_bulk_poller("t_once_r", jid, provider=bp.ClaudeBatchProvider(engine), engine=engine,
                           sleep=lambda s: None, interval=0.01)

        pipeline = isolated_db.get_bulk_job(jid)["pipeline_id"]
        stages = [j["stage"] for j in isolated_db.list_bulk_jobs(pipeline_id=pipeline)]
        assert stages.count("reflect") == 1
        assert _usage_ops(isolated_db, did) == ["reflect_faithful_bulk"]
        background_jobs.clear_job("t_once_r")

    def test_a_job_left_applying_by_a_crash_is_not_applied_again_on_resume(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        bulk_id = _submit(isolated_db, did, engine)
        isolated_db.update_bulk_job(bulk_id, status="applying")
        background_jobs.clear_all_jobs()

        out = bulk_poll.resume_pending(did, lambda e, m: engine)

        assert bulk_id not in out
        assert not background_jobs.is_running(bulk_poll.poll_job_id(bulk_id))
        job = isolated_db.get_bulk_job(bulk_id)
        assert job["status"] == "failed" and job["last_error"]


    def test_resume_does_not_fail_an_apply_that_finished_meanwhile(self, isolated_db, monkeypatch):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        bulk_id = _submit(isolated_db, did, engine)
        isolated_db.update_bulk_job(bulk_id, status="applying")
        background_jobs.clear_all_jobs()

        class FinishesInTheGap:
            def acquire(self, blocking=True):
                isolated_db.update_bulk_job(bulk_id, status="applied")
                return True

            def release(self):
                pass
        monkeypatch.setattr(bulk_poll, "_check_lock", lambda _id: FinishesInTheGap())

        bulk_poll.resume_pending(did, lambda e, m: engine)

        assert isolated_db.get_bulk_job(bulk_id)["status"] == "applied"


class TestUnreturnedRequests:
    def test_a_request_with_no_result_counts_as_failed_and_missing(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=4)
        bulk_id = _submit(isolated_db, did, engine)  # requests of 3 and 1 lines
        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.results_list = [r for r in batches.results_list if r.custom_id.endswith("_b0")]
        batches.status = "ended"

        assert bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine)) == "applied"
        summary = isolated_db.get_bulk_job(bulk_id)["result_summary"]
        assert (summary["applied"], summary["missing"], summary["failed_requests"]) == (3, 1, 1)

    def test_a_batch_that_returns_nothing_fails(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=4)
        bulk_id = _submit(isolated_db, did, engine)
        engine.client.messages.batches.results_list = []
        engine.client.messages.batches.status = "ended"

        assert bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine)) == "failed"
        job = isolated_db.get_bulk_job(bulk_id)
        assert job["last_error"] and job["result_summary"]["missing"] == 4

    def test_a_flag_request_with_no_result_counts_as_failed(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=4, en="x")
        jid = bulk_translate.submit_bulk_flag(did, isolated_db.load_line_objects(did), engine, "claude",
                                  batch_size=2)
        counts = bulk_poll.apply_results_for_job(jid, [(bulk_translate.request_key(did, 0), "[]", None, None)])
        assert counts["failed_requests"] == 1


class TestReflectStageSubmitFailure:
    def _faithful_results(self, isolated_db, engine, jid):
        batches = engine.client.messages.batches
        ids = [r["line_id"] for r in isolated_db.list_bulk_job_lines(jid)]
        batches.results_list = [_succeeded(batches.created[0]["custom_id"],
                                           {str(i): f"draft-{i}" for i in ids})]
        batches.status = "ended"
        return ids

    def _pipeline(self, isolated_db, n=2):
        engine = _claude_engine()
        did = _drama(isolated_db, n=n)
        jid = bulk_reflect.submit_reflect_pipeline(did, isolated_db.load_line_objects(did), engine, "claude",
                                         {"locale": "en-US"})
        return engine, did, jid

    def _break_submit(self, monkeypatch, engine, times):
        batches = engine.client.messages.batches
        real, left = batches.create, {"n": times}

        def create(requests):
            if left["n"]:
                left["n"] -= 1
                raise ConnectionError("provider unreachable")
            return real(requests)
        monkeypatch.setattr(batches, "create", create)

    def test_a_transient_submit_failure_is_retried_on_the_next_check(self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        self._faithful_results(isolated_db, engine, jid)
        self._break_submit(monkeypatch, engine, times=1)
        provider = bp.ClaudeBatchProvider(engine)

        assert bulk_poll.check_once(jid, provider, engine=engine) == "needs_resubmit"
        pipeline = isolated_db.get_bulk_job(jid)["pipeline_id"]
        assert bulk_reflect.sibling_stage_job(pipeline, "reflect", skip_failed=True) is None

        assert bulk_poll.check_once(jid, provider, engine=engine) == "applied"
        stage2 = bulk_reflect.sibling_stage_job(pipeline, "reflect")
        assert stage2 is not None and stage2["status"] == "submitted"
        assert _usage_ops(isolated_db, did) == ["reflect_faithful_bulk"]

    def test_a_cancel_during_a_resubmit_is_not_overwritten(self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        self._faithful_results(isolated_db, engine, jid)
        self._break_submit(monkeypatch, engine, times=1)
        provider = bp.ClaudeBatchProvider(engine)
        assert bulk_poll.check_once(jid, provider, engine=engine) == "needs_resubmit"
        batches = engine.client.messages.batches
        real = batches.create

        def create(requests):
            bulk_poll.cancel_bulk_job(jid)
            return real(requests)
        monkeypatch.setattr(batches, "create", create)

        assert bulk_poll.check_once(jid, provider, engine=engine) == "cancelled"
        assert bulk_poll.check_once(jid, provider, engine=engine) == "cancelled"
        assert isolated_db.get_bulk_job(jid)["status"] == "cancelled"
        pipeline = isolated_db.get_bulk_job(jid)["pipeline_id"]
        # The stage sent just before the Cancel won is a live paid batch:
        # it is cancelled here and at the provider, not left running.
        assert sorted(j["status"] for j in isolated_db.list_bulk_jobs(pipeline_id=pipeline)
                      if j["stage"] == "reflect") == ["cancelled", "failed"]
        assert batches.cancelled == ["msgbatch_01"]

    def test_the_drafts_are_kept_when_the_next_stage_never_gets_submitted(self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        ids = self._faithful_results(isolated_db, engine, jid)
        self._break_submit(monkeypatch, engine, times=100)
        provider = bp.ClaudeBatchProvider(engine)

        statuses = [bulk_poll.check_once(jid, provider, engine=engine)
                    for _ in range(bulk_reflect.STAGE_SUBMIT_ATTEMPTS)]

        assert statuses[-1] == "applied" and set(statuses[:-1]) == {"needs_resubmit"}
        assert {ln.id: ln.en for ln in isolated_db.load_line_objects(did)} == {
            i: f"draft-{i}" for i in ids}
        job = isolated_db.get_bulk_job(jid)
        assert job["result_summary"]["drafts_applied"] == 2 and "unreachable" in job["last_error"]

    def _answer(self, isolated_db, engine, job_id, prefix, skip=()):
        by_key = {}
        for r in isolated_db.list_bulk_job_lines(job_id):
            by_key.setdefault(r["request_key"], []).append(r["line_id"])
        batches = engine.client.messages.batches
        batches.results_list = [_succeeded(k, {str(i): f"{prefix}-{i}" for i in ids})
                                for k, ids in by_key.items() if k not in skip]
        batches.status = "ended"
        return by_key

    def _to_stage(self, isolated_db, stage, n=2):
        engine, did, jid = self._pipeline(isolated_db, n=n)
        provider = bp.ClaudeBatchProvider(engine)
        by_key = self._answer(isolated_db, engine, jid, "draft")
        ids = [i for v in by_key.values() for i in v]
        assert bulk_poll.check_once(jid, provider, engine=engine) == "applied"
        pipeline = isolated_db.get_bulk_job(jid)["pipeline_id"]
        if stage == "expressive":
            reflect = bulk_reflect.sibling_stage_job(pipeline, "reflect")
            self._answer(isolated_db, engine, reflect["id"], "critique")
            assert bulk_poll.check_once(reflect["id"], provider, engine=engine) == "applied"
        return engine, did, ids, bulk_reflect.sibling_stage_job(pipeline, stage), provider

    def _edit(self, isolated_db, did, line_id, text):
        lines = isolated_db.load_line_objects(did)
        for ln in lines:
            if ln.id == line_id:
                ln.en = text
        isolated_db.save_lines(did, lines, fields=("en",))

    def _expired_gemini(self, monkeypatch):
        _patch_http(monkeypatch, get=lambda url, headers=None, timeout=None, stream=None: _Resp(
            {"name": "batches/x", "metadata": {"state": "BATCH_STATE_EXPIRED"}}))
        return bp.make_provider("gemini", engine_gemini.GeminiEngine("gm-key"))

    @pytest.mark.parametrize("stage", ["reflect", "expressive"])
    def test_a_gemini_batch_that_expires_keeps_the_paid_drafts(self, isolated_db, monkeypatch, stage):
        engine, did, ids, job, _ = self._to_stage(isolated_db, stage)
        self._edit(isolated_db, did, ids[0], "Mine")
        sent = len(engine.client.messages.batches.created)

        status = bulk_poll.check_once(job["id"], self._expired_gemini(monkeypatch), engine=engine)

        assert status == "failed"
        assert {ln.id: ln.en for ln in isolated_db.load_line_objects(did)} == {
            ids[0]: "Mine", ids[1]: f"draft-{ids[1]}"}
        saved = isolated_db.get_bulk_job(job["id"])
        assert saved["result_summary"]["drafts_applied"] == 1 and "EXPIRED" in saved["last_error"]
        assert len(engine.client.messages.batches.created) == sent
        assert bulk_poll.check_once(job["id"], self._expired_gemini(monkeypatch), engine=engine) == "failed"

    def test_a_reflect_batch_that_returns_nothing_keeps_the_paid_drafts(self, isolated_db):
        engine, did, ids, job, provider = self._to_stage(isolated_db, "reflect")
        engine.client.messages.batches.results_list = []

        assert bulk_poll.check_once(job["id"], provider, engine=engine) == "failed"
        assert {ln.id: ln.en for ln in isolated_db.load_line_objects(did)} == {
            i: f"draft-{i}" for i in ids}

    def test_an_expressive_request_missing_from_the_results_keeps_its_drafts(self, isolated_db):
        _engine, did, ids, job, _ = self._to_stage(isolated_db, "expressive", n=31)
        by_key = {}
        for r in isolated_db.list_bulk_job_lines(job["id"]):
            by_key.setdefault(r["request_key"], []).append(r["line_id"])
        answered, missing = sorted(by_key.items(), key=lambda kv: -len(kv[1]))
        results = [(answered[0], json.dumps({str(i): f"final-{i}" for i in answered[1]}), None, None)]

        bulk_poll.apply_results_for_job(job["id"], results)

        en = {ln.id: ln.en for ln in isolated_db.load_line_objects(did)}
        assert all(en[i] == f"final-{i}" for i in answered[1])
        assert all(en[i] == f"draft-{i}" for i in missing[1])

    def test_a_resubmit_re_entry_does_not_bring_back_a_cleared_draft(self, isolated_db, monkeypatch):
        # 21 lines go out as requests of 20 and 1; the 1-line reflection will fail.
        engine, did, ids, reflect, provider = self._to_stage(isolated_db, "reflect", n=61)
        sizes = {}
        for r in isolated_db.list_bulk_job_lines(reflect["id"]):
            sizes.setdefault(r["request_key"], []).append(r["line_id"])
        lone_key = min(sizes, key=lambda k: len(sizes[k]))
        lone = sizes[lone_key][0]
        self._answer(isolated_db, engine, reflect["id"], "critique", skip=(lone_key,))
        self._break_submit(monkeypatch, engine, times=1)

        assert bulk_poll.check_once(reflect["id"], provider, engine=engine) == "needs_resubmit"
        assert {ln.id: ln.en for ln in isolated_db.load_line_objects(did)}[lone] == f"draft-{lone}"
        self._edit(isolated_db, did, lone, "")

        assert bulk_poll.check_once(reflect["id"], provider, engine=engine) == "applied"
        assert {ln.id: ln.en for ln in isolated_db.load_line_objects(did)}[lone] == ""

    def _lines(self, isolated_db, did):
        return {ln.id: ln.en for ln in isolated_db.load_line_objects(did)}

    def _one_failed_reflection(self, isolated_db, monkeypatch=None, submit_failures=0):
        """21 lines = reflect requests of 20 and 1; the 1-line one is left unanswered."""
        engine, did, ids, reflect, provider = self._to_stage(isolated_db, "reflect", n=61)
        sizes = {}
        for r in isolated_db.list_bulk_job_lines(reflect["id"]):
            sizes.setdefault(r["request_key"], []).append(r["line_id"])
        lone_key = min(sizes, key=lambda k: len(sizes[k]))
        self._answer(isolated_db, engine, reflect["id"], "critique", skip=(lone_key,))
        if submit_failures:
            self._break_submit(monkeypatch, engine, times=submit_failures)
        return engine, did, reflect, provider, sizes[lone_key][0]

    def test_an_expired_expressive_batch_does_not_overwrite_a_reverted_draft(self, isolated_db, monkeypatch):
        engine, did, reflect, provider, lone = self._one_failed_reflection(isolated_db)
        assert bulk_poll.check_once(reflect["id"], provider, engine=engine) == "applied"
        assert self._lines(isolated_db, did)[lone] == f"draft-{lone}"
        self._edit(isolated_db, did, lone, "")
        expressive = bulk_reflect.sibling_stage_job(reflect["pipeline_id"], "expressive")

        status = bulk_poll.check_once(expressive["id"], self._expired_gemini(monkeypatch), engine=engine)

        en = self._lines(isolated_db, did)
        assert status == "failed" and en[lone] == ""
        assert all(v == f"draft-{k}" for k, v in en.items() if k != lone)

    def test_a_cancel_after_the_claim_stays_cancelled_but_the_drafts_are_written(
            self, isolated_db, monkeypatch):
        engine, did, ids, job, _ = self._to_stage(isolated_db, "reflect")
        real = bulk_reflect._apply_reflect_drafts

        def cancelled_while_applying(*a, **k):
            written = real(*a, **k)
            bulk_poll.cancel_bulk_job(job["id"])
            return written
        monkeypatch.setattr(bulk_reflect, "_apply_reflect_drafts", cancelled_while_applying)

        assert bulk_poll.check_once(job["id"], self._expired_gemini(monkeypatch), engine=engine) == "cancelled"
        assert isolated_db.get_bulk_job(job["id"])["status"] == "cancelled"
        assert self._lines(isolated_db, did) == {i: f"draft-{i}" for i in ids}

    def test_the_last_resort_drafts_leave_a_line_the_user_reverted_between_attempts(
            self, isolated_db, monkeypatch):
        engine, did, reflect, provider, lone = self._one_failed_reflection(
            isolated_db, monkeypatch, submit_failures=100)
        assert bulk_poll.check_once(reflect["id"], provider, engine=engine) == "needs_resubmit"
        self._edit(isolated_db, did, lone, "")

        statuses = [bulk_poll.check_once(reflect["id"], provider, engine=engine)
                    for _ in range(bulk_reflect.STAGE_SUBMIT_ATTEMPTS - 1)]

        assert statuses[-1] == "applied"
        en = self._lines(isolated_db, did)
        assert en[lone] == "" and all(v == f"draft-{k}" for k, v in en.items() if k != lone)

    def test_an_expressive_batch_that_returns_nothing_keeps_the_paid_drafts(self, isolated_db):
        engine, did, ids, job, provider = self._to_stage(isolated_db, "expressive")
        engine.client.messages.batches.results_list = []

        assert bulk_poll.check_once(job["id"], provider, engine=engine) == "failed"
        assert self._lines(isolated_db, did) == {i: f"draft-{i}" for i in ids}

    def test_a_failed_batch_left_applying_by_a_restart_is_failed_without_rewriting_lines(self, isolated_db):
        engine, did, ids, job, _ = self._to_stage(isolated_db, "expressive")
        isolated_db.update_bulk_job(job["id"], status="applying")
        background_jobs.clear_all_jobs()

        bulk_poll.resume_pending(did, lambda e, m: engine)

        assert isolated_db.get_bulk_job(job["id"])["status"] == "failed"
        assert self._lines(isolated_db, did) == {i: "" for i in ids}

    def test_the_monthly_cap_is_checked_before_the_next_stage_is_submitted(self, isolated_db):
        engine, did, jid = self._pipeline(isolated_db)
        ids = self._faithful_results(isolated_db, engine, jid)
        isolated_db.log_usage(did, "claude", "m", "translate", 1, 1, 50.0)

        status = bulk_poll.check_once(jid, bp.ClaudeBatchProvider(engine), engine=engine,
                               monthly_cap_usd=10.0)

        assert status == "applied"
        assert len(engine.client.messages.batches.created) == 1  # only the faithful stage
        assert {ln.id: ln.en for ln in isolated_db.load_line_objects(did)} == {
            i: f"draft-{i}" for i in ids}
        assert "cap" in isolated_db.get_bulk_job(jid)["last_error"]

    def test_a_next_stage_that_does_not_fit_the_remaining_cap_is_not_submitted(self, isolated_db):
        engine, did, jid = self._pipeline(isolated_db)
        ids = self._faithful_results(isolated_db, engine, jid)
        isolated_db.log_usage(did, "claude", "m", "translate", 1, 1, 9.999999)

        status = bulk_poll.check_once(jid, bp.ClaudeBatchProvider(engine), engine=engine,
                                      monthly_cap_usd=10.0)

        assert status == "applied"
        assert len(engine.client.messages.batches.created) == 1
        assert {ln.id: ln.en for ln in isolated_db.load_line_objects(did)} == {
            i: f"draft-{i}" for i in ids}
        assert "spending cap" in isolated_db.get_bulk_job(jid)["last_error"]

    def _count_creates(self, monkeypatch, engine, before=None, batch_id="msgbatch_01"):
        batches = engine.client.messages.batches
        real, sent = batches.create, []

        def create(requests):
            sent.append(requests)
            if before:
                before()
            real(requests)
            return NS(id=batch_id, processing_status="in_progress")
        monkeypatch.setattr(batches, "create", create)
        return sent

    def test_a_cancel_during_the_next_stage_submit_cancels_that_paid_stage(
            self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        self._faithful_results(isolated_db, engine, jid)
        sent = self._count_creates(monkeypatch, engine, before=lambda: bulk_poll.cancel_bulk_job(jid),
                                   batch_id="msgbatch_child")

        status = bulk_poll.check_once(jid, bp.ClaudeBatchProvider(engine), engine=engine)

        assert status == "cancelled" and isolated_db.get_bulk_job(jid)["status"] == "cancelled"
        child = bulk_reflect.sibling_stage_job(isolated_db.get_bulk_job(jid)["pipeline_id"], "reflect")
        assert child["status"] == "cancelled" and "provider" in child["last_error"]
        assert len(sent) == 1
        assert engine.client.messages.batches.cancelled == ["msgbatch_child"]

    def test_a_failed_provider_cancel_of_the_orphaned_stage_is_redacted(self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        self._faithful_results(isolated_db, engine, jid)
        self._count_creates(monkeypatch, engine, before=lambda: bulk_poll.cancel_bulk_job(jid))

        def refuse(batch_id):
            raise RuntimeError("HTTP 500 for key sk-ant-api03-SECRETSECRETSECRET")
        monkeypatch.setattr(engine.client.messages.batches, "cancel", refuse)

        assert bulk_poll.check_once(jid, bp.ClaudeBatchProvider(engine), engine=engine) == "cancelled"
        child = bulk_reflect.sibling_stage_job(isolated_db.get_bulk_job(jid)["pipeline_id"], "reflect")
        assert child["status"] == "cancelled"
        assert "cancel call failed" in child["last_error"] and "SECRETSECRET" not in child["last_error"]

    def test_a_cancel_while_the_results_apply_sends_no_next_stage(self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        self._faithful_results(isolated_db, engine, jid)
        sent = self._count_creates(monkeypatch, engine)
        real = isolated_db.set_bulk_job_line_result_texts

        def record_then_cancel(*a, **k):
            real(*a, **k)
            bulk_poll.cancel_bulk_job(jid)
        monkeypatch.setattr(isolated_db, "set_bulk_job_line_result_texts", record_then_cancel)

        assert bulk_poll.check_once(jid, bp.ClaudeBatchProvider(engine), engine=engine) == "cancelled"
        assert sent == []
        pipeline = isolated_db.get_bulk_job(jid)["pipeline_id"]
        assert [j["stage"] for j in isolated_db.list_bulk_jobs(pipeline_id=pipeline)] == ["faithful"]

    def test_a_failure_building_the_next_stage_prompts_counts_as_an_attempt(
            self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        ids = self._faithful_results(isolated_db, engine, jid)
        sent = self._count_creates(monkeypatch, engine)

        def broken(*a, **k):
            raise ValueError("prompt template broke")
        monkeypatch.setattr(reflect_prompts, "build_reflect_reflection_prompt", broken)
        provider = bp.ClaudeBatchProvider(engine)

        assert bulk_poll.check_once(jid, provider, engine=engine) == "needs_resubmit"
        job = isolated_db.get_bulk_job(jid)
        assert job["result_summary"]["next_stage_attempts"] == 1 and "template" in job["last_error"]
        statuses = [bulk_poll.check_once(jid, provider, engine=engine)
                    for _ in range(bulk_reflect.STAGE_SUBMIT_ATTEMPTS - 1)]
        assert statuses[-1] == "applied" and sent == []
        assert self._lines(isolated_db, did) == {i: f"draft-{i}" for i in ids}
        assert _usage_ops(isolated_db, did) == ["reflect_faithful_bulk"]

    def _crash_mid_apply(self, isolated_db, engine, jid):
        """The faithful drafts are recorded, then the app stops before the
        next stage is sent: the job is left "applying"."""
        isolated_db.update_bulk_job(jid, status="applying")
        bulk_reflect.apply_reflect_stage_results(jid, list(bp.ClaudeBatchProvider(engine).results("x")))
        background_jobs.clear_all_jobs()

    def test_a_reflect_stage_stranded_by_a_crash_sends_its_next_stage_once_at_startup(
            self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        ids = self._faithful_results(isolated_db, engine, jid)
        self._crash_mid_apply(isolated_db, engine, jid)
        sent = self._count_creates(monkeypatch, engine)

        bulk_poll.reconcile_stranded_applies(min_age_seconds=0)
        bulk_poll.reconcile_stranded_applies(min_age_seconds=0)
        assert isolated_db.get_bulk_job(jid)["status"] == "needs_resubmit" and sent == []

        provider = bp.ClaudeBatchProvider(engine)
        assert bulk_poll.check_once(jid, provider, engine=engine) == "applied"
        assert bulk_poll.check_once(jid, provider, engine=engine) == "applied"
        assert len(sent) == 1 and f"draft-{ids[0]}" in json.dumps(sent[0], ensure_ascii=False)
        pipeline = isolated_db.get_bulk_job(jid)["pipeline_id"]
        assert [j["stage"] for j in isolated_db.list_bulk_jobs(pipeline_id=pipeline)] == [
            "reflect", "faithful"]
        assert _usage_ops(isolated_db, did) == ["reflect_faithful_bulk"]

    def test_a_reflect_stage_stranded_by_a_crash_is_resumed_not_failed(self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        self._faithful_results(isolated_db, engine, jid)
        self._crash_mid_apply(isolated_db, engine, jid)
        sent = self._count_creates(monkeypatch, engine)

        assert bulk_poll.resume_pending(did, lambda e, m: engine) == {jid: "polling"}
        _wait_for(bulk_poll.poll_job_id(jid))

        assert isolated_db.get_bulk_job(jid)["status"] == "applied" and len(sent) == 1
        background_jobs.clear_job(bulk_poll.poll_job_id(jid))

    def test_a_stranded_stage_whose_next_stage_was_sent_is_not_sent_again(self, isolated_db, monkeypatch):
        engine, did, ids, reflect, provider = self._to_stage(isolated_db, "reflect")
        faithful = bulk_reflect.sibling_stage_job(reflect["pipeline_id"], "faithful")
        isolated_db.update_bulk_job(faithful["id"], status="applying")
        background_jobs.clear_all_jobs()
        sent = self._count_creates(monkeypatch, engine)

        bulk_poll.reconcile_stranded_applies(min_age_seconds=0)

        assert isolated_db.get_bulk_job(faithful["id"])["status"] == "failed" and sent == []

    def _cancel_from_jobs_page(self, jid):
        from services import jobs_service
        jobs_service.cancel_job(bulk_poll.poll_job_id(jid))

    def test_a_jobs_page_cancel_during_the_apply_sends_no_next_stage(self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        self._faithful_results(isolated_db, engine, jid)
        sent = self._count_creates(monkeypatch, engine)
        real = isolated_db.set_bulk_job_line_result_texts

        def record_then_cancel(*a, **k):
            real(*a, **k)
            self._cancel_from_jobs_page(jid)
        monkeypatch.setattr(isolated_db, "set_bulk_job_line_result_texts", record_then_cancel)

        bulk_poll.start_poller(jid, provider=bp.ClaudeBatchProvider(engine), engine=engine)
        _wait_for(bulk_poll.poll_job_id(jid))

        assert sent == [] and isolated_db.get_bulk_job(jid)["status"] == "cancelled"
        pipeline = isolated_db.get_bulk_job(jid)["pipeline_id"]
        assert [j["stage"] for j in isolated_db.list_bulk_jobs(pipeline_id=pipeline)] == ["faithful"]
        background_jobs.clear_job(bulk_poll.poll_job_id(jid))

    def test_a_jobs_page_cancel_during_the_next_stage_submit_cancels_that_stage(
            self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        self._faithful_results(isolated_db, engine, jid)
        sent = self._count_creates(monkeypatch, engine, before=lambda: self._cancel_from_jobs_page(jid),
                                   batch_id="msgbatch_child")

        bulk_poll.start_poller(jid, provider=bp.ClaudeBatchProvider(engine), engine=engine)
        _wait_for(bulk_poll.poll_job_id(jid))

        assert len(sent) == 1 and isolated_db.get_bulk_job(jid)["status"] == "cancelled"
        child = bulk_reflect.sibling_stage_job(isolated_db.get_bulk_job(jid)["pipeline_id"], "reflect")
        assert child["status"] == "cancelled"
        assert engine.client.messages.batches.cancelled == ["msgbatch_child"]
        background_jobs.clear_job(bulk_poll.poll_job_id(jid))

    def test_a_panel_cancel_is_offered_while_results_apply(self, isolated_db):
        from services import bulk_job_view
        engine, did, jid = self._pipeline(isolated_db)
        isolated_db.update_bulk_job(jid, status="applying")
        assert bulk_job_view.bulk_entry(isolated_db.get_bulk_job(jid))["cancellable"] is True

    def test_a_faithful_stage_stranded_before_its_drafts_were_saved_stays_failed(
            self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        isolated_db.update_bulk_job(jid, status="applying")
        background_jobs.clear_all_jobs()
        sent = self._count_creates(monkeypatch, engine)

        bulk_poll.resume_pending(did, lambda e, m: engine)

        job = isolated_db.get_bulk_job(jid)
        assert job["status"] == "failed" and "run again" in job["last_error"] and sent == []

    def test_startup_leaves_a_recent_apply_to_the_process_running_it(self, isolated_db, monkeypatch):
        engine, did, jid = self._pipeline(isolated_db)
        self._faithful_results(isolated_db, engine, jid)
        self._crash_mid_apply(isolated_db, engine, jid)

        bulk_poll.reconcile_stranded_applies()

        assert isolated_db.get_bulk_job(jid)["status"] == "applying"


class TestCancelWins:
    def _translate_job(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        bulk_id = _submit(isolated_db, did, engine)
        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"
        return engine, did, bulk_id

    def test_a_cancel_while_a_translation_applies_stays_cancelled(self, isolated_db, monkeypatch):
        engine, did, bulk_id = self._translate_job(isolated_db)
        real = bulk_poll.apply_results_for_job

        def apply_then_cancel(*a, **k):
            counts = real(*a, **k)
            bulk_poll.cancel_bulk_job(bulk_id)
            return counts
        monkeypatch.setattr(bulk_poll, "apply_results_for_job", apply_then_cancel)

        assert bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine)) == "cancelled"
        job = isolated_db.get_bulk_job(bulk_id)
        assert job["status"] == "cancelled" and job["result_summary"]["applied"] == 3

    def test_a_failed_apply_does_not_overwrite_a_cancel(self, isolated_db, monkeypatch):
        engine, did, bulk_id = self._translate_job(isolated_db)

        def cancel_then_break(*a, **k):
            bulk_poll.cancel_bulk_job(bulk_id)
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(bulk_poll, "apply_results_for_job", cancel_then_break)

        assert bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine)) == "cancelled"
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "cancelled"

    def test_cancel_leaves_a_finished_job_alone(self, isolated_db):
        engine, did, bulk_id = self._translate_job(isolated_db)
        provider = bp.make_provider("claude", engine)
        assert bulk_poll.check_once(bulk_id, provider) == "applied"

        note = bulk_poll.cancel_bulk_job(bulk_id, provider)

        assert "already finished" in note
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "applied"
        assert engine.client.messages.batches.cancelled == []

    def test_an_auth_error_does_not_overwrite_a_cancel(self, isolated_db):
        engine, did, bulk_id = self._translate_job(isolated_db)

        class CancelledThenRefused:
            def poll(self, batch_id):
                bulk_poll.cancel_bulk_job(bulk_id)
                raise bp.BulkAuthError("Gemini refused the API key (HTTP 403).")
        with pytest.raises(bp.BulkAuthError):
            bulk_poll.check_once(bulk_id, CancelledThenRefused())
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "cancelled"

    def test_a_cleared_auth_error_does_not_overwrite_a_cancel(self, isolated_db):
        engine, did, bulk_id = self._translate_job(isolated_db)
        isolated_db.update_bulk_job(bulk_id, status="auth_error", last_error="refused")

        class CancelledWhilePending:
            def poll(self, batch_id):
                bulk_poll.cancel_bulk_job(bulk_id)
                return "pending"
        assert bulk_poll.check_once(bulk_id, CancelledWhilePending()) == "cancelled"
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "cancelled"


class TestBulkReflectMatchesLiveReflect:
    _guidelines = "Pronoun default: she/her unless a character is known to be male."
    _novel = "林走进了房间。他来了。"

    def _setup(self, isolated_db):
        did = isolated_db.create_drama(title_en="R", status="aligned", translation_engine="claude",
                                       source_language="zh")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="他来了", speaker="SPEAKER_00")])
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Lin")
        drama = isolated_db.get_drama(did)
        args = {"style_note": "", "drama_meta": drama, "novel_reference": self._novel,
                "locale": "en-US", "glossary_terms": [], "style_guidelines": self._guidelines}
        return did, drama, args

    def _store_novel(self, isolated_db, did):
        folder = os.path.join(isolated_db.DRAMAS_DIR, str(did))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "novel_reference.txt"), "w", encoding="utf-8") as f:
            f.write(self._novel)
        isolated_db.update_drama(did, novel_reference_filename="novel_reference.txt")

    def test_the_stored_job_points_at_the_title_instead_of_copying_the_novel(self, isolated_db):
        did, _drama_row, args = self._setup(isolated_db)
        self._store_novel(isolated_db, did)
        jid = bulk_reflect.submit_reflect_pipeline(did, isolated_db.load_line_objects(did), _claude_engine(),
                                                   "claude", args)
        stored = isolated_db.get_bulk_job(jid)["translate_args"]
        assert "novel_reference" not in stored and stored["novel_reference_from_title"] is True
        assert bulk_translate.resolve_novel_reference(did, stored) == self._novel

    def test_a_job_stored_with_the_novel_text_still_runs_with_it(self, isolated_db):
        did, _drama_row, _args = self._setup(isolated_db)
        old = {"novel_reference": "stored copy"}
        assert bulk_translate.resolve_novel_reference(did, old) == "stored copy"
        assert bulk_translate.resolve_novel_reference(did, {}) is None
        assert bulk_translate.resolve_novel_reference(did, {"novel_reference_from_title": True}) is None

    def test_an_offpeak_job_stores_the_marker_too(self, isolated_db):
        did, _drama_row, _args = self._setup(isolated_db)
        jid = bulk_translate.schedule_offpeak_translation(
            did, isolated_db.load_line_objects(did), "deepseek", "deepseek-v4-flash",
            {"novel_reference": self._novel, "style_note": ""})
        stored = isolated_db.get_bulk_job(jid)["translate_args"]
        assert "novel_reference" not in stored and stored["novel_reference_from_title"] is True

    def test_the_faithful_prompt_is_the_live_pass_one_prompt(self, isolated_db, monkeypatch):
        from engine_backends import reflect_pass
        import translation_guide
        did, drama, args = self._setup(isolated_db)
        engine = _claude_engine()
        lines = isolated_db.load_line_objects(did)
        bulk_reflect.submit_reflect_pipeline(did, lines, engine, "claude", args)
        bulk_prompt = _request_text(engine)

        seen = []
        monkeypatch.setattr(reflect_pass, "call_llm_json",
                            lambda eng, prompt, system=None, **k: seen.append(f"{system}\n\n{prompt}") or "{}")
        names = translation_guide.build_speaker_labels(
            isolated_db.list_characters_with_series_names(did), [])
        ctx = translate_pipeline.build_translation_context(engine, drama, novel_reference=self._novel,
                                           glossary_terms=[], style_guidelines=self._guidelines)
        ctx.update(speaker_labels=[names.get(ln.speaker) for ln in lines],
                   line_ids=[ln.id for ln in lines], batch_source_lines=[ln.zh for ln in lines],
                   line_languages=None)
        engine_reflect_pass.reflect_translate_batch(engine, prompts.pronoun_neutral_texts(ctx, [ln.zh for ln in lines]), ctx)

        assert bulk_prompt == seen[0]
        assert self._novel in bulk_prompt and "[Lin" in bulk_prompt
        assert "TA来了" in bulk_prompt.split("Lines:\n", 1)[1]

    def test_later_stages_keep_the_novel_block_and_pronoun_handling(self, isolated_db):
        did, _drama_row, args = self._setup(isolated_db)
        self._store_novel(isolated_db, did)
        engine = _claude_engine()
        jid = bulk_reflect.submit_reflect_pipeline(did, isolated_db.load_line_objects(did), engine, "claude", args)
        batches = engine.client.messages.batches
        lid = isolated_db.list_bulk_job_lines(jid)[0]["line_id"]
        batches.results_list = [_succeeded(batches.created[0]["custom_id"], {str(lid): "She came."})]
        batches.status = "ended"
        assert bulk_poll.check_once(jid, bp.ClaudeBatchProvider(engine), engine=engine) == "applied"

        second = _request_text(engine)
        assert self._novel in second and "TA来了" in second.split("Lines:\n", 1)[1]
        assert prompts.pronoun_batch_note(self._guidelines).strip() in second


class TestOffPeakRunFinishes:
    _saturday = datetime.datetime(2026, 9, 26, 12, 0)

    def _engine(self, on_call=None, block=None):
        class Engine:
            name = "deepseek"
            supports_reference = True
            checks_replies = False  # echoes the source script back
            model = "deepseek-v4-flash"

            def __init__(self):
                self.last_usage, self.seen = {}, []

            def translate_batch(self, zh_lines, context):
                self.seen.append(list(zh_lines))
                if on_call:
                    on_call()
                if block and block in zh_lines:
                    raise engine_shared.ContentModerationBlocked("deepseek", "Content Exists Risk")
                return [f"Xiaoming says {z}" for z in zh_lines]
        return Engine()

    def _schedule(self, isolated_db, did, args):
        return bulk_translate.schedule_offpeak_translation(did, isolated_db.load_line_objects(did), "deepseek",
                                               "deepseek-v4-flash", args, now=self._saturday)

    def test_cancel_stops_a_running_offpeak_job_and_it_stays_cancelled(self, isolated_db, monkeypatch):
        monkeypatch.setattr(bulk_poll, "_utcnow", lambda: self._saturday)
        did = _drama(isolated_db, n=3)
        bulk_id = self._schedule(isolated_db, did, {"batch_size": 1})
        engine = self._engine(on_call=lambda: bulk_poll.cancel_bulk_job(bulk_id))

        bulk_poll.start_poller(bulk_id, engine=engine, monthly_cap_usd=50.0)
        _wait_for(bulk_poll.poll_job_id(bulk_id))

        assert engine.seen == [["第0句"]]
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "cancelled"
        background_jobs.clear_job(bulk_poll.poll_job_id(bulk_id))

    def test_the_run_finishes_like_a_normal_translation(self, isolated_db):
        did = _drama(isolated_db, n=3)
        term = {"term_original": "第0句", "term_translation": "Xiao Ming", "notes": "Xiaoming",
                "enforce_exact": True}
        bulk_id = self._schedule(isolated_db, did, {"batch_size": 1, "glossary_terms": [term],
                                                    "style_preset": "audio_drama"})

        bulk_poll.run_bulk_poller("t_ds_finish", bulk_id, engine=self._engine(block="第1句"),
                           sleep=lambda s: None, now_fn=lambda: self._saturday, monthly_cap_usd=50.0)

        rows = isolated_db.load_lines(did)
        assert rows[0]["en"] == "Xiao Ming says 第0句"
        assert rows[1]["flag"] == "content_blocked"
        assert isolated_db.list_translation_versions(did)
        assert json.loads(isolated_db.get_drama(did)["last_translate_errors"])
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "applied"
        background_jobs.clear_job("t_ds_finish")


class TestResumeWhileOuterJobIsActive:
    def test_resume_leaves_a_running_offpeak_job_under_its_outer_job_alone(self, isolated_db):
        # run_bulk_translate_job polls under bulk_translate_<drama>, so the
        # bulkpoll_<id> id is free while a paid off-peak run is in flight.
        import threading
        did = _drama(isolated_db, n=2)
        lines = isolated_db.load_line_objects(did)
        bulk_id = bulk_translate.schedule_offpeak_translation(did, lines, "deepseek", "m", {})
        isolated_db.update_bulk_job(bulk_id, status="running")
        release = threading.Event()
        outer = f"bulk_translate_{did}"
        assert job_launch.start_job(outer, lambda: release.wait(10))
        asked = []
        try:
            out = bulk_poll.resume_pending(did, lambda e, m: asked.append((e, m)) or object())
        finally:
            release.set()
            _wait_for(outer)
        assert out == {bulk_id: "running"}
        assert asked == []
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "running"
        assert not background_jobs.is_running(bulk_poll.poll_job_id(bulk_id))


class TestResumeAndSchedulerRaces:
    def _offpeak(self, isolated_db, status):
        did = _drama(isolated_db, n=2)
        bulk_id = bulk_translate.schedule_offpeak_translation(
            did, isolated_db.load_line_objects(did), "deepseek", "m", {})
        isolated_db.update_bulk_job(bulk_id, status=status)
        return did, bulk_id

    def test_resume_does_not_rerun_an_offpeak_job_that_finishes_between_check_and_write(
            self, isolated_db, monkeypatch):
        did, bulk_id = self._offpeak(isolated_db, "running")

        def finishes_in_the_gap(job):
            isolated_db.update_bulk_job(bulk_id, status="applied")
            return False
        monkeypatch.setattr(bulk_poll, "_polled_by_outer_job", finishes_in_the_gap)
        asked = []

        out = bulk_poll.resume_pending(did, lambda e, m: asked.append(e) or object())

        assert out == {} and asked == []
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "applied"

    def test_resume_treats_a_force_stopped_outer_job_with_a_live_worker_as_running(self, isolated_db):
        import threading
        import job_force_stop
        did, bulk_id = self._offpeak(isolated_db, "running")
        release = threading.Event()
        worker = threading.Thread(target=release.wait, args=(10,), daemon=True)
        worker.start()
        job_force_stop._abandoned[f"bulk_translate_{did}"] = worker
        asked = []
        try:
            out = bulk_poll.resume_pending(did, lambda e, m: asked.append(e) or object())
        finally:
            release.set()
            worker.join()
            job_force_stop._abandoned.clear()

        assert out == {bulk_id: "running"} and asked == []
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "running"

    def test_the_scheduler_does_not_start_a_run_that_was_cancelled_in_the_gap(
            self, isolated_db, monkeypatch):
        did, bulk_id = self._offpeak(isolated_db, "scheduled")
        saturday_noon = datetime.datetime(2099, 9, 26, 12, 0)
        isolated_db.update_bulk_job(bulk_id, scheduled_for=saturday_noon.isoformat())

        def cancelled_in_the_gap():
            isolated_db.update_bulk_job(bulk_id, status="cancelled")
            return saturday_noon
        monkeypatch.setattr(bulk_poll, "run_scheduled_job",
                            lambda *a, **k: pytest.fail("a cancelled job must not run"))

        bulk_poll.run_bulk_poller("t_race", bulk_id, engine=object(), now_fn=cancelled_in_the_gap,
                           sleep=lambda s: None)

        assert isolated_db.get_bulk_job(bulk_id)["status"] == "cancelled"


class TestApplyKeepsEditsSavedDuringTheApply:
    """An edit saved between an apply's load and its save (e.g. while it
    flags dense lines) is kept, and the saved version holds it."""

    @staticmethod
    def _edit_after_load(monkeypatch, isolated_db, did, edit,
                         fields=("en", "flag", "flag_note")):
        real_load, done = isolated_db.load_line_objects, []

        def load(drama_id, *a, **kw):
            loaded = real_load(drama_id, *a, **kw)
            if not done:
                done.append(True)
                fresh = real_load(did)
                edit(fresh)
                isolated_db.save_lines(did, fresh, fields=fields)
            return loaded
        monkeypatch.setattr(bulk_apply.db, "load_line_objects", load)

    def _apply_translation(self, monkeypatch, isolated_db, edit, prepare=None,
                           answer=lambda zh: _en(zh), **kw):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        ids = [r["id"] for r in isolated_db.load_lines(did)]
        if prepare:
            lines = isolated_db.load_line_objects(did)
            prepare(lines)
            isolated_db.save_lines(did, lines)
        bulk_id = _submit(isolated_db, did, engine)
        batches = engine.client.messages.batches
        _answer_every_request(batches, answer)
        batches.status = "ended"
        self._edit_after_load(monkeypatch, isolated_db, did, edit, **kw)
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))
        [active] = [v for v in isolated_db.list_translation_versions(did) if v["is_active"]]
        version = {ln["id"]: ln["en"] for ln in isolated_db.get_translation_version(active["id"])["lines"]}
        rows = {r["id"]: r["en"] for r in isolated_db.load_lines(did)}
        return ids, rows, version, isolated_db.get_bulk_job(bulk_id)["result_summary"]

    def test_an_edit_to_an_applied_line_wins_and_is_counted(self, monkeypatch, isolated_db):
        def edit(fresh):
            fresh[1].en = "Typed during the apply"
        ids, rows, version, summary = self._apply_translation(monkeypatch, isolated_db, edit)
        assert rows[ids[1]] == "Typed during the apply"
        assert version[ids[1]] == "Typed during the apply"
        assert rows[ids[0]] == version[ids[0]] == "EN[line0]"
        assert summary["kept_your_edit"] == 1 and summary["applied"] == 2

    def test_an_edit_to_an_unrelated_line_survives_in_the_version_too(self, monkeypatch, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        ids = [r["id"] for r in isolated_db.load_lines(did)]
        lines = isolated_db.load_line_objects(did)
        lines[2].en = "Already mine"
        isolated_db.save_lines(did, lines, fields=("en",))
        bulk_id = _submit(isolated_db, did, engine)  # line 2 has English: not a target
        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"

        def edit(fresh):
            fresh[2].en = "Reworded during the apply"
        self._edit_after_load(monkeypatch, isolated_db, did, edit)
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))
        [active] = [v for v in isolated_db.list_translation_versions(did) if v["is_active"]]
        version = {ln["id"]: ln["en"] for ln in isolated_db.get_translation_version(active["id"])["lines"]}
        rows = {r["id"]: r["en"] for r in isolated_db.load_lines(did)}
        assert rows[ids[2]] == version[ids[2]] == "Reworded during the apply"
        assert rows[ids[0]] == "EN[line0]"

    def test_a_flag_set_on_a_line_during_a_flag_apply_is_kept(self, monkeypatch, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="translated")
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]
        jid = bulk_translate.submit_bulk_flag(did, lines, engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]

        def edit(fresh):
            fresh[0].flag, fresh[0].flag_note = "slang_idiom", "mine"
        self._edit_after_load(monkeypatch, isolated_db, did, edit)
        payload = [{"line_idx": i, "reason": "uncertain_translation", "note": "auto"} for i in ids]
        summary = bulk_apply.apply_flag_results(jid, [(key, json.dumps(payload), {}, None)])
        final = {r["id"]: (r["flag"], r["flag_note"]) for r in isolated_db.load_lines(did)}
        assert final[ids[0]] == ("slang_idiom", "mine")
        assert final[ids[1]] == ("uncertain_translation", "auto")
        assert summary["kept_your_edit"] == 1 and summary["applied"] == 1

    def test_a_flag_change_alone_does_not_drop_the_new_english(self, monkeypatch, isolated_db):
        def prepare(lines):
            lines[1].flag, lines[1].flag_note = "source_changed", "old source"

        def edit(fresh):
            fresh[1].flag, fresh[1].flag_note = "slang_idiom", "mine"
        ids, rows, _version, summary = self._apply_translation(
            monkeypatch, isolated_db, edit, prepare=prepare)
        assert rows[ids[1]] == "EN[line1]"
        row = next(r for r in isolated_db.load_lines(_drama_of(isolated_db, ids[1]))
                   if r["id"] == ids[1])
        assert (row["flag"], row["flag_note"]) == ("slang_idiom", "mine")
        assert summary["applied"] == 3 and summary["kept_your_edit"] == 0

    def test_english_for_a_source_changed_during_the_apply_is_not_written(
            self, monkeypatch, isolated_db):
        def edit(fresh):
            fresh[1].zh = "改过的句子"
        ids, rows, version, summary = self._apply_translation(
            monkeypatch, isolated_db, edit, fields=("zh",))
        assert rows[ids[1]] == "" and version[ids[1]] == ""
        assert rows[ids[0]] == "EN[line0]" and summary["applied"] == 2

    def test_no_density_flag_from_timing_changed_during_the_apply(self, monkeypatch, isolated_db):
        long_en = "A long English line nobody could read in a second and a half"

        def prepare(lines):
            lines[0].end = lines[0].start + 1.5

        def edit(fresh):
            fresh[0].end = fresh[0].start + 30
        ids, rows, _version, _summary = self._apply_translation(
            monkeypatch, isolated_db, edit, prepare=prepare, answer=lambda zh: long_en,
            fields=("start", "end"))
        row = next(r for r in isolated_db.load_lines(_drama_of(isolated_db, ids[0]))
                   if r["id"] == ids[0])
        assert rows[ids[0]] == long_en and row["flag"] is None

    def test_a_review_flag_is_not_written_onto_english_replaced_meanwhile(
            self, monkeypatch, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="translated")
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]
        jid = bulk_translate.submit_bulk_flag(did, lines, engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]

        def edit(fresh):
            fresh[0].en = "Rewritten by hand"
        self._edit_after_load(monkeypatch, isolated_db, did, edit)
        payload = [{"line_idx": i, "reason": "uncertain_translation", "note": "auto"} for i in ids]
        summary = bulk_apply.apply_flag_results(jid, [(key, json.dumps(payload), {}, None)])
        final = {r["id"]: r["flag"] for r in isolated_db.load_lines(did)}
        assert final == {ids[0]: None, ids[1]: "uncertain_translation"}
        assert summary["applied"] == 1 and summary["kept_your_edit"] == 1

    def test_a_source_changed_flag_that_did_not_land_is_not_counted(self, monkeypatch, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="translated")
        lines = isolated_db.load_line_objects(did)
        jid = bulk_translate.submit_bulk_flag(did, lines, engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]
        lines = isolated_db.load_line_objects(did)
        lines[0].zh = "changed text"
        isolated_db.save_lines(did, lines, fields=("zh",))

        def edit(fresh):
            fresh[0].flag, fresh[0].flag_note = "slang_idiom", "mine"
        self._edit_after_load(monkeypatch, isolated_db, did, edit)
        summary = bulk_apply.apply_flag_results(jid, [(key, "[]", {}, None)])
        assert isolated_db.load_lines(did)[0]["flag"] == "slang_idiom"
        assert summary["flagged_source_changed"] == 0


def _drama_of(isolated_db, line_id):
    return next(d["id"] for d in isolated_db.list_dramas()
                if any(r["id"] == line_id for r in isolated_db.load_lines(d["id"])))


def test_finish_translation_run_requires_the_runs_own_line_ids(isolated_db):
    did = _drama(isolated_db, n=1)
    with pytest.raises(TypeError):
        translation_finish.finish_translation_run(did, isolated_db.load_line_objects(did), NS(model="m"),
                                  "claude", "", [], [])


def test_engines_without_a_batch_api_get_the_exact_message():
    from bulk import bulk_reflect
    from bulk import bulk_translate
    generic = "ollama has no batch API -- bulk mode needs Claude or Gemini."
    for submit in (bulk_translate.submit_bulk_flag, bulk_translate.submit_bulk_consistency,
                   bulk_translate.submit_bulk_emotion, bulk_translate.submit_bulk_translation_notes):
        with pytest.raises(ValueError) as exc:
            submit(1, [], None, "ollama")
        assert str(exc.value) == generic
    with pytest.raises(ValueError) as exc:
        bulk_reflect.submit_reflect_pipeline(1, [], None, "ollama", {})
    assert str(exc.value) == generic
    with pytest.raises(ValueError) as exc:
        bulk_translate.submit_bulk_translation(1, [], None, "ollama", {})
    assert str(exc.value) == "ollama has no batch API -- use the off-peak schedule instead."
    provider = object()
    assert bulk_translate._require_batch_engine(provider, "claude") is provider


def test_load_apply_context_indexes_rows_by_line_and_request_key(isolated_db, monkeypatch):
    from bulk import bulk_apply
    rows = [{"line_id": 1, "request_key": "a"}, {"line_id": 2, "request_key": "a"},
            {"line_id": 3, "request_key": "b"}]
    lines = [NS(id=1), NS(id=3)]
    monkeypatch.setattr(bulk_apply.db, "list_bulk_job_lines", lambda job_id: rows)
    monkeypatch.setattr(bulk_apply.db, "load_line_objects", lambda drama_id: lines)

    ctx = bulk_apply._load_apply_context(7, 9)
    assert ctx.ids_by_key == {"a": [1, 2], "b": [3]}
    assert ctx.row_by_id[3] is rows[2]
    assert ctx.current is lines and set(ctx.cur_by_id) == {1, 3}

    lazy = bulk_apply._load_apply_context(7, 9, with_current=False)
    assert lazy.current == [] and lazy.cur_by_id == {}


class TestBulkJobOutcome:
    """The bulk job's final result replaces the poller's; how the run ended
    must survive that, or a run that gave up reads as a plain success."""

    @pytest.mark.parametrize("poller_result, summary, says", [
        ({"stopped": "Stopped: 15 checks in a row failed"}, None,
         "Stopped: 15 checks in a row failed. Finished work was kept."),
        ({"translated": 4, "batch_errors": 2, "cap_reached": None}, {"batch_errors": 2},
         "Only part of the work finished."),
        ({"translated": 4, "batch_errors": 0, "cap_reached": 1.5},
         {"batch_errors": 0, "cap_reached": 1.5}, "Stopped at the spending cap"),
        (None, {"applied": 8, "failed_requests": 2, "unknown_requests": 0},
         "Only part of the work finished."),
    ])
    def test_a_run_that_did_not_finish_cleanly_is_not_finished(
            self, isolated_db, monkeypatch, poller_result, summary, says):
        from services import jobs_service, translate_run_service
        monkeypatch.setattr(bp, "make_provider", lambda name, engine: object())
        monkeypatch.setattr(bulk_poll, "run_bulk_poller",
                            lambda job_id, current, **kw: background_jobs.set_result(job_id, poller_result))
        monkeypatch.setattr(isolated_db, "get_bulk_job", lambda bid: {
            "status": "submitted" if summary is None else "applied", "stage": None,
            "result_summary": summary, "last_error": None})
        job_id = "bulk_translate_outcome"
        assert job_launch.start_job(job_id, translate_run_service.run_bulk_translate_job, job_id,
                                    object(), "deepseek", lambda: 7)
        _wait_for(job_id)
        status = background_jobs.get_status(job_id)
        outcome, message = jobs_service.derive_outcome(status["status"], status["error"],
                                                       jobs_service.project_result(status["result"]))
        background_jobs.clear_job(job_id)
        assert outcome == "partial" and says in message

    def test_an_earlier_stages_flags_do_not_mark_a_clean_last_stage(self, isolated_db):
        job_id = "bulk_translate_stages"
        assert job_launch.start_job(job_id, lambda: background_jobs.set_result(
            job_id, {"translated": 4, "batch_errors": 2, "cap_reached": 1.0}))
        _wait_for(job_id)
        clean = {"status": "applied", "result_summary": {"applied": 8, "failed_requests": 0}}
        assert bulk_poll.poller_outcome(job_id, clean) == {}
        background_jobs.clear_job(job_id)


# ---------------------------------------------------------------------------
# Junk replies and a failed apply (paid results are kept)
# ---------------------------------------------------------------------------

class TestBulkReplyChecks:
    JUNK = {0: "<think>plan</think> Hello", 1: "第1句 hello", 2: '["a", "b"]'}

    def _run(self, isolated_db, reply, args=None):
        engine = _claude_engine()
        did = _drama(isolated_db, n=4)
        lines = isolated_db.load_line_objects(did)
        bulk_id = bulk_translate.submit_bulk_translation(
            did, lines, engine, "claude", {"drama_meta": {}}, batch_size=10,
            translate_args={"style_preset": "audio_drama", **(args or {})})
        batches = engine.client.messages.batches
        _answer_every_request(batches, lambda zh: reply(int(re.search(r"\d+", zh).group())))
        batches.status = "ended"
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))
        return did, isolated_db.get_bulk_job(bulk_id)

    def _reply(self, i):
        return self.JUNK.get(i, _en(str(i)))

    def test_junk_replies_are_not_written_and_the_line_is_flagged(self, isolated_db):
        did, job = self._run(isolated_db, self._reply)
        rows = isolated_db.load_lines(did)
        assert [r["en"] for r in rows[:3]] == ["", "", ""]
        assert [r["flag"] for r in rows[:3]] == ["uncertain_translation"] * 3
        assert "unusable" in rows[0]["flag_note"]
        assert rows[0]["flag_note"].endswith("<think>plan</think> Hello")  # kept for a hand fix
        assert rows[3]["en"] == "EN[line3]" and not rows[3]["flag"]
        assert job["status"] == "applied"
        assert job["result_summary"]["rejected"] == 3 and job["result_summary"]["applied"] == 1

    def test_turning_the_reply_check_off_writes_them_as_before(self, isolated_db):
        did, job = self._run(isolated_db, self._reply, args={"check_replies": False})
        assert [r["en"] for r in isolated_db.load_lines(did)][:3] == [
            "<think>plan</think> Hello", "第1句 hello", '["a", "b"]']

    def test_reflect_english_is_checked_too(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        jid = bulk_reflect.submit_reflect_pipeline(did, isolated_db.load_line_objects(did), engine, "claude",
                                                   {"locale": "en-US"})
        job = isolated_db.get_bulk_job(jid)
        rows = {r["line_id"]: r for r in isolated_db.list_bulk_job_lines(jid)}
        ids = list(rows)
        counts = {"applied": 0, "dropped_deleted": 0, "flagged_source_changed": 0, "kept_your_edit": 0}
        bulk_reflect._write_reflect_english(job, rows, {ids[0]: "<think>x</think> No", ids[1]: "Fine."},
                                            {}, counts, "t")
        by_id = {r["id"]: r for r in isolated_db.load_lines(did)}
        assert by_id[ids[0]]["en"] == "" and by_id[ids[0]]["flag"] == "uncertain_translation"
        assert by_id[ids[1]]["en"] == "Fine."
        assert counts["rejected"] == 1


class TestPaidResultsSurviveAFailedApply:
    def _ended(self, isolated_db, n=7):
        engine = _claude_engine()
        did = _drama(isolated_db, n=n)
        bulk_id = _submit(isolated_db, did, engine)
        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"
        return engine, did, bulk_id

    def test_a_locked_database_before_any_english_is_written_is_retried_from_the_same_results(
            self, isolated_db, monkeypatch):
        engine, did, bulk_id = self._ended(isolated_db)
        _fail_first(monkeypatch, isolated_db, "save_lines", when=lambda *a, **k: k.get("fields") == ("en",))

        assert bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine)) == "applied"

        assert all(r["en"] == _en(r["zh"]) for r in isolated_db.load_lines(did))
        assert _usage_ops(isolated_db, did) == ["translate_bulk"] * 3  # one row per request, not two

    def test_usage_logged_before_the_failure_is_not_logged_again(self, isolated_db, monkeypatch):
        engine, did, bulk_id = self._ended(isolated_db)
        _fail_first(monkeypatch, isolated_db, "log_usage",
                    when=lambda *a, **k: len(_usage_ops(isolated_db, did)) == 1)

        assert bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine)) == "applied"

        assert all(r["en"] == _en(r["zh"]) for r in isolated_db.load_lines(did))
        assert _usage_ops(isolated_db, did) == ["translate_bulk"] * 3

    def test_one_unreadable_reply_does_not_cost_the_other_requests(self, isolated_db, monkeypatch):
        engine, did, bulk_id = self._ended(isolated_db)
        real = bulk_apply._parse_strict

        def parse(text, ids):
            if "line0" in text:
                raise ValueError("unreadable")
            return real(text, ids)
        monkeypatch.setattr(bulk_apply, "_parse_strict", parse)

        assert bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine)) == "applied"

        rows = isolated_db.load_lines(did)
        assert sum(1 for r in rows if r["en"]) == 4  # batches of 3: the first batch's 3 lines are missing
        summary = isolated_db.get_bulk_job(bulk_id)["result_summary"]
        assert summary["failed_requests"] == 1 and summary["missing"] == 3 and summary["applied"] == 4

    def test_a_failure_after_the_english_was_written_is_not_applied_again(self, isolated_db, monkeypatch):
        engine, did, bulk_id = self._ended(isolated_db)
        saves = []
        real = isolated_db.save_lines
        monkeypatch.setattr(isolated_db, "save_lines",
                            lambda *a, **k: (saves.append(k.get("fields")), real(*a, **k))[1])
        _fail_first(monkeypatch, isolated_db, "save_translation_version")

        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))

        assert isolated_db.get_bulk_job(bulk_id)["status"] == "failed"
        assert all(r["en"] == _en(r["zh"]) for r in isolated_db.load_lines(did))  # what landed stays
        assert saves.count(("en",)) == 1  # no second apply
        assert _usage_ops(isolated_db, did) == ["translate_bulk"] * 3
