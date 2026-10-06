"""Re-translate only the lines a glossary change affects: the service, its
two routes and the CLI flag. Fully mocked: the offline test engine or a
stub, no network."""
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import background_jobs
import bulk_translate
import cli
import db
import subtitle_formats
import translate_engines
from tests import fake_engine
from api import auth as api_auth
from api.server import create_app
from core import Line
from services import glossary_retranslate_service as svc
from services import line_provenance_service, settings_service, translate_run_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                      UnsupportedOperationError)


@pytest.fixture(autouse=True)
def _clean_jobs(monkeypatch):
    monkeypatch.setattr(translate_engines, "check_ollama_reachable", lambda url: False)
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


def _wait(job_id):
    for _ in range(400):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _seed(lines, terms=(), machine=None, series="S"):
    """lines: [(zh, en)]; terms: [dict of glossary fields]; machine: the
    indexes whose English a translate run recorded (None = all with en)."""
    sid = db.get_or_create_series(series) if series else None
    did = db.create_drama(title_zh="D", series_id=sid)
    db.save_lines(did, [Line(idx=i, start=i * 5, end=i * 5 + 4, zh=z, en=en)
                        for i, (z, en) in enumerate(lines)])
    for t in terms:
        db.upsert_glossary_term(sid, t["term_original"], t.get("term_translation", ""),
                                aliases=t.get("aliases"),
                                banned_translations=t.get("banned_translations"))
    rows = db.load_lines(did)
    marked = [r for i, r in enumerate(rows)
              if r["en"] and (machine is None or i in machine)]
    line_provenance_service.record(did, {r["id"]: (r["zh"], r["en"]) for r in marked},
                                   "fake", "test-offline", "v", "")
    return did, sid, rows


LIN = {"term_original": "林晚", "term_translation": "Lin Wan", "aliases": "晚晚",
       "banned_translations": "Lin Wanwan"}


def _by_zh(preview):
    return {ln["zh"]: ln for ln in preview["lines"]}


class TestFindAffected:
    def test_term_alias_and_banned_translation_match(self, isolated_db):
        did, _sid, _rows = _seed([
            ("林晚来了", "Lin came"),          # term in the source
            ("晚晚你好", "Hi Wanwan"),         # alias in the source
            ("她走了", "Lin Wanwan left"),     # banned translation in the English
            ("天气很好", "Nice weather"),      # unaffected
            ("林晚", ""),                      # no English yet: nothing to re-translate
        ], terms=[LIN])
        p = svc.find_glossary_affected_lines(did, engine_name="fake")
        got = _by_zh(p)
        assert set(got) == {"林晚来了", "晚晚你好", "她走了"}
        assert got["她走了"]["matched_terms"][0]["reason"] == "banned"
        assert got["林晚来了"]["matched_terms"][0]["term_translation"] == "Lin Wan"
        assert p["has_glossary"] and p["hand_edited_count"] == 0
        assert p["estimate"]["target_line_count"] == 3

    def test_series_glossary_is_shared_by_dramas_of_the_series(self, isolated_db):
        _did, sid, _ = _seed([("x", "x")], terms=[LIN])
        other = db.create_drama(title_zh="E", series_id=sid)
        db.save_lines(other, [Line(idx=0, start=0, end=4, zh="林晚", en="Lin")])
        assert [ln["zh"] for ln in svc.find_glossary_affected_lines(other)["lines"]] == ["林晚"]

    def test_no_series_means_no_glossary(self, isolated_db):
        did, _sid, _ = _seed([("林晚", "Lin")], series=None)
        p = svc.find_glossary_affected_lines(did)
        assert p["has_glossary"] is False and p["lines"] == []

    def test_term_selection(self, isolated_db):
        did, sid, _ = _seed([("林晚", "Lin"), ("苏芮", "Su")],
                            terms=[LIN, {"term_original": "苏芮", "term_translation": "Su Rui"}])
        su = next(t["id"] for t in db.list_glossary_terms(sid) if t["term_original"] == "苏芮")
        p = svc.find_glossary_affected_lines(did, term_ids=[su])
        assert [ln["zh"] for ln in p["lines"]] == ["苏芮"] and p["selected_term_ids"] == [su]
        with pytest.raises(InvalidInputError):
            svc.find_glossary_affected_lines(did, term_ids=[su + 999])

    def test_hand_edited_and_unknown_provenance(self, isolated_db):
        did, _sid, rows = _seed([("林晚一", "machine"), ("林晚二", "was machine"),
                                 ("林晚三", "never recorded")], terms=[LIN], machine={0, 1})
        # A person's edit after the machine run.
        db.update_line_fields_if(did, rows[1]["id"], {"en": "my edit"}, {"en": "was machine"})
        got = _by_zh(svc.find_glossary_affected_lines(did))
        assert got["林晚一"]["hand_edited"] is False
        assert got["林晚二"]["hand_edited"] is True
        # No provenance at all (an older line, an import, a CLI run from before): fail safe.
        assert got["林晚三"]["hand_edited"] is True

    def test_real_translate_run_counts_as_machine_made(self, isolated_db):
        did, _sid, _ = _seed([("林晚", "")], terms=[LIN], machine=set())
        _wait(translate_run_service.start_translate_run(did, engine_name="fake")["job_id"])
        [line] = svc.find_glossary_affected_lines(did)["lines"]
        assert line["hand_edited"] is False and line["en"].startswith("[TEST]")

    def test_exact_term_substitution_keeps_machine_status(self, isolated_db):
        did, sid, _ = _seed([("林晚", "")], machine=set())
        db.upsert_glossary_term(sid, "林晚", "Lin Wan", notes="[TEST]", enforce_exact=True)
        _wait(translate_run_service.start_translate_run(did, engine_name="fake")["job_id"])
        [line] = svc.find_glossary_affected_lines(did)["lines"]
        assert line["en"] == "Lin Wan 林晚" and line["hand_edited"] is False

    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            svc.find_glossary_affected_lines(999)


def _preview_ids(did, **kw):
    p = svc.find_glossary_affected_lines(did, **kw)
    return p, [ln["id"] for ln in p["lines"]]


class TestStart:
    def test_only_machine_lines_rewritten_by_default(self, isolated_db):
        did, _sid, rows = _seed([("林晚一", "old one"), ("林晚二", "my edit"), ("天气", "Weather")],
                                terms=[LIN], machine={0, 2})
        p, ids = _preview_ids(did)
        out = svc.start_affected_retranslate(did, ids, p["preview_hash"], engine_name="fake")
        assert out["line_ids"] == [rows[0]["id"]] and out["skipped_hand_edited_count"] == 1
        _wait(out["job_id"])
        after = {r["zh"]: r["en"] for r in db.load_lines(did)}
        assert after["林晚一"] == "[TEST] 林晚一"
        assert after["林晚二"] == "my edit"
        assert after["天气"] == "Weather"

    def test_include_hand_edited_replaces_them_after_a_snapshot(self, isolated_db):
        did, _sid, _ = _seed([("林晚一", "old one"), ("林晚二", "my edit")], terms=[LIN],
                             machine={0})
        p, ids = _preview_ids(did)
        out = svc.start_affected_retranslate(did, ids, p["preview_hash"], include_hand_edited=True,
                                             engine_name="fake")
        assert out["skipped_hand_edited_count"] == 0
        _wait(out["job_id"])
        assert {r["en"] for r in db.load_lines(did)} == {"[TEST] 林晚一", "[TEST] 林晚二"}
        [hist] = db.list_line_history(did)
        snap = db.get_line_history_snapshot(hist["id"])
        assert "my edit" in repr(snap)

    def test_only_hand_edited_chosen_is_refused(self, isolated_db):
        did, _sid, rows = _seed([("林晚", "my edit")], terms=[LIN], machine=set())
        p, ids = _preview_ids(did)
        with pytest.raises(UnsupportedOperationError):
            svc.start_affected_retranslate(did, ids, p["preview_hash"], engine_name="fake")
        assert db.load_lines(did)[0]["en"] == "my edit"

    def test_stale_preview_is_a_conflict(self, isolated_db):
        did, _sid, rows = _seed([("林晚", "machine")], terms=[LIN])
        p, ids = _preview_ids(did)
        db.update_line_fields_if(did, rows[0]["id"], {"en": "edited since"}, {"en": "machine"})
        with pytest.raises(ConflictError):
            svc.start_affected_retranslate(did, ids, p["preview_hash"], engine_name="fake")
        assert db.load_lines(did)[0]["en"] == "edited since"

    def test_glossary_change_after_preview_is_a_conflict(self, isolated_db):
        did, sid, _ = _seed([("林晚", "machine")], terms=[LIN])
        p, ids = _preview_ids(did)
        db.upsert_glossary_term(sid, "苏芮", "Su Rui")
        # A new term that matches nothing still changes the glossary used.
        with pytest.raises(ConflictError):
            svc.start_affected_retranslate(did, ids, p["preview_hash"], engine_name="fake")
        p, ids = _preview_ids(did)
        db.upsert_glossary_term(sid, "林晚", "Lin Waner", aliases="晚晚")
        # The preview showed the old rendering.
        with pytest.raises(ConflictError):
            svc.start_affected_retranslate(did, ids, p["preview_hash"], engine_name="fake")

    def test_ids_are_revalidated(self, isolated_db):
        did, _sid, rows = _seed([("林晚", "machine"), ("天气", "Weather")], terms=[LIN])
        other, _s, other_rows = _seed([("林晚", "x")], terms=[LIN], series="T")
        p, ids = _preview_ids(did)
        with pytest.raises(InvalidInputError):   # another drama's line
            svc.start_affected_retranslate(did, ids + [other_rows[0]["id"]], p["preview_hash"],
                                           engine_name="fake")
        with pytest.raises(InvalidInputError):   # this drama's, but not affected
            svc.start_affected_retranslate(did, ids + [rows[1]["id"]], p["preview_hash"],
                                           engine_name="fake")
        with pytest.raises(InvalidInputError):
            svc.start_affected_retranslate(did, [], p["preview_hash"], engine_name="fake")
        assert db.load_lines(did)[0]["en"] == "machine"
        assert db.load_lines(other)[0]["en"] == "x"

    def test_only_en_of_the_chosen_lines_is_written(self, isolated_db, monkeypatch):
        did, _sid, rows = _seed([("林晚一", "one"), ("林晚二", "two")], terms=[LIN])
        db.save_lines(did, [Line(idx=r["idx"], start=r["start"], end=r["end"], zh=r["zh"],
                                 en=r["en"], speaker="Lin", flag="uncertain_translation",
                                 flag_note="check", speaker_manual=True, sfx=True, id=r["id"])
                            for r in rows])
        db.save_translation_notes(did, [{"line_id": rows[0]["id"], "term": "林晚",
                                         "note_type": "name", "note": "a note"}])
        calls = []
        real = db.save_lines

        def spy(d, ls, fields=None, **kw):
            calls.append(fields)
            return real(d, ls, fields=fields, **kw)
        monkeypatch.setattr(db, "save_lines", spy)
        p, _ids = _preview_ids(did)
        _wait(svc.start_affected_retranslate(did, [rows[0]["id"]], p["preview_hash"],
                                             engine_name="fake")["job_id"])
        assert calls and None not in calls
        after = {r["id"]: r for r in db.load_lines(did)}
        first, second = after[rows[0]["id"]], after[rows[1]["id"]]
        assert first["en"] == "[TEST] 林晚一" and second["en"] == "two"
        for r in (first, second):
            assert (r["speaker"], r["flag"], r["flag_note"], r["speaker_manual"], r["sfx"]) == \
                ("Lin", "uncertain_translation", "check", 1, 1)
        assert [n["note"] for n in db.list_translation_notes(did)] == ["a note"]

    def test_exact_terms_are_substituted_only_in_the_chosen_lines(self, isolated_db):
        did, sid, rows = _seed([("林晚一", "Lin one"), ("林晚二", "Lin two")], terms=[LIN])
        db.upsert_glossary_term(sid, "林晚", "Lin Wan", notes="Lin", enforce_exact=True)
        p, _ids = _preview_ids(did)
        _wait(svc.start_affected_retranslate(did, [rows[0]["id"]], p["preview_hash"],
                                             engine_name="fake")["job_id"])
        assert db.load_lines(did)[1]["en"] == "Lin two"

    def test_edit_saved_while_the_job_runs_is_kept(self, isolated_db, monkeypatch):
        did, _sid, rows = _seed([("林晚", "machine")], terms=[LIN])
        entered, release = threading.Event(), threading.Event()

        class Slow(fake_engine.FakeEngine):
            def translate_batch(self, zh_lines, context):
                entered.set()
                release.wait(5)
                return super().translate_batch(zh_lines, context)
        monkeypatch.setitem(translate_engines.ENGINES, "fake", Slow)
        p, ids = _preview_ids(did)
        out = svc.start_affected_retranslate(did, ids, p["preview_hash"], engine_name="fake")
        assert entered.wait(5)
        db.update_line_fields_if(did, rows[0]["id"], {"en": "edited meanwhile"}, {"en": "machine"})
        release.set()
        _wait(out["job_id"])
        assert db.load_lines(did)[0]["en"] == "edited meanwhile"
        # ...and the line now reads as hand-edited, so a later preview leaves it out.
        assert svc.find_glossary_affected_lines(did)["lines"][0]["hand_edited"] is True

    def test_cancel_stops_before_the_next_batch(self, isolated_db, monkeypatch):
        did, _sid, rows = _seed([(f"林晚{i}", f"old {i}") for i in range(4)], terms=[LIN])
        entered, release = threading.Event(), threading.Event()

        class Slow(fake_engine.FakeEngine):
            def translate_batch(self, zh_lines, context):
                entered.set()
                release.wait(5)
                return super().translate_batch(zh_lines, context)
        monkeypatch.setitem(translate_engines.ENGINES, "fake", Slow)
        p, ids = _preview_ids(did)
        out = svc.start_affected_retranslate(did, ids, p["preview_hash"],
                                             engine_name="fake", batch_size=1)
        assert entered.wait(5)
        background_jobs.request_cancel(out["job_id"])
        release.set()
        _wait(out["job_id"])
        ens = [r["en"] for r in db.load_lines(did)]
        assert ens[0] == "[TEST] 林晚0"
        assert ens[1:] == ["old 1", "old 2", "old 3"]

    def test_monthly_cap_refusal_and_job_cap_forwarded(self, isolated_db, monkeypatch):
        did, _sid, _ = _seed([("林晚", "machine")], terms=[LIN])
        real = settings_service.resolve_key
        monkeypatch.setattr(settings_service, "resolve_key",
                            lambda k, *a, **kw: {"monthly_cap_usd": "1", "claude": "sk-ant-FAKE"}.get(
                                k, real(k, *a, **kw)))
        monkeypatch.setattr(db, "get_month_spend", lambda *a, **kw: 5.0)
        p, ids = _preview_ids(did)
        with pytest.raises(UnsupportedOperationError):
            svc.start_affected_retranslate(did, ids, p["preview_hash"], engine_name="claude")
        seen = {}
        monkeypatch.setattr(translate_run_service, "start_translate_run",
                            lambda *a, **kw: seen.update(kw) or {"job_id": "j"})
        svc.start_affected_retranslate(did, ids, p["preview_hash"], engine_name="claude",
                                       job_cost_cap_usd=0.25)
        assert seen["job_cost_cap_usd"] == 0.25 and seen["force_retranslate"] is True
        assert seen["own_lines_only"] is True and seen["line_ids"] == ids


def _slow_engine(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class Slow(fake_engine.FakeEngine):
        def translate_batch(self, zh_lines, context):
            entered.set()
            release.wait(5)
            return super().translate_batch(zh_lines, context)
    monkeypatch.setitem(translate_engines.ENGINES, "fake", Slow)
    return entered, release


class TestOwnLinesOnly:
    """A line the user edits while the re-translate runs stays exactly as edited."""

    def test_mid_run_edit_gets_no_exact_term_substitution(self, isolated_db, monkeypatch):
        did, sid, rows = _seed([("林晚", "machine")], terms=[LIN])
        db.upsert_glossary_term(sid, "林晚", "Lin Wan", notes="Lynn", enforce_exact=True)
        entered, release = _slow_engine(monkeypatch)
        p, ids = _preview_ids(did)
        out = svc.start_affected_retranslate(did, ids, p["preview_hash"], engine_name="fake")
        assert entered.wait(5)
        # The edit uses the term's notes variant, which the substitution would rewrite.
        db.update_line_fields_if(did, rows[0]["id"], {"en": "Lynn says hi"}, {"en": "machine"})
        release.set()
        _wait(out["job_id"])
        assert db.load_lines(did)[0]["en"] == "Lynn says hi"

    def test_edit_after_the_save_gets_no_exact_term_substitution(self, isolated_db, monkeypatch):
        did, sid, rows = _seed([("林晚", "machine")], terms=[LIN])
        db.upsert_glossary_term(sid, "林晚", "Lin Wan", notes="Lynn", enforce_exact=True)
        real_finish = bulk_translate.finish_translation_run
        edited = []

        def finish(drama_id, lines, *a, **kw):
            edited.append(db.update_line_fields_if(drama_id, rows[0]["id"], {"en": "Lynn waves"},
                                                   {"en": "[TEST] 林晚"}))
            return real_finish(drama_id, lines, *a, **kw)
        monkeypatch.setattr(bulk_translate, "finish_translation_run", finish)
        p, ids = _preview_ids(did)
        _wait(svc.start_affected_retranslate(did, ids, p["preview_hash"],
                                             engine_name="fake")["job_id"])
        assert edited and edited[0]
        assert db.load_lines(did)[0]["en"] == "Lynn waves"

    def test_edit_right_after_the_final_read_gets_no_exact_term_substitution(
            self, isolated_db, monkeypatch):
        did, sid, rows = _seed([("林晚", "machine")], terms=[LIN])
        db.upsert_glossary_term(sid, "林晚", "Lin Wan", notes="Lynn", enforce_exact=True)
        real_finish, real_load = bulk_translate.finish_translation_run, db.load_line_objects
        edited = []

        def load_then_edit(drama_id, *a, **kw):
            out = real_load(drama_id, *a, **kw)
            if not edited:
                # The user saves an edit just after finish reads the lines.
                edited.append(db.update_line_fields_if(
                    drama_id, rows[0]["id"], {"en": "Lynn waves"}, {"en": "[TEST] 林晚"}))
            return out

        def finish(*a, **kw):
            monkeypatch.setattr(db, "load_line_objects", load_then_edit)
            return real_finish(*a, **kw)
        monkeypatch.setattr(bulk_translate, "finish_translation_run", finish)
        p, ids = _preview_ids(did)
        _wait(svc.start_affected_retranslate(did, ids, p["preview_hash"],
                                             engine_name="fake")["job_id"])
        assert edited and edited[0]
        assert db.load_lines(did)[0]["en"] == "Lynn waves"

    def test_mid_run_edit_gets_no_flag_from_the_run(self, isolated_db, monkeypatch):
        did, _sid, rows = _seed([("林晚一", "one"), ("林晚二", "two")], terms=[LIN])

        def flag_all(lines, field="en", mode="normal"):
            for ln in lines:
                ln.flag, ln.flag_note = "dense", "too fast"
            return len(lines)
        monkeypatch.setattr(subtitle_formats, "flag_dense_lines", flag_all)
        entered, release = _slow_engine(monkeypatch)
        p, ids = _preview_ids(did)
        out = svc.start_affected_retranslate(did, ids, p["preview_hash"], engine_name="fake")
        assert entered.wait(5)
        db.update_line_fields_if(did, rows[1]["id"], {"en": "my edit"}, {"en": "two"})
        release.set()
        _wait(out["job_id"])
        after = {r["id"]: r for r in db.load_lines(did)}
        assert (after[rows[0]["id"]]["en"], after[rows[0]["id"]]["flag"]) == ("[TEST] 林晚一", "dense")
        assert after[rows[1]["id"]]["en"] == "my edit"
        assert not after[rows[1]["id"]]["flag"] and not after[rows[1]["id"]]["flag_note"]

    def test_edit_right_after_the_flag_read_gets_no_flag_and_is_reported(
            self, isolated_db, monkeypatch):
        did, _sid, rows = _seed([("林晚一", "one"), ("林晚二", "two")], terms=[LIN])

        def flag_all(lines, field="en", mode="normal"):
            for ln in lines:
                ln.flag, ln.flag_note = "dense", "too fast"
            return len(lines)
        monkeypatch.setattr(subtitle_formats, "flag_dense_lines", flag_all)
        real_finish, real_load = bulk_translate.finish_translation_run, db.load_line_objects
        edited = []

        def load_then_edit(drama_id, *a, **kw):
            out = real_load(drama_id, *a, **kw)
            if not edited:
                # The user saves an edit just after finish reads the lines,
                # before the density flags are saved.
                edited.append(db.update_line_fields_if(
                    drama_id, rows[1]["id"], {"en": "my edit"}, {"en": "[TEST] 林晚二"}))
            return out

        def finish(*a, **kw):
            monkeypatch.setattr(db, "load_line_objects", load_then_edit)
            return real_finish(*a, **kw)
        monkeypatch.setattr(bulk_translate, "finish_translation_run", finish)
        p, ids = _preview_ids(did)
        job = _wait(svc.start_affected_retranslate(did, ids, p["preview_hash"],
                                                   engine_name="fake")["job_id"])
        assert edited and edited[0]
        after = {r["id"]: r for r in db.load_lines(did)}
        assert (after[rows[0]["id"]]["en"], after[rows[0]["id"]]["flag"],
                after[rows[0]["id"]]["flag_note"]) == ("[TEST] 林晚一", "dense", "too fast")
        assert after[rows[1]["id"]]["en"] == "my edit"
        assert not after[rows[1]["id"]]["flag"] and not after[rows[1]["id"]]["flag_note"]
        assert job["result"]["flags_needing_recheck"] == [rows[1]["id"]]
        from services import jobs_service
        projected = jobs_service.project_result(job["result"])
        assert projected["flags_needing_recheck"] == [rows[1]["id"]]
        assert "recheck" in jobs_service.derive_outcome("done", None, projected)[1]

    @pytest.mark.parametrize("edit,expected", [
        ({"start": 40.0, "end": 44.5}, {"start": 5.0, "end": 9.0}),
        ({"flag": "manual", "flag_note": "check"}, {"flag": "", "flag_note": ""}),
    ], ids=["timing-only", "flag-changed"])
    def test_timing_or_flag_change_after_the_read_skips_the_flag_and_reports(
            self, isolated_db, monkeypatch, edit, expected):
        did, _sid, rows = _seed([("林晚一", "one"), ("林晚二", "two")], terms=[LIN])

        def flag_all(lines, field="en", mode="normal"):
            for ln in lines:
                ln.flag, ln.flag_note = "dense", "too fast"
            return len(lines)
        monkeypatch.setattr(subtitle_formats, "flag_dense_lines", flag_all)
        real_finish, real_load = bulk_translate.finish_translation_run, db.load_line_objects
        edited = []

        def load_then_edit(drama_id, *a, **kw):
            out = real_load(drama_id, *a, **kw)
            if not edited:
                edited.append(db.update_line_fields_if(drama_id, rows[1]["id"], edit, expected))
            return out

        def finish(*a, **kw):
            monkeypatch.setattr(db, "load_line_objects", load_then_edit)
            return real_finish(*a, **kw)
        monkeypatch.setattr(bulk_translate, "finish_translation_run", finish)
        p, ids = _preview_ids(did)
        job = _wait(svc.start_affected_retranslate(did, ids, p["preview_hash"],
                                                   engine_name="fake")["job_id"])
        assert edited and edited[0]
        after = {r["id"]: r for r in db.load_lines(did)}
        assert after[rows[0]["id"]]["flag"] == "dense"
        assert after[rows[1]["id"]]["flag"] != "dense"
        if "flag" in edit:
            assert after[rows[1]["id"]]["flag"] == "manual"
        else:
            assert (after[rows[1]["id"]]["start"], after[rows[1]["id"]]["end"]) == (40.0, 44.5)
            assert not after[rows[1]["id"]]["flag"]
        assert after[rows[1]["id"]]["en"] == "[TEST] 林晚二"
        assert job["result"]["flags_needing_recheck"] == [rows[1]["id"]]

    def test_flag_save_guards_the_lines_timing_as_well_as_its_text(self, isolated_db, monkeypatch):
        # Dropping start/end from guard_fields would let a timing edit
        # through; this fails then.
        did, _sid, rows = _seed([("林晚一", "one")], terms=[LIN])
        calls = []
        real_save = db.save_lines

        def spy(drama_id, lines, **kw):
            calls.append(kw)
            return real_save(drama_id, lines, **kw)
        monkeypatch.setattr(db, "save_lines", spy)
        lines = db.load_line_objects(did)
        bulk_translate.finish_translation_run(
            did, lines, None, "fake", "", [], [],
            enforce_ids={rows[0]["id"]}, flags_needing_recheck=set())
        guarded = [kw for kw in calls if kw.get("only_if_unchanged")
                   and kw.get("fields") == ("flag", "flag_note")]
        assert guarded and set(guarded[0]["guard_fields"]) >= {"en", "start", "end"}

    def test_flag_guard_treats_empty_and_missing_english_alike(self, isolated_db):
        # A content-blocked line has no English: its flag must still be saved.
        did, _sid, _rows = _seed([("一", "")], series=None)
        [ln] = db.load_line_objects(did)
        ln.en, ln.flag = None, "content_blocked"
        assert not db.save_lines(did, [ln], fields=("flag",), only_if_unchanged=True,
                                 guard_fields=("en", "start", "end"))
        assert db.load_lines(did)[0]["flag"] == "content_blocked"

    def test_reflect_note_is_dropped_for_a_skipped_write(self, isolated_db):
        did, _sid, rows = _seed([("一", "one"), ("二", "two")], series=None)
        lines = db.load_line_objects(did)
        saved = []
        save_cb, notes_cb = bulk_translate.own_lines_callbacks(did, lines, saved.extend)
        # A Reflect critique arrives before its batch is saved.
        notes_cb([{"line_idx": ln.idx, "term": "", "note_type": "reflection",
                   "note": f"critique {ln.idx}"} for ln in lines])
        assert db.list_translation_notes(did) == []
        db.update_line_fields_if(did, rows[1]["id"], {"en": "my edit"}, {"en": "two"})
        for ln in lines:
            ln.en = f"new {ln.idx}"
        save_cb(lines)
        assert [r["en"] for r in db.load_lines(did)] == ["new 0", "my edit"]
        assert [(n["line_id"], n["note"]) for n in db.list_translation_notes(did)] == \
            [(rows[0]["id"], "critique 0")]
        assert [ln.id for ln in saved] == [rows[0]["id"]]

    def test_edit_between_selection_and_load_is_kept(self, isolated_db, monkeypatch):
        did, _sid, rows = _seed([("林晚一", "one"), ("林晚二", "two")], terms=[LIN])
        p, ids = _preview_ids(did)
        real = svc._affected

        def affected_then_edit(*a, **kw):
            out = real(*a, **kw)
            db.update_line_fields_if(did, rows[1]["id"], {"en": "my edit"}, {"en": "two"})
            return out
        monkeypatch.setattr(svc, "_affected", affected_then_edit)
        out = svc.start_affected_retranslate(did, ids, p["preview_hash"], engine_name="fake")
        assert out["target_line_count"] == 1
        # The response names only the line the run started; the edited one is skipped.
        assert out["line_ids"] == [rows[0]["id"]] and out["skipped_hand_edited_count"] == 1
        _wait(out["job_id"])
        assert [r["en"] for r in db.load_lines(did)] == ["[TEST] 林晚一", "my edit"]

    def test_every_chosen_line_edited_before_load_is_a_conflict(self, isolated_db, monkeypatch):
        did, _sid, rows = _seed([("林晚", "one")], terms=[LIN])
        p, ids = _preview_ids(did)
        real = svc._affected

        def affected_then_edit(*a, **kw):
            out = real(*a, **kw)
            db.update_line_fields_if(did, rows[0]["id"], {"en": "my edit"}, {"en": "one"})
            return out
        monkeypatch.setattr(svc, "_affected", affected_then_edit)
        with pytest.raises(ConflictError):
            svc.start_affected_retranslate(did, ids, p["preview_hash"], engine_name="fake")
        assert db.load_lines(did)[0]["en"] == "my edit"

    def test_preview_term_deleted_since_is_a_conflict(self, isolated_db, monkeypatch):
        did, sid, _ = _seed([("林晚", "Lin"), ("苏芮", "Su")],
                            terms=[LIN, {"term_original": "苏芮", "term_translation": "Su Rui"}])
        su = next(t["id"] for t in db.list_glossary_terms(sid) if t["term_original"] == "苏芮")
        p, ids = _preview_ids(did, term_ids=[su])
        db.delete_glossary_term(su)
        with pytest.raises(ConflictError) as err:
            svc.start_affected_retranslate(did, ids, p["preview_hash"], term_ids=[su],
                                           engine_name="fake")
        assert err.value.details == {"reason": "stale_preview"}
        # Read once: the term list that decides staleness is the one the run uses.
        calls = []
        real = db.list_glossary_terms
        monkeypatch.setattr(db, "list_glossary_terms",
                            lambda *a, **kw: calls.append(1) or real(*a, **kw))
        with pytest.raises(ConflictError):
            svc.start_affected_retranslate(did, ids, p["preview_hash"], term_ids=[su],
                                           engine_name="fake")
        assert len(calls) == 1


class TestApi:
    def test_routes_declare_one_permission(self):
        found = {(tuple(sorted(m)), path): decls
                 for _r, path, m, decls in api_auth.iter_route_declarations(create_app())
                 if "glossary-affected" in path}
        assert found == {
            (("GET",), "/api/translate-run/dramas/{drama_id}/glossary-affected"):
                [("permission", "lines.read")],
            (("POST",), "/api/translate-run/dramas/{drama_id}/glossary-affected/run"):
                [("permission", "jobs.start")],
        }

    def test_preview_and_run(self, isolated_db):
        did, _sid, rows = _seed([("林晚", "machine"), ("晚晚", "mine")], terms=[LIN],
                                machine={0})
        client = TestClient(create_app())
        base = f"/api/translate-run/dramas/{did}/glossary-affected"
        assert client.get("/api/translate-run/dramas/999/glossary-affected").status_code == 404
        r = client.get(base, params={"engine": "fake"})
        assert r.status_code == 200
        p = r.json()
        assert [ln["hand_edited"] for ln in p["lines"]] == [False, True]
        ids = [ln["id"] for ln in p["lines"]]
        body = {"line_ids": ids, "preview_hash": p["preview_hash"], "engine": "fake"}
        assert client.post(f"{base}/run", json={**body, "bogus": 1}).status_code == 422
        stale = client.post(f"{base}/run", json={**body, "preview_hash": "stale"})
        assert stale.status_code == 409
        assert stale.json()["error"]["details"] == {"reason": "stale_preview"}
        r = client.post(f"{base}/run", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["line_ids"] == [rows[0]["id"]]
        assert r.json()["skipped_hand_edited_count"] == 1
        _wait(r.json()["job_id"])
        assert [x["en"] for x in db.load_lines(did)] == ["[TEST] 林晚", "mine"]


class TestCli:
    def _args(self, did, **kw):
        base = dict(id=did, status=None, engine="fake", api_key=None, model=None,
                    episode_summary_engine="ollama", episode_summary_api_key=None,
                    style_note=None, style_preset=None, locale=None, female_pronouns=False,
                    no_genre_notes=False, force=False, ollama_num_ctx=None, ollama_url=None,
                    reflect=False, cost_cap=None, monthly_cap=0.0, context_window=None,
                    context_window_ahead=None, batch_size=None, fallback=None,
                    glossary_affected=True, include_hand_edited=False)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_glossary_affected_skips_hand_edited(self, isolated_db, monkeypatch):
        monkeypatch.setattr(translate_engines, "get_engine",
                            lambda name, *a, **k: fake_engine.FakeEngine())
        did, _sid, _ = _seed([("林晚一", "old"), ("林晚二", "my edit"), ("天气", "Weather")],
                             terms=[LIN], machine={0, 2})
        cli.cmd_translate(self._args(did))
        assert [r["en"] for r in db.load_lines(did)] == ["[TEST] 林晚一", "my edit", "Weather"]
        cli.cmd_translate(self._args(did, include_hand_edited=True))
        assert [r["en"] for r in db.load_lines(did)] == ["[TEST] 林晚一", "[TEST] 林晚二", "Weather"]

    def test_glossary_affected_needs_id(self, isolated_db):
        with pytest.raises(SystemExit):
            cli.cmd_translate(self._args(None))
        with pytest.raises(SystemExit):
            cli.cmd_translate(self._args(1, glossary_affected=False, include_hand_edited=True))


    def _two_terms(self):
        wan = {"term_original": "林晚", "term_translation": "Lin Wan"}
        zhou = {"term_original": "周然", "term_translation": "Zhou Ran"}
        did, sid, _ = _seed([("林晚一", "a"), ("周然二", "b"), ("天气", "c")], terms=[wan, zhou])
        ids = {t["term_original"]: t["id"] for t in db.list_glossary_terms(sid)}
        return did, ids

    def _run(self, monkeypatch, did, **kw):
        monkeypatch.setattr(translate_engines, "get_engine",
                            lambda name, *a, **k: fake_engine.FakeEngine())
        cli.cmd_translate(self._args(did, **kw))
        return [r["en"] for r in db.load_lines(did)]

    def test_term_by_text_selects_only_its_lines(self, isolated_db, monkeypatch, capsys):
        did, ids = self._two_terms()
        assert self._run(monkeypatch, did, term=["林晚"]) == ["[TEST] 林晚一", "b", "c"]
        assert f"glossary terms: {ids['林晚']}; 1 lines selected" in capsys.readouterr().out

    def test_term_by_id_and_repeat(self, isolated_db, monkeypatch):
        did, ids = self._two_terms()
        assert self._run(monkeypatch, did, term=[str(ids["周然"])]) == ["a", "[TEST] 周然二", "c"]
        assert self._run(monkeypatch, did, term=["林晚", str(ids["周然"])]) == [
            "[TEST] 林晚一", "[TEST] 周然二", "c"]

    def test_unknown_term_errors(self, isolated_db, monkeypatch):
        did, _ids = self._two_terms()
        for ref in ("nope", "99999"):
            with pytest.raises(SystemExit, match="matches no term"):
                self._run(monkeypatch, did, term=[ref])
        assert [r["en"] for r in db.load_lines(did)] == ["a", "b", "c"]

    def test_ambiguous_term_errors(self, isolated_db, monkeypatch):
        # A term whose source text is another term's id matches both ways.
        did, sid, _ = _seed([("林晚一", "a")], terms=[LIN])
        lin_id = db.list_glossary_terms(sid)[0]["id"]
        db.upsert_glossary_term(sid, str(lin_id), "Digits")
        with pytest.raises(SystemExit, match="matches 2 glossary terms"):
            self._run(monkeypatch, did, term=[str(lin_id)])

    def test_term_requires_glossary_affected(self, isolated_db):
        with pytest.raises(SystemExit, match="--term only applies"):
            cli.cmd_translate(self._args(1, glossary_affected=False, term=["林晚"]))

    def test_matches_service_selection(self, isolated_db, monkeypatch):
        did, ids = self._two_terms()
        expected = {ln["id"] for ln in svc.find_glossary_affected_lines(
            did, term_ids=[ids["周然"]])["lines"]}
        assert expected == set(svc.affected_line_ids(did, term_ids=[ids["周然"]]))
        before = {r["id"]: r["en"] for r in db.load_lines(did)}
        self._run(monkeypatch, did, term=["周然"])
        changed = {r["id"] for r in db.load_lines(did) if r["en"] != before[r["id"]]}
        assert changed == expected
