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
