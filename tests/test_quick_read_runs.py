"""Quick read runs listed and dropped from Jobs."""

import time

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
    monkeypatch.setattr(extension_bridge_service, "_streams", {})


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


def _wait_until(check, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not check() and time.monotonic() < deadline:
        time.sleep(0.01)
    return check()


class TestPhoneGone:
    """A run whose answer stream was lost and not opened again within the grace is dropped."""

    @pytest.fixture(autouse=True)
    def _short_grace(self, monkeypatch):
        monkeypatch.setattr(extension_bridge_service, "STREAM_GONE_GRACE_SECONDS", 0.05)

    def test_a_lost_stream_not_reopened_drops_the_run_and_says_why(self):
        _start("run-aaaaaaaa")
        stream = extension_bridge_service.stream_opened("run-aaaaaaaa")
        extension_bridge_service.stream_closed("run-aaaaaaaa", stream, lost=True)
        assert _wait_until(lambda: extension_bridge_service._run_cancelled("run-aaaaaaaa"))
        assert "stopped listening" in quick_read_runs.dropped_reason("run-aaaaaaaa")
        assert quick_read_runs.list_runs(OWNER) == []

    def test_a_stream_reopened_within_the_grace_keeps_the_run(self, monkeypatch):
        monkeypatch.setattr(extension_bridge_service, "STREAM_GONE_GRACE_SECONDS", 0.2)
        _start("run-aaaaaaaa")
        first = extension_bridge_service.stream_opened("run-aaaaaaaa")
        extension_bridge_service.stream_closed("run-aaaaaaaa", first, lost=True)
        again = extension_bridge_service.stream_opened("run-aaaaaaaa")
        time.sleep(0.4)
        assert not extension_bridge_service._run_cancelled("run-aaaaaaaa")
        # Lost again later: the new loss gets its own full grace.
        extension_bridge_service.stream_closed("run-aaaaaaaa", again, lost=True)
        assert _wait_until(lambda: extension_bridge_service._run_cancelled("run-aaaaaaaa"))

    def test_another_stream_of_the_run_still_open_keeps_it(self):
        _start("run-aaaaaaaa")
        first = extension_bridge_service.stream_opened("run-aaaaaaaa")
        second = extension_bridge_service.stream_opened("run-aaaaaaaa")
        extension_bridge_service.stream_closed("run-aaaaaaaa", second, lost=True)
        time.sleep(0.2)
        assert not extension_bridge_service._run_cancelled("run-aaaaaaaa")
        extension_bridge_service.stream_closed("run-aaaaaaaa", first, lost=False)
        assert _wait_until(lambda: extension_bridge_service._run_cancelled("run-aaaaaaaa"))

    def test_answers_that_ended_normally_drop_nothing(self):
        _start("run-aaaaaaaa")
        stream = extension_bridge_service.stream_opened("run-aaaaaaaa")
        extension_bridge_service.stream_closed("run-aaaaaaaa", stream, lost=False)
        time.sleep(0.2)
        assert not extension_bridge_service._run_cancelled("run-aaaaaaaa")
        assert extension_bridge_service._streams == {}

    def test_a_run_with_nothing_left_is_not_marked(self):
        _start("run-aaaaaaaa", pages=1)
        quick_read_runs.page_finished("run-aaaaaaaa")
        stream = extension_bridge_service.stream_opened("run-aaaaaaaa")
        extension_bridge_service.stream_closed("run-aaaaaaaa", stream, lost=True)
        assert _wait_until(lambda: extension_bridge_service._streams == {})
        assert not extension_bridge_service._run_cancelled("run-aaaaaaaa")

    def test_a_late_loss_of_a_stream_older_than_the_newest_is_not_counted(self):
        _start("run-aaaaaaaa")
        old = extension_bridge_service.stream_opened("run-aaaaaaaa")
        new = extension_bridge_service.stream_opened("run-aaaaaaaa")
        extension_bridge_service.stream_closed("run-aaaaaaaa", old, lost=True)
        extension_bridge_service.stream_closed("run-aaaaaaaa", new, lost=False)
        time.sleep(0.2)
        assert not extension_bridge_service._run_cancelled("run-aaaaaaaa")
        assert extension_bridge_service._streams == {}

    def test_only_a_quick_read_that_asked_for_the_heartbeat_is_watched(self):
        watched = {"run_id": "run-aaaaaaaa", "store": False, "heartbeat": True}
        assert extension_bridge_service.stream_run_id(watched) == "run-aaaaaaaa"
        # The desktop extension streams without the heartbeat and cuts its calls itself.
        assert extension_bridge_service.stream_run_id({**watched, "heartbeat": None}) == ""
        assert extension_bridge_service.stream_run_id({**watched, "store": True}) == ""


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


def test_drop_all_skips_a_run_that_finished_meanwhile(monkeypatch):
    _start("run-aaaaaaaa")
    _start("run-bbbbbbbb", owner=None)
    real = quick_read_runs.drop_run

    def flaky(run_id, principal=None):
        if run_id == "run-aaaaaaaa":
            raise quick_read_runs.NotFoundError("gone")
        return real(run_id, principal)

    monkeypatch.setattr(quick_read_runs, "drop_run", flaky)
    assert quick_read_runs.drop_all(OWNER)["dropped_runs"] == 1


def test_pages_finishing_after_a_drop_never_push_done_past_received():
    _start("run-aaaaaaaa", pages=2)
    quick_read_runs.drop_run("run-aaaaaaaa", OWNER)
    for _ in range(5):
        quick_read_runs.page_finished("run-aaaaaaaa")
    assert quick_read_runs._runs["run-aaaaaaaa"]["done"] == 2


def test_another_account_cannot_add_pages_to_a_run_id():
    _start("run-aaaaaaaa", owner=7, pages=2)
    _start("run-aaaaaaaa", owner=8, pages=9)
    assert quick_read_runs._runs["run-aaaaaaaa"]["received"] == 2


def test_a_finished_run_logs_its_counts_and_timings(monkeypatch):
    lines = []
    monkeypatch.setattr(quick_read_runs, "get_logger", lambda: type(
        "L", (), {"info": staticmethod(lambda msg, *args: lines.append(msg % args))})())
    _start("run-bbbbbbbb", pages=2)
    quick_read_runs.page_finished("run-bbbbbbbb", cached=True)
    assert not [line for line in lines if "finished" in line]
    quick_read_runs.page_finished("run-bbbbbbbb")
    [line] = [line for line in lines if "finished" in line]
    assert "2 pages (1 from cache)" in line and "first page after" in line
    assert "site.example" not in line and "token" not in line
