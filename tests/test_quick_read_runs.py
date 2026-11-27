"""Quick read runs listed and dropped from Jobs."""

import pytest

from lib.errors import NotFoundError
from services import extension_bridge_service, quick_read_runs

OWNER = {"is_local_owner": True}
MEMBER = {"user_id": 7}
OTHER = {"user_id": 8}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(quick_read_runs, "_runs", {})
    monkeypatch.setattr(extension_bridge_service, "_cancelled_runs", {})


def _start(run_id, owner=None, pages=3, device=None, url="https://site.example/series/ch-4?token=abc"):
    quick_read_runs.begin(run_id, owner, url, pages, extension_bridge_service.mark_cancelled, device=device)


def test_run_lists_site_chapter_and_pages_left_without_the_query():
    _start("run-aaaaaaaa")
    quick_read_runs.page_finished("run-aaaaaaaa")
    [run] = quick_read_runs.list_runs(OWNER)
    assert (run["site"], run["pages_left"], run["pages_received"]) == ("site.example", 2, 3)
    assert "token" not in str(run)


def test_finished_run_leaves_the_list():
    _start("run-aaaaaaaa", pages=1)
    quick_read_runs.page_finished("run-aaaaaaaa")
    assert quick_read_runs.list_runs(OWNER) == []


def test_drop_marks_run_cancelled_so_queued_pages_stop():
    _start("run-aaaaaaaa")
    assert quick_read_runs.drop_run("run-aaaaaaaa", OWNER)["dropped_pages"] == 3
    assert extension_bridge_service._run_cancelled("run-aaaaaaaa")
    assert quick_read_runs.list_runs(OWNER) == []


def test_new_run_from_the_same_device_drops_the_older_one_and_says_why():
    _start("run-aaaaaaaa", owner=7, device=1)
    _start("run-bbbbbbbb", owner=8, device=2)
    _start("run-cccccccc", owner=7, device=1)
    assert extension_bridge_service._run_cancelled("run-aaaaaaaa")
    assert "newer Quick read" in quick_read_runs.dropped_reason("run-aaaaaaaa")
    assert not extension_bridge_service._run_cancelled("run-bbbbbbbb")
    assert not extension_bridge_service._run_cancelled("run-cccccccc")


def test_a_second_device_of_the_same_account_is_left_alone():
    _start("run-aaaaaaaa", owner=7, device=1)
    _start("run-cccccccc", owner=7, device=2)
    assert not extension_bridge_service._run_cancelled("run-aaaaaaaa")


def test_without_a_device_only_a_run_on_the_same_page_is_dropped():
    _start("run-aaaaaaaa", url="https://site.example/a/ch-1")
    _start("run-bbbbbbbb", url="https://site.example/a/ch-2")
    assert not extension_bridge_service._run_cancelled("run-aaaaaaaa")
    _start("run-cccccccc", url="https://site.example/a/ch-2")
    assert extension_bridge_service._run_cancelled("run-bbbbbbbb")


def test_drop_from_jobs_records_the_reason():
    _start("run-aaaaaaaa")
    quick_read_runs.drop_run("run-aaaaaaaa", OWNER)
    assert "dropped from Jobs" in quick_read_runs.dropped_reason("run-aaaaaaaa")


def test_more_pages_of_a_known_run_do_not_drop_anything():
    _start("run-aaaaaaaa", owner=7, device=1)
    _start("run-cccccccc", owner=7, device=1)
    _start("run-cccccccc", owner=7, device=1)
    assert not extension_bridge_service._run_cancelled("run-cccccccc")


def test_member_sees_and_drops_only_their_runs():
    _start("run-aaaaaaaa", owner=7)
    _start("run-bbbbbbbb", owner=8)
    assert [r["run_id"] for r in quick_read_runs.list_runs(MEMBER)] == ["run-aaaaaaaa"]
    with pytest.raises(NotFoundError):
        quick_read_runs.drop_run("run-bbbbbbbb", MEMBER)
    assert quick_read_runs.drop_all(OTHER) == {"dropped_runs": 1, "dropped_pages": 3}
    assert extension_bridge_service._run_cancelled("run-bbbbbbbb")
    assert not extension_bridge_service._run_cancelled("run-aaaaaaaa")


def test_cancel_endpoint_also_clears_the_run_from_the_list():
    _start("run-aaaaaaaa")
    extension_bridge_service.cancel_run({"run_id": "run-aaaaaaaa"})
    assert quick_read_runs.list_runs(OWNER) == []
