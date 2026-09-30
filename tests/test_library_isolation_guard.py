"""Running the test suite must never read or write the real library
(<repo>/library). tests/conftest.py points every library-rooted path at
a session temp folder before any test code runs, and fails a test that
touches the real folder anyway. These tests pin both halves."""

import os
import sqlite3

import pytest

import db

REAL_LIBRARY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "library")
PROBE = ".baihe_guard_probe"


def _under_real_library(path):
    path = os.path.abspath(path)
    return path == REAL_LIBRARY_DIR or path.startswith(REAL_LIBRARY_DIR + os.sep)


def test_library_paths_point_away_from_the_real_library():
    """Checked without isolated_db: the default every test gets."""
    import applog
    import dictionary
    import page_server
    from sources import store

    paths = {
        "db.LIBRARY_DIR": db.LIBRARY_DIR,
        "db.DRAMAS_DIR": db.DRAMAS_DIR,
        "db.DB_PATH": db.DB_PATH,
        "db.BENCHMARK_DIR": db.BENCHMARK_DIR,
        "db.VOICE_BANK_DIR": db.VOICE_BANK_DIR,
        "sources.db": store.db_path(),
        "source_cache": store.cache_dir(),
        "profiles": store.browser_profiles_root(),
        "page_server token": page_server.token_path(),
        "dictionary.CEDICT_PATH": dictionary.CEDICT_PATH,
    }
    assert applog  # its log path is derived from db.LIBRARY_DIR per call
    leaked = {k: v for k, v in paths.items() if _under_real_library(v)}
    assert not leaked, f"still pointing at the real library: {leaked}"


def test_clearing_jobs_without_isolated_db_uses_the_temp_library():
    """The bug this guards: an autouse fixture called clear_all_jobs()
    outside isolated_db and ran DELETE FROM job_records on the real
    library.db."""
    import background_jobs

    background_jobs.clear_all_jobs()
    assert not _under_real_library(db.DB_PATH)
    assert os.path.exists(db.DB_PATH)


@pytest.mark.parametrize("touch", [
    lambda p: open(os.path.join(p, PROBE), "rb"),
    lambda p: sqlite3.connect(os.path.join(p, PROBE)),
    lambda p: os.makedirs(os.path.join(p, PROBE)),
], ids=["open", "sqlite3.connect", "makedirs"])
def test_touching_the_real_library_fails_loudly(touch, request):
    """Probes a name the app never uses, so a broken guard creates
    nothing that matters in the real folder."""
    with pytest.raises(RuntimeError, match="real library"):
        touch(REAL_LIBRARY_DIR)
    # The guard also records the hit so a caller that swallows the error
    # still fails its test; this test expected the hit, so clear it.
    request.config._baihe_real_library_hits.clear()
