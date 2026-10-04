"""
tests/test_bulk_translate.py -- Step 9's bulk mode. Every provider here
is a fake: no network, no real batch API.
"""
import datetime
import json
import os
import re
import sys
import time
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs
import bulk_translate as bt
import translate_engines as te
from core import Line


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
    return bt.submit_bulk_translation(did, lines, engine, "claude", {"drama_meta": {}},
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
            assert r["zh_hash"] == bt.zh_hash(rows[r["line_id"]]["zh"])
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

        assert bt.check_once(bulk_id, bt.make_provider("claude", engine)) == "applied"
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
        bt.check_once(bulk_id, bt.make_provider("claude", engine))

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
        bt.check_once(bulk_id, bt.make_provider("claude", engine))

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
        bt.check_once(bulk_id, bt.make_provider("claude", engine))

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
        bt.check_once(bulk_id, bt.make_provider("claude", engine))
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
        bt.check_once(bulk_id, bt.make_provider("claude", engine))
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
        bt.check_once(bulk_id, bt.make_provider("claude", engine))
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
        bt.check_once(bulk_id, bt.make_provider("claude", engine))
        full = te.estimate_cost("claude-sonnet-5", 1000, 100)
        assert isolated_db.get_usage_summary(did)["estimated_cost_usd"] == pytest.approx(full * 0.5)

    def test_enforced_glossary_terms_apply_to_bulk_results(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=1)
        lines = isolated_db.load_line_objects(did)
        bulk_id = bt.submit_bulk_translation(
            did, lines, engine, "claude", {"drama_meta": {}}, batch_size=5,
            translate_args={"glossary_terms": [{"term_original": "苏杉", "term_translation": "Su Shan",
                                                "notes": "Su Xian", "enforce_exact": True}]})
        batches = engine.client.messages.batches
        _answer_every_request(batches, fn=lambda zh: "Su Xian smiled.")
        batches.status = "ended"
        bt.check_once(bulk_id, bt.make_provider("claude", engine))
        assert isolated_db.load_lines(did)[0]["en"] == "Su Shan smiled."

    def test_still_pending_changes_nothing(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        bulk_id = _submit(isolated_db, did, engine)
        assert bt.check_once(bulk_id, bt.make_provider("claude", engine)) == "submitted"
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
        out = bt.resume_pending(did, lambda engine_choice, model: fresh)
        assert out == {bulk_id: "polling"}
        _wait_for(bt.poll_job_id(bulk_id))

        assert fresh.client.messages.batches.retrieved == ["msgbatch_01"]
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "applied"
        assert all(r["en"] == f"EN[{r['zh']}]" for r in isolated_db.load_lines(did))
        background_jobs.clear_job(bt.poll_job_id(bulk_id))

    def test_resume_without_a_key_reports_it_instead_of_polling(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        bulk_id = _submit(isolated_db, did, engine)
        background_jobs.clear_all_jobs()
        assert bt.resume_pending(did, lambda e, m: None) == {bulk_id: "needs_key"}
        assert not background_jobs.is_running(bt.poll_job_id(bulk_id))

    def test_cancel_stops_polling_and_later_results_are_ignored(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        bulk_id = _submit(isolated_db, did, engine)
        provider = bt.make_provider("claude", engine)
        bt.start_poller(bulk_id, provider=provider)
        time.sleep(0.05)
        note = bt.cancel_bulk_job(bulk_id, provider)
        _wait_for(bt.poll_job_id(bulk_id))

        assert "Cancelled at the provider" in note
        assert engine.client.messages.batches.cancelled == ["msgbatch_01"]
        assert not background_jobs.is_running(bt.poll_job_id(bulk_id))
        # The batch finishes anyway -- nothing is applied.
        batches = engine.client.messages.batches
        _answer_every_request(batches)
        batches.status = "ended"
        assert bt.check_once(bulk_id, provider) == "cancelled"
        assert all(r["en"] == "" for r in isolated_db.load_lines(did))
        background_jobs.clear_job(bt.poll_job_id(bulk_id))

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
        with pytest.raises(bt.BulkAuthError):
            bt.check_once(bulk_id, bt.make_provider("claude", engine))
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
                raise bt.BulkAuthError("Gemini refused the API key (HTTP 403).")
        bt.run_bulk_poller("t_auth", bulk_id, provider=RefusingProvider(), sleep=lambda s: None)
        assert RefusingProvider.calls == 1
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "auth_error"

    def test_check_now_after_fixing_the_key_clears_the_auth_error(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        bulk_id = _submit(isolated_db, did, engine)
        isolated_db.update_bulk_job(bulk_id, status="auth_error", last_error="refused")
        assert bt.check_once(bulk_id, bt.make_provider("claude", engine)) == "submitted"
        job = isolated_db.get_bulk_job(bulk_id)
        assert job["status"] == "submitted" and job["last_error"] is None


# ---------------------------------------------------------------------------
# Gemini provider
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class TestGeminiProvider:
    def _engine(self):
        return te.GeminiEngine("gm-key", model="gemini-flash-lite-latest")

    def test_submission_shape_and_key_in_header(self, isolated_db, monkeypatch):
        sent = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            sent.update(url=url, headers=headers, json=json, timeout=timeout)
            return _Resp({"name": "batches/abc123"})
        monkeypatch.setattr("requests.post", fake_post)
        did = _drama(isolated_db, n=4)
        lines = isolated_db.load_line_objects(did)
        bulk_id = bt.submit_bulk_translation(did, lines, self._engine(), "gemini", {"drama_meta": {}},
                                             batch_size=2)
        assert sent["url"].endswith("/models/gemini-flash-lite-latest:batchGenerateContent")
        assert sent["headers"] == {"x-goog-api-key": "gm-key"} and "key=" not in sent["url"]
        reqs = sent["json"]["batch"]["input_config"]["requests"]["requests"]
        assert [r["metadata"]["key"] for r in reqs] == [f"d{did}_b0", f"d{did}_b1"]
        assert "systemInstruction" in reqs[0]["request"]
        assert isolated_db.get_bulk_job(bulk_id)["provider_batch_id"] == "batches/abc123"

    def test_results_are_matched_by_metadata_key_not_position(self, isolated_db, monkeypatch):
        submitted = {}
        monkeypatch.setattr("requests.post", lambda url, headers=None, json=None, timeout=None:
                            submitted.update(json=json) or _Resp({"name": "batches/abc"}))
        did = _drama(isolated_db, n=4)
        lines = isolated_db.load_line_objects(did)
        bulk_id = bt.submit_bulk_translation(did, lines, self._engine(), "gemini", {"drama_meta": {}},
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
        monkeypatch.setattr("requests.get", lambda url, headers=None, timeout=None: _Resp(done))
        assert bt.check_once(bulk_id, bt.make_provider("gemini", self._engine())) == "applied"
        assert all(r["en"] == f"G[{r['zh']}]" for r in isolated_db.load_lines(did))

    def test_a_response_without_its_key_is_never_attributed(self, isolated_db, monkeypatch):
        monkeypatch.setattr("requests.post", lambda *a, **k: _Resp({"name": "batches/abc"}))
        did = _drama(isolated_db, n=1)
        lines = isolated_db.load_line_objects(did)
        bulk_id = bt.submit_bulk_translation(did, lines, self._engine(), "gemini", {"drama_meta": {}})
        lid = lines[0].id
        done = {"done": True, "metadata": {"state": "JOB_STATE_SUCCEEDED"}, "response": {
            "inlinedResponses": [{"response": {"candidates": [{"content": {"parts": [
                {"text": json.dumps({str(lid): "orphan"})}]}}]}}]}}
        monkeypatch.setattr("requests.get", lambda *a, **k: _Resp(done))
        bt.check_once(bulk_id, bt.make_provider("gemini", self._engine()))
        assert isolated_db.load_lines(did)[0]["en"] == ""

    def test_a_401_while_polling_becomes_an_auth_error_on_the_job(self, isolated_db, monkeypatch):
        monkeypatch.setattr("requests.post", lambda *a, **k: _Resp({"name": "batches/abc"}))
        did = _drama(isolated_db, n=1)
        bulk_id = bt.submit_bulk_translation(did, isolated_db.load_line_objects(did), self._engine(),
                                             "gemini", {"drama_meta": {}})
        monkeypatch.setattr("requests.get", lambda *a, **k: _Resp({}, status=401))
        with pytest.raises(bt.BulkAuthError):
            bt.check_once(bulk_id, bt.make_provider("gemini", self._engine()))
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "auth_error"

    def test_a_failed_batch_is_marked_failed(self, isolated_db, monkeypatch):
        monkeypatch.setattr("requests.post", lambda *a, **k: _Resp({"name": "batches/abc"}))
        did = _drama(isolated_db, n=1)
        bulk_id = bt.submit_bulk_translation(did, isolated_db.load_line_objects(did), self._engine(),
                                             "gemini", {"drama_meta": {}})
        monkeypatch.setattr("requests.get", lambda *a, **k: _Resp(
            {"done": True, "metadata": {"state": "BATCH_STATE_EXPIRED"}}))
        assert bt.check_once(bulk_id, bt.make_provider("gemini", self._engine())) == "failed"
        assert "EXPIRED" in isolated_db.get_bulk_job(bulk_id)["last_error"]

    def test_cancel_posts_to_the_cancel_endpoint(self, isolated_db, monkeypatch):
        calls = []
        monkeypatch.setattr("requests.post", lambda url, headers=None, json=None, timeout=None:
                            calls.append(url) or _Resp({"name": "batches/abc"}))
        did = _drama(isolated_db, n=1)
        bulk_id = bt.submit_bulk_translation(did, isolated_db.load_line_objects(did), self._engine(),
                                             "gemini", {"drama_meta": {}})
        bt.cancel_bulk_job(bulk_id, bt.make_provider("gemini", self._engine()))
        assert calls[-1].endswith("/batches/abc:cancel")
        assert isolated_db.get_bulk_job(bulk_id)["status"] == "cancelled"


# ---------------------------------------------------------------------------
# DeepSeek off-peak scheduling
# ---------------------------------------------------------------------------

class TestDeepSeekOffPeak:
    def test_weekday_peak_waits_for_the_window_to_end(self):
        wed = datetime.datetime(2026, 9, 23, 2, 30)  # Wednesday 02:30 UTC -- peak
        assert not bt.is_deepseek_offpeak(wed)
        assert bt.next_deepseek_offpeak_start(wed) == datetime.datetime(2026, 9, 23, 4, 0)
        morning = datetime.datetime(2026, 9, 23, 9, 59)
        assert bt.next_deepseek_offpeak_start(morning) == datetime.datetime(2026, 9, 23, 10, 0)

    def test_offpeak_now_starts_now(self):
        gap = datetime.datetime(2026, 9, 23, 5, 0)  # between the two peak windows
        assert bt.next_deepseek_offpeak_start(gap) == gap
        sat = datetime.datetime(2026, 9, 26, 7, 0)  # Saturday, inside weekday peak hours
        assert bt.is_deepseek_offpeak(sat)

    def _engine(self):
        class FakeDeepSeek:
            name = "deepseek"
            supports_reference = True
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
        bulk_id = bt.schedule_offpeak_translation(did, lines[:3], "deepseek", "deepseek-v4-flash",
                                                  {"style_preset": "audio_drama"}, now=peak)
        assert isolated_db.get_bulk_job(bulk_id)["scheduled_for"] == "2026-09-23T04:00:00"
        # Meanwhile the user translates one of the scheduled lines by hand.
        lines = isolated_db.load_line_objects(did)
        lines[1].en = "Mine."
        isolated_db.save_lines(did, lines, fields=("en",))

        clock = iter([peak, datetime.datetime(2026, 9, 23, 4, 0, 5)])
        engine = self._engine()
        bt.run_bulk_poller("t_ds", bulk_id, engine=engine, sleep=lambda s: None,
                           now_fn=lambda: next(clock))

        rows = isolated_db.load_lines(did)
        assert [r["en"] for r in rows] == ["DS[第0句]", "Mine.", "DS[第2句]", ""]
        assert engine.seen == ["第0句", "第2句"]
        job = isolated_db.get_bulk_job(bulk_id)
        assert job["status"] == "applied" and job["result_summary"]["skipped_changed"] == 1
        background_jobs.clear_job("t_ds")

    def test_an_orphaned_running_job_is_resumed_after_a_restart(self, isolated_db, monkeypatch):
        saturday_noon = datetime.datetime(2026, 9, 26, 12, 0)
        monkeypatch.setattr(bt, "_utcnow", lambda: saturday_noon)
        did = _drama(isolated_db, n=2)
        bulk_id = bt.schedule_offpeak_translation(
            did, isolated_db.load_line_objects(did), "deepseek", "m", {}, now=saturday_noon)
        isolated_db.update_bulk_job(bulk_id, status="running")
        background_jobs.clear_all_jobs()
        engine = self._engine()
        out = bt.resume_pending(did, lambda e, m: engine)
        assert out == {bulk_id: "polling"}
        _wait_for(bt.poll_job_id(bulk_id))
        assert all(r["en"].startswith("DS[") for r in isolated_db.load_lines(did))
        background_jobs.clear_job(bt.poll_job_id(bulk_id))


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

        jid = bt.submit_bulk_flag(did, lines, engine, "claude", batch_size=10)
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
        status = bt.check_once(jid, bt.ClaudeBatchProvider(engine))

        assert status == "applied"
        summary = isolated_db.get_bulk_job(jid)["result_summary"]
        assert summary == {"applied": 1, "dropped_deleted": 0, "flagged_source_changed": 1,
                           "kept_your_edit": 1, "failed_requests": 0, "unknown_requests": 0}
        final = {ln.id: ln for ln in isolated_db.load_line_objects(did)}
        assert final[ids[0]].flag == "slang_idiom" and final[ids[0]].flag_note == "manual"
        assert final[ids[1]].flag == "bulk_source_changed"
        assert final[ids[2]].flag == "uncertain_translation" and final[ids[2]].flag_note == "auto"

    def test_a_line_deleted_meanwhile_is_dropped(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="translated")
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]
        jid = bt.submit_bulk_flag(did, lines, engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]

        remaining = [ln for ln in isolated_db.load_line_objects(did) if ln.id != ids[0]]
        isolated_db.save_lines(did, remaining)

        payload = [{"line_idx": lid, "reason": "uncertain_translation", "note": ""} for lid in ids]
        results = [(key, json.dumps(payload), {}, None)]
        summary = bt.apply_flag_results(jid, results)
        assert summary["dropped_deleted"] == 1 and summary["applied"] == 1

    def test_engines_without_a_batch_api_are_rejected(self, isolated_db):
        did = _drama(isolated_db, n=1, en="x")
        with pytest.raises(ValueError, match="batch API"):
            bt.submit_bulk_flag(did, isolated_db.load_line_objects(did), NS(model="m"), "deepseek")


class TestBulkConsistency:
    def test_a_window_with_a_stale_line_is_dropped_a_clean_one_is_kept(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=6, en="translated")
        isolated_db.save_consistency_issues(did, [{"term": "old", "variants": ["a"], "note": "stale"}])
        lines = isolated_db.load_line_objects(did)
        jid = bt.submit_bulk_consistency(did, lines, engine, "claude", batch_size=3)
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
        summary = bt.apply_consistency_results(jid, results)
        assert summary == {"issues_found": 1, "dropped_windows": 1, "failed_requests": 0,
                           "unknown_requests": 0}
        assert [i["term"] for i in isolated_db.load_consistency_issues(did)] == ["kept"]

    def test_no_usable_windows_leaves_existing_issues_untouched(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="translated")
        isolated_db.save_consistency_issues(did, [{"term": "keep me", "variants": [], "note": ""}])
        lines = isolated_db.load_line_objects(did)
        jid = bt.submit_bulk_consistency(did, lines, engine, "claude", batch_size=10)
        key = engine.client.messages.batches.created[0]["custom_id"]
        isolated_db.save_lines(did, [])  # every line gone
        summary = bt.apply_consistency_results(jid, [(key, json.dumps([]), {}, None)])
        assert summary["dropped_windows"] == 1
        assert [i["term"] for i in isolated_db.load_consistency_issues(did)] == ["keep me"]


class TestBulkEmotion:
    def test_applies_by_line_id_with_drop_flag_and_kept_edit(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]
        jid = bt.submit_bulk_emotion(did, lines, engine, "claude", batch_size=10)
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
        summary = bt.apply_emotion_results(jid, [(key, json.dumps(payload), {}, None)])

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
        jid = bt.submit_bulk_emotion(did, lines, engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]
        isolated_db.save_lines(did, [ln for ln in isolated_db.load_line_objects(did) if ln.id != ids[0]])
        payload = [{"line_idx": lid, "emotion": "warm", "intensity": 0.5, "note": ""} for lid in ids]
        summary = bt.apply_emotion_results(jid, [(key, json.dumps(payload), {}, None)])
        assert summary["dropped_deleted"] == 1 and summary["applied"] == 1


class TestBulkTranslationNotes:
    def test_applies_by_line_id_and_drops_a_stale_or_deleted_line(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3, en="translated")
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]
        jid = bt.submit_bulk_translation_notes(did, lines, engine, "claude", batch_size=10)
        key = engine.client.messages.batches.created[0]["custom_id"]

        mutated = isolated_db.load_line_objects(did)
        mutated[1].zh = "changed"
        isolated_db.save_lines(did, mutated, fields=("zh",))
        remaining = [ln for ln in isolated_db.load_line_objects(did) if ln.id != ids[2]]
        isolated_db.save_lines(did, remaining)

        payload = [{"line_idx": lid, "term": f"term{lid}", "note_type": "idiom", "note": f"note{lid}"}
                  for lid in ids]
        summary = bt.apply_notes_results(jid, [(key, json.dumps(payload), {}, None)])

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
        jid = bt.submit_bulk_translation_notes(did, lines, engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]
        payload = [{"line_idx": lid, "term": "new term", "note_type": "idiom", "note": "new"}]
        bt.apply_notes_results(jid, [(key, json.dumps(payload), {}, None)])
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
        return bt.check_once(job_id, bt.ClaudeBatchProvider(engine), engine=engine)

    def test_three_sequential_batches_each_keyed_off_the_previous_stage(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2)
        lines = isolated_db.load_line_objects(did)
        ids = [ln.id for ln in lines]

        jid1 = bt.submit_reflect_pipeline(did, lines, engine, "claude", {"locale": "en-US"})
        job1 = isolated_db.get_bulk_job(jid1)
        assert (job1["kind"], job1["stage"]) == ("reflect", "faithful")
        assert len(isolated_db.list_bulk_jobs(did)) == 1

        status1 = self._drive_stage(isolated_db, engine, jid1, lambda lid: f"draft-{lid}")
        assert status1 == "applied"

        stage2 = bt.sibling_stage_job(job1["pipeline_id"], "reflect")
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

        stage3 = bt.sibling_stage_job(job1["pipeline_id"], "expressive")
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
        jid1 = bt.submit_reflect_pipeline(did, lines, engine, "claude", {"locale": "en-US"})
        self._drive_stage(isolated_db, engine, jid1, lambda i: "draft")
        stage2 = bt.sibling_stage_job(isolated_db.get_bulk_job(jid1)["pipeline_id"], "reflect")
        self._drive_stage(isolated_db, engine, stage2["id"], lambda i: None)

        mutated = isolated_db.load_line_objects(did)
        mutated[0].en = "my own edit"
        isolated_db.save_lines(did, mutated, fields=("en",))

        stage3 = bt.sibling_stage_job(isolated_db.get_bulk_job(jid1)["pipeline_id"], "expressive")
        self._drive_stage(isolated_db, engine, stage3["id"], lambda i: "FINAL")
        assert isolated_db.get_bulk_job(stage3["id"])["result_summary"]["kept_your_edit"] == 1
        assert isolated_db.load_line_objects(did)[0].en == "my own edit"

    def test_engines_without_a_batch_api_are_rejected(self, isolated_db):
        did = _drama(isolated_db, n=1)
        with pytest.raises(ValueError, match="batch API"):
            bt.submit_reflect_pipeline(did, isolated_db.load_line_objects(did), NS(model="m"),
                                       "deepseek", {})


class TestFinishTranslationRunGlossaryEnforcement:
    """Step 25d item 5: the enforce_exact glossary substitution pass used
    to substitute into (and save) the job's own possibly-stale in-memory
    line copies -- if a user edited a line's English while the job was
    still running, this pass's unconditional write won even though its
    own baseline text was stale, silently clobbering the user's edit."""

    _term = {"term_translation": "Xiao Ming", "notes": "Xiaoming", "enforce_exact": True}

    def test_substitution_is_applied_to_the_current_db_text_not_a_stale_job_copy(self, isolated_db):
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

        bt.finish_translation_run(did, job_lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=[self._term], errors=[])

        saved = isolated_db.load_lines(did)[0]["en"]
        # The user's edit survives, with the enforced term applied to IT --
        # not the job's stale snapshot silently winning instead.
        assert saved == "Xiao Ming actually left already"

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

        bt.finish_translation_run(did, job_lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=[self._term], errors=[])

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

        bt.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], summary_engine=engine)

        assert engine.call_count == 1
        assert isolated_db.get_drama(did)["episode_summary"] == "Auto-generated summary."

    def test_paid_summary_skipped_once_the_monthly_cap_is_used_up(self, isolated_db):
        """Security review (PR #439): a cloud summary spends the owner's key,
        so it counts against the monthly cap like the translation does."""
        did, lines = self._translated_drama(isolated_db)
        isolated_db.log_usage(did, "claude", "m", "translate", estimated_cost_usd=5.0)
        engine = _FakeSummaryEngine()

        bt.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], summary_engine=engine,
                                  summary_engine_choice="claude", summary_monthly_cap_usd=5.0)

        assert engine.call_count == 0
        assert isolated_db.get_drama(did)["episode_summary"] is None

    def test_paid_summary_runs_while_under_the_monthly_cap(self, isolated_db):
        did, lines = self._translated_drama(isolated_db)
        isolated_db.log_usage(did, "claude", "m", "translate", estimated_cost_usd=1.0)
        engine = _FakeSummaryEngine()

        bt.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], summary_engine=engine,
                                  summary_engine_choice="claude", summary_monthly_cap_usd=5.0)

        assert engine.call_count == 1

    def test_free_summary_engine_ignores_a_used_up_cap(self, isolated_db):
        did, lines = self._translated_drama(isolated_db)
        isolated_db.log_usage(did, "claude", "m", "translate", estimated_cost_usd=9.0)
        engine = _FakeSummaryEngine()

        bt.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], summary_engine=engine,
                                  summary_engine_choice="ollama", summary_monthly_cap_usd=5.0)

        assert engine.call_count == 1

    def test_no_summary_engine_skips_generation_entirely(self, isolated_db):
        """summary_engine=None (its default) -- e.g. no engine could be
        built -- must never fail or alter the translation run itself."""
        did, lines = self._translated_drama(isolated_db)

        assert bt.finish_translation_run(
            did, lines, NS(model="fake"), "claude", "audio_drama",
            glossary_terms=None, errors=[]) is True
        assert isolated_db.get_drama(did)["episode_summary"] is None

    def test_not_generated_for_a_cancelled_run(self, isolated_db):
        did, lines = self._translated_drama(isolated_db)
        engine = _FakeSummaryEngine()

        bt.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], cancelled=True,
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

        bt.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], summary_engine=engine)

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

        bt.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], summary_engine=engine)

        assert engine.call_count == 1

    def test_a_declining_summary_engine_does_not_overwrite_an_existing_summary(self, isolated_db):
        """generate_episode_summary returning "" (a decline -- parse
        failure, pure-MT engine, etc.) must not blank out a summary a
        previous run (or a manual edit) already stored."""
        did, lines = self._translated_drama(isolated_db)
        isolated_db.update_drama(did, episode_summary="Earlier good summary.")
        engine = _FakeSummaryEngine(summary_text="not valid json")

        bt.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], summary_engine=engine)

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
    monkeypatch.setattr(bt, "make_provider", lambda choice, engine: object())

    def boom(*a, **k):
        raise RuntimeError("no row created key=sk-ant-abcdefghijklmnopqrstuvwxyz0123")
    monkeypatch.setattr(bt, "submit_reflect_stage", boom)
    did = _drama(isolated_db, n=2)
    ids = [ln.id for ln in isolated_db.load_line_objects(did)]
    job = {"drama_id": did, "engine": "claude", "model": "m", "pipeline_id": "p"}
    bt._advance_to_reflection_stage(job, ids, {i: "d" for i in ids}, {i: "" for i in ids}, NS(model="m"))
    assert len(log.records) == 1 and "reflect" in log.records[0]
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz0123" not in log.records[0]
