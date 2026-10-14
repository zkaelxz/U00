"""
tests/test_bulk_translate.py -- Step 9's bulk mode. Every provider here
is a fake: no network, no real batch API.
"""
import datetime
import itertools
import json
import os
import re
import sqlite3
import sys
import time
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs
from jobs import job_launch
import bulk_providers as bp
import bulk_apply
import bulk_poll
import bulk_reflect
import bulk_translate
import translation_finish
import translate_engines as te
from core import Line
from lib import http
from tests.http_fakes import StreamedBody


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _numbered_lines(text: str) -> dict:
    """{line_id: zh} from the numbered block of a request's user message."""
    block = text.split("Translate these lines:\n\n", 1)[1]
    return {int(m.group(1)): m.group(2) for m in re.finditer(r"^(\d+)\. (?:\[[^\]]*\] )?(.*)$", block, re.M)}


class FakeClaudeBatches:
    def __init__(self):
        self.created = None
        self.status = "in_progress"
        self.results_list = []
        self.cancelled = []
        self.retrieved = []
        self.raise_on_retrieve = None

    def create(self, requests):
        self.created = requests
        return NS(id="msgbatch_01", processing_status="in_progress")

    def retrieve(self, batch_id):
        if self.raise_on_retrieve:
            raise self.raise_on_retrieve
        self.retrieved.append(batch_id)
        return NS(processing_status=self.status)

    def results(self, batch_id):
        return iter(self.results_list)

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)
        return NS(processing_status="canceling")


def _claude_engine():
    engine = te.ClaudeEngine("sk-ant-fake")
    engine.client = NS(messages=NS(batches=FakeClaudeBatches()))
    return engine


def _succeeded(custom_id, payload: dict, usage=None):
    msg = NS(content=[NS(type="text", text=json.dumps(payload, ensure_ascii=False))],
             usage=usage or NS(input_tokens=1000, output_tokens=100,
                               cache_read_input_tokens=0, cache_creation_input_tokens=0))
    return NS(custom_id=custom_id, result=NS(type="succeeded", message=msg))


def _answer_every_request(batches: FakeClaudeBatches, fn=lambda zh: f"EN[{zh}]"):
    """Builds a correct result for every submitted request -- then
    returns them in REVERSE order, with each response's keys reversed
    too, so nothing lines up by position."""
    results = []
    for req in batches.created:
        lines = _numbered_lines(req["params"]["messages"][0]["content"])
        payload = {str(i): fn(zh) for i, zh in reversed(list(lines.items()))}
        results.append(_succeeded(req["custom_id"], payload))
    batches.results_list = list(reversed(results))


def _drama(isolated_db, n=7, **kw):
    did = isolated_db.create_drama(title_en="Bulk", status="aligned", translation_engine="claude")
    isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"第{i}句", **kw) for i in range(n)])
    return did


def _submit(isolated_db, did, engine, batch_size=3, **kw):
    lines = isolated_db.load_line_objects(did)
    return bulk_translate.submit_bulk_translation(did, lines, engine, "claude", {"drama_meta": {}},
                                      translate_args={"style_preset": "audio_drama"},
                                      batch_size=batch_size, **kw)


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

class TestSubmit:
    def test_every_batch_goes_in_one_submission_with_valid_custom_ids(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db)
        bulk_id = _submit(isolated_db, did, engine)
        created = engine.client.messages.batches.created
        assert len(created) == 3  # 7 lines in batches of 3
        for req in created:
            assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", req["custom_id"])
        assert [r["custom_id"] for r in created] == [f"d{did}_b0", f"d{did}_b1", f"d{did}_b2"]
        job = isolated_db.get_bulk_job(bulk_id)
        assert job["status"] == "submitted" and job["provider_batch_id"] == "msgbatch_01"

    def test_lines_are_numbered_by_permanent_id_not_position(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db)
        _submit(isolated_db, did, engine)
        rows = isolated_db.load_lines(did)
        first = _numbered_lines(engine.client.messages.batches.created[0]["params"]["messages"][0]["content"])
        assert list(first) == [r["id"] for r in rows[:3]]

    def test_saves_each_lines_id_hash_and_english_at_submission(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db)
        bulk_id = _submit(isolated_db, did, engine)
        saved = isolated_db.list_bulk_job_lines(bulk_id)
        rows = {r["id"]: r for r in isolated_db.load_lines(did)}
        assert len(saved) == 7
        for r in saved:
            assert r["zh_hash"] == bulk_translate.zh_hash(rows[r["line_id"]]["zh"])
            assert r["en_at_submit"] == ""

    def test_the_stable_system_prompt_is_identical_in_every_request(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db)
        _submit(isolated_db, did, engine)
        systems = {repr(r["params"]["system"]) for r in engine.client.messages.batches.created}
        assert len(systems) == 1

    def test_only_untranslated_lines_are_submitted(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=4)
        lines = isolated_db.load_line_objects(did)
        lines[1].en = "Already done."
        isolated_db.save_lines(did, lines, fields=("en",))
        bulk_id = _submit(isolated_db, did, engine, batch_size=10)
        assert len(isolated_db.list_bulk_job_lines(bulk_id)) == 3

    def test_a_submission_failure_is_recorded_not_lost(self, isolated_db):
        engine = _claude_engine()

        def boom(requests):
            raise RuntimeError("upstream exploded")
        engine.client.messages.batches.create = boom
        did = _drama(isolated_db)
        with pytest.raises(RuntimeError):
            _submit(isolated_db, did, engine)
        [job] = isolated_db.list_bulk_jobs(did)
        assert job["status"] == "failed" and "upstream exploded" in job["last_error"]

    def test_a_key_in_the_provider_error_is_not_stored_or_shown(self, isolated_db):
        from services.bulk_job_view import bulk_entry
        secret = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789SECRET"
        engine = _claude_engine()

        def boom(requests):
            raise RuntimeError(f"401 invalid x-api-key {secret}")
        engine.client.messages.batches.create = boom
        did = _drama(isolated_db)
        with pytest.raises(RuntimeError):
            _submit(isolated_db, did, engine)
        [job] = isolated_db.list_bulk_jobs(did)
        assert job["status"] == "failed" and job["last_error"]
        assert "SECRET" not in job["last_error"] and secret not in job["last_error"]
        # The stored row is checked separately: the view redacts again, which
        # would hide a missing redaction at write time.
        assert "SECRET" not in json.dumps(bulk_entry(job))


# ---------------------------------------------------------------------------
# Applying results (the part that must never misassign)
# ---------------------------------------------------------------------------

class TestApplyResults:
    def test_out_of_order_results_still_land_on_the_right_lines(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db)
        bulk_id = _submit(isolated_db, did, engine)
        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"

        assert bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine)) == "applied"
        for r in isolated_db.load_lines(did):
            assert r["en"] == f"EN[{r['zh']}]"
        summary = isolated_db.get_bulk_job(bulk_id)["result_summary"]
        assert summary["applied"] == 7

    def test_a_line_merged_away_meanwhile_has_its_result_dropped(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db)
        bulk_id = _submit(isolated_db, did, engine)
        # Merge line 2 into line 1 the way the app does: the survivor's
        # source text changes, the merged-away line is deleted.
        lines = isolated_db.load_line_objects(did)
        lines[1].zh = lines[1].zh + lines[2].zh
        del lines[2]
        isolated_db.save_lines(did, lines)

        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))

        rows = isolated_db.load_lines(did)
        assert len(rows) == 6
        survivor = rows[1]
        assert survivor["en"] == ""  # its source changed -- never gets line 1's old result
        assert survivor["flag"] == "bulk_source_changed"
        for r in rows[:1] + rows[2:]:
            assert r["en"] == f"EN[{r['zh']}]"
        summary = isolated_db.get_bulk_job(bulk_id)["result_summary"]
        assert summary["dropped_deleted"] == 1
        assert summary["flagged_source_changed"] == 1
        assert summary["applied"] == 5
        # Step 25d item 13: the merge survivor above never got a
        # translation applied (its source changed) -- the drama must not
        # be marked "translated" while a line is still untranslated, same
        # root cause and fix as Step 25c's CLI/Workspace guard.
        assert isolated_db.get_drama(did)["status"] != "translated"

    def test_a_line_whose_source_was_edited_is_flagged_not_applied(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        bulk_id = _submit(isolated_db, did, engine)
        lines = isolated_db.load_line_objects(did)
        lines[0].zh = "改过的句子"
        isolated_db.save_lines(did, lines)

        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))

        rows = isolated_db.load_lines(did)
        assert rows[0]["en"] == "" and rows[0]["flag"] == "bulk_source_changed"
        assert rows[1]["en"] == "EN[第1句]"

    def test_an_english_edit_made_meanwhile_is_kept(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        bulk_id = _submit(isolated_db, did, engine)
        lines = isolated_db.load_line_objects(did)
        lines[2].en = "My own wording."
        isolated_db.save_lines(did, lines, fields=("en",))

        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))

        rows = isolated_db.load_lines(did)
        assert rows[2]["en"] == "My own wording."
        assert isolated_db.get_bulk_job(bulk_id)["result_summary"]["kept_your_edit"] == 1

    def test_a_response_cannot_assign_to_lines_outside_its_own_request(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=6)
        bulk_id = _submit(isolated_db, did, engine)
        ids = [r["id"] for r in isolated_db.load_lines(did)]
        batches = engine.client.messages.batches
        # Request b0 covers ids[0:3]; its response also "answers" ids[3]
        # (which belongs to b1). That stray key must be ignored.
        batches.results_list = [
            _succeeded(f"d{did}_b0", {str(ids[0]): "A", str(ids[1]): "B", str(ids[2]): "C",
                                      str(ids[3]): "WRONG"}),
            _succeeded(f"d{did}_b1", {str(ids[4]): "E", str(ids[5]): "F"}),
        ]
        batches.status = "ended"
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))
        assert [r["en"] for r in isolated_db.load_lines(did)] == ["A", "B", "C", "", "E", "F"]
        assert isolated_db.get_bulk_job(bulk_id)["result_summary"]["missing"] == 1

    def test_a_positional_array_response_is_not_accepted_for_bulk(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        bulk_id = _submit(isolated_db, did, engine, batch_size=5)
        batches = engine.client.messages.batches
        msg = NS(content=[NS(type="text", text='["A", "B"]')],
                 usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                          cache_creation_input_tokens=0))
        batches.results_list = [NS(custom_id=f"d{did}_b0", result=NS(type="succeeded", message=msg))]
        batches.status = "ended"
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))
        assert [r["en"] for r in isolated_db.load_lines(did)] == ["", ""]

    def test_errored_and_expired_requests_leave_lines_untranslated(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=6)
        bulk_id = _submit(isolated_db, did, engine)
        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.results_list = [
            r if r.custom_id != f"d{did}_b0" else NS(custom_id=r.custom_id, result=NS(type="expired"))
            for r in batches.results_list]
        batches.status = "ended"
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))
        rows = isolated_db.load_lines(did)
        assert [r["en"] for r in rows[:3]] == ["", "", ""]
        assert all(r["en"] for r in rows[3:])
        assert isolated_db.get_bulk_job(bulk_id)["result_summary"]["failed_requests"] == 1

    def test_usage_is_logged_at_the_batch_discount(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        bulk_id = _submit(isolated_db, did, engine)
        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))
        full = te.estimate_cost("claude-sonnet-5", 1000, 100)
        assert isolated_db.get_usage_summary(did)["estimated_cost_usd"] == pytest.approx(full * 0.5)

    def test_enforced_glossary_terms_apply_to_bulk_results(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=1)
        lines = isolated_db.load_line_objects(did)
        bulk_id = bulk_translate.submit_bulk_translation(
            did, lines, engine, "claude", {"drama_meta": {}}, batch_size=5,
            translate_args={"glossary_terms": [{"term_original": "苏杉", "term_translation": "Su Shan",
                                                "notes": "Su Xian", "enforce_exact": True}]})
        batches = engine.client.messages.batches
        _answer_every_request(batches, fn=lambda zh: "Su Xian smiled.")
        batches.status = "ended"
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))
        assert isolated_db.load_lines(did)[0]["en"] == "Su Shan smiled."

    def test_still_pending_changes_nothing(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        bulk_id = _submit(isolated_db, did, engine)
        assert bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine)) == "submitted"
        assert all(r["en"] == "" for r in isolated_db.load_lines(did))


# ---------------------------------------------------------------------------
# Restart, cancel, auth errors
# ---------------------------------------------------------------------------

def _wait_for(job_id, timeout=5.0):
    deadline = time.time() + timeout
    while background_jobs.is_running(job_id) and time.time() < deadline:
        time.sleep(0.02)


class TestRestartCancelAuth:
    def test_a_restarted_app_picks_up_a_pending_batch_by_its_saved_id(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db)
        bulk_id = _submit(isolated_db, did, engine)
        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"

        # "Restart": the in-memory job tracker is wiped, and the engine is
        # rebuilt from scratch -- only the database knows about the batch.
        background_jobs.clear_all_jobs()
        fresh = _claude_engine()
        fresh.client.messages.batches.results_list = batches.results_list
        fresh.client.messages.batches.status = "ended"
        out = bulk_poll.resume_pending(did, lambda engine_choice, model: fresh)
        assert out == {bulk_id: "polling"}
        _wait_for(bulk_poll.poll_job_id(bulk_id))

        assert fresh.client.messages.batches.retrieved == ["msgbatch_01"]
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "applied"
        assert all(r["en"] == f"EN[{r['zh']}]" for r in isolated_db.load_lines(did))
        background_jobs.clear_job(bulk_poll.poll_job_id(bulk_id))

    def test_resume_rebuilds_the_engine_with_the_model_id_stored_on_the_job(self, isolated_db):
        did = _drama(isolated_db, n=2)
        bulk_id = isolated_db.create_bulk_job(did, "claude", "claude-opus-4-8", "submitted", [])
        background_jobs.clear_all_jobs()
        seen = []
        bulk_poll.resume_pending(did, lambda e, m: seen.append((e, m)))
        assert seen == [("claude", "claude-opus-4-8")]
        assert bulk_id

    def test_resume_without_a_key_reports_it_instead_of_polling(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        bulk_id = _submit(isolated_db, did, engine)
        background_jobs.clear_all_jobs()
        assert bulk_poll.resume_pending(did, lambda e, m: None) == {bulk_id: "needs_key"}
        assert not background_jobs.is_running(bulk_poll.poll_job_id(bulk_id))

    def test_cancel_stops_polling_and_later_results_are_ignored(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        bulk_id = _submit(isolated_db, did, engine)
        provider = bp.make_provider("claude", engine)
        bulk_poll.start_poller(bulk_id, provider=provider)
        time.sleep(0.05)
        note = bulk_poll.cancel_bulk_job(bulk_id, provider)
        _wait_for(bulk_poll.poll_job_id(bulk_id))

        assert "Cancelled at the provider" in note
        assert engine.client.messages.batches.cancelled == ["msgbatch_01"]
        assert not background_jobs.is_running(bulk_poll.poll_job_id(bulk_id))
        # The batch finishes anyway -- nothing is applied.
        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"
        assert bulk_poll.check_once(bulk_id, provider) == "cancelled"
        assert all(r["en"] == "" for r in isolated_db.load_lines(did))
        background_jobs.clear_job(bulk_poll.poll_job_id(bulk_id))

    def test_a_polling_auth_error_is_recorded_on_the_job(self, isolated_db):
        import anthropic
        import httpx2
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        bulk_id = _submit(isolated_db, did, engine)
        req = httpx2.Request("GET", "https://api.anthropic.com/v1/messages/batches/msgbatch_01")
        engine.client.messages.batches.raise_on_retrieve = anthropic.AuthenticationError(
            "invalid x-api-key sk-ant-api03-SECRETSECRETSECRET", response=httpx2.Response(401, request=req),
            body=None)
        with pytest.raises(bp.BulkAuthError):
            bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))
        job = isolated_db.get_bulk_job(bulk_id)
        assert job["status"] == "auth_error"
        assert "SECRETSECRET" not in job["last_error"]

    def test_the_poller_stops_on_an_auth_error_instead_of_retrying_forever(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        bulk_id = _submit(isolated_db, did, engine)

        class RefusingProvider:
            calls = 0

            def poll(self, batch_id):
                RefusingProvider.calls += 1
                raise bp.BulkAuthError("Gemini refused the API key (HTTP 403).")
        bulk_poll.run_bulk_poller("t_auth", bulk_id, provider=RefusingProvider(), sleep=lambda s: None)
        assert RefusingProvider.calls == 1
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "auth_error"

    def test_check_now_after_fixing_the_key_clears_the_auth_error(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        bulk_id = _submit(isolated_db, did, engine)
        isolated_db.update_bulk_job(bulk_id, status="auth_error", last_error="refused")
        assert bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine)) == "submitted"
        job = isolated_db.get_bulk_job(bulk_id)
        assert job["status"] == "submitted" and job["last_error"] is None


# ---------------------------------------------------------------------------
# Gemini provider
# ---------------------------------------------------------------------------

class _Resp(StreamedBody):
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


def _patch_http(monkeypatch, post=None, get=None):
    """Route lib.http's one connection call to per-verb fakes taking the old
    requests-style (url, headers=, json=, timeout=, stream=) arguments. A
    second call in a test adds its verb to the first one's."""
    current = http.pinned_get
    calls = getattr(current, "calls", {})
    calls.update({k: v for k, v in (("POST", post), ("GET", get)) if v})

    def fake(url, ip, headers, timeout=None, method="GET", **kw):
        extra = {"json": kw["json"]} if method == "POST" else {}
        return calls[method](url, headers=headers, timeout=timeout, stream=True, **extra)
    fake.calls = calls
    monkeypatch.setattr(http, "pinned_get", fake)


class TestGeminiProvider:
    def _engine(self):
        return te.GeminiEngine("gm-key", model="gemini-flash-lite-latest")

    def test_submission_shape_and_key_in_header(self, isolated_db, monkeypatch):
        sent = {}

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            sent.update(url=url, headers=headers, json=json, timeout=timeout)
            return _Resp({"name": "batches/abc123"})
        _patch_http(monkeypatch, post=fake_post)
        did = _drama(isolated_db, n=4)
        lines = isolated_db.load_line_objects(did)
        bulk_id = bulk_translate.submit_bulk_translation(did, lines, self._engine(), "gemini", {"drama_meta": {}},
                                             batch_size=2)
        assert sent["url"].endswith("/models/gemini-flash-lite-latest:batchGenerateContent")
        assert sent["headers"] == {"x-goog-api-key": "gm-key"} and "key=" not in sent["url"]
        reqs = sent["json"]["batch"]["input_config"]["requests"]["requests"]
        assert [r["metadata"]["key"] for r in reqs] == [f"d{did}_b0", f"d{did}_b1"]
        assert "systemInstruction" in reqs[0]["request"]
        assert isolated_db.get_bulk_job(bulk_id)["provider_batch_id"] == "batches/abc123"

    def test_results_are_matched_by_metadata_key_not_position(self, isolated_db, monkeypatch):
        submitted = {}
        _patch_http(monkeypatch, post=lambda url, headers=None, json=None, timeout=None, stream=None:
                            submitted.update(json=json) or _Resp({"name": "batches/abc"}))
        did = _drama(isolated_db, n=4)
        lines = isolated_db.load_line_objects(did)
        bulk_id = bulk_translate.submit_bulk_translation(did, lines, self._engine(), "gemini", {"drama_meta": {}},
                                             batch_size=2)
        responses = []
        for r in submitted["json"]["batch"]["input_config"]["requests"]["requests"]:
            got = _numbered_lines(r["request"]["contents"][0]["parts"][0]["text"])
            responses.append({"metadata": r["metadata"], "response": {
                "candidates": [{"content": {"parts": [{"text": json.dumps(
                    {str(i): f"G[{zh}]" for i, zh in got.items()}, ensure_ascii=False)}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}})
        done = {"name": "batches/abc", "done": True,
                "metadata": {"state": "BATCH_STATE_SUCCEEDED"},
                "response": {"inlinedResponses": {"inlinedResponses": list(reversed(responses))}}}
        _patch_http(monkeypatch, get=lambda url, headers=None, timeout=None, stream=None: _Resp(done))
        assert bulk_poll.check_once(bulk_id, bp.make_provider("gemini", self._engine())) == "applied"
        assert all(r["en"] == f"G[{r['zh']}]" for r in isolated_db.load_lines(did))

    def test_a_response_without_its_key_is_never_attributed(self, isolated_db, monkeypatch):
        _patch_http(monkeypatch, post=lambda *a, **k: _Resp({"name": "batches/abc"}))
        did = _drama(isolated_db, n=1)
        lines = isolated_db.load_line_objects(did)
        bulk_id = bulk_translate.submit_bulk_translation(did, lines, self._engine(), "gemini", {"drama_meta": {}}, batch_size=20)
        lid = lines[0].id
        done = {"done": True, "metadata": {"state": "JOB_STATE_SUCCEEDED"}, "response": {
            "inlinedResponses": [{"response": {"candidates": [{"content": {"parts": [
                {"text": json.dumps({str(lid): "orphan"})}]}}]}}]}}
        _patch_http(monkeypatch, get=lambda *a, **k: _Resp(done))
        bulk_poll.check_once(bulk_id, bp.make_provider("gemini", self._engine()))
        assert isolated_db.load_lines(did)[0]["en"] == ""

    def test_a_401_while_polling_becomes_an_auth_error_on_the_job(self, isolated_db, monkeypatch):
        _patch_http(monkeypatch, post=lambda *a, **k: _Resp({"name": "batches/abc"}))
        did = _drama(isolated_db, n=1)
        bulk_id = bulk_translate.submit_bulk_translation(did, isolated_db.load_line_objects(did), self._engine(),
                                             "gemini", {"drama_meta": {}}, batch_size=20)
        _patch_http(monkeypatch, get=lambda *a, **k: _Resp({}, status=401))
        with pytest.raises(bp.BulkAuthError):
            bulk_poll.check_once(bulk_id, bp.make_provider("gemini", self._engine()))
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "auth_error"

    def test_a_failed_batch_is_marked_failed(self, isolated_db, monkeypatch):
        _patch_http(monkeypatch, post=lambda *a, **k: _Resp({"name": "batches/abc"}))
        did = _drama(isolated_db, n=1)
        bulk_id = bulk_translate.submit_bulk_translation(did, isolated_db.load_line_objects(did), self._engine(),
                                             "gemini", {"drama_meta": {}}, batch_size=20)
        _patch_http(monkeypatch, get=lambda *a, **k: _Resp(
            {"done": True, "metadata": {"state": "BATCH_STATE_EXPIRED"}}))
        assert bulk_poll.check_once(bulk_id, bp.make_provider("gemini", self._engine())) == "failed"
        assert "EXPIRED" in isolated_db.get_bulk_job(bulk_id)["last_error"]

    def test_cancel_posts_to_the_cancel_endpoint(self, isolated_db, monkeypatch):
        calls = []
        _patch_http(monkeypatch, post=lambda url, headers=None, json=None, timeout=None, stream=None:
                            calls.append(url) or _Resp({"name": "batches/abc"}))
        did = _drama(isolated_db, n=1)
        bulk_id = bulk_translate.submit_bulk_translation(did, isolated_db.load_line_objects(did), self._engine(),
                                             "gemini", {"drama_meta": {}}, batch_size=20)
        bulk_poll.cancel_bulk_job(bulk_id, bp.make_provider("gemini", self._engine()))
        assert calls[-1].endswith("/batches/abc:cancel")
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "cancelled"


# ---------------------------------------------------------------------------
# DeepSeek off-peak scheduling
# ---------------------------------------------------------------------------

class TestDeepSeekOffPeak:
    def test_weekday_peak_waits_for_the_window_to_end(self):
        wed = datetime.datetime(2026, 9, 23, 2, 30)  # Wednesday 02:30 UTC -- peak
        assert not bp.is_deepseek_offpeak(wed)
        assert bp.next_deepseek_offpeak_start(wed) == datetime.datetime(2026, 9, 23, 4, 0)
        morning = datetime.datetime(2026, 9, 23, 9, 59)
        assert bp.next_deepseek_offpeak_start(morning) == datetime.datetime(2026, 9, 23, 10, 0)

    def test_offpeak_now_starts_now(self):
        gap = datetime.datetime(2026, 9, 23, 5, 0)  # between the two peak windows
        assert bp.next_deepseek_offpeak_start(gap) == gap
        sat = datetime.datetime(2026, 9, 26, 7, 0)  # Saturday, inside weekday peak hours
        assert bp.is_deepseek_offpeak(sat)

    def _engine(self):
        class FakeDeepSeek:
            name = "deepseek"
            supports_reference = True
            checks_replies = False  # echoes the source script back
            model = "deepseek-v4-flash"

            def __init__(self):
                self.last_usage = {}
                self.seen = []

            def translate_batch(self, zh_lines, context):
                self.seen.extend(zh_lines)
                self.last_usage = {"input_tokens": 10, "output_tokens": 5}
                return [f"DS[{z}]" for z in zh_lines]
        return FakeDeepSeek()

    def test_a_scheduled_job_waits_then_translates_only_what_it_was_scheduled_for(self, isolated_db):
        did = _drama(isolated_db, n=4)
        lines = isolated_db.load_line_objects(did)
        peak = datetime.datetime(2026, 9, 23, 2, 0)
        bulk_id = bulk_translate.schedule_offpeak_translation(did, lines[:3], "deepseek", "deepseek-v4-flash",
                                                  {"style_preset": "audio_drama"}, now=peak)
        assert isolated_db.get_bulk_job(bulk_id)["scheduled_for"] == "2026-09-23T04:00:00"
        # Meanwhile the user translates one of the scheduled lines by hand.
        lines = isolated_db.load_line_objects(did)
        lines[1].en = "Mine."
        isolated_db.save_lines(did, lines, fields=("en",))

        offpeak = datetime.datetime(2026, 9, 23, 4, 0, 5)
        # The window is re-read before every batch, so the clock keeps answering.
        clock = itertools.chain([peak], itertools.repeat(offpeak))
        engine = self._engine()
        bulk_poll.run_bulk_poller("t_ds", bulk_id, engine=engine, sleep=lambda s: None,
                           now_fn=lambda: next(clock), monthly_cap_usd=50.0)

        rows = isolated_db.load_lines(did)
        assert [r["en"] for r in rows] == ["DS[第0句]", "Mine.", "DS[第2句]", ""]
        assert engine.seen == ["第0句", "第2句"]
        job = isolated_db.get_bulk_job(bulk_id)
        assert job["status"] == "applied" and job["result_summary"]["skipped_changed"] == 1
        background_jobs.clear_job("t_ds")

    def test_an_orphaned_running_job_is_resumed_after_a_restart(self, isolated_db, monkeypatch):
        saturday_noon = datetime.datetime(2026, 9, 26, 12, 0)
        monkeypatch.setattr(bulk_poll, "_utcnow", lambda: saturday_noon)
        did = _drama(isolated_db, n=2)
        bulk_id = bulk_translate.schedule_offpeak_translation(
            did, isolated_db.load_line_objects(did), "deepseek", "m", {}, now=saturday_noon)
        isolated_db.update_bulk_job(bulk_id, status="running")
        background_jobs.clear_all_jobs()
        engine = self._engine()
        out = bulk_poll.resume_pending(did, lambda e, m: engine, monthly_cap_usd=50.0)
        assert out == {bulk_id: "polling"}
        _wait_for(bulk_poll.poll_job_id(bulk_id))
        assert all(r["en"].startswith("DS[") for r in isolated_db.load_lines(did))
        background_jobs.clear_job(bulk_poll.poll_job_id(bulk_id))


# ---------------------------------------------------------------------------
# Step 9d: flag/consistency/emotion/translation_notes routed through the
# same bulk-submission machinery Step 9 built for translation, plus
# Reflect's own three-sequential-stage pipeline.
# ---------------------------------------------------------------------------

class TestBulkFlag:
    def test_applies_by_line_id_with_drop_flag_and_kept_edit(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3, en="translated")
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]

        jid = bulk_translate.submit_bulk_flag(did, lines, engine, "claude", batch_size=10)
        batches = engine.client.messages.batches
        key = batches.created[0]["custom_id"]

        # The user manually flags line 0 while the batch is pending -- kept.
        mutated = isolated_db.load_line_objects(did)
        mutated[0].flag, mutated[0].flag_note = "slang_idiom", "manual"
        isolated_db.save_lines(did, mutated, fields=("flag", "flag_note"))
        # Line 1's source changes meanwhile -- flagged, not applied.
        mutated = isolated_db.load_line_objects(did)
        mutated[1].zh = "changed text"
        isolated_db.save_lines(did, mutated, fields=("zh",))

        payload = [{"line_idx": lid, "reason": "uncertain_translation", "note": "auto"} for lid in ids]
        batches.results_list = [_succeeded(key, payload)]
        batches.status = "ended"
        status = bulk_poll.check_once(jid, bp.ClaudeBatchProvider(engine))

        assert status == "applied"
        summary = isolated_db.get_bulk_job(jid)["result_summary"]
        assert summary == {"applied": 1, "dropped_deleted": 0, "flagged_source_changed": 1,
                           "kept_your_edit": 1, "failed_requests": 0, "unknown_requests": 0,
                           "unparsable_entries": 0}
        final = {ln.id: ln for ln in isolated_db.load_line_objects(did)}
        assert final[ids[0]].flag == "slang_idiom" and final[ids[0]].flag_note == "manual"
        assert final[ids[1]].flag == "bulk_source_changed"
        assert final[ids[2]].flag == "uncertain_translation" and final[ids[2]].flag_note == "auto"

    def test_bracketed_ids_apply_and_unreadable_ones_are_counted(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="translated")
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]
        jid = bulk_translate.submit_bulk_flag(did, lines, engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]
        payload = [{"line_idx": f"[{ids[0]}]", "reason": "slang_idiom", "note": "n"},
                   {"line_idx": "first", "reason": "slang_idiom"}]
        summary = bulk_apply.apply_flag_results(jid, [(key, json.dumps(payload), {}, None)])
        assert summary["applied"] == 1 and summary["unparsable_entries"] == 1
        final = {ln.id: ln for ln in isolated_db.load_line_objects(did)}
        assert final[ids[0]].flag == "slang_idiom" and final[ids[1]].flag is None

    def test_system_flags_are_not_sent_and_not_overwritten(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="translated")
        lines = isolated_db.load_line_objects(did)
        lines[0].flag, lines[0].flag_note = "timing_uncertain", "approx"
        isolated_db.save_lines(did, lines, fields=("flag", "flag_note"))
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]
        jid = bulk_translate.submit_bulk_flag(did, lines, engine, "claude")
        request = str(engine.client.messages.batches.created[0])
        assert f"[{ids[0]}]" not in request and f"[{ids[1]}]" in request
        key = engine.client.messages.batches.created[0]["custom_id"]
        payload = [{"line_idx": i, "reason": "slang_idiom", "note": "model"} for i in ids]
        bulk_apply.apply_flag_results(jid, [(key, json.dumps(payload), {}, None)])
        final = {ln.id: ln for ln in isolated_db.load_line_objects(did)}
        assert (final[ids[0]].flag, final[ids[0]].flag_note) == ("timing_uncertain", "approx")
        assert final[ids[1]].flag == "slang_idiom"

    def test_a_line_deleted_meanwhile_is_dropped(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="translated")
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]
        jid = bulk_translate.submit_bulk_flag(did, lines, engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]

        remaining = [ln for ln in isolated_db.load_line_objects(did) if ln.id != ids[0]]
        isolated_db.save_lines(did, remaining)

        payload = [{"line_idx": lid, "reason": "uncertain_translation", "note": ""} for lid in ids]
        results = [(key, json.dumps(payload), {}, None)]
        summary = bulk_apply.apply_flag_results(jid, results)
        assert summary["dropped_deleted"] == 1 and summary["applied"] == 1

    def test_engines_without_a_batch_api_are_rejected(self, isolated_db):
        did = _drama(isolated_db, n=1, en="x")
        with pytest.raises(ValueError, match="batch API"):
            bulk_translate.submit_bulk_flag(did, isolated_db.load_line_objects(did), NS(model="m"), "deepseek")


class TestBulkConsistency:
    def test_a_window_with_a_stale_line_is_dropped_a_clean_one_is_kept(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=6, en="translated")
        isolated_db.save_consistency_issues(did, [{"term": "old", "variants": ["a"], "note": "stale"}])
        lines = isolated_db.load_line_objects(did)
        jid = bulk_translate.submit_bulk_consistency(did, lines, engine, "claude", batch_size=3)
        keys = [r["custom_id"] for r in engine.client.messages.batches.created]
        assert len(keys) == 2

        job_lines = isolated_db.list_bulk_job_lines(jid)
        window0_ids = [r["line_id"] for r in job_lines if r["request_key"] == keys[0]]
        mutated = isolated_db.load_line_objects(did)
        by_id = {ln.id: ln for ln in mutated}
        by_id[window0_ids[0]].zh = "edited"
        isolated_db.save_lines(did, mutated, fields=("zh",))

        results = [(keys[0], json.dumps([{"term": "dropped", "variants": ["x"], "note": "n"}]), {}, None),
                  (keys[1], json.dumps([{"term": "kept", "variants": ["y"], "note": "n"}]), {}, None)]
        summary = bulk_apply.apply_consistency_results(jid, results)
        assert summary == {"issues_found": 1, "dropped_windows": 1, "failed_requests": 0,
                           "unknown_requests": 0}
        assert [i["term"] for i in isolated_db.load_consistency_issues(did)] == ["kept"]

    def test_no_usable_windows_leaves_existing_issues_untouched(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="translated")
        isolated_db.save_consistency_issues(did, [{"term": "keep me", "variants": [], "note": ""}])
        lines = isolated_db.load_line_objects(did)
        jid = bulk_translate.submit_bulk_consistency(did, lines, engine, "claude", batch_size=10)
        key = engine.client.messages.batches.created[0]["custom_id"]
        isolated_db.save_lines(did, [])  # every line gone
        summary = bulk_apply.apply_consistency_results(jid, [(key, json.dumps([]), {}, None)])
        assert summary["dropped_windows"] == 1
        assert [i["term"] for i in isolated_db.load_consistency_issues(did)] == ["keep me"]


class TestBulkEmotion:
    def test_applies_by_line_id_with_drop_flag_and_kept_edit(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]
        jid = bulk_translate.submit_bulk_emotion(did, lines, engine, "claude", batch_size=10)
        key = engine.client.messages.batches.created[0]["custom_id"]

        # The user (or a live run) already tagged line 0 differently -- kept.
        isolated_db.save_emotions(did, {lines[0].idx: {"emotion": "sad", "intensity": 0.9,
                                                       "note": "manual"}},
                                  id_by_idx={lines[0].idx: ids[0]})
        # Line 1's source changes meanwhile.
        mutated = isolated_db.load_line_objects(did)
        mutated[1].zh = "changed"
        isolated_db.save_lines(did, mutated, fields=("zh",))

        payload = [{"line_idx": lid, "emotion": "warm", "intensity": 0.6, "note": "n"} for lid in ids]
        summary = bulk_apply.apply_emotion_results(jid, [(key, json.dumps(payload), {}, None)])

        assert summary == {"applied": 1, "dropped_deleted": 0, "flagged_source_changed": 1,
                           "kept_your_edit": 1, "failed_requests": 0, "unknown_requests": 0}
        tags = isolated_db.load_emotions(did)
        final = {ln.id: ln for ln in isolated_db.load_line_objects(did)}
        assert tags[final[ids[0]].idx]["emotion"] == "sad"  # kept
        assert final[ids[1]].flag == "bulk_source_changed"
        assert tags[final[ids[2]].idx]["emotion"] == "warm"

    def test_a_line_deleted_meanwhile_is_dropped(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]
        jid = bulk_translate.submit_bulk_emotion(did, lines, engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]
        isolated_db.save_lines(did, [ln for ln in isolated_db.load_line_objects(did) if ln.id != ids[0]])
        payload = [{"line_idx": lid, "emotion": "warm", "intensity": 0.5, "note": ""} for lid in ids]
        summary = bulk_apply.apply_emotion_results(jid, [(key, json.dumps(payload), {}, None)])
        assert summary["dropped_deleted"] == 1 and summary["applied"] == 1


class TestBulkTranslationNotes:
    def test_applies_by_line_id_and_drops_a_stale_or_deleted_line(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3, en="translated")
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]
        jid = bulk_translate.submit_bulk_translation_notes(did, lines, engine, "claude", batch_size=10)
        key = engine.client.messages.batches.created[0]["custom_id"]

        mutated = isolated_db.load_line_objects(did)
        mutated[1].zh = "changed"
        isolated_db.save_lines(did, mutated, fields=("zh",))
        remaining = [ln for ln in isolated_db.load_line_objects(did) if ln.id != ids[2]]
        isolated_db.save_lines(did, remaining)

        payload = [{"line_idx": lid, "term": f"term{lid}", "note_type": "idiom", "note": f"note{lid}"}
                  for lid in ids]
        summary = bulk_apply.apply_notes_results(jid, [(key, json.dumps(payload), {}, None)])

        assert summary == {"applied": 1, "dropped_deleted": 1, "flagged_source_changed": 1,
                           "failed_requests": 0, "unknown_requests": 0}
        notes = isolated_db.list_translation_notes(did)
        assert len(notes) == 1 and notes[0]["line_id"] == ids[0]

    def test_notes_are_additive_not_overwritten(self, isolated_db):
        """Unlike flag/emotion, notes have no "kept your edit" check --
        applying a bulk result never removes a note a live run already
        saved for a different term on the same line."""
        engine = _claude_engine()
        did = _drama(isolated_db, n=1, en="translated")
        lines = isolated_db.load_line_objects(did)
        lid = lines[0].id
        isolated_db.save_translation_notes(did, [{"line_id": lid, "term": "existing", "note": "old"}])
        jid = bulk_translate.submit_bulk_translation_notes(did, lines, engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]
        payload = [{"line_idx": lid, "term": "new term", "note_type": "idiom", "note": "new"}]
        bulk_apply.apply_notes_results(jid, [(key, json.dumps(payload), {}, None)])
        terms = {n["term"] for n in isolated_db.list_translation_notes(did)}
        assert terms == {"existing", "new term"}


class TestBulkReflectPipeline:
    """Exit condition: a mocked test shows a bulk Reflect job submits its
    three passes as three sequential batches, each keyed off the previous
    pass's actual saved results, and surfaces its multi-stage pending
    status (not just "pending") on the Bulk jobs panel."""

    def _drive_stage(self, isolated_db, engine, job_id, payload_by_id_fn):
        batches = engine.client.messages.batches
        req = batches.created[-1]
        key = req["custom_id"]
        job_lines = isolated_db.list_bulk_job_lines(job_id)
        ids = [r["line_id"] for r in job_lines if r["request_key"] == key]
        payload = {str(lid): payload_by_id_fn(lid) for lid in ids}
        batches.results_list = [_succeeded(key, payload)]
        batches.status = "ended"
        return bulk_poll.check_once(job_id, bp.ClaudeBatchProvider(engine), engine=engine)

    def test_three_sequential_batches_each_keyed_off_the_previous_stage(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]

        jid1 = bulk_reflect.submit_reflect_pipeline(did, lines, engine, "claude", {"locale": "en-US"})
        job1 = isolated_db.get_bulk_job(jid1)
        assert (job1["kind"], job1["stage"]) == ("reflect", "faithful")
        assert len(isolated_db.list_bulk_jobs(did)) == 1

        status1 = self._drive_stage(isolated_db, engine, jid1, lambda lid: f"draft-{lid}")
        assert status1 == "applied"

        stage2 = bulk_reflect.sibling_stage_job(job1["pipeline_id"], "reflect")
        assert stage2 is not None and stage2["status"] == "submitted"
        assert len(isolated_db.list_bulk_jobs(did)) == 2
        # Stage 2's own prompt embeds stage 1's actual saved draft, not a
        # fresh translation -- confirming it's genuinely keyed off the
        # previous stage's saved result, not independently regenerated.
        stage2_prompt = engine.client.messages.batches.created[0]["params"]["messages"][0]["content"]
        assert f"draft-{ids[0]}" in stage2_prompt

        # Only critique line 0 -- line 1 needs none, a normal outcome.
        status2 = self._drive_stage(
            isolated_db, engine, stage2["id"],
            lambda lid: "needs polish" if lid == ids[0] else None)
        assert status2 == "applied"

        stage3 = bulk_reflect.sibling_stage_job(job1["pipeline_id"], "expressive")
        assert stage3 is not None and stage3["status"] == "submitted"
        assert len(isolated_db.list_bulk_jobs(did)) == 3
        stage3_prompt = engine.client.messages.batches.created[0]["params"]["messages"][0]["content"]
        assert f"draft-{ids[0]}" in stage3_prompt and "needs polish" in stage3_prompt
        assert f"draft-{ids[1]}" in stage3_prompt and "Critique" not in stage3_prompt.split(
            f"draft-{ids[1]}")[1].split("\n\n")[0]

        status3 = self._drive_stage(isolated_db, engine, stage3["id"], lambda lid: f"FINAL-{lid}")
        assert status3 == "applied"

        final = {ln.id: ln.en for ln in isolated_db.load_line_objects(did)}
        assert final == {ids[0]: f"FINAL-{ids[0]}", ids[1]: f"FINAL-{ids[1]}"}
        notes = isolated_db.list_translation_notes(did)
        assert len(notes) == 1 and notes[0]["line_id"] == ids[0] and notes[0]["note"] == "needs polish"
        assert isolated_db.get_drama(did)["status"] == "translated"

    def test_a_kept_edit_survives_through_every_stage(self, isolated_db):
        """The "kept your edit" comparison at the final stage is against
        English as it stood at the ORIGINAL faithfulness submission, not
        re-snapshotted at each later stage."""
        engine = _claude_engine()
        did = _drama(isolated_db, n=1)
        lines = isolated_db.load_line_objects(did)
        jid1 = bulk_reflect.submit_reflect_pipeline(did, lines, engine, "claude", {"locale": "en-US"})
        self._drive_stage(isolated_db, engine, jid1, lambda i: "draft")
        stage2 = bulk_reflect.sibling_stage_job(isolated_db.get_bulk_job(jid1)["pipeline_id"], "reflect")
        self._drive_stage(isolated_db, engine, stage2["id"], lambda i: None)

        mutated = isolated_db.load_line_objects(did)
        mutated[0].en = "my own edit"
        isolated_db.save_lines(did, mutated, fields=("en",))

        stage3 = bulk_reflect.sibling_stage_job(isolated_db.get_bulk_job(jid1)["pipeline_id"], "expressive")
        self._drive_stage(isolated_db, engine, stage3["id"], lambda i: "FINAL")
        assert isolated_db.get_bulk_job(stage3["id"])["result_summary"]["kept_your_edit"] == 1
        assert isolated_db.load_line_objects(did)[0].en == "my own edit"

    def test_a_failed_faithful_job_still_feeds_its_drafts_to_the_expressive_stage(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=1)
        jid1 = bulk_reflect.submit_reflect_pipeline(did, isolated_db.load_line_objects(did), engine, "claude",
                                          {"locale": "en-US"})
        lid = isolated_db.list_bulk_job_lines(jid1)[0]["line_id"]
        self._drive_stage(isolated_db, engine, jid1, lambda i: "the-draft")
        isolated_db.update_bulk_job(jid1, status="failed")
        stage2 = bulk_reflect.sibling_stage_job(isolated_db.get_bulk_job(jid1)["pipeline_id"], "reflect")

        assert self._drive_stage(isolated_db, engine, stage2["id"], lambda i: "polish") == "applied"
        stage3_prompt = engine.client.messages.batches.created[0]["params"]["messages"][0]["content"]
        assert "the-draft" in stage3_prompt and lid in [
            r["line_id"] for r in isolated_db.list_bulk_job_lines(stage2["id"])]

    def test_engines_without_a_batch_api_are_rejected(self, isolated_db):
        did = _drama(isolated_db, n=1)
        with pytest.raises(ValueError, match="batch API"):
            bulk_reflect.submit_reflect_pipeline(did, isolated_db.load_line_objects(did), NS(model="m"),
                                       "deepseek", {})


class TestFinishTranslationRunGlossaryEnforcement:
    """Step 25d item 5: the enforce_exact glossary substitution pass used
    to substitute into (and save) the job's own possibly-stale in-memory
    line copies -- if a user edited a line's English while the job was
    still running, this pass's unconditional write won even though its
    own baseline text was stale, silently clobbering the user's edit."""

    _term = {"term_translation": "Xiao Ming", "notes": "Xiaoming", "enforce_exact": True}

    def test_an_edit_saved_during_the_run_wins_over_the_stale_job_copy(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test", status="aligned",
                                       translation_engine="claude")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0, end=1, zh="一", en="Xiaoming went home"),
        ])
        # The job's own snapshot, taken when the run started.
        job_lines = isolated_db.load_line_objects(did)
        # The user edits the line's English while the job is still
        # running, AFTER the job took its snapshot above -- a real write
        # straight to the database, same as Review & edit's own Save.
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="一",
                                          en="Xiaoming actually left already",
                                          id=job_lines[0].id)], fields=("en",))

        translation_finish.finish_translation_run(did, job_lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=[self._term], errors=[],
                                  enforce_ids={ln.id for ln in job_lines})

        saved = isolated_db.load_lines(did)[0]["en"]
        # The user's edit survives as typed: the line is the user's now, so
        # the run's term substitution leaves it alone too.
        assert saved == "Xiaoming actually left already"

    def test_a_line_the_substitution_does_not_change_is_left_alone(self, isolated_db):
        """No enforce_exact term matches this line -- its `en` must not be
        rewritten at all (the old code rewrote every line unconditionally
        whenever any enforce_exact term existed anywhere in the glossary)."""
        did = isolated_db.create_drama(title_en="Test", status="aligned",
                                       translation_engine="claude")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0, end=1, zh="二", en="Nothing to enforce here"),
        ])
        job_lines = isolated_db.load_line_objects(did)

        translation_finish.finish_translation_run(did, job_lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=[self._term], errors=[],
                                  enforce_ids={ln.id for ln in job_lines})

        assert isolated_db.load_lines(did)[0]["en"] == "Nothing to enforce here"


class _FakeSummaryBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeSummaryEngine:
    """Claude-shaped fake for Step 74's episode-summary call -- records
    how many times it was actually asked to generate something, so tests
    can confirm this runs once per finished episode, not once per batch
    or once per line."""
    supports_reference = True
    model = "fake-summary-model"
    name = "claude"

    def __init__(self, summary_text='{"summary": "Auto-generated summary."}'):
        self.client = self
        self.messages = self
        self.call_count = 0
        self.summary_text = summary_text

    def create(self, model, max_tokens, messages):
        self.call_count += 1
        return type("Resp", (), {"content": [_FakeSummaryBlock(self.summary_text)]})()


class TestFinishTranslationRunEpisodeSummary:
    """Step 74: finish_translation_run is the one place (shared by
    Workspace's run_translate_job and cli.py translate) where a finished
    episode's running summary gets generated and stored."""

    def _translated_drama(self, isolated_db, status="aligned"):
        did = isolated_db.create_drama(title_en="Test", status=status, translation_engine="claude")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0, end=1, zh="一", en="Xiaoling arrived home."),
        ])
        return did, isolated_db.load_line_objects(did)

    def test_generates_and_stores_a_summary_once_the_episode_is_fully_translated(self, isolated_db):
        did, lines = self._translated_drama(isolated_db)
        engine = _FakeSummaryEngine()

        translation_finish.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], enforce_ids={ln.id for ln in lines}, summary_engine=engine)

        assert engine.call_count == 1
        assert isolated_db.get_drama(did)["episode_summary"] == "Auto-generated summary."

    def test_paid_summary_skipped_once_the_monthly_cap_is_used_up(self, isolated_db):
        """Security review (PR #439): a cloud summary spends the owner's key,
        so it counts against the monthly cap like the translation does."""
        did, lines = self._translated_drama(isolated_db)
        isolated_db.log_usage(did, "claude", "m", "translate", estimated_cost_usd=5.0)
        engine = _FakeSummaryEngine()

        translation_finish.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], enforce_ids={ln.id for ln in lines}, summary_engine=engine,
                                  summary_engine_choice="claude", summary_monthly_cap_usd=5.0)

        assert engine.call_count == 0
        assert isolated_db.get_drama(did)["episode_summary"] is None

    def test_paid_summary_runs_while_under_the_monthly_cap(self, isolated_db):
        did, lines = self._translated_drama(isolated_db)
        isolated_db.log_usage(did, "claude", "m", "translate", estimated_cost_usd=1.0)
        engine = _FakeSummaryEngine()

        translation_finish.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], enforce_ids={ln.id for ln in lines}, summary_engine=engine,
                                  summary_engine_choice="claude", summary_monthly_cap_usd=5.0)

        assert engine.call_count == 1

    def test_free_summary_engine_ignores_a_used_up_cap(self, isolated_db):
        did, lines = self._translated_drama(isolated_db)
        isolated_db.log_usage(did, "claude", "m", "translate", estimated_cost_usd=9.0)
        engine = _FakeSummaryEngine()

        translation_finish.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], enforce_ids={ln.id for ln in lines}, summary_engine=engine,
                                  summary_engine_choice="ollama", summary_monthly_cap_usd=5.0)

        assert engine.call_count == 1

    def test_no_summary_engine_skips_generation_entirely(self, isolated_db):
        """summary_engine=None (its default) -- e.g. no engine could be
        built -- must never fail or alter the translation run itself."""
        did, lines = self._translated_drama(isolated_db)

        assert translation_finish.finish_translation_run(
            did, lines, NS(model="fake"), "claude", "audio_drama",
            glossary_terms=None, errors=[], enforce_ids={ln.id for ln in lines}) is True
        assert isolated_db.get_drama(did)["episode_summary"] is None

    def test_not_generated_for_a_cancelled_run(self, isolated_db):
        did, lines = self._translated_drama(isolated_db)
        engine = _FakeSummaryEngine()

        translation_finish.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], enforce_ids={ln.id for ln in lines}, cancelled=True,
                                  summary_engine=engine)

        assert engine.call_count == 0
        assert isolated_db.get_drama(did)["episode_summary"] is None

    def test_not_generated_while_lines_remain_untranslated(self, isolated_db):
        """Only once the drama actually reaches 'translated' -- a batch
        failure or a partial run leaving lines untouched shouldn't
        generate a summary from an incomplete episode."""
        did = isolated_db.create_drama(title_en="Test", status="aligned",
                                       translation_engine="claude")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0, end=1, zh="一", en="Done."),
            Line(idx=1, start=1, end=2, zh="二", en=""),  # still untranslated
        ])
        lines = isolated_db.load_line_objects(did)
        engine = _FakeSummaryEngine()

        translation_finish.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], enforce_ids={ln.id for ln in lines}, summary_engine=engine)

        assert engine.call_count == 0
        assert isolated_db.get_drama(did)["episode_summary"] is None

    def test_called_once_regardless_of_how_many_lines_the_episode_has(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test", status="aligned",
                                       translation_engine="claude")
        isolated_db.save_lines(did, [
            Line(idx=i, start=i, end=i + 1, zh=f"line {i}", en=f"Line {i}.")
            for i in range(40)
        ])
        lines = isolated_db.load_line_objects(did)
        engine = _FakeSummaryEngine()

        translation_finish.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], enforce_ids={ln.id for ln in lines}, summary_engine=engine)

        assert engine.call_count == 1

    def test_a_declining_summary_engine_does_not_overwrite_an_existing_summary(self, isolated_db):
        """generate_episode_summary returning "" (a decline -- parse
        failure, pure-MT engine, etc.) must not blank out a summary a
        previous run (or a manual edit) already stored."""
        did, lines = self._translated_drama(isolated_db)
        isolated_db.update_drama(did, episode_summary="Earlier good summary.")
        engine = _FakeSummaryEngine(summary_text="not valid json")

        translation_finish.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], enforce_ids={ln.id for ln in lines}, summary_engine=engine)

        assert isolated_db.get_drama(did)["episode_summary"] == "Earlier good summary."


class _ListLogger:
    def __init__(self):
        self.records = []

    def warning(self, msg, *args):
        self.records.append(msg % args if args else msg)


def test_failed_next_reflect_stage_submit_is_logged_and_redacted(isolated_db, monkeypatch):
    import applog
    log = _ListLogger()
    monkeypatch.setattr(applog, "get_logger", lambda: log)
    monkeypatch.setattr(bulk_reflect, "make_provider", lambda choice, engine: object())

    def boom(*a, **k):
        raise RuntimeError("no row created key=sk-ant-abcdefghijklmnopqrstuvwxyz0123")
    monkeypatch.setattr(bulk_reflect, "submit_reflect_stage", boom)
    did = _drama(isolated_db, n=2)
    ids = [ln.id for ln in isolated_db.load_line_objects(did)]
    job = {"drama_id": did, "engine": "claude", "model": "m", "pipeline_id": "p"}
    bulk_reflect._advance_to_reflection_stage(job, ids, {i: "d" for i in ids}, {i: "" for i in ids}, NS(model="m"))
    assert len(log.records) == 1 and "reflect" in log.records[0]
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz0123" not in log.records[0]


# ---------------------------------------------------------------------------
# Per-line spoken language
# ---------------------------------------------------------------------------

def _mixed_drama(isolated_db):
    did = isolated_db.create_drama(title_en="Mixed", status="aligned", translation_engine="claude",
                                   source_language="ja")
    isolated_db.save_lines(did, [
        Line(idx=0, start=0, end=1, zh="こんにちは"),
        Line(idx=1, start=1, end=2, zh="안녕", lang="ko"),
        Line(idx=2, start=2, end=3, zh="hello there", lang="en"),
    ])
    return did


def _request_text(engine, i=-1):
    return engine.client.messages.batches.created[i]["params"]["messages"][0]["content"]


class TestSpokenLanguage:
    def test_bulk_tags_other_languages_and_skips_english(self, isolated_db):
        engine = _claude_engine()
        did = _mixed_drama(isolated_db)
        _submit(isolated_db, did, engine, batch_size=10)
        # The English line may still appear as look-ahead context, never as a line to translate.
        text = _request_text(engine).split("Translate these lines:\n\n", 1)[1]
        assert "(spoken in Korean) 안녕" in text and "こんにちは" in text
        assert "hello there" not in text
        rows = {r["zh"]: r for r in isolated_db.load_lines(did)}
        assert rows["hello there"]["en"] == "hello there"
        assert rows["안녕"]["en"] == "" and rows["안녕"]["lang"] == "ko"

    def test_bulk_does_not_track_the_copied_english_line(self, isolated_db):
        engine = _claude_engine()
        did = _mixed_drama(isolated_db)
        bulk_id = _submit(isolated_db, did, engine, batch_size=10)
        assert len(isolated_db.list_bulk_job_lines(bulk_id)) == 2

    def test_an_edit_made_while_submission_copies_english_lines_is_kept(self, isolated_db):
        engine = _claude_engine()
        did = _mixed_drama(isolated_db)
        lines = isolated_db.load_line_objects(did)
        english = next(ln for ln in lines if ln.zh == "hello there")
        with isolated_db.get_conn() as conn:
            conn.execute("UPDATE lines SET en = 'Hi, typed meanwhile' WHERE id = ?", (english.id,))
        bulk_translate.submit_bulk_translation(did, lines, engine, "claude", {"drama_meta": {}},
                                   translate_args={"style_preset": "audio_drama"}, batch_size=10)
        rows = {r["zh"]: r for r in isolated_db.load_lines(did)}
        assert rows["hello there"]["en"] == "Hi, typed meanwhile"

    def test_single_language_bulk_prompt_has_no_tag(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db)
        _submit(isolated_db, did, engine, batch_size=10)
        assert "spoken in" not in _request_text(engine)

    def test_bulk_with_only_english_lines_copies_then_reports_nothing_to_send(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, lang="en")
        with pytest.raises(ValueError, match="Nothing to translate"):
            _submit(isolated_db, did, engine)
        assert [r["en"] for r in isolated_db.load_lines(did)] == ["第0句", "第1句"]
        assert engine.client.messages.batches.created is None

    def test_reflect_stages_tag_the_source(self, isolated_db):
        engine = _claude_engine()
        did = _mixed_drama(isolated_db)
        lines = isolated_db.load_line_objects(did)
        jid = bulk_reflect.submit_reflect_pipeline(did, lines, engine, "claude", {"locale": "en-US"})
        batches = engine.client.messages.batches
        first = batches.created[-1]["params"]["messages"][0]["content"]
        assert "(spoken in Korean) 안녕" in first
        assert "hello there" not in first.split("Lines:\n", 1)[1]
        key = batches.created[-1]["custom_id"]
        ids = [r["line_id"] for r in isolated_db.list_bulk_job_lines(jid)]
        batches.results_list = [_succeeded(key, {str(i): f"draft-{i}" for i in ids})]
        batches.status = "ended"
        assert bulk_poll.check_once(jid, bp.ClaudeBatchProvider(engine), engine=engine) == "applied"
        second = batches.created[-1]["params"]["messages"][0]["content"]
        assert "(spoken in Korean) 안녕" in second

    def test_single_language_reflect_prompt_has_no_tag(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        bulk_reflect.submit_reflect_pipeline(did, isolated_db.load_line_objects(did), engine, "claude", {})
        assert "spoken in" not in _request_text(engine)

    def test_offpeak_run_uses_the_thinking_value_stored_with_the_run(self, isolated_db):
        did = _mixed_drama(isolated_db)
        lines = isolated_db.load_line_objects(did)
        jid = bulk_translate.schedule_offpeak_translation(did, lines, "deepseek", "m", {"thinking": True})
        isolated_db.update_drama(did, translate_thinking=0)  # changed before the job runs
        contexts = []

        class Engine:
            model = "m"

            def translate_batch(self, zh_lines, context):
                contexts.append(context.get("reply_with_thinking"))
                return [f"EN:{z}" for z in zh_lines]

        bulk_poll.run_scheduled_job(jid, Engine())
        assert contexts and all(contexts)

    def test_offpeak_run_keeps_english_typed_while_it_runs(self, isolated_db):
        did = _mixed_drama(isolated_db)
        jid = bulk_translate.schedule_offpeak_translation(did, isolated_db.load_line_objects(did),
                                              "deepseek", "m", {})

        class Engine:
            model = "m"

            def translate_batch(self, zh_lines, context):
                [ln] = [ln for ln in isolated_db.load_line_objects(did) if ln.zh == "안녕"]
                ln.en = "Typed while it ran"
                isolated_db.save_lines(did, [ln], fields=("en",))
                return [f"EN:{z}" for z in zh_lines]

        summary = bulk_poll.run_scheduled_job(jid, Engine())
        rows = {r["zh"]: r["en"] for r in isolated_db.load_lines(did)}
        assert rows["안녕"] == "Typed while it ran"
        assert rows["こんにちは"] == "EN:こんにちは"
        assert summary["kept_your_edit"] == 1

    def test_offpeak_run_tags_and_skips_english(self, isolated_db):
        did = _mixed_drama(isolated_db)
        lines = isolated_db.load_line_objects(did)
        jid = bulk_translate.schedule_offpeak_translation(did, lines, "deepseek", "m", {})
        seen = []

        class Engine:
            model = "m"
            checks_replies = False  # echoes the source script back

            def translate_batch(self, zh_lines, context):
                seen.append((list(zh_lines), context.get("line_languages")))
                return [f"EN:{z}" for z in zh_lines]

        bulk_poll.run_scheduled_job(jid, Engine())
        assert seen == [(["こんにちは", "안녕"], [None, "ko"])]
        rows = {r["zh"]: r["en"] for r in isolated_db.load_lines(did)}
        assert rows["hello there"] == "hello there" and rows["안녕"] == "EN:안녕"


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
        return bp.make_provider("gemini", te.GeminiEngine("gm-key"))

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
        _engine, did, ids, job, _ = self._to_stage(isolated_db, "expressive", n=61)
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
        monkeypatch.setattr(te, "build_reflect_reflection_prompt", broken)
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
        ctx = te.build_translation_context(engine, drama, novel_reference=self._novel,
                                           glossary_terms=[], style_guidelines=self._guidelines)
        ctx.update(speaker_labels=[names.get(ln.speaker) for ln in lines],
                   line_ids=[ln.id for ln in lines], batch_source_lines=[ln.zh for ln in lines],
                   line_languages=None)
        te.reflect_translate_batch(engine, te.pronoun_neutral_texts(ctx, [ln.zh for ln in lines]), ctx)

        assert bulk_prompt == seen[0]
        assert self._novel in bulk_prompt and "[Lin" in bulk_prompt
        assert "TA来了" in bulk_prompt.split("Lines:\n", 1)[1]

    def test_later_stages_keep_the_novel_block_and_pronoun_handling(self, isolated_db):
        did, _drama_row, args = self._setup(isolated_db)
        engine = _claude_engine()
        jid = bulk_reflect.submit_reflect_pipeline(did, isolated_db.load_line_objects(did), engine, "claude", args)
        batches = engine.client.messages.batches
        lid = isolated_db.list_bulk_job_lines(jid)[0]["line_id"]
        batches.results_list = [_succeeded(batches.created[0]["custom_id"], {str(lid): "She came."})]
        batches.status = "ended"
        assert bulk_poll.check_once(jid, bp.ClaudeBatchProvider(engine), engine=engine) == "applied"

        second = _request_text(engine)
        assert self._novel in second and "TA来了" in second.split("Lines:\n", 1)[1]
        assert te.pronoun_batch_note(self._guidelines).strip() in second


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
                    raise te.ContentModerationBlocked("deepseek", "Content Exists Risk")
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
        term = {"term_translation": "Xiao Ming", "notes": "Xiaoming", "enforce_exact": True}
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
                           answer=lambda zh: f"EN[{zh}]", **kw):
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
        assert rows[ids[0]] == version[ids[0]] == "EN[第0句]"
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
        assert rows[ids[0]] == "EN[第0句]"

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
        assert rows[ids[1]] == "EN[第1句]"
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
        assert rows[ids[0]] == "EN[第0句]" and summary["applied"] == 2

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
