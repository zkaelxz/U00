"""Streamlit widget, AppTest and tab-source tests split out of tests/test_bulk_translate.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_bulk_translate.py."""

import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bulk_translate as bt
import translate_engines as te
from core import Line


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


def _drama(isolated_db, n=7, **kw):
    did = isolated_db.create_drama(title_en="Bulk", status="aligned", translation_engine="claude")
    isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"第{i}句", **kw) for i in range(n)])
    return did


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

    def test_panel_shows_the_current_stage_not_just_pending(self, isolated_db, monkeypatch):
        """UI-level half of the exit condition: the Bulk jobs panel shows
        which Reflect stage is current, not a generic status."""
        import tabs.workspace_tab as wt
        engine = _claude_engine()
        monkeypatch.setattr(bt, "make_provider", lambda engine_choice, e: bt.ClaudeBatchProvider(engine))
        # Avoids a real background poller thread racing this test's own
        # already-consumed fake results -- the panel's own rendering (what
        # this test actually checks) doesn't depend on resume_pending.
        monkeypatch.setattr(bt, "resume_pending", lambda *a, **kw: {})
        did = _drama(isolated_db, n=1)
        lines = isolated_db.load_line_objects(did)
        jid1 = bt.submit_reflect_pipeline(did, lines, engine, "claude", {"locale": "en-US"})
        assert wt._bulk_job_title(isolated_db.get_bulk_job(jid1)) == (
            "Bulk Reflect -- faithfulness pass (stage 1/3)")

        self._drive_stage(isolated_db, engine, jid1, lambda i: "draft")
        stage2 = bt._sibling_stage_job(isolated_db.get_bulk_job(jid1)["pipeline_id"], "reflect")
        assert wt._bulk_job_title(isolated_db.get_bulk_job(stage2["id"])) == (
            "Bulk Reflect -- reflection pass (stage 2/3)")

        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["settings_claude"] = "sk-ant-fake"
        at.run(timeout=30)
        at.run(timeout=30)
        assert any("Bulk Reflect -- reflection pass (stage 2/3)" in m.value for m in at.markdown)
        # Stage 1 (already applied, superseded) is not shown as its own
        # separate card -- only the pipeline's current stage is.
        assert not any("faithfulness pass" in m.value for m in at.markdown)
