"""Scene-aware translate batching: plan_batches itself, the pipeline's use of
it with id-keyed matching, and CLI/app parity on the saved setting."""
import contextlib
import io

import pytest

import background_jobs
import cli_translate
import translate_engines as te
from core import Line
from engine_backends.translate_pipeline import plan_batches
from services import settings_service
from tests.test_cli import _translate_args
from services.workspace_job_service import run_translate_job


def _timed(starts_and_gaps):
    """Lines 1s long; each entry is the pause before that line."""
    lines, t = [], 0.0
    for i, gap in enumerate(starts_and_gaps):
        t += gap
        lines.append(Line(idx=i, start=t, end=t + 1.0, zh=f"l{i}", id=100 + i))
        t += 1.0
    return lines


def _sizes(batches):
    return [len(b) for b in batches]


class TestPlanBatches:
    def test_no_gaps_matches_fixed_slices(self):
        lines = _timed([0.0] * 10)
        assert _sizes(plan_batches(lines, 4)) == [4, 4, 2]

    def test_cuts_at_the_largest_gap_in_the_last_quarter(self):
        gaps = [0.0] * 30
        gaps[17] = 4.0
        # Max 20: the tail window is cuts 15..20 and the 4s pause before line 17 wins.
        assert _sizes(plan_batches(_timed(gaps), 20)) == [17, 13]

    def test_gap_before_the_window_is_ignored(self):
        gaps = [0.0] * 30
        gaps[5] = 9.0
        assert _sizes(plan_batches(_timed(gaps), 20)) == [20, 10]

    def test_small_gaps_below_the_threshold_do_not_cut(self):
        gaps = [0.0] * 30
        gaps[17] = 1.0
        assert _sizes(plan_batches(_timed(gaps), 20)) == [20, 10]

    def test_largest_gap_wins_and_a_tie_takes_the_later_cut(self):
        gaps = [0.0] * 30
        gaps[16] = 5.0
        gaps[18] = 5.0
        gaps[19] = 3.5
        assert _sizes(plan_batches(_timed(gaps), 20)) == [18, 12]

    @pytest.mark.parametrize("n,max_size", [(0, 5), (1, 5), (1, 1), (2, 1), (5, 5), (6, 5)])
    def test_tiny_inputs(self, n, max_size):
        lines = _timed([5.0] * n)
        batches = plan_batches(lines, max_size)
        assert [ln for b in batches for ln in b] == lines
        assert all(1 <= len(b) <= max_size for b in batches)

    def test_never_empty_or_oversize_and_keeps_every_line_in_order(self):
        gaps = [(i * 7) % 5 * 1.1 for i in range(97)]
        lines = _timed(gaps)
        for max_size in (1, 2, 3, 7, 20, 60):
            batches = plan_batches(lines, max_size)
            assert [ln for b in batches for ln in b] == lines
            assert all(1 <= len(b) <= max_size for b in batches)

    def test_deterministic_and_resumable(self):
        lines = _timed([(i * 3) % 4 * 1.5 for i in range(50)])
        assert plan_batches(lines, 12) == plan_batches(list(lines), 12)
        # A resume plans only what is left: still a valid plan of those lines.
        rest = lines[17:]
        assert [ln for b in plan_batches(rest, 12) for ln in b] == rest


class _Recorder:
    """Fake engine that answers by line id, as the real engines are required to."""
    supports_reference = True
    model = "m"

    def __init__(self):
        self.batches = []

    def translate_batch(self, zh_lines, context):
        self.batches.append(list(zh_lines))
        return [f"en {z}" for z in zh_lines]


class TestPipeline:
    def test_scene_aware_run_batches_at_breaks_and_translates_every_line(self):
        gaps = [0.0] * 12
        gaps[7] = 6.0
        lines = _timed(gaps)
        engine = _Recorder()
        events = []
        te.translate_lines_with_engine(
            lines, engine, {}, batch_size=8, scene_aware_batches=True,
            detail_cb=lambda f, m: events.append(m))
        assert [len(b) for b in engine.batches] == [7, 5]
        assert all(ln.en == f"en {ln.zh}" for ln in lines)
        assert any("of 2" in m for m in events)

    def test_off_keeps_fixed_slices(self):
        gaps = [0.0] * 12
        gaps[7] = 6.0
        engine = _Recorder()
        te.translate_lines_with_engine(_timed(gaps), engine, {}, batch_size=8)
        assert [len(b) for b in engine.batches] == [8, 4]

    def test_id_keyed_matching_is_unchanged(self, monkeypatch):
        """A reply that is out of order or incomplete is matched by id, so a
        different batch layout can't shift a translation onto another line."""
        gaps = [0.0] * 12
        gaps[7] = 6.0
        lines = _timed(gaps)
        seen = []

        def fake_request(ids, build, call, max_retries):
            seen.append(list(ids))
            return {str(i): f"t{i}" for i in reversed(ids)}

        monkeypatch.setattr("engine_backends.translate_pipeline._id_keyed_batch_request",
                            fake_request)
        te.translate_lines_with_engine(lines, _Recorder(), {}, batch_size=8, reflect=True,
                                       scene_aware_batches=True)
        assert [len(ids) for ids in seen[::3]] == [7, 5]

    def test_a_resumed_run_translates_only_what_is_left(self):
        gaps = [0.0] * 12
        gaps[7] = 6.0
        lines = _timed(gaps)
        for ln in lines[:7]:
            ln.en = "done"
        engine = _Recorder()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=8,
                                       scene_aware_batches=True)
        assert engine.batches == [[f"l{i}" for i in range(7, 12)]]
        assert [ln.en for ln in lines[:7]] == ["done"] * 7


class TestCliParity:
    def _seen(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="Test", status="aligned")
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好")]
        isolated_db.save_lines(did, lines)
        monkeypatch.setattr(te, "get_engine", lambda *a, **k: object())
        seen = []

        def fake_translate(lines, engine, **kwargs):
            seen.append(kwargs)
            return lines, []
        monkeypatch.setattr(te, "translate_lines_with_engine", fake_translate)

        args = _translate_args(id=did)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_translate.cmd_translate(args)
        job_id = "test_plan_batches_parity"
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False,
                                          "result": None}
        run_translate_job(job_id, did, lines, object(), {"id": did}, "", None, False, "en-US",
                          None, "", "claude", "audio_drama")
        background_jobs._jobs.pop(job_id, None)
        return seen

    def test_default_is_on_for_both(self, isolated_db, monkeypatch):
        cli_kw, app_kw = self._seen(isolated_db, monkeypatch)
        assert cli_kw["scene_aware_batches"] is app_kw["scene_aware_batches"] is True

    def test_the_saved_setting_reaches_both(self, isolated_db, monkeypatch):
        settings_service.set_settings({"scene_aware_batches": False})
        cli_kw, app_kw = self._seen(isolated_db, monkeypatch)
        assert cli_kw["scene_aware_batches"] is app_kw["scene_aware_batches"] is False
