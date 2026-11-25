"""The activity log and daily GPU chart: rows are written when a job ends and read back per caller."""
import time

import pytest

import background_jobs
import db
from jobs import job_launch, job_runner, job_table
from services import activity_service
from tests.job_helpers import wait_for_job


def test_finished_job_is_logged(isolated_db):
    job_launch.start_job("transcribe_7", lambda: None, gpu_touching=False, description="Transcribe: A show")
    wait_for_job("transcribe_7")
    job_runner.wait_for_job_threads(5)
    items = activity_service.list_activity(None)["items"]
    assert [i["label"] for i in items] == ["Transcribe: A show"]
    assert items[0]["outcome"] == "done" and items[0]["gpu"] is False


def test_failed_and_gpu_flag(isolated_db):
    def boom():
        raise RuntimeError("no")
    job_launch.start_job("dub_3", boom, gpu_touching=True, description="Dub")
    wait_for_job("dub_3")
    job_runner.wait_for_job_threads(5)
    item = activity_service.list_activity(None)["items"][0]
    assert item["outcome"] == "error" and item["gpu"] is True


def test_requeued_run_is_not_logged(isolated_db):
    with job_table._lock:
        job_table._jobs["transcribe_9"] = {"status": "queued", "description": "x"}
    job_runner._log_activity("transcribe_9", time.time() - 5, True)
    assert activity_service.list_activity(None)["items"] == []


def test_member_sees_only_own_rows(isolated_db):
    now = time.time()
    db.add_activity("other_1", "mine", 5, "done", now - 10, now, True)
    db.add_activity("other_2", "theirs", 6, "done", now - 10, now, True)
    member = {"user_id": 5, "is_admin": False, "is_local_owner": False}
    assert [i["label"] for i in activity_service.list_activity(member)["items"]] == ["mine"]
    assert len(activity_service.list_activity(None)["items"]) == 2


def test_paging(isolated_db):
    now = time.time()
    for n in range(5):
        db.add_activity(f"other_{n}", f"job {n}", None, "done", now - 10, now, False)
    first = activity_service.list_activity(None, limit=2)
    assert [i["label"] for i in first["items"]] == ["job 4", "job 3"]
    second = activity_service.list_activity(None, limit=2, before_id=first["next_before_id"])
    assert [i["label"] for i in second["items"]] == ["job 2", "job 1"]


def test_gpu_daily_lists_every_day_and_sums_gpu_only(isolated_db):
    now = time.time()
    db.add_activity("dub_1", "a", None, "done", now - 3600, now, True)
    db.add_activity("dub_2", "b", None, "done", now - 1800, now, True)
    db.add_activity("other_1", "cpu", None, "done", now - 900, now, False)
    out = activity_service.gpu_daily(None, days=7)
    assert len(out["days"]) == 7
    assert out["days"][-1]["jobs"] == 2
    assert out["total_gpu_seconds"] == pytest.approx(5400, abs=5)


def test_old_rows_are_dropped(isolated_db):
    old = time.time() - (db.activity.KEEP_DAYS + 5) * 86400
    db.add_activity("other_1", "old", None, "done", old - 10, old, False)
    db.add_activity("other_2", "new", None, "done", time.time() - 5, time.time(), False)
    assert [i["label"] for i in activity_service.list_activity(None)["items"]] == ["new"]


def test_routes(local_client):
    now = time.time()
    db.add_activity("other_1", "Backup", None, "done", now - 30, now, False)
    body = local_client.get("/api/activity").json()
    assert body["items"][0]["label"] == "Backup" and "job_id" not in body["items"][0]
    assert len(local_client.get("/api/activity/gpu-daily?days=3").json()["days"]) == 3
