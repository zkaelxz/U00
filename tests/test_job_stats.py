"""The stats line written to app.log when a job ends."""

import os
import time

import pytest

import applog
import background_jobs
import db
from jobs import job_launch, job_runner, job_stats
from lib.error_kinds import error_kind


@pytest.mark.parametrize("text, kind", [
    ("429 Too Many Requests", "429"),
    ("the model is overloaded", "overload"),
    ("read timed out", "timeout"),
    ("12 of 12 lines got no translation (reply may have been cut off)", "blank"),
    ("reply was not valid JSON", "parse"),
    ("something odd", "other"),
    (None, "other"),
])
def test_error_kind(text, kind):
    assert error_kind(text) == kind


def _log_text():
    for h in applog.get_logger().handlers:
        h.flush()
    with open(os.path.join(db.LIBRARY_DIR, "logs", "app.log"), encoding="utf-8") as f:
        return f.read()


def test_a_finished_job_writes_one_stats_line_with_numbers_only(isolated_db):
    def work():
        import job_timing
        job_timing.mark_stage("translate_1", "Translate 3 of 9 for Moonlit Garden /home/alex/x.mkv")
        time.sleep(0.05)
        background_jobs.set_result("translate_1", {"errors": {"7": "429 Too Many Requests", "8": "no translation"}})

    job_launch.start_job("translate_1", work, line_fields=(), description="Translate (Moonlit Garden)")
    deadline = time.time() + 10
    while time.time() < deadline and "Job stats:" not in _log_text():
        time.sleep(0.05)
    job_runner.wait_for_job_threads(5)
    stats = [ln for ln in _log_text().splitlines() if "Job stats:" in ln]
    assert len(set(stats)) == 1   # the test log handler writes each record twice
    line = stats[0].split("Job stats:")[1]
    assert "kind=" in line and "status=" in line and "total_s=" in line and "queue_wait_s=" in line
    assert "429 1" in line and "blank 1" in line
    for private in ("Moonlit", "alex", ".mkv", "/", "\\", "translate_1"):
        assert private not in line, private


def test_a_stage_name_built_from_data_is_logged_as_other():
    assert job_stats._stage_label("Chapter 12: /home/a/b.mkv") == "other"
    assert job_stats._stage_label("Finish (glossary checks, version)") == "Finish (glossary checks, version)"


def test_the_daily_summary_is_logged_when_the_date_changes(isolated_db):
    job_stats._day.update(date=None, jobs=[])
    job = {"finished_at": 110.0, "queue_wait_s": 4.0, "result": {"errors": {"1": "429 Too Many Requests"}}}
    job_stats._record("translate_1", job, 100.0, "done", "2026-11-28")
    job_stats._record("translate_2", {"finished_at": 130.0}, 100.0, "error", "2026-11-28")
    assert "Daily job summary" not in _log_text()
    job_stats._record("translate_3", job, 100.0, "done", "2026-11-29")
    summary = [ln for ln in _log_text().splitlines() if "Daily job summary" in ln][0]
    assert ("Daily job summary 2026-11-28: 2 jobs; translate 2 (done 1, error 1) median 20 s; "
            "median queue wait 2.0 s; errors: 429 1") in summary


def test_the_stats_lines_pass_the_send_logs_scrub_unchanged():
    import diagnostics_report
    lines = [
        "Job stats: kind=translate status=done total_s=312.4 queue_wait_s=0.0 gpu=0 "
        "stages=Translate 300.1, Finish (glossary checks, version) 4.2 errors=429 2, blank 1",
        "Daily job summary 2026-11-28: 9 jobs; translate 4 (done 3, error 1) median 312 s; "
        "transcribe 5 (done 5) median 640 s; median queue wait 1.5 s; errors: 429 2, blank 1",
        "Text reader stats: 40 reads on the GPU, 6 on the CPU, 1 GPU loads, "
        "seconds per read median 0.6, max 18.2",
        "Live: stats (10 min): 60 chunks, median 7.4 s each, worst lag 21.0 s, "
        "0 translations skipped, errors: none",
    ]
    scrub = diagnostics_report.bundle_redactor()
    assert [scrub(line) for line in lines] == lines
