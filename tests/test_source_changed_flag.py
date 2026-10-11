"""A translated line whose source text changes keeps its English and is
flagged `source_changed` (source_change.py), by every writer that changes
source text; new English clears the flag. Fully mocked: isolated_db, the
offline "fake" engine, faked finished re-transcription runs."""
import time

import pytest
from fastapi.testclient import TestClient

import background_jobs
import bulk_translate
import db
import resegment
import source_change
from api.server import create_app
from core import Line
from services import (compare_transcription_service as compare, lines_service,
                      restructure_service, retranscribe_many_service as many,
                      source_change_service, subtitle_import_service, transcribe_service,
                      translate_run_service, workspace_job_service)
from services.service_errors import ConflictError

FLAG = source_change.SOURCE_CHANGED_FLAG


@pytest.fixture(autouse=True)
def _jobs():
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


def _seed(rows):
    """rows: (zh, en) or (zh, en, flag)."""
    did = db.create_drama(title_zh="D", status="translated")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 0.9, zh=r[0], en=r[1],
                             flag=r[2] if len(r) > 2 else None)
                        for i, r in enumerate(rows)])
    return did, [r["id"] for r in db.load_lines(did)]


def _by_id(did):
    return {r["id"]: r for r in db.load_lines(did)}


def _wait(job_id):
    for _ in range(400):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


class TestRule:
    @pytest.mark.parametrize("old, new, en, flagged", [
        ("旧", "新", "Old", True),
        ("旧", "", "Old", True),         # the source text was removed
        ("旧", " 旧 ", "Old", False),     # whitespace only
        ("旧", "旧", "Old", False),
        ("旧", "新", "", False),          # nothing translated yet
        ("旧", "新", "   ", False),
        ("", "新", "Old", False),         # no source before
    ])
    def test_orphans_english(self, old, new, en, flagged):
        assert source_change.orphans_english(old, new, en) is flagged

    def test_clear_leaves_other_flags(self):
        ln = Line(idx=0, start=0, end=1, zh="a", en="b", flag="timing_drift", flag_note="n")
        assert not source_change.clear_for_new_english(ln)
        assert (ln.flag, ln.flag_note) == ("timing_drift", "n")


class TestManualEdit:
    def test_source_edit_keeps_english_and_flags(self, isolated_db):
        did, ids = _seed([("旧", "Old")])
        out = lines_service.patch_line(did, ids[0], zh="新")
        assert (out["zh"], out["en"], out["flag"]) == ("新", "Old", FLAG)

    def test_with_expected_too(self, isolated_db):
        did, ids = _seed([("旧", "Old")])
        lines_service.patch_line(did, ids[0], zh="新", expected={"zh": "旧"})
        assert _by_id(did)[ids[0]]["flag"] == FLAG

    def test_no_flag_without_a_real_change_or_english(self, isolated_db):
        did, ids = _seed([("旧", "Old"), ("空", "")])
        lines_service.patch_line(did, ids[0], zh=" 旧 ")
        lines_service.patch_line(did, ids[1], zh="新")
        rows = _by_id(did)
        assert rows[ids[0]]["flag"] is None and rows[ids[1]]["flag"] is None

    def test_source_and_english_edited_together_is_not_flagged(self, isolated_db):
        did, ids = _seed([("旧", "Old")])
        out = lines_service.patch_line(did, ids[0], zh="新", en="New")
        assert (out["en"], out["flag"]) == ("New", None)

    def test_editing_the_english_clears_the_flag(self, isolated_db):
        did, ids = _seed([("新", "Old", FLAG)])
        assert lines_service.patch_line(did, ids[0], en="New")["flag"] is None


class TestFindReplaceAndTm:
    def test_find_replace_never_flags_and_clears_on_new_english(self, isolated_db):
        did, ids = _seed([("新", "Old cat", FLAG), ("旧", "Old dog")])
        out = lines_service.apply_find_replace(did, [
            {"id": ids[0], "old_text": "Old cat", "new_text": "New cat"},
            {"id": ids[1], "old_text": "Old dog", "new_text": "New dog"}])
        assert out["applied"] == 2
        rows = _by_id(did)
        assert (rows[ids[0]]["zh"], rows[ids[0]]["flag"]) == ("新", None)
        assert (rows[ids[1]]["zh"], rows[ids[1]]["flag"]) == ("旧", None)

    def test_tm_accept_clears(self, isolated_db):
        sid = db.get_or_create_series("S")
        did = db.create_drama(title_zh="D", series_id=sid)
        db.save_lines(did, [Line(idx=0, start=0, end=1, zh="新", en="Old", flag=FLAG)])
        lid = db.load_lines(did)[0]["id"]
        db.record_translation_memory(sid, "新", "From memory")
        entry = db.list_translation_memory(sid)[0]
        out = lines_service.accept_tm_suggestion(did, lid, entry["id"], "Old")
        assert (out["en"], out["flag"]) == ("From memory", None)


class TestRetranscribeOne:
    def _proposal(self, monkeypatch, base, proposed):
        monkeypatch.setattr(transcribe_service, "_finished_proposal", lambda d, l: {
            "proposed_zh": proposed, "base_zh": base, "base_start": 0.0, "base_end": 0.9})

    def test_apply_keeps_english_and_flags(self, isolated_db, monkeypatch):
        did, ids = _seed([("旧", "Old")])
        self._proposal(monkeypatch, "旧", "新")
        transcribe_service.apply_retranscribe_line(
            did, ids[0], transcribe_service.retranscribe_line_job_id(did), "旧", "新")
        row = _by_id(did)[ids[0]]
        assert (row["zh"], row["en"], row["flag"]) == ("新", "Old", FLAG)

    def test_untranslated_line_is_not_flagged(self, isolated_db, monkeypatch):
        did, ids = _seed([("旧", "")])
        self._proposal(monkeypatch, "旧", "新")
        transcribe_service.apply_retranscribe_line(
            did, ids[0], transcribe_service.retranscribe_line_job_id(did), "旧", "新")
        assert _by_id(did)[ids[0]]["flag"] is None

    def test_english_edited_before_the_write_wins(self, isolated_db, monkeypatch):
        did, ids = _seed([("旧", "Old")])
        self._proposal(monkeypatch, "旧", "新")
        real = db.save_line_history_snapshot

        def edit_then_snapshot(d, lines, label):
            # The user's English edit lands between the read and the write.
            lines_service.patch_line(did, ids[0], en="Mine")
            return real(d, lines, label)
        monkeypatch.setattr(db, "save_line_history_snapshot", edit_then_snapshot)
        with pytest.raises(ConflictError):
            transcribe_service.apply_retranscribe_line(
                did, ids[0], transcribe_service.retranscribe_line_job_id(did), "旧", "新")
        row = _by_id(did)[ids[0]]
        assert (row["zh"], row["en"], row["flag"]) == ("旧", "Mine", None)


def _proposals(monkeypatch, module, fn, proposals):
    monkeypatch.setattr(module, fn, lambda d: {"result": {"proposals": proposals},
                                               "finished_at": 1.0})


class TestRetranscribeMany:
    def _items(self, props):
        return [{"line_id": p["line_id"], "expected_zh": p["base_zh"],
                 "expected_proposed": p["proposed_zh"]} for p in props]

    def test_keeps_english_and_flags_only_translated_changed_lines(self, isolated_db, monkeypatch):
        did, ids = _seed([("旧0", "Old 0"), ("旧1", ""), ("旧2", "Old 2")])
        props = [{"line_id": lid, "base_zh": z, "proposed_zh": p, "base_en": e,
                  "base_start": float(i), "base_end": i + 0.9}
                 for i, (lid, z, p, e) in enumerate([(ids[0], "旧0", "新0", "Old 0"),
                                                     (ids[1], "旧1", "新1", ""),
                                                     (ids[2], "旧2", "旧2 ", "Old 2")])]
        _proposals(monkeypatch, many, "_finished_run", props)
        out = many.apply_retranscribe_many(did, f"retranscribe_{did}", self._items(props))
        assert out["applied"] == ids
        rows = _by_id(did)
        assert (rows[ids[0]]["zh"], rows[ids[0]]["en"], rows[ids[0]]["flag"]) == ("新0", "Old 0", FLAG)
        assert (rows[ids[1]]["zh"], rows[ids[1]]["flag"]) == ("新1", None)
        assert (rows[ids[2]]["en"], rows[ids[2]]["flag"]) == ("Old 2", None)

    def test_a_concurrent_edit_survives(self, isolated_db, monkeypatch):
        did, ids = _seed([("旧0", "Old 0")])
        props = [{"line_id": ids[0], "base_zh": "旧0", "proposed_zh": "新0", "base_en": "Old 0",
                  "base_start": 0.0, "base_end": 0.9}]
        _proposals(monkeypatch, many, "_finished_run", props)
        lines_service.patch_line(did, ids[0], en="Mine")
        out = many.apply_retranscribe_many(did, f"retranscribe_{did}", self._items(props))
        assert out["skipped"] == ids
        row = _by_id(did)[ids[0]]
        assert (row["zh"], row["en"], row["flag"]) == ("旧0", "Mine", None)

    def test_snapshot_restore_does_not_flag(self, isolated_db, monkeypatch):
        did, ids = _seed([("旧0", "Old 0")])
        props = [{"line_id": ids[0], "base_zh": "旧0", "proposed_zh": "新0", "base_en": "Old 0",
                  "base_start": 0.0, "base_end": 0.9}]
        _proposals(monkeypatch, many, "_finished_run", props)
        many.apply_retranscribe_many(did, f"retranscribe_{did}", self._items(props))
        assert _by_id(did)[ids[0]]["flag"] == FLAG
        history_id = db.list_line_history(did)[0]["id"]
        restructure_service.restore_version(did, history_id, ids)
        row = _by_id(did)[ids[0]]
        assert (row["zh"], row["en"], row["flag"]) == ("旧0", "Old 0", None)

    def test_restoring_a_snapshot_without_recorded_flags_does_not_flag(self, isolated_db,
                                                                      monkeypatch):
        did, ids = _seed([("新0", "Old 0", FLAG)])
        real = db.get_line_history_snapshot
        db.save_line_history_snapshot(did, [Line(idx=0, start=0.0, end=0.9, zh="旧0", en="Old 0",
                                                 id=ids[0])], "older")
        history_id = db.list_line_history(did)[0]["id"]

        def without_marks(hid):
            rows = real(hid)
            return [{k: v for k, v in r.items() if k not in ("flag", "flag_note")} for r in rows]
        monkeypatch.setattr(db, "get_line_history_snapshot", without_marks)
        restructure_service.restore_version(did, history_id, ids)
        row = _by_id(did)[ids[0]]
        assert (row["zh"], row["flag"]) == ("旧0", None)


class TestCompare:
    def _setup(self, monkeypatch, ids, candidate_en=""):
        props = [{"line_id": ids[0], "base_zh": "旧", "candidate_zh": "新", "base_en": "Old",
                  "candidate_en": candidate_en, "start": 0.0, "end": 0.9}]
        _proposals(monkeypatch, compare, "_finished_job", props)
        return props

    def test_without_english_keeps_and_flags(self, isolated_db, monkeypatch):
        did, ids = _seed([("旧", "Old")])
        self._setup(monkeypatch, ids)
        out = compare.apply_compare(did, compare.compare_job_id(did), [
            {"line_id": ids[0], "expected_base_zh": "旧", "expected_candidate_zh": "新"}])
        assert out["applied"] == ids
        row = _by_id(did)[ids[0]]
        assert (row["zh"], row["en"], row["flag"]) == ("新", "Old", FLAG)

    def test_with_its_english_is_not_flagged_and_clears(self, isolated_db, monkeypatch):
        did, ids = _seed([("旧", "Old", FLAG)])
        self._setup(monkeypatch, ids, candidate_en="New")
        compare.apply_compare(did, compare.compare_job_id(did), [
            {"line_id": ids[0], "expected_base_zh": "旧", "expected_candidate_zh": "新",
             "use_english": True, "expected_candidate_en": "New"}])
        row = _by_id(did)[ids[0]]
        assert (row["zh"], row["en"], row["flag"]) == ("新", "New", None)

    def test_english_edited_since_the_run_is_skipped(self, isolated_db, monkeypatch):
        did, ids = _seed([("旧", "Old")])
        self._setup(monkeypatch, ids)
        lines_service.patch_line(did, ids[0], en="Mine")
        out = compare.apply_compare(did, compare.compare_job_id(did), [
            {"line_id": ids[0], "expected_base_zh": "旧", "expected_candidate_zh": "新"}])
        assert out["skipped"] == ids
        assert (_by_id(did)[ids[0]]["zh"], _by_id(did)[ids[0]]["en"]) == ("旧", "Mine")


class _HearEngine:
    """translate_batch fails for `fail` texts; `during` runs before the first call."""
    name = "fake"
    model = "m"

    def __init__(self, fail=(), during=None):
        self.fail, self.during = set(fail), during
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, zh_lines, context):
        if self.during:
            self.during(), setattr(self, "during", None)
        if zh_lines[0] in self.fail:
            raise RuntimeError("engine down")
        return [f"EN {z}" for z in zh_lines]


class TestFixFlagged:
    def _run(self, did, engine, monkeypatch, heard):
        monkeypatch.setattr(workspace_job_service.fixflag_transcribe, "hear_flagged",
                            lambda job_id, flagged, audio, settings: (
                                [{"idx": ln.idx, "text": heard.get(ln.zh, "")} for ln in flagged],
                                False, None))
        monkeypatch.setattr(workspace_job_service.fixflag_transcribe, "hearing_settings",
                            lambda *a: {})
        monkeypatch.setattr(workspace_job_service.whisper_models, "release_gpu_models",
                            lambda: None)
        monkeypatch.setattr(workspace_job_service.os.path, "exists", lambda p: True)
        job_id = f"fixflag_{did}"
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                         "error": None, "cancel_requested": False, "result": None}
        workspace_job_service.run_fix_flagged_lines_job(
            job_id, did, db.load_line_objects(did), "audio.wav", "small", False, "zh", engine,
            "fake")
        return background_jobs.get_status(job_id)["result"]

    def test_new_source_without_new_english_is_flagged(self, isolated_db, monkeypatch):
        did, ids = _seed([("旧", "Old", "uncertain_translation"),
                          ("对", "Right", "uncertain_translation")])
        self._run(did, _HearEngine(fail={"新"}), monkeypatch, {"旧": "新", "对": "对"})
        rows = _by_id(did)
        assert (rows[ids[0]]["zh"], rows[ids[0]]["en"], rows[ids[0]]["flag"]) == ("新", "Old", FLAG)
        assert (rows[ids[1]]["en"], rows[ids[1]]["flag"]) == ("EN 对", None)

    def test_new_source_with_new_english_is_not_flagged(self, isolated_db, monkeypatch):
        did, ids = _seed([("旧", "Old", "uncertain_translation")])
        self._run(did, _HearEngine(), monkeypatch, {"旧": "新"})
        row = _by_id(did)[ids[0]]
        assert (row["zh"], row["en"], row["flag"]) == ("新", "EN 新", None)

    def test_an_edit_made_while_it_runs_survives(self, isolated_db, monkeypatch):
        did, ids = _seed([("旧", "Old", "uncertain_translation")])

        def user_edits():
            lines_service.patch_line(did, ids[0], en="Mine")
        result = self._run(did, _HearEngine(during=user_edits), monkeypatch, {"旧": "新"})
        row = _by_id(did)[ids[0]]
        assert (row["zh"], row["en"], row["flag"]) == ("旧", "Mine", None)
        assert result["kept_your_edit"] == ids


class TestStructureWriters:
    def test_resegment_never_flags(self, isolated_db, monkeypatch):
        monkeypatch.setattr(resegment, "word_boundaries", lambda *a, **k: None)
        long = "我今天早上很早就起床了然后去公园跑步。" * 3
        did, ids = _seed([("a", "A"), (long, "translated")])
        job = _wait(restructure_service.start_resegmentation(did, ids, confirm=True)["job_id"])
        assert job["status"] == "done", job
        assert not [r for r in db.load_lines(did) if r["flag"] == FLAG]

    def test_source_reimport_replaces_lines_without_flags(self, isolated_db):
        did, ids = _seed([("旧", "Old")])
        srt = b"1\n00:00:00,000 --> 00:00:01,000\n\xe6\x96\xb0\n"
        subtitle_import_service.import_subtitle(did, srt, "a.srt", mode="source",
                                                confirm_replace_lines=True)
        rows = db.load_lines(did)
        assert [(r["zh"], r["en"], r["flag"]) for r in rows] == [("新", "", None)]

    def test_translation_import_clears(self, isolated_db):
        did, ids = _seed([("新", "Old", FLAG)])
        srt = b"1\n00:00:00,000 --> 00:00:00,900\nImported\n"
        subtitle_import_service.import_subtitle(did, srt, "a.srt", mode="translation",
                                                confirm_overwrite=True)
        row = _by_id(did)[ids[0]]
        assert (row["en"], row["flag"]) == ("Imported", None)


class TestTranslateClears:
    def test_run_clears_only_retranslated_lines(self, isolated_db):
        did, ids = _seed([("一", "Old 1", FLAG), ("二", "Old 2", FLAG), ("三", "", None)])
        out = translate_run_service.start_translate_run(did, engine_name="fake",
                                                        line_ids=[ids[0], ids[2]])
        assert _wait(out["job_id"])["status"] == "done"
        rows = _by_id(did)
        # Without force only the empty line is translated: line 1 keeps its flag.
        assert (rows[ids[0]]["en"], rows[ids[0]]["flag"]) == ("Old 1", FLAG)
        assert rows[ids[1]]["flag"] == FLAG
        out = translate_run_service.start_translate_run(did, engine_name="fake",
                                                        line_ids=[ids[0]], force_retranslate=True)
        _wait(out["job_id"])
        rows = _by_id(did)
        assert (rows[ids[0]]["en"], rows[ids[0]]["flag"]) == ("[TEST] 一", None)
        assert (rows[ids[1]]["en"], rows[ids[1]]["flag"]) == ("Old 2", FLAG)

    def test_a_failed_line_keeps_its_flag(self, isolated_db, monkeypatch):
        did, ids = _seed([("一", "Old 1", FLAG)])
        from tests import fake_engine
        monkeypatch.setattr(fake_engine.FakeEngine, "translate_batch",
                            lambda self, zh, ctx: [""] * len(zh))
        out = translate_run_service.start_translate_run(did, engine_name="fake",
                                                        force_retranslate=True)
        _wait(out["job_id"])
        row = _by_id(did)[ids[0]]
        assert (row["en"], row["flag"]) == ("Old 1", FLAG)

    def test_source_changed_again_mid_run_keeps_the_flag(self, isolated_db, monkeypatch):
        did, ids = _seed([("一", "Old 1", FLAG)])
        from tests import fake_engine
        real = fake_engine.FakeEngine.translate_batch

        def edit_source_then_translate(self, zh, ctx):
            lines_service.patch_line(did, ids[0], zh="又改了")
            return real(self, zh, ctx)
        monkeypatch.setattr(fake_engine.FakeEngine, "translate_batch", edit_source_then_translate)
        out = translate_run_service.start_translate_run(did, engine_name="fake",
                                                        force_retranslate=True)
        _wait(out["job_id"])
        row = _by_id(did)[ids[0]]
        assert (row["zh"], row["flag"]) == ("又改了", FLAG)

    def test_bulk_result_clears(self, isolated_db):
        import bulk_providers
        from tests.test_bulk_translate import (_answer_every_request, _claude_engine, _drama,
                                               _submit)
        engine = _claude_engine()
        did = _drama(isolated_db, n=2, en="Old", flag=FLAG)
        bulk_id = _submit(isolated_db, did, engine, force_retranslate=True)
        _answer_every_request(engine.client.messages.batches)
        engine.client.messages.batches.status = "ended"
        provider = bulk_providers.make_provider("claude", engine)
        assert bulk_translate.check_once(bulk_id, provider) == "applied"
        assert [(r["en"], r["flag"]) for r in db.load_lines(did)] == [
            ("EN[第0句]", None), ("EN[第1句]", None)]

    def test_save_cleared_puts_the_flag_back_when_english_was_edited(self, isolated_db):
        did, ids = _seed([("一", "Old 1", FLAG)])
        lines = db.load_line_objects(did)
        lines[0].en = "Run's"
        db.save_lines(did, lines, fields=("en",))
        source_change.clear_for_new_english(lines[0])
        lines_service.patch_line(did, ids[0], en="Mine")
        source_change.save_cleared_flags(did, lines)
        assert lines[0].flag == FLAG
        # A later flag save from the run's copies leaves the user's line alone.
        db.save_lines(did, lines, fields=("flag", "flag_note"))
        row = _by_id(did)[ids[0]]
        assert (row["en"], row["flag"]) == ("Mine", None)


class TestReviewActions:
    def test_summary_lists_flagged_ids_in_line_order(self, isolated_db):
        did, ids = _seed([("一", "A", FLAG), ("二", "B", "uncertain_translation"), ("三", "C", FLAG)])
        assert source_change_service.get_source_changed(did) == {"count": 2,
                                                                 "line_ids": [ids[0], ids[2]]}

    def test_keep_english_clears_only_source_changed(self, isolated_db):
        did, ids = _seed([("一", "A", FLAG), ("二", "B", "uncertain_translation"), ("三", "C")])
        out = source_change_service.keep_english(did, ids + [999999])
        assert out == {"cleared": 1, "line_ids": [ids[0]], "skipped_ids": ids[1:] + [999999]}
        rows = _by_id(did)
        assert [rows[i]["flag"] for i in ids] == [None, "uncertain_translation", None]
        assert [rows[i]["en"] for i in ids] == ["A", "B", "C"]

    def test_retranslate_these_runs_on_exactly_the_flagged_ids(self, isolated_db, monkeypatch):
        did, ids = _seed([("一", "Old 1", FLAG), ("二", "Old 2"), ("三", "Old 3", FLAG),
                          ("四", "Old 4", FLAG)])
        from tests import fake_engine
        seen = []
        real = fake_engine.FakeEngine.translate_batch

        def spy(self, zh, ctx):
            seen.append((list(ctx["line_ids"]), list(zh)))
            return real(self, zh, ctx)
        monkeypatch.setattr(fake_engine.FakeEngine, "translate_batch", spy)
        out = source_change_service.start_retranslate(
            did, [ids[0], ids[1], ids[2]], engine_name="fake", thinking=False)
        assert out["line_ids"] == [ids[0], ids[2]] and out["target_line_count"] == 2
        assert _wait(out["job_id"])["status"] == "done"
        assert seen == [([ids[0], ids[2]], ["一", "三"])]
        rows = _by_id(did)
        assert [(rows[i]["en"], rows[i]["flag"]) for i in ids] == [
            ("[TEST] 一", None), ("Old 2", None), ("[TEST] 三", None), ("Old 4", FLAG)]

    def test_retranslate_refuses_when_nothing_is_flagged_any_more(self, isolated_db):
        did, ids = _seed([("一", "Old 1")])
        with pytest.raises(ConflictError):
            source_change_service.start_retranslate(did, ids, engine_name="fake")

    def test_routes(self, isolated_db):
        did, ids = _seed([("一", "Old 1", FLAG), ("二", "Old 2", FLAG)])
        client = TestClient(create_app())
        got = client.get(f"/api/review/dramas/{did}/source-changed")
        assert got.status_code == 200 and got.json() == {"count": 2, "line_ids": ids}
        kept = client.post(f"/api/lines/dramas/{did}/source-changed/keep-english",
                           json={"line_ids": [ids[1]]})
        assert kept.status_code == 200 and kept.json()["line_ids"] == [ids[1]]
        run = client.post(f"/api/translate-run/dramas/{did}/source-changed/run",
                          json={"line_ids": ids, "engine": "fake"})
        assert run.status_code == 200, run.text
        assert run.json()["line_ids"] == [ids[0]]
        _wait(run.json()["job_id"])
        assert client.get(f"/api/review/dramas/{did}/source-changed").json() == {
            "count": 0, "line_ids": []}
