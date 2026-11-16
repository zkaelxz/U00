"""Tests for library.db connections across threads and processes: leak recovery, nesting, leaked-connection cleanup and the GPU lock."""

import contextlib
import gc
import sqlite3
import sys
import os

import threading
import time
import weakref

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import gpu_slots


class TestConnectionLeakRecovery:
    """Regression tests for a real bug: a statement raising between
    get_conn() and conn.close() leaked the connection, and under WAL that
    made every subsequent write fail with 'database is locked' -- one bad
    call poisoned the whole session until restart. Step 69 wrapped every
    public function's own get_conn()/close() pair in try/finally, so a
    failure inside one of them (like log_usage below) no longer leaks a
    connection at all -- see TestLeakedConnectionCleanup for the
    still-needed last-resort net this leaves in get_conn() itself, for a
    caller that opens one directly without going through that pattern."""

    def test_healthy_calls_leave_no_open_connections(self, isolated_db):
        did = isolated_db.create_drama(title_en="X")
        for _ in range(5):
            isolated_db.list_dramas()
            isolated_db.get_drama(did)
        assert len(isolated_db._open_connections) == 0

    def test_write_succeeds_after_a_failed_write(self, isolated_db):
        did = isolated_db.create_drama(title_en="X")
        try:
            isolated_db.log_usage(99999, "claude", "m", "translate", 1, 1, 0.0)
        except Exception:
            pass
        isolated_db.save_progress(did, last_line_idx=5, percent_complete=10.0)
        assert isolated_db.get_progress(did)["last_line_idx"] == 5

    def test_recovers_from_repeated_failures(self, isolated_db):
        did = isolated_db.create_drama(title_en="X")
        for _ in range(3):
            try:
                isolated_db.log_usage(99999, "c", "m", "t", 1, 1, 0.0)
            except Exception:
                pass
        isolated_db.update_drama(did, status="translated")
        assert isolated_db.get_drama(did)["status"] == "translated"

    def test_a_failed_write_no_longer_leaks_a_connection(self, isolated_db):
        # Before Step 69, this same failing call left its connection
        # tracked as "leaked" until the next get_conn() call reclaimed it
        # -- log_usage's own get_conn()/close() pair had no try/finally.
        # Now the failure is caught by its own with-block on the way out,
        # so nothing is ever tracked as open in the first place.
        try:
            isolated_db.log_usage(99999, "c", "m", "t", 1, 1, 0.0)
        except Exception:
            pass
        assert len(isolated_db._open_connections) == 0


class TestGpuLock:
    """Step 25w: the cross-process "one GPU job at a time" guard.
    background_jobs.py's own guard (Step 5c) is plain in-process module
    state, invisible to a separate OS process -- a `cli.py` run and the
    live Streamlit UI could each hold the GPU at once with neither seeing
    the other. This single-row table in the shared library.db is the
    coordination point both sides check."""

    def test_free_lock_is_acquired(self, isolated_db):
        assert gpu_slots.take("ui:job1", "Transcription") is True
        holder, description = gpu_slots.status()
        assert holder == "ui:job1"
        assert description == "Transcription"

    def test_held_lock_refuses_a_different_holder(self, isolated_db):
        assert gpu_slots.take("ui:job1", "Transcription") is True
        assert gpu_slots.take("cli:1234", "CLI dub") is False
        # Still job1's -- the failed attempt above must not have touched it.
        holder, description = gpu_slots.status()
        assert holder == "ui:job1"
        assert description == "Transcription"

    def test_same_holder_can_reacquire_its_own_lock(self, isolated_db):
        assert gpu_slots.take("ui:job1", "Transcription") is True
        assert gpu_slots.take("ui:job1", "Transcription") is True

    def test_release_frees_it_for_someone_else(self, isolated_db):
        gpu_slots.take("ui:job1", "Transcription")
        gpu_slots.release("ui:job1")
        assert gpu_slots.status() == (None, None)
        assert gpu_slots.take("cli:1234", "CLI dub") is True

    def test_releasing_the_wrong_holder_is_a_no_op(self, isolated_db):
        """A lock that went stale and was taken over by someone else must
        not be released out from under its new, legitimate holder by a
        late release() call from whoever held it before."""
        gpu_slots.take("ui:job1", "Transcription")
        gpu_slots.release("cli:1234")  # never held it
        holder, _ = gpu_slots.status()
        assert holder == "ui:job1"

    def test_stale_lock_is_taken_over(self, isolated_db):
        """A holder that crashed without releasing shouldn't permanently
        block the GPU -- a lock whose heartbeat is older than
        GPU_LOCK_STALE_SECONDS is treated as abandoned."""
        gpu_slots.take("ui:job1", "Transcription")
        conn = isolated_db.get_conn()
        conn.execute("UPDATE gpu_lock SET heartbeat_at = heartbeat_at - ? WHERE id = 1",
                    (gpu_slots.GPU_LOCK_STALE_SECONDS + 1,))
        conn.commit()
        conn.close()
        assert gpu_slots.status() == (None, None)
        assert gpu_slots.take("cli:1234", "CLI dub") is True

    def test_heartbeat_keeps_a_long_running_holder_from_going_stale(self, isolated_db):
        gpu_slots.take("ui:job1", "Transcription")
        conn = isolated_db.get_conn()
        conn.execute("UPDATE gpu_lock SET heartbeat_at = heartbeat_at - ? WHERE id = 1",
                    (gpu_slots.GPU_LOCK_STALE_SECONDS - 1,))
        conn.commit()
        conn.close()
        gpu_slots.heartbeat(["ui:job1"])
        # Refreshed -- still held, and a competing holder is still refused.
        assert gpu_slots.take("cli:1234", "CLI dub") is False

    def test_status_is_free_when_nothing_has_ever_held_it(self, isolated_db):
        assert gpu_slots.status() == (None, None)

    def test_max_holders_allows_that_many_and_no_more(self, isolated_db):
        assert gpu_slots.take("ui:a", "A", max_holders=2) is True
        assert gpu_slots.take("cli:1", "B", max_holders=2) is True
        assert gpu_slots.take("ui:c", "C", max_holders=2) is False
        assert gpu_slots.take("ui:c", "C") is False  # a cap of 1 too
        assert gpu_slots.holder_count() == 2
        assert gpu_slots.holder_count(exclude_holder="ui:a") == 1
        gpu_slots.release("ui:a")
        assert gpu_slots.status() == ("cli:1", "B")  # only its own row went
        assert gpu_slots.take("ui:c", "C", max_holders=2) is True

    def test_max_holders_is_capped_by_the_table(self, isolated_db):
        for i in range(gpu_slots.GPU_LOCK_MAX_SLOTS):
            assert gpu_slots.take(f"ui:{i}", max_holders=99) is True
        assert gpu_slots.take("ui:extra", max_holders=99) is False

    def test_settle_seconds_refuses_joining_a_fresh_holder(self, isolated_db):
        assert gpu_slots.take("ui:a", "A", max_holders=2, settle_seconds=60) is True
        assert gpu_slots.take("ui:b", "B", max_holders=2, settle_seconds=60) is False
        conn = isolated_db.get_conn()
        conn.execute("UPDATE gpu_lock SET acquired_at = acquired_at - 61")
        conn.commit()
        conn.close()
        assert gpu_slots.take("ui:b", "B", max_holders=2, settle_seconds=60) is True

    def test_a_stale_holder_does_not_count_and_heartbeat_is_per_holder(self, isolated_db):
        gpu_slots.take("ui:a", "A", max_holders=2)
        gpu_slots.take("ui:b", "B", max_holders=2)
        conn = isolated_db.get_conn()
        conn.execute("UPDATE gpu_lock SET heartbeat_at = heartbeat_at - ?",
                     (gpu_slots.GPU_LOCK_STALE_SECONDS + 1,))
        conn.commit()
        conn.close()
        gpu_slots.heartbeat(["ui:b"])
        assert gpu_slots.holder_count() == 1
        assert gpu_slots.take("ui:c", "C", max_holders=2) is True  # took a's slot
        assert gpu_slots.take("ui:d", "D", max_holders=2) is False

    def test_old_single_row_table_is_migrated_keeping_the_holder(self, isolated_db):
        conn = isolated_db.get_conn()
        conn.execute("DROP TABLE gpu_lock")
        conn.execute("""CREATE TABLE gpu_lock (id INTEGER PRIMARY KEY CHECK (id = 1),
                        holder TEXT NOT NULL, description TEXT, acquired_at REAL NOT NULL,
                        heartbeat_at REAL NOT NULL)""")
        conn.execute("INSERT INTO gpu_lock VALUES (1, 'cli:1', 'CLI', ?, ?)", (time.time(), time.time()))
        conn.commit()
        conn.close()
        isolated_db.init_db()
        assert gpu_slots.status() == ("cli:1", "CLI")
        assert gpu_slots.take("ui:a", "A", max_holders=2) is True


def _is_tracked(db_module, conn):
    return any(ref() is conn for refs in db_module._open_connections.values() for ref in refs)


class TestNestedConnections:
    """A helper that opens its own connection while its caller still holds
    one on the same thread used to close the caller's connection, silently
    rolling back the caller's uncommitted writes."""

    def test_a_nested_get_conn_keeps_the_outer_transaction(self, isolated_db):
        did = isolated_db.create_drama(title_en="Before")
        with contextlib.closing(isolated_db.get_conn()) as outer:
            outer.execute("UPDATE dramas SET title_en = 'After' WHERE id = ?", (did,))
            assert outer.in_transaction
            isolated_db.list_dramas()   # opens and closes its own connection
            assert outer.in_transaction
            outer.commit()
        assert isolated_db.get_drama(did)["title_en"] == "After"
        assert len(isolated_db._open_connections) == 0

    def test_a_connection_dropped_without_close_releases_the_write_lock(self, isolated_db):
        did = isolated_db.create_drama(title_en="X")
        conn = isolated_db.get_conn()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE dramas SET title_en = 'lost' WHERE id = ?", (did,))
        # The registry must not keep it, and its write lock, alive. sqlite3's
        # statement cache refers back to its connection, so the cycle
        # collector is what frees a dropped one.
        del conn
        gc.collect()
        isolated_db.update_drama(did, status="translated")
        assert isolated_db.get_drama(did)["title_en"] == "X"
        assert isolated_db.get_drama(did)["status"] == "translated"


class TestLeakedConnectionCleanup:
    """Step 69: get_conn()'s own connection-tracking exists to catch a
    connection leaked by a mid-statement failure (get_conn() called, but
    the exception skips the matching close()). This app runs real
    background_jobs.py threading.Thread workers concurrently with the
    main Streamlit thread, so that leak is routinely left by one thread
    and only discovered by a get_conn() call on another."""

    def test_leaked_connection_from_a_dead_background_thread_is_genuinely_closed(self, isolated_db):
        leaked = {}

        def worker():
            conn = isolated_db.get_conn()
            leaked["conn"] = conn
            # Simulates a mid-statement failure (a transient "database is
            # locked", a bad parameter) that skips conn.close() -- exactly
            # the scenario this cleanup exists to catch. The thread then
            # exits without ever closing its own connection. Caught here
            # (rather than left to propagate) only so the test doesn't
            # also have to deal with pytest's own unraisable-in-thread
            # warning -- the leak itself doesn't depend on that.
            try:
                raise RuntimeError("simulated transient db failure")
            except RuntimeError:
                pass

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert not t.is_alive()

        leaked_conn = leaked["conn"]
        assert _is_tracked(isolated_db, leaked_conn)

        # The dead worker thread can never touch its own connection again,
        # so the main thread's next get_conn() call should be able to
        # actually close it -- not just drop it from tracking (the bug:
        # closing it used to raise sqlite3.ProgrammingError for being on
        # the wrong thread, caught and silently discarded, leaving the
        # connection open but untracked and never retried).
        import pytest

        conn2 = isolated_db.get_conn()
        try:
            assert not _is_tracked(isolated_db, leaked_conn)
            with pytest.raises(sqlite3.ProgrammingError):
                leaked_conn.execute("SELECT 1")
        finally:
            conn2.close()


    def test_leaked_connection_close_failure_is_logged_not_silently_discarded(self, isolated_db, monkeypatch):
        # sqlite3.Connection is a C type that forbids monkeypatching its
        # own close() (an "immutable type" TypeError), so a real close
        # failure is triggered here instead: a connection opened with
        # sqlite3's default check_same_thread=True (get_conn() itself
        # always passes False, but this exercises the fallback logging
        # path in case some other close failure ever occurs) on a
        # now-dead thread genuinely raises ProgrammingError when closed
        # from a different thread -- exactly the failure this cleanup's
        # own warning exists to surface instead of silently discarding.
        import applog

        holder = {}

        def worker():
            holder["conn"] = sqlite3.connect(isolated_db.DB_PATH,
                                             factory=isolated_db._TrackedConnection)
            holder["ident"] = threading.get_ident()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert not t.is_alive()

        # Register it as if it were a leak left by that (now-dead) thread.
        isolated_db._open_connections[holder["ident"]] = [weakref.ref(holder["conn"])]

        logged = []

        class FakeLogger:
            def warning(self, *args, **kwargs):
                logged.append((args, kwargs))

        monkeypatch.setattr(applog, "get_logger", lambda: FakeLogger())

        # The next get_conn() call sweeps the dead-thread entry, hits the
        # real cross-thread close failure, and must log it rather than pass.
        conn2 = isolated_db.get_conn()
        try:
            assert logged, "a failed leaked-connection close should be logged via applog"
            assert holder["ident"] not in isolated_db._open_connections
        finally:
            conn2.close()

    def test_a_still_running_threads_own_connection_is_never_closed_by_another_thread(self, isolated_db):
        # The other half of the same fix: a connection isn't a leak just
        # because it's still open when another thread calls get_conn() --
        # it might be in perfectly ordinary use by a thread that's still
        # running. Closing it out from under that thread is a worse bug
        # than the one this cleanup exists to catch (this reproduced for
        # real during this step: Streamlit's own AppTest runs the app
        # script in its own thread while the test thread also calls
        # db.py, and an earlier version of this fix closed the script
        # thread's still-in-use connection, which then failed its very
        # next statement with "Cannot operate on a closed database").
        conn_opened = threading.Event()
        release = threading.Event()
        holder = {}

        def worker():
            conn = isolated_db.get_conn()
            holder["conn"] = conn
            conn_opened.set()
            release.wait(timeout=5)
            conn.execute("SELECT 1")  # must still work -- conn must still be open
            conn.close()

        t = threading.Thread(target=worker)
        t.start()
        try:
            assert conn_opened.wait(timeout=5), "worker never opened its connection"
            # The worker's own thread is still alive and hasn't closed its
            # connection yet -- a get_conn() call on the main thread must
            # not treat that as a leak.
            conn2 = isolated_db.get_conn()
            try:
                assert _is_tracked(isolated_db, holder["conn"])
            finally:
                conn2.close()
        finally:
            release.set()
            t.join(timeout=5)
        assert not t.is_alive()
