"""Chapter checks when two processes (the API and Streamlit) both run the
scheduler: a cycle is claimed atomically, and a new chapter is notified
(and returned for auto-import) exactly once. Fakes only; no network."""
import threading
import time
from types import SimpleNamespace

from sources import chapter_check, registry, store

from .sources_helpers import ScriptedTransport, make_client
from .test_sources_workflows import FakeComicSource


def _ch(cid):
    return SimpleNamespace(chapter_id=cid, title=f"Ch {cid}")


def test_record_new_chapters_is_idempotent(isolated_db):
    first = store.record_new_chapters("src", "s1", [_ch("c1"), _ch("c2"), _ch("c1")])
    assert [c.chapter_id for c in first] == ["c1", "c2"]
    assert store.record_new_chapters("src", "s1", [_ch("c1"), _ch("c2")]) == []
    assert sorted(n["chapter_id"] for n in store.list_notifications()) == ["c1", "c2"]


def test_concurrent_recorders_notify_once(isolated_db):
    store.connect().close()    # create the schema before the race
    barrier = threading.Barrier(4)
    results = []

    def worker():
        barrier.wait()
        results.append(store.record_new_chapters("src", "s1", [_ch(f"c{i}") for i in range(20)]))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sum(len(r) for r in results) == 20
    notes = store.list_notifications()
    assert len(notes) == 20 and len({n["chapter_id"] for n in notes}) == 20


def test_claim_lease_and_release(isolated_db):
    t1 = store.claim_check_cycle(1000.0, lease_seconds=60)
    assert t1 is not None
    assert store.claim_check_cycle(1010.0, lease_seconds=60) is None       # still held
    t2 = store.claim_check_cycle(1100.0, lease_seconds=60)                  # expired: taken over
    assert t2 is not None
    store.release_check_cycle(t1)                 # the stale owner can't clear the new claim
    assert store.claim_check_cycle(1110.0, lease_seconds=60) is None
    store.release_check_cycle(t2)
    assert store.claim_check_cycle(1120.0, lease_seconds=60) is not None


def test_claim_min_gap_uses_last_finished_cycle(isolated_db):
    store.set_setting("last_check_cycle", 5000.0)
    assert store.claim_check_cycle(5100.0, 60, min_gap_seconds=3600) is None
    assert store.claim_check_cycle(5100.0, 60, min_gap_seconds=0) is not None


def _adapter(chapters):
    return FakeComicSource(make_client("fake_comic", ScriptedTransport()), chapters=chapters)


def test_second_cycle_while_one_runs_is_skipped(isolated_db, monkeypatch):
    monkeypatch.setattr(registry, "is_enabled", lambda name: True)
    store.track_series("fake_comic", "s1", "Series")
    gate, entered = threading.Event(), threading.Event()

    class Slow(FakeComicSource):
        def get_chapters(self, series_id):
            entered.set()
            gate.wait(10)
            return super().get_chapters(series_id)

    slow = Slow(make_client("fake_comic", ScriptedTransport()), chapters=[("c1", "1")])
    out = {}
    t = threading.Thread(target=lambda: out.update(
        a=chapter_check.run_check_cycle(adapter_factory=lambda n: slow)))
    t.start()
    assert entered.wait(10)
    other = _adapter([("c1", "1")])
    second = chapter_check.run_check_cycle(adapter_factory=lambda n: other)
    assert second["skipped"] is True and second["checked"] == 0
    gate.set()
    t.join(10)
    assert out["a"]["new"] == 1 and "skipped" not in out["a"]
    assert len(store.list_notifications()) == 1
    # released at the end: a manual cycle runs again
    assert "skipped" not in chapter_check.run_check_cycle(adapter_factory=lambda n: other)


def test_scheduled_cycle_skips_if_another_process_just_finished(isolated_db, monkeypatch):
    monkeypatch.setattr(registry, "is_enabled", lambda name: True)
    store.track_series("fake_comic", "s1", "Series")
    store.set_setting("check_interval_hours", 1)
    store.set_setting("last_check_cycle", time.time() - 60)
    called = []
    adapter = _adapter([("c1", "1")])
    factory = lambda n: called.append(n) or adapter  # noqa: E731
    assert chapter_check.run_check_cycle(adapter_factory=factory, scheduled=True)["skipped"]
    assert called == []
    store.set_setting("last_check_cycle", time.time() - 2 * 3600)
    out = chapter_check.run_check_cycle(adapter_factory=factory, scheduled=True)
    assert "skipped" not in out and called == ["fake_comic"]


def test_scheduler_loop_starts_scheduled_cycles(isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(chapter_check, "start_check_now", lambda **k: calls.append(k))
    monkeypatch.setattr(chapter_check, "_scheduler_started", False)
    monkeypatch.setattr(store, "list_tracked_series", lambda: [{"source": "x"}])
    monkeypatch.setattr(chapter_check, "check_due", lambda: True)
    chapter_check.ensure_scheduler_started(poll_seconds=3600)
    end = time.time() + 5
    while not calls and time.time() < end:
        time.sleep(0.01)
    assert calls[0] == {"scheduled": True}
