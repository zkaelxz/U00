"""
The owner's clip set in the Benchmark Lab (services/benchmark_clip_service.py)
and what it feeds: exclude and edit a case, compare only shared cases, delete
a run, CSV/JSON export, and the one transcript normaliser. Mocked: audio is a
few bytes and no Whisper model runs (the file runner is patched).
"""
import csv
import io
import json
import os
import time

import pytest

import db
from services import benchmark_clip_service as clips
from services import benchmark_export_service as export
from services import benchmark_lab_service as svc
from services import benchmark_scoring_service as scoring
from services.service_errors import ConflictError, InvalidInputError, NotFoundError
from tests.job_helpers import wait_for_job


def _audio(data=b"RIFFfake"):
    return io.BytesIO(data)


def _row(name="a.wav", **kw):
    return {"filename": name, "language": "zh", "transcript": "你好世界", "translation": "Hello world",
            "title": "Show", "tags": ["music"], **kw}


def _import(rows, set_name="mine"):
    files = {r["filename"]: _audio() for r in rows}
    return clips.import_clips(set_name, rows, files)


def _clip_files():
    return sorted(os.listdir(db.BENCHMARK_DIR)) if os.path.isdir(db.BENCHMARK_DIR) else []


def _scored_run(monkeypatch, outputs, **kw):
    """A transcription run whose output for every case comes from `outputs`
    (a callable on the case), so no model loads."""
    monkeypatch.setattr(svc, "_run_file_case", lambda stage, cfg, case, gpu, folder=None: {
        "output_text": outputs(case), "duration_seconds": 0.1})
    started = svc.start_run("transcription", [{"engine": "whisper", "model": "small"}], **kw)
    assert wait_for_job(svc.JOB_ID, timeout=15)["status"] == "done"
    return started["session_ids"][0]


class TestLanguageWarning:
    @pytest.mark.parametrize("language,text,expected", [
        ("zh", "こんにちは世界", "ja"), ("ja", "안녕하세요", "ko"), ("zh", "안녕", "ko"),
        ("ko", "ありがとう", "ja"), ("ja", "你好世界", None), ("ja", "ありがとう", None),
        ("ko", "안녕하세요", None), ("zh", "你好", None),
    ])
    def test_script_points_at_another_language(self, language, text, expected):
        message = clips.language_warning(language, text)
        assert (message is not None) == (expected is not None)
        if expected:
            assert {"ja": "Japanese", "ko": "Korean"}[expected] in message

    def test_check_names_the_clip_and_never_relabels(self):
        out = clips.check_clips([_row("one.wav"), _row("two.wav", transcript="こんにちは")])
        assert [w["clip"] for w in out["warnings"]] == ["two.wav"] and out["problems"] == []

    def test_check_reports_unusable_rows(self):
        out = clips.check_clips([_row("x.wav", language="en"), _row("y.wav", transcript=" ")])
        assert [p["clip"] for p in out["problems"]] == ["x.wav", "y.wav"]


class TestImport:
    def test_each_clip_makes_a_transcription_and_a_translation_case(self, isolated_db):
        out = _import([_row("a.wav"), _row("b.mp3", language="ja", transcript="ありがとう",
                                           translation=None, title="Show", tags="whisper, music")])
        assert (out["clips"], out["transcription_cases"], out["translation_cases"]) == (2, 2, 1)
        cases = {c["id"]: c for c in db.list_benchmark_cases()}
        trans = [c for c in cases.values() if c["stage"] == "transcription"]
        assert [c["source_language"] for c in trans] == ["zh", "ja"]
        assert [c["scene_order"] for c in trans] == [1, 2]       # order within the title
        assert json.loads(trans[1]["tags"]) == ["whisper", "music"]
        translation = next(c for c in cases.values() if c["stage"] == "translation")
        assert (translation["source_text"], translation["reference_text"]) == ("你好世界", "Hello world")
        assert translation["clip_key"] == trans[0]["clip_key"]
        for c in trans:
            assert os.path.isfile(os.path.join(db.BENCHMARK_DIR, c["input_filename"]))

    def test_names_are_generated_and_never_returned(self, isolated_db):
        out = _import([_row("secret name.wav")])
        files = _clip_files()
        assert len(files) == 1 and "secret" not in files[0] and files[0].endswith(".wav")
        shown = json.dumps([svc.list_cases()["cases"], out])
        assert files[0] not in shown and db.BENCHMARK_DIR not in shown

    def test_a_set_can_mix_languages(self, isolated_db):
        _import([_row("a.wav"), _row("b.wav", language="ko", transcript="안녕하세요", translation="Hi")])
        assert {c["source_language"] for c in db.list_benchmark_cases("transcription")} == {"zh", "ko"}

    @pytest.mark.parametrize("bad", [
        {"language": "en"}, {"transcript": ""}, {"filename": "a.txt"}, {"tags": "x" * 40},
        {"scene_order": -1}, {"title": "t" * 81},
    ])
    def test_a_refused_row_writes_nothing(self, isolated_db, bad):
        rows = [_row("ok.wav"), _row(**bad) if "filename" in bad else _row("b.wav", **bad)]
        with pytest.raises(InvalidInputError):
            _import(rows)
        assert db.list_benchmark_cases() == [] and _clip_files() == []

    def test_missing_and_repeated_audio_are_refused(self, isolated_db):
        with pytest.raises(InvalidInputError, match="wasn't sent"):
            clips.import_clips("s", [_row("a.wav")], {})
        with pytest.raises(InvalidInputError, match="already uses"):
            clips.import_clips("s", [_row("a.wav"), _row("a.wav")], {"a.wav": _audio()})

    def test_caps_on_count_and_size(self, isolated_db, monkeypatch):
        monkeypatch.setattr(clips, "MAX_CLIPS", 1)
        with pytest.raises(InvalidInputError, match="At most 1"):
            _import([_row("a.wav"), _row("b.wav")])
        monkeypatch.setattr(clips, "MAX_CLIPS", 10)
        monkeypatch.setattr(clips, "MAX_CLIP_BYTES", 4)
        with pytest.raises(InvalidInputError, match="larger than"):
            _import([_row("a.wav")])
        assert _clip_files() == []

    def test_a_failure_while_saving_removes_the_copied_audio(self, isolated_db, monkeypatch):
        def boom(rows):
            raise RuntimeError("disk full")
        monkeypatch.setattr(db, "create_benchmark_clip_cases", boom)
        with pytest.raises(RuntimeError):
            _import([_row("a.wav"), _row("b.wav")])
        assert _clip_files() == []

    def test_an_unnamed_file_is_reported_not_imported(self, isolated_db):
        out = clips.import_clips("s", [_row("a.wav")], {"a.wav": _audio(), "extra.wav": _audio()})
        assert out["clips"] == 1 and out["warnings"] == [
            {"clip": "extra.wav", "message": "No row names this file; it was not imported."}]

    def test_mismatch_warns_but_keeps_the_label(self, isolated_db):
        out = _import([_row("a.wav", transcript="こんにちは")])
        assert out["warnings"][0]["clip"] == "a.wav"
        assert db.list_benchmark_cases("transcription")[0]["source_language"] == "zh"

    def test_manifest_lines_are_clip_rows(self):
        text = '# clips\n{"audio": "c1.wav", "language": "ja", "transcript": "はい", "tags": ["asmr"]}\n'
        (row,) = clips.parse_manifest(text)
        assert row["filename"] == "c1.wav" and row["language"] == "ja"
        with pytest.raises(InvalidInputError):
            clips.parse_manifest('{"language": "ja"}')
        with pytest.raises(InvalidInputError):
            clips.parse_manifest("not json")

    def test_import_is_refused_while_a_run_is_going(self, isolated_db, monkeypatch):
        monkeypatch.setattr(svc, "_job_active", lambda: True)
        with pytest.raises(ConflictError):
            _import([_row("a.wav")])

    def test_deleting_the_case_removes_its_audio(self, isolated_db):
        _import([_row("a.wav")])
        (case,) = db.list_benchmark_cases("transcription")
        svc.delete_case(case["id"])
        assert _clip_files() == []


class TestExclude:
    def test_an_excluded_case_is_not_run_and_can_return(self, isolated_db, monkeypatch):
        _import([_row("a.wav"), _row("b.wav")])
        first, second = [c["id"] for c in db.list_benchmark_cases("transcription")]
        assert clips.set_excluded(first, True)["excluded"] is True
        assert {c["id"] for c in svc.list_cases("transcription")["cases"]} == {first, second}
        assert svc.estimate("transcription", [{"engine": "whisper", "model": "small"}])["case_count"] == 1
        sid = _scored_run(monkeypatch, lambda c: "你好世界")
        assert [r["case_id"] for r in svc.get_run(sid)["results"]] == [second]
        clips.set_excluded(first, False)
        assert svc.estimate("transcription", [{"engine": "whisper", "model": "small"}])["case_count"] == 2

    def test_sets_count_the_excluded(self, isolated_db):
        _import([_row("a.wav")])
        (case,) = db.list_benchmark_cases("transcription")
        clips.set_excluded(case["id"], True)
        (row,) = [s for s in svc.list_sets()["sets"] if s["stage"] == "transcription"]
        assert (row["case_count"], row["excluded"]) == (1, 1)

    def test_unknown_case(self, isolated_db):
        with pytest.raises(NotFoundError):
            clips.set_excluded(999, True)


class TestEditReference:
    def test_the_change_is_logged_and_versions_the_case(self, isolated_db):
        _import([_row("a.wav")])
        (case,) = db.list_benchmark_cases("transcription")
        out = clips.edit_reference(case["id"], "你好，世界！")
        assert (out["reference_text"], out["ref_version"]) == ("你好，世界！", 2)
        (edit,) = clips.list_edits(case["id"])["edits"]
        assert (edit["field"], edit["old_text"], edit["new_text"], edit["version"]) == (
            "reference_text", "你好世界", "你好，世界！", 2)
        assert edit["edited_at"]

    def test_a_corrected_transcript_reaches_the_translation_source(self, isolated_db):
        _import([_row("a.wav")])
        (case,) = db.list_benchmark_cases("transcription")
        clips.edit_reference(case["id"], "你好朋友")
        (translation,) = db.list_benchmark_cases("translation")
        assert translation["source_text"] == "你好朋友" and translation["ref_version"] == 2
        assert translation["reference_text"] == "Hello world"

    def test_same_text_changes_nothing_and_empty_is_refused(self, isolated_db):
        _import([_row("a.wav")])
        (case,) = db.list_benchmark_cases("transcription")
        assert clips.edit_reference(case["id"], "你好世界")["ref_version"] == 1
        assert clips.list_edits(case["id"])["edits"] == []
        with pytest.raises(InvalidInputError):
            clips.edit_reference(case["id"], "  ")

    def test_old_results_stay_tied_to_the_case(self, isolated_db, monkeypatch):
        _import([_row("a.wav")])
        (case,) = db.list_benchmark_cases("transcription")
        before = _scored_run(monkeypatch, lambda c: "你好世界")
        clips.edit_reference(case["id"], "你好朋友")
        after = _scored_run(monkeypatch, lambda c: "你好世界")
        old, new = svc.get_run(before)["results"][0], svc.get_run(after)["results"][0]
        assert (old["case_id"], new["case_id"]) == (case["id"], case["id"])
        assert (old["ref_version"], new["ref_version"]) == (1, 2)
        assert old["score"] == 1.0 and new["score"] < 1.0


class TestCompareSharedCases:
    def _two_runs(self, monkeypatch):
        _import([_row("a.wav"), _row("b.wav"), _row("c.wav")])
        ids = [c["id"] for c in db.list_benchmark_cases("transcription")]
        a = _scored_run(monkeypatch, lambda c: "你好世界")
        return ids, a

    def test_everything_shared_leaves_nothing_out(self, isolated_db, monkeypatch):
        ids, a = self._two_runs(monkeypatch)
        b = _scored_run(monkeypatch, lambda c: "你好世")
        view = svc.arena([a, b])
        assert [r["case_id"] for r in view["rows"]] == ids
        assert view["left_out"]["total"] == 0
        assert view["runs"][1]["delta_vs_first"] < 0

    def test_an_edited_excluded_or_deleted_case_is_left_out_and_counted(self, isolated_db, monkeypatch):
        ids, a = self._two_runs(monkeypatch)
        clips.edit_reference(ids[0], "你好朋友")
        clips.set_excluded(ids[1], True)
        b = _scored_run(monkeypatch, lambda c: "你好世界")
        view = svc.arena([a, b])
        assert [r["case_id"] for r in view["rows"]] == [ids[2]]
        assert view["left_out"] == {"total": 2, "removed": 0, "excluded": 1, "edited": 1,
                                    "not_in_every_run": 0}
        clips.set_excluded(ids[1], False)
        c = _scored_run(monkeypatch, lambda c: "你好世界")
        view = svc.arena([a, c])
        assert [r["case_id"] for r in view["rows"]] == [ids[1], ids[2]]
        assert view["left_out"] == {"total": 1, "removed": 0, "excluded": 0, "edited": 1,
                                    "not_in_every_run": 0}
        svc.delete_case(ids[2])
        view = svc.arena([a, c])
        assert view["left_out"]["removed"] == 2 and [r["case_id"] for r in view["rows"]] == [ids[1]]

    def test_totals_are_recomputed_over_the_compared_cases(self, isolated_db, monkeypatch):
        ids, a = self._two_runs(monkeypatch)
        clips.edit_reference(ids[0], "完全不同的文字")
        b = _scored_run(monkeypatch, lambda c: "你好世界")
        stored = svc.get_run(b)["run"]["aggregate_score"]
        view = svc.arena([a, b])
        # Case 0 differs by reference version, so only the other two count.
        assert view["runs"][0]["aggregate_score"] == 1.0
        assert view["runs"][1]["aggregate_score"] == 1.0 and view["runs"][1]["case_count"] == 2
        assert view["runs"][1]["stored_aggregate_score"] == stored and stored < 1.0
        assert view["runs"][1]["delta_vs_first"] == 0


class TestDeleteRun:
    def test_results_and_judge_scores_go_with_the_run(self, isolated_db, monkeypatch):
        _import([_row("a.wav")])
        sid = _scored_run(monkeypatch, lambda c: "你好世界")
        other = _scored_run(monkeypatch, lambda c: "你好世界")
        for run in (sid, other):
            db.set_benchmark_judge(run, {"status": "done"}, {
                r["case_id"]: {"overall": 1.0} for r in db.list_benchmark_results(run)})
        assert svc.delete_run(sid) == {"deleted": True, "id": sid}
        kept = db.list_benchmark_results(other)
        assert kept and all(json.loads(r["judge_scores"]) == {"overall": 1.0} for r in kept)
        assert json.loads(db.get_benchmark_session(other)["judge_meta"]) == {"status": "done"}
        assert db.get_benchmark_session(sid) is None and db.list_benchmark_results(sid) == []
        assert db.get_app_setting(f"benchmark_judge.{sid}") is None
        assert len(db.list_benchmark_cases("transcription")) == 1     # cases are untouched

    def test_unknown_run_and_running_job(self, isolated_db, monkeypatch):
        with pytest.raises(NotFoundError):
            svc.delete_run(999)
        _import([_row("a.wav")])
        sid = _scored_run(monkeypatch, lambda c: "x")
        monkeypatch.setattr(svc, "_job_active", lambda: True)
        with pytest.raises(ConflictError):
            svc.delete_run(sid)
        assert db.get_benchmark_session(sid)


class TestExport:
    def test_run_as_csv_and_json(self, isolated_db, monkeypatch):
        _import([_row("a.wav")])
        sid = _scored_run(monkeypatch, lambda c: "你好，\n世界")
        name, media, body = export.render(export.run_data(sid), "csv")
        assert name == f"benchmark-run-{sid}.csv" and media.startswith("text/csv")
        assert body.startswith(b"\xef\xbb\xbf")
        (row,) = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        assert (row["engine"], row["model"], row["language"], row["reference_version"]) == (
            "whisper", "small", "zh", "1")
        assert row["output_text"] == "你好，\n世界" and float(row["score"]) == 1.0
        assert "text_normaliser" in row["settings"]
        _name, _media, raw = export.render(export.run_data(sid), "json")
        data = json.loads(raw)
        assert data["kind"] == "run" and data["results"][0]["reference_text"] == "你好世界"

    def test_comparison_has_one_row_per_case_and_run_and_the_left_out_count(self, isolated_db, monkeypatch):
        _import([_row("a.wav"), _row("b.wav")])
        a = _scored_run(monkeypatch, lambda c: "你好世界")
        (first, _second) = [c["id"] for c in db.list_benchmark_cases("transcription")]
        clips.edit_reference(first, "你好朋友")
        b = _scored_run(monkeypatch, lambda c: "你好世界")
        data = export.comparison_data([a, b])
        assert data["compared_cases"] == 1 and data["left_out"]["edited"] == 1
        _n, _m, body = export.render(data, "csv")
        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        assert [r["run_id"] for r in rows] == [str(a), str(b)]

    def test_cells_are_not_defanged(self, isolated_db, monkeypatch):
        _import([_row("a.wav")])
        sid = _scored_run(monkeypatch, lambda c: "=SUM(1)")
        _n, _m, body = export.render(export.run_data(sid), "csv")
        assert "=SUM(1)" in body.decode("utf-8-sig") and "'=SUM" not in body.decode("utf-8-sig")

    def test_unknown_format(self, isolated_db):
        with pytest.raises(InvalidInputError):
            export.render({"kind": "run", "run": {"id": 1}, "results": []}, "xml")


class TestOneNormaliser:
    @pytest.mark.parametrize("a,b", [
        ("你好，世界。", "你好世界"), ("ＡＢＣ１２３", "abc123"), ("你好 世 界", "你好世界"),
        ("ありがとう♪", "ありがとう"), ("ｱﾘｶﾞﾄｳ", "アリガトウ"),
    ])
    def test_differences_that_are_not_recognition_errors_score_one(self, a, b):
        assert scoring.score_output("transcription", a, b, "zh")[0] == 1.0

    def test_both_scorers_agree(self, monkeypatch):
        pytest.importorskip("jiwer")
        cases = [("你好，世界。", "你好世界吗"), ("ＡＢＣ", "abd"), ("はい、そう。", "はいそうです")]
        with_jiwer = [scoring.score_output("transcription", o, r, "ja")[0] for o, r in cases]
        monkeypatch.setattr(scoring, "_jiwer_error_rate", lambda *a, **k: None)
        builtin = [scoring.score_output("transcription", o, r, "ja")[0] for o, r in cases]
        assert with_jiwer == pytest.approx(builtin)

    def test_traditional_still_folds_to_simplified_for_chinese(self):
        assert scoring.score_output("transcription", "愛你，世界", "爱你世界", "zh")[0] == 1.0

    def test_ocr_keeps_punctuation_and_width(self):
        assert scoring.score_output("ocr", "你好，世界", "你好世界")[0] < 1.0


class TestReviewFixes:
    def test_an_old_case_keeps_a_file_this_app_did_not_generate(self, isolated_db):
        os.makedirs(db.BENCHMARK_DIR, exist_ok=True)
        path = os.path.join(db.BENCHMARK_DIR, "p.png")
        with open(path, "wb") as f:
            f.write(b"png")
        case_id = db.create_benchmark_case("old", "ocr", "comic", input_filename="p.png",
                                           reference_text="x")
        svc.delete_case(case_id)
        assert os.path.exists(path)

    def test_warnings_and_problems_name_only_the_file_not_its_folders(self):
        out = clips.check_clips([_row("C:\\Users\\kae\\Music\\a.wav", transcript="こんにちは"),
                                 _row("/home/kae/b.wav", language="en")])
        assert [w["clip"] for w in out["warnings"]] == ["a.wav"]
        assert [p["clip"] for p in out["problems"]] == ["b.wav"]
        assert "kae" not in json.dumps(out)

    def test_stale_leftovers_are_swept_but_used_and_fresh_files_stay(self, isolated_db):
        _import([_row("a.wav")])
        os.makedirs(db.BENCHMARK_DIR, exist_ok=True)
        old = time.time() - 7200
        names = {"stale": "clip_0123456789abcdef.wav.part", "orphan": "clip_fedcba9876543210.mp3",
                 "fresh": "clip_aaaaaaaaaaaaaaaa.wav.part", "other": "notes.txt"}
        for key, name in names.items():
            path = os.path.join(db.BENCHMARK_DIR, name)
            with open(path, "wb") as f:
                f.write(b"x")
            if key != "fresh":
                os.utime(path, (old, old))
        before = set(_clip_files())
        _import([_row("b.wav")])
        left = set(_clip_files())
        assert names["stale"] not in left and names["orphan"] not in left
        assert names["fresh"] in left and names["other"] in left
        assert len(left - set(names.values())) == 2 and (before - set(names.values())) <= left


class TestScoringRulesAndOldReferences:
    def test_runs_scored_under_different_text_rules_are_not_compared(self, isolated_db, monkeypatch):
        _import([_row("a.wav")])
        a = _scored_run(monkeypatch, lambda c: "你好世界")
        b = _scored_run(monkeypatch, lambda c: "你好世界")
        with db.get_conn() as conn:
            conn.execute("UPDATE benchmark_sessions SET context_settings = ? WHERE id = ?",
                         (json.dumps({"script_normaliser": "x"}), a))
            conn.commit()
        with pytest.raises(InvalidInputError, match="different text rules"):
            svc.arena([a, b])

    def test_a_row_shows_the_reference_its_scores_were_made_against(self, isolated_db, monkeypatch):
        _import([_row("a.wav")])
        (case,) = db.list_benchmark_cases("transcription")
        a = _scored_run(monkeypatch, lambda c: "你好世界")
        b = _scored_run(monkeypatch, lambda c: "你好世")
        clips.edit_reference(case["id"], "你好朋友")
        # Both runs were scored on version 1, the case now reads version 2.
        (row,) = svc.arena([a, b])["rows"]
        assert (row["ref_version"], row["reference_text"]) == (1, "你好世界")
        data = export.comparison_data([a, b])
        assert {r["reference_text"] for r in data["rows"][0]["results"]} == {"你好世界"}
        assert export.run_data(a)["results"][0]["reference_text"] == "你好世界"
