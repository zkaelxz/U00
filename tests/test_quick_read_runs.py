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


def _start(run_id, owner=None, pages=3):
    quick_read_runs.begin(run_id, owner, "https://site.example/series/ch-4?token=abc", pages,
                          extension_bridge_service.mark_cancelled)


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


def test_new_run_of_same_reader_drops_the_older_one_but_not_someone_elses():
    _start("run-aaaaaaaa", owner=7)
    _start("run-bbbbbbbb", owner=8)
    _start("run-cccccccc", owner=7)
    assert extension_bridge_service._run_cancelled("run-aaaaaaaa")
    assert not extension_bridge_service._run_cancelled("run-bbbbbbbb")
    assert not extension_bridge_service._run_cancelled("run-cccccccc")


def test_more_pages_of_a_known_run_do_not_drop_anything():
    _start("run-aaaaaaaa", owner=7)
    _start("run-cccccccc", owner=7)
    _start("run-cccccccc", owner=7)
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
