"""
tests/test_bulk_translate.py -- Step 9's bulk mode. Every provider here
is a fake: no network, no real batch API.
"""
import datetime
import itertools
import json
import os
import re
import sys
import time
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs
from bulk import bulk_providers as bp
from bulk import bulk_poll
from bulk import bulk_reflect
from bulk import bulk_translate
from engine_backends import pricing
from engine_backends import claude as engine_claude
from engine_backends import gemini as engine_gemini
from core import Line
from lib import http
from tests.http_fakes import StreamedBody
import paid_limits


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
    engine = engine_claude.ClaudeEngine("sk-ant-fake")
    engine.client = NS(messages=NS(batches=FakeClaudeBatches()))
    return engine


def _succeeded(custom_id, payload: dict, usage=None):
    msg = NS(content=[NS(type="text", text=json.dumps(payload, ensure_ascii=False))],
             usage=usage or NS(input_tokens=1000, output_tokens=100,
                               cache_read_input_tokens=0, cache_creation_input_tokens=0))
    return NS(custom_id=custom_id, result=NS(type="succeeded", message=msg))


def _en(zh: str) -> str:
    """A reply with no source script in it: the bulk apply rejects a reply that
    leaves source text in the English, as a live reply is."""
    return f"EN[line{re.search(r'[0-9]+', zh).group()}]"


def _answer_every_request(batches: FakeClaudeBatches, fn=lambda zh: _en(zh)):
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
            assert r["en"] == _en(r['zh'])
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
            assert r["en"] == _en(r['zh'])
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
        assert rows[1]["en"] == "EN[line1]"

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
        full = pricing.estimate_cost("claude-sonnet-5", 1000, 100)
        assert isolated_db.get_usage_summary(did)["estimated_cost_usd"] == pytest.approx(full * 0.5)

    def test_a_reply_cut_off_at_the_output_limit_keeps_its_finished_lines(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        bulk_id = _submit(isolated_db, did, engine, batch_size=5)
        batches = engine.client.messages.batches
        ids = [r["id"] for r in isolated_db.load_lines(did)]
        cut = '{"%d": "First.", "%d": "Second.", "%d": "Thi' % tuple(ids)
        msg = NS(content=[NS(type="text", text=cut)], stop_reason="max_tokens",
                 usage=NS(input_tokens=10, output_tokens=5,
                          cache_read_input_tokens=0, cache_creation_input_tokens=0))
        batches.results_list = [NS(custom_id=batches.created[0]["custom_id"],
                                   result=NS(type="succeeded", message=msg))]
        batches.status = "ended"
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))
        assert [r["en"] for r in isolated_db.load_lines(did)] == ["First.", "Second.", ""]

    def test_the_same_cut_off_text_without_the_signal_is_not_kept(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=3)
        bulk_id = _submit(isolated_db, did, engine, batch_size=5)
        batches = engine.client.messages.batches
        ids = [r["id"] for r in isolated_db.load_lines(did)]
        cut = '{"%d": "First.", "%d": "Thi' % tuple(ids[:2])
        msg = NS(content=[NS(type="text", text=cut)], stop_reason="end_turn",
                 usage=NS(input_tokens=10, output_tokens=5,
                          cache_read_input_tokens=0, cache_creation_input_tokens=0))
        batches.results_list = [NS(custom_id=batches.created[0]["custom_id"],
                                   result=NS(type="succeeded", message=msg))]
        batches.status = "ended"
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))
        assert all(r["en"] == "" for r in isolated_db.load_lines(did))

    def test_enforced_glossary_terms_apply_to_bulk_results(self, isolated_db):
        engine = _claude_engine()
        did = isolated_db.create_drama(title_en="Bulk", status="aligned", translation_engine="claude")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="苏杉笑了"),
                                     Line(idx=1, start=1, end=2, zh="她笑了")])
        lines = isolated_db.load_line_objects(did)
        bulk_id = bulk_translate.submit_bulk_translation(
            did, lines, engine, "claude", {"drama_meta": {}}, batch_size=5,
            translate_args={"glossary_terms": [{"term_original": "苏杉", "term_translation": "Su Shan",
                                                "notes": "Su Xian", "enforce_exact": True}]})
        batches = engine.client.messages.batches
        _answer_every_request(batches, fn=lambda zh: "Su Xian smiled.")
        batches.status = "ended"
        bulk_poll.check_once(bulk_id, bp.make_provider("claude", engine))
        # The second line's source never names 苏杉, so its "Su Xian" is not the term.
        assert [r["en"] for r in isolated_db.load_lines(did)] == ["Su Shan smiled.", "Su Xian smiled."]

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
        assert all(r["en"] == _en(r['zh']) for r in isolated_db.load_lines(did))
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
        return engine_gemini.GeminiEngine("gm-key", model="gemini-flash-lite-latest")

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
                    {str(i): "G" + _en(zh) for i, zh in got.items()}, ensure_ascii=False)}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}})
        done = {"name": "batches/abc", "done": True,
                "metadata": {"state": "BATCH_STATE_SUCCEEDED"},
                "response": {"inlinedResponses": {"inlinedResponses": list(reversed(responses))}}}
        _patch_http(monkeypatch, get=lambda url, headers=None, timeout=None, stream=None: _Resp(done))
        assert bulk_poll.check_once(bulk_id, bp.make_provider("gemini", self._engine())) == "applied"
        assert all(r["en"] == "G" + _en(r["zh"]) for r in isolated_db.load_lines(did))

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

    def test_a_members_offpeak_run_is_refused_before_any_batch_once_their_limit_is_used(self, isolated_db):
        from services import auth_service
        uid = auth_service.add_user("kid@example.com")["id"]
        did = _drama(isolated_db, n=2)
        peak = datetime.datetime(2026, 9, 23, 2, 0)
        with isolated_db.spending_for(uid):
            bulk_id = bulk_translate.schedule_offpeak_translation(
                did, isolated_db.load_line_objects(did), "deepseek", "deepseek-v4-flash",
                {"style_preset": "audio_drama"}, now=peak)
        assert isolated_db.get_bulk_job(bulk_id)["user_id"] == uid
        auth_service.admin_set_paid_limit(uid, 1.0)
        with isolated_db.spending_for(uid):
            isolated_db.log_usage(None, "claude", "m", "translate", 1, 1, 1.0)
        engine = self._engine()
        offpeak = datetime.datetime(2026, 9, 23, 4, 0, 5)
        bulk_poll.run_bulk_poller("t_ds_limit", bulk_id, engine=engine, sleep=lambda s: None,
                                  now_fn=lambda: offpeak, monthly_cap_usd=50.0)
        job = isolated_db.get_bulk_job(bulk_id)
        assert engine.seen == []
        assert job["status"] == "failed" and job["last_error"] == paid_limits.PAID_LIMIT_MESSAGE
        background_jobs.clear_job("t_ds_limit")

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

    def test_offpeak_run_reads_the_novel_reference_from_the_title_when_it_runs(self, isolated_db):
        did = _mixed_drama(isolated_db)
        folder = os.path.join(isolated_db.DRAMAS_DIR, str(did))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "novel_reference.txt"), "w", encoding="utf-8") as f:
            f.write("THE BOOK")
        isolated_db.update_drama(did, novel_reference_filename="novel_reference.txt")
        jid = bulk_translate.schedule_offpeak_translation(
            did, isolated_db.load_line_objects(did), "deepseek", "m", {"novel_reference": "THE BOOK"})
        assert "THE BOOK" not in json.dumps(isolated_db.get_bulk_job(jid)["translate_args"])
        seen = []

        class Engine:
            model = "m"
            supports_reference = True

            def translate_batch(self, zh_lines, context):
                seen.append(context.get("novel_reference"))
                return [f"EN:{z}" for z in zh_lines]

        bulk_poll.run_scheduled_job(jid, Engine())
        assert seen and all(ref == "THE BOOK" for ref in seen)

    def test_offpeak_run_stops_when_the_month_spend_reaches_the_monthly_cap(self, isolated_db):
        did = _mixed_drama(isolated_db)
        jid = bulk_translate.schedule_offpeak_translation(
            did, isolated_db.load_line_objects(did), "deepseek", "m", {"batch_size": 1})
        calls = []

        class Engine:
            model = "m"

            def translate_batch(self, zh_lines, context):
                calls.append(zh_lines)
                # Another run spends the rest of the month's cap meanwhile.
                isolated_db.log_usage(None, "deepseek", "m", "other", 0, 0, 5.0)
                return [f"EN:{z}" for z in zh_lines]

        bulk_poll.run_scheduled_job(jid, Engine(), monthly_cap_usd=1.0)
        assert len(calls) == 1

    def test_offpeak_run_does_not_send_a_batch_priced_past_the_monthly_cap(self, isolated_db):
        did = _mixed_drama(isolated_db)
        jid = bulk_translate.schedule_offpeak_translation(
            did, isolated_db.load_line_objects(did), "deepseek", "m", {"batch_size": 1})
        calls = []

        class Engine:
            name = "claude"
            model = "claude-sonnet-5"  # 1M input tokens a batch is $2.00 on this model

            def __init__(self):
                self.last_usage = {}

            def translate_batch(self, zh_lines, context):
                calls.append(zh_lines)
                self.last_usage = {"input_tokens": 1_000_000, "output_tokens": 0}
                return [f"EN:{z}" for z in zh_lines]

        bulk_poll.run_scheduled_job(jid, Engine(), monthly_cap_usd=3.0)
        assert len(calls) == 1

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


def test_an_offpeak_deepseek_run_out_of_credit_moves_to_gemma(isolated_db, monkeypatch):
    from engine_backends import fallback as engine_fallback
    from services import translate_run_options

    class NoCredit(Exception):
        status_code = 402

    class DeepSeek:
        name, model = "deepseek", "deepseek-flash"

        def translate_batch(self, zh_lines, context):
            raise NoCredit("Insufficient Balance")

    class Gemma:
        name, model = "ollama", "gemma4:31b-cloud"

        def translate_batch(self, zh_lines, context):
            return [f"G:{z}" for z in zh_lines]

    monkeypatch.setattr(engine_fallback, "_slow_until", {})
    monkeypatch.setattr(engine_fallback, "_slow_reason", {})
    monkeypatch.setattr(translate_run_options.engine_registry, "get_engine", lambda *a, **k: Gemma())
    did = _mixed_drama(isolated_db)
    jid = bulk_translate.schedule_offpeak_translation(did, isolated_db.load_line_objects(did), "deepseek", "m", {})
    summary = bulk_poll.run_scheduled_job(jid, translate_run_options.with_slow_switch(DeepSeek()))
    assert summary["batch_errors"] == 0
    assert [ln.en[:2] for ln in isolated_db.load_line_objects(did)][:2] == ["G:", "G:"]
    assert summary["fallbacks"] == [{"from": "deepseek", "to": "ollama", "reason": engine_fallback.OUT_OF_CREDIT}]


def test_offpeak_deepseek_runs_get_the_switching_engine_when_started_and_resumed(isolated_db, monkeypatch):
    from engine_backends import engine_registry, fallback as engine_fallback
    from services import translate_run_service, translate_service

    class Plain:
        def __init__(self, name):
            self.name, self.model = name, "m"

    monkeypatch.setattr(engine_registry, "get_engine", lambda name, *a, **k: Plain(name))
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name: "k")
    polled = []
    monkeypatch.setattr(bulk_poll, "run_bulk_poller", lambda *a, engine=None, **k: polled.append(engine))
    monkeypatch.setattr(isolated_db, "get_bulk_job", lambda _id: {"status": "applied"})
    translate_run_service.run_bulk_translate_job("j", Plain("deepseek"), "deepseek", lambda: 1)
    assert isinstance(polled[0], engine_fallback.SlowSwitchEngine)
    factory = translate_run_service._bulk_engine_factory
    assert isinstance(factory("deepseek", None), engine_fallback.SlowSwitchEngine)
    assert factory("claude", None).name == "claude"


def test_an_offpeak_run_decides_its_start_engine_when_the_window_opens(isolated_db, monkeypatch):
    from engine_backends import fallback as engine_fallback
    from services import translate_run_options

    class DeepSeek:
        name, model = "deepseek", "deepseek-flash"

        def translate_batch(self, zh_lines, context):
            return [f"D:{z}" for z in zh_lines]

    class Gemma:
        name, model = "ollama", "gemma4:31b-cloud"

        def translate_batch(self, zh_lines, context):
            return [f"G:{z}" for z in zh_lines]

    deepseek = DeepSeek()
    monkeypatch.setattr(engine_fallback, "_slow_until", {})
    monkeypatch.setattr(engine_fallback, "_slow_reason", {})
    monkeypatch.setattr(translate_run_options.engine_registry, "get_engine", lambda *a, **k: Gemma())
    engine_fallback._remember_slow(deepseek)
    engine = translate_run_options.with_slow_switch(deepseek)  # built while DeepSeek was slow
    engine_fallback._slow_until.clear()  # the memory has run out by the time the window opens
    did = _mixed_drama(isolated_db)
    jid = bulk_translate.schedule_offpeak_translation(did, isolated_db.load_line_objects(did), "deepseek", "m", {})
    summary = bulk_poll.run_scheduled_job(jid, engine)
    assert [ln.en[:2] for ln in isolated_db.load_line_objects(did)][:2] == ["D:", "D:"]
    assert "fallbacks" not in summary
