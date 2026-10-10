"""A cancel requested mid-run ends these jobs without doing the remaining
work: chapter OCR, cloud re-segmentation, automatic backup, benchmark runs;
a forgotten sign-in window gives up; tesseract carries a per-page timeout.
Fakes only: no network, models, browser or real tesseract binary."""

import os
import time

import pytest

import background_jobs
import db
import ocr
import page_fetch
import resegment
import translate_engines
from core import Line
from services import auto_backup_service as abs_
from services import benchmark_lab_service as bench
from services import novel_attach_service as novel
from services import restructure_service as restructure
from services import sources_signin_service as signin


def _cancel_after(monkeypatch, checks):
    """Makes is_cancel_requested report False `checks` times, then True."""
    calls = {"n": 0}

    def fake(job_id):
        calls["n"] += 1
        return calls["n"] > checks
    monkeypatch.setattr(background_jobs, "is_cancel_requested", fake)
    return calls


class TestOcrChapter:
    def test_cancel_stops_before_the_next_page(self, monkeypatch, tmp_path):
        pages = []
        monkeypatch.setattr(ocr, "extract_text_tesseract",
                            lambda p, lang=None, tesseract_cmd=None: pages.append(p) or "text")
        _cancel_after(monkeypatch, 1)
        stage = tmp_path / "stage"
        stage.mkdir()
        with pytest.raises(background_jobs.JobCancelled):
            novel._run_ocr_job("ocrchapter_1", 1, str(stage), ["a.png", "b.png", "c.png"],
                               "tesseract", "replace", "zh", "simplified")
        assert pages == ["a.png"]
        assert not stage.exists()

    def test_tesseract_call_carries_a_timeout(self, monkeypatch, tmp_path):
        pytesseract = pytest.importorskip("pytesseract")
        from PIL import Image
        seen = {}

        def fake(image, lang=None, config=None, timeout=0):
            seen["timeout"] = timeout
            return "x"
        monkeypatch.setattr(pytesseract, "image_to_string", fake)
        path = tmp_path / "p.png"
        Image.new("L", (4, 4)).save(path)
        ocr.extract_text_tesseract(str(path))
        assert seen["timeout"] == ocr.TESSERACT_PAGE_TIMEOUT_SECONDS > 0

    def test_a_timed_out_page_is_skipped_not_fatal(self, monkeypatch, tmp_path):
        pytesseract = pytest.importorskip("pytesseract")
        from PIL import Image

        def fake(image, lang=None, config=None, timeout=0):
            raise RuntimeError("Tesseract process timeout")
        monkeypatch.setattr(pytesseract, "image_to_string", fake)
        path = tmp_path / "p.png"
        Image.new("L", (4, 4)).save(path)
        assert ocr.extract_text_tesseract(str(path)) == ""


class TestCloudResegment:
    @pytest.mark.parametrize("runner", ["apply", "preview"])
    def test_cancel_between_lines_stops_llm_calls(self, monkeypatch, runner):
        calls = []
        monkeypatch.setattr(resegment, "llm_split_spans",
                            lambda *a, **k: calls.append(1))
        lines = [Line(idx=i, start=i, end=i + 1, zh="长" * 200) for i in range(3)]
        for ln in lines:
            ln.id = ln.idx + 1
        _cancel_after(monkeypatch, 1)
        stored = []
        monkeypatch.setattr(restructure, "_apply_resegmented", lambda *a: stored.append(1))
        monkeypatch.setattr(restructure, "_store_llm_preview", lambda *a: stored.append(1))
        monkeypatch.setattr(restructure, "_usage_logger", lambda *a: None)
        engine = object()
        with pytest.raises(background_jobs.JobCancelled):
            if runner == "apply":
                restructure._run_resegment_job("resegment_1", 1, lines, [1, 2, 3], "zh", engine,
                                               "claude", None, "simplified", 0.3)
            else:
                restructure._run_llm_preview_job("resegpreview_1", 1, lines, "zh", engine,
                                                 "claude", None, "simplified", 0.3)
        assert len(calls) == 1 and not stored


class TestAutoBackup:
    def test_cancel_between_files_removes_the_partial_archive(self, isolated_db, monkeypatch):
        for n in range(4):
            with open(os.path.join(db.LIBRARY_DIR, f"media{n}.bin"), "wb") as fh:
                fh.write(b"x" * 10)
        # one check for the snapshot, then one per file
        _cancel_after(monkeypatch, 3)
        with pytest.raises(background_jobs.JobCancelled):
            abs_._backup_job(abs_.JOB_ID, True)
        assert os.listdir(abs_._target_dir(create=False)) == []


class TestSigninDeadline:
    def test_window_left_open_is_closed_after_the_deadline(self, monkeypatch):
        class Ctx:
            def wait_for_event(self, name, timeout):
                raise TimeoutError
        monkeypatch.setattr(page_fetch, "_playwright_timeout_error", lambda: TimeoutError)
        monkeypatch.setattr(page_fetch, "LOGIN_MAX_WAIT_SECONDS", 0.05)
        with pytest.raises(page_fetch.LoginWindowTimeout):
            page_fetch._wait_for_close(Ctx())

    def test_job_fails_with_a_plain_reason(self, monkeypatch):
        class Adapter:
            def login(self, url):
                raise page_fetch.LoginWindowTimeout("raw")
        monkeypatch.setattr(signin.registry, "get_adapter", lambda name: Adapter())
        results = {}
        monkeypatch.setattr(background_jobs, "set_result",
                            lambda job_id, r: results.update(r))
        with pytest.raises(signin.JobFailed, match="left open too long"):
            signin._signin_job("sources_signin_x", "x", "https://example.com/")
        assert "left open too long" in results["error"]["message"]


class TestBenchmarkCancel:
    def test_cancel_during_a_retry_stops_the_run(self, isolated_db, monkeypatch):
        flag = {"cancel": False}
        attempts = []

        class Failing:
            name = "claude"
            model = "m"
            last_usage = None

            def __init__(self, *a, **k):
                pass

            def translate_batch(self, lines, ctx):
                attempts.append(1)
                flag["cancel"] = True
                raise ValueError("boom")
        monkeypatch.setitem(translate_engines.ENGINES, "claude", Failing)
        monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda job_id: flag["cancel"])
        bench.import_golden_set("g", "你好\tHello\n谢谢\tThanks\n", "tsv", "public")
        from services import translate_service
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda *a, **k: "sk-test")
        started = bench.start_run("translation", [{"engine": "claude", "model": "claude-sonnet-5"}])
        end = time.time() + 15
        while time.time() < end:
            st = background_jobs.get_status(bench.JOB_ID)
            if st and st["status"] in ("done", "error", "cancelled"):
                break
            time.sleep(0.05)
        run = bench.get_run(started["session_ids"][0])["run"]
        assert run["status"] == "cancelled"
        assert len(attempts) == 1
