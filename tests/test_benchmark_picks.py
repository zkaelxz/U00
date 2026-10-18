"""
Benchmark Lab: judge scores stored beside the results, and the owner's blind
A/B picks (services/benchmark_picks_service.py). No engines are called.
"""
import json
import random

import pytest

import db
from services import benchmark_judge_service as judge_svc
from services import benchmark_picks_service as picks
from services.service_errors import InvalidInputError, NotFoundError



def _setup(outputs_a, outputs_b):
    """Two translation runs over the same cases; outputs are lists of text."""
    case_ids = [db.create_benchmark_case(f"c{i}", "translation", "novel", source_text=f"s{i}",
                                         reference_text=f"r{i}") for i in range(len(outputs_a))]
    runs = []
    for outputs in (outputs_a, outputs_b):
        sid = db.create_benchmark_session({"stage": "translation", "engine": "x", "status": "done",
                                           "case_count": len(case_ids)})
        for cid, text in zip(case_ids, outputs):
            db.save_benchmark_result(sid, {"id": cid, "label": f"c{cid}"},
                                     {"output_text": text, "score": 0.5})
        runs.append(sid)
    return case_ids, runs


class TestJudgeScoresBesideResults:
    def test_scores_survive_a_read_and_go_with_the_run(self, isolated_db):
        ids, (a, _b) = _setup(["x", "y"], ["x", "z"])
        db.set_benchmark_judge(a, {"engine": "e", "status": "done", "summary": {}, "scored": 1},
                               {ids[0]: {"accuracy": 1.0, "tone": 1.0, "naturalness": 1.0, "overall": 1.0}})
        run = {"id": a}
        results = [{"case_id": ids[0]}, {"case_id": ids[1]}]
        judge_svc.annotate_results(run, results)
        assert results[0]["judge"]["overall"] == 1.0 and results[1]["judge"] is None
        assert judge_svc.annotate_runs([run])[0]["judge"]["status"] == "done"
        db.delete_benchmark_sessions([a])
        assert db.list_benchmark_results(a) == []

    def test_a_run_without_a_judge_pass_has_none(self, isolated_db):
        _ids, (a, _b) = _setup(["x"], ["y"])
        assert judge_svc.annotate_runs([{"id": a}])[0]["judge"] is None


class TestOldJudgeSettingsMove:
    def _legacy(self, sid, ids):
        db.set_app_setting(f"benchmark_judge.{sid}", {
            "engine": "deepseek", "model": "m", "status": "done", "same_as_tested": False,
            "cost_usd": 0.01, "note": None, "scored": 2,
            "summary": {"overall": 0.5},
            "scores": {str(ids[0]): {"accuracy": 0.9, "tone": 0.5, "naturalness": 0.7, "overall": 0.7},
                       str(ids[1]): {"accuracy": 0.1, "tone": 0.1, "naturalness": 0.1, "overall": 0.1}}})

    def test_every_score_and_the_run_summary_move_losslessly(self, isolated_db):
        ids, (a, _b) = _setup(["x", "y"], ["x", "z"])
        self._legacy(a, ids)
        before = db.get_app_setting(f"benchmark_judge.{a}")
        isolated_db.init_db()
        assert db.get_app_setting(f"benchmark_judge.{a}") is None
        results = [{"case_id": c} for c in ids]
        judge_svc.annotate_results({"id": a}, results)
        assert [r["judge"] for r in results] == [before["scores"][str(c)] for c in ids]
        run = judge_svc.annotate_runs([{"id": a}])[0]["judge"]
        assert run["engine"] == "deepseek" and run["cost_usd"] == 0.01 and run["scored"] == 2
        assert run["average"] == {"overall": 0.5}

    def test_running_the_move_twice_changes_nothing(self, isolated_db):
        ids, (a, _b) = _setup(["x", "y"], ["x", "z"])
        self._legacy(a, ids)
        isolated_db.init_db()
        first = db.list_benchmark_results(a)
        isolated_db.init_db()
        assert db.list_benchmark_results(a) == first

    def test_a_score_with_no_result_row_keeps_its_setting(self, isolated_db):
        ids, (a, _b) = _setup(["x", "y"], ["x", "z"])
        self._legacy(a, ids)
        state = db.get_app_setting(f"benchmark_judge.{a}")
        state["scores"]["999"] = {"accuracy": 1.0, "tone": 1.0, "naturalness": 1.0, "overall": 1.0}
        db.set_app_setting(f"benchmark_judge.{a}", state)
        isolated_db.init_db()
        assert db.get_app_setting(f"benchmark_judge.{a}") == state

    def test_bad_old_values_never_stop_init_db_and_keep_their_keys(self, isolated_db):
        ids, (a, _b) = _setup(["x"], ["y"])
        good = {"scores": {str(ids[0]): {"overall": 0.5}, "²": {"overall": 0.1},
                           "9" * 30: {"overall": 0.1}}}
        db.set_app_setting(f"benchmark_judge.{a}", good)
        db.set_app_setting("benchmark_judge.²", {"scores": {}})
        db.set_app_setting(f"benchmark_judge.{'9' * 30}", {"scores": {}})
        with db.get_conn() as conn:
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('benchmark_judge.77', 'not json')")
        isolated_db.init_db()
        for key in (f"benchmark_judge.{a}", "benchmark_judge.²", f"benchmark_judge.{'9' * 30}"):
            assert db.get_app_setting(key) is not None
        with db.get_conn() as conn:
            assert conn.execute("SELECT 1 FROM app_settings WHERE key = 'benchmark_judge.77'").fetchone()

    def test_a_setting_for_a_deleted_run_is_left_alone(self, isolated_db):
        db.set_app_setting("benchmark_judge.404", {"scores": {}})
        isolated_db.init_db()
        assert db.get_app_setting("benchmark_judge.404") == {"scores": {}}


class TestBlindPairs:
    def test_the_pair_names_no_run_and_either_side_can_hold_either_output(self, isolated_db):
        _ids, (a, b) = _setup(["alpha"], ["beta"])
        seen = set()
        for seed in range(20):
            pair = picks.next_pair(a, b, random.Random(seed))
            assert set(pair) == {"total", "remaining", "done", "case_id", "label", "source_text",
                                 "reference_text", "left_text", "right_text", "ticket"}
            seen.add(pair["left_text"])
        assert seen == {"alpha", "beta"}

    def test_a_pick_is_stored_against_the_run_that_made_the_chosen_output(self, isolated_db):
        _ids, (a, b) = _setup(["alpha"], ["beta"])
        pair = picks.next_pair(a, b)
        picks.record_pick(pair["ticket"], "left" if pair["left_text"] == "beta" else "right")
        [pick] = db.list_benchmark_picks()
        assert pick["winner"] == "b" and (pick["session_a"], pick["session_b"]) == (a, b)
        assert picks.next_pair(a, b)["done"] is True

    def test_same_text_and_failed_outputs_are_not_offered(self, isolated_db):
        ids, (a, b) = _setup(["same", "one", "two"], ["same", "", "three"])
        assert picks.next_pair(a, b)["total"] == 1

    def test_choosing_again_replaces_the_earlier_pick(self, isolated_db):
        _ids, (a, b) = _setup(["alpha"], ["beta"])
        pair = picks.next_pair(a, b)
        picks.record_pick(pair["ticket"], "left")
        picks.record_pick(pair["ticket"], "right")
        assert len(db.list_benchmark_picks()) == 1

    def test_refusals(self, isolated_db):
        _ids, (a, b) = _setup(["alpha"], ["beta"])
        with pytest.raises(InvalidInputError):
            picks.next_pair(a, a)
        with pytest.raises(NotFoundError):
            picks.next_pair(a, 999)
        with pytest.raises(InvalidInputError):
            picks.record_pick("not-a-ticket", "left")
        with pytest.raises(InvalidInputError):
            picks.record_pick(picks.next_pair(a, b)["ticket"], "middle")

    def test_ids_too_big_for_sqlite_are_refused_not_a_500(self, isolated_db):
        import base64
        big = 2 ** 63
        ticket = base64.urlsafe_b64encode(f"{big}:1:2".encode()).decode()
        with pytest.raises(InvalidInputError):
            picks.record_pick(ticket, "left")

    def test_picks_go_with_a_deleted_run(self, isolated_db):
        _ids, (a, b) = _setup(["alpha"], ["beta"])
        picks.record_pick(picks.next_pair(a, b)["ticket"], "tie")
        db.delete_benchmark_sessions([b])
        assert db.list_benchmark_picks() == []


class TestShownBesideScores:
    def test_totals_per_run_and_per_case(self, isolated_db):
        ids, (a, b) = _setup(["a0", "a1"], ["b0", "b1"])
        for _ in range(2):
            pair = picks.next_pair(a, b)
            winner = "a0" if pair["case_id"] == ids[0] else "b1"
            picks.record_pick(pair["ticket"], "left" if pair["left_text"] == winner else "right")
        runs = picks.annotate_runs([{"id": a}, {"id": b}])
        assert runs[0]["picks"] == {"won": 1, "lost": 1, "tie": 0}
        assert runs[1]["picks"] == {"won": 1, "lost": 1, "tie": 0}
        results = [{"case_id": ids[0]}, {"case_id": ids[1]}]
        picks.annotate_results({"id": a}, results)
        assert [r["picks"] for r in results] == [{"won": 1, "lost": 0, "tie": 0},
                                                 {"won": 0, "lost": 1, "tie": 0}]

    def test_a_run_nobody_picked_has_none(self, isolated_db):
        _ids, (a, _b) = _setup(["a0"], ["b0"])
        assert picks.annotate_runs([{"id": a}])[0]["picks"] is None

    def test_the_summary_says_whether_the_judge_agreed(self, isolated_db):
        ids, (a, b) = _setup(["a0", "a1", "a2"], ["b0", "b1", "b2"])

        def score(overall):
            return {"accuracy": overall, "tone": overall, "naturalness": overall, "overall": overall}
        db.set_benchmark_judge(a, {"status": "done"}, {ids[0]: score(0.9), ids[1]: score(0.9), ids[2]: score(0.5)})
        db.set_benchmark_judge(b, {"status": "done"}, {ids[0]: score(0.4), ids[1]: score(0.4)})
        # Owner: a wins case 0 (judge agrees), b wins case 1 (judge disagrees), tie on case 2.
        wanted = {ids[0]: "a0", ids[1]: "b1", ids[2]: None}
        for _ in range(3):
            pair = picks.next_pair(a, b)
            want = wanted[pair["case_id"]]
            picks.record_pick(pair["ticket"], "tie" if want is None
                              else "left" if pair["left_text"] == want else "right")
        out = picks.summary(b, a)
        assert out == {"run_a": a, "run_b": b, "total": 3, "picked": 3, "a_wins": 1, "b_wins": 1,
                       "ties": 1, "judge_agrees": 1, "judge_disagrees": 1, "judge_undecided": 1}
