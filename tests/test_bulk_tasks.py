"""Tests for the other LLM tasks sent through bulk mode (flag, consistency, emotion, translation
notes, Reflect's three stages) and for finishing a translation run."""

import json
import os
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk import bulk_providers as bp
from bulk import bulk_apply
from bulk import bulk_poll
from bulk import bulk_reflect
from bulk import bulk_translate
import translation_finish
from engine_backends import shared as engine_shared
from core import Line
from tests.test_bulk_translate import _claude_engine, _drama, _succeeded


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
                           "unparsable_requests": 0, "unparsable_entries": 0}
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

    @pytest.mark.parametrize("cut_off", [True, False])
    def test_a_cut_off_or_unreadable_reply_is_counted_and_reads_as_partly_done(self, isolated_db, cut_off):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="translated")
        lines = isolated_db.load_line_objects(did)
        jid = bulk_translate.submit_bulk_flag(did, lines, engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]
        text = (engine_shared.TruncatedReply(f'[{{"line_idx": {lines[0].id}, "reason": "slang_idiom"}}, {{"line_')
                if cut_off else "Sorry, here are the flags: none.")
        summary = bulk_apply.apply_flag_results(jid, [(key, text, {}, None)])
        assert summary["unparsable_requests"] == 1 and summary["applied"] == 0
        assert bulk_poll.poller_outcome("no-job", {"result_summary": summary}) == {"partial": True}

    def test_an_unreadable_reply_still_flags_a_source_edited_meanwhile(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="translated")
        lines = isolated_db.load_line_objects(did)
        jid = bulk_translate.submit_bulk_flag(did, lines, engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]
        edited = isolated_db.load_line_objects(did)
        edited[0].zh = "改过的原文"
        isolated_db.save_lines(did, edited, fields=("zh",))
        summary = bulk_apply.apply_flag_results(jid, [(key, "no JSON here", {}, None)])
        assert summary["unparsable_requests"] == 1 and summary["flagged_source_changed"] == 1
        assert isolated_db.load_line_objects(did)[0].flag == "bulk_source_changed"

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
                           "unknown_requests": 0, "unparsable_requests": 0}
        assert [i["term"] for i in isolated_db.load_consistency_issues(did)] == ["kept"]

    def test_an_unreadable_window_is_counted_and_keeps_the_existing_issues(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="translated")
        isolated_db.save_consistency_issues(did, [{"term": "keep me", "variants": [], "note": ""}])
        jid = bulk_translate.submit_bulk_consistency(did, isolated_db.load_line_objects(did), engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]
        summary = bulk_apply.apply_consistency_results(jid, [(key, '[{"term": "cut', {}, None)])
        assert summary["unparsable_requests"] == 1
        assert [i["term"] for i in isolated_db.load_consistency_issues(did)] == ["keep me"]

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
                           "failed_requests": 0, "unknown_requests": 0, "unparsable_requests": 0}
        notes = isolated_db.list_translation_notes(did)
        assert len(notes) == 1 and notes[0]["line_id"] == ids[0]

    def test_a_cut_off_reply_is_counted(self, isolated_db):
        engine = _claude_engine()
        did = _drama(isolated_db, n=1, en="translated")
        jid = bulk_translate.submit_bulk_translation_notes(did, isolated_db.load_line_objects(did), engine, "claude")
        key = engine.client.messages.batches.created[0]["custom_id"]
        summary = bulk_apply.apply_notes_results(jid, [(key, engine_shared.TruncatedReply('[{"line_idx": 1, "term": "x", "note": "cut'), {}, None)])
        assert summary["unparsable_requests"] == 1 and summary["applied"] == 0

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

    def test_summary_call_gets_its_own_timing_stage(self, isolated_db):
        did, lines = self._translated_drama(isolated_db)
        stages = []

        translation_finish.finish_translation_run(
            did, lines, NS(model="fake"), "claude", "audio_drama", glossary_terms=None,
            errors=[], enforce_ids={ln.id for ln in lines}, summary_engine=_FakeSummaryEngine(),
            summary_engine_choice="claude", stage_cb=stages.append)

        assert stages == ["Episode summary"]

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

    def test_free_summary_engine_ignores_a_used_up_cap(self, isolated_db, free_engine):
        did, lines = self._translated_drama(isolated_db)
        isolated_db.log_usage(did, "claude", "m", "translate", estimated_cost_usd=9.0)
        engine = _FakeSummaryEngine()

        translation_finish.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], enforce_ids={ln.id for ln in lines}, summary_engine=engine,
                                  summary_engine_choice=free_engine, summary_monthly_cap_usd=5.0)

        assert engine.call_count == 1

    def test_a_hosted_ollama_summary_is_held_to_the_monthly_cap(self, isolated_db):
        did, lines = self._translated_drama(isolated_db)
        isolated_db.log_usage(did, "claude", "m", "translate", estimated_cost_usd=9.0)
        engine = _FakeSummaryEngine()
        engine.model = "gemma4:31b-cloud"

        translation_finish.finish_translation_run(did, lines, NS(model="fake"), "claude", "audio_drama",
                                  glossary_terms=None, errors=[], enforce_ids={ln.id for ln in lines}, summary_engine=engine,
                                  summary_engine_choice="ollama", summary_monthly_cap_usd=5.0)

        assert engine.call_count == 0

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
