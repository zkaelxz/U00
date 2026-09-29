"""Streamlit widget, AppTest and tab-source tests split out of tests/test_db.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_db.py."""

import sys
import os
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLeakedConnectionCleanup:
    """Step 69: get_conn()'s own connection-tracking exists to catch a
    connection leaked by a mid-statement failure (get_conn() called, but
    the exception skips the matching close()). This app runs real
    background_jobs.py threading.Thread workers concurrently with the
    main Streamlit thread, so that leak is routinely left by one thread
    and only discovered by a get_conn() call on another."""

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
                assert holder["conn"] in isolated_db._open_connections.values()
            finally:
                conn2.close()
        finally:
            release.set()
            t.join(timeout=5)
        assert not t.is_alive()
