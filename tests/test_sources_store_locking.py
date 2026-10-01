"""sources.db locking: WAL, fail-fast busy error, schema init once per file."""

import os
import sqlite3
import time

import pytest

from sources import domains, health, store


@pytest.fixture
def short_timeout(isolated_db, monkeypatch):
    monkeypatch.setattr(store, "BUSY_TIMEOUT_SECONDS", 0.3)


def _hold_write_lock():
    store.get_setting("x")  # initialise the file before another connection locks it
    holder = sqlite3.connect(store.db_path(), timeout=1)
    holder.execute("BEGIN IMMEDIATE")
    return holder


def test_wal_enabled(isolated_db):
    with store.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_default_busy_timeout_is_short():
    assert store.BUSY_TIMEOUT_SECONDS <= 10


def test_locked_write_fails_fast_with_clear_message(short_timeout):
    store.set_setting("x", 1)
    holder = _hold_write_lock()
    try:
        started = time.monotonic()
        with pytest.raises(store.SourcesDatabaseBusy) as err:
            store.set_setting("x", 2)
        assert time.monotonic() - started < 3
        assert "busy" in str(err.value) and "locked" not in str(err.value)
        # A reader is not blocked by the writer in WAL mode.
        assert store.get_setting("x") == 1
    finally:
        holder.rollback()
        holder.close()


def test_schema_runs_once_per_path(isolated_db, monkeypatch):
    store.get_setting("pace_min_delay")
    calls = []
    real = store._add_missing_columns
    monkeypatch.setattr(store, "_add_missing_columns", lambda c: (calls.append(1), real(c)))
    for _ in range(3):
        store.get_setting("pace_min_delay")
    assert calls == []


def test_schema_reinitialised_when_file_is_gone(isolated_db):
    store.set_setting("k", 1)
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(store.db_path() + suffix):
            os.remove(store.db_path() + suffix)
    assert store.get_setting("k") is None  # tables exist again, no error


def test_locked_health_write_is_dropped_not_raised(short_timeout):
    holder = _hold_write_lock()
    try:
        health.record_success("src", 0.1)
        result = health.record_failure("src", "TIMEOUT", "boom")
        assert result["consecutive_failures"] == 1
    finally:
        holder.rollback()
        holder.close()


def test_locked_proposal_and_last_good_are_dropped(short_timeout):
    class Adapter:
        @staticmethod
        def verify_site(text):
            return True

    site = domains.SiteDomains.__new__(domains.SiteDomains)
    site.source, site.fixed, site.base, site.adapter = "src", None, None, Adapter()
    holder = _hold_write_lock()
    try:
        assert site._maybe_propose("https://new.example/", object()) is False
        site._worked("https://a.example")
    finally:
        holder.rollback()
        holder.close()
    assert store.last_good_domain("src") is None
