"""
tests/conftest.py -- shared pytest fixtures.

Key fixture: `isolated_db` redirects db.py's library path to a fresh
temp directory for each test, so running the test suite never reads,
writes, or deletes anything in your actual library.
"""

import sys
import os
import shutil
import tempfile
import pytest

# Never start the API's background services (chapter-check scheduler,
# extension endpoint) from a test that enters TestClient's lifespan with
# settings built by load_settings(); see api/background.py.
os.environ["BAIHE_API_BACKGROUND"] = "0"

# Make the project root importable when running `pytest` from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db

# --- keeping the real torch importable -------------------------------
#
# torch registers C++ operators when its module body runs, so running
# that body a second time in one process raises
#
#   RuntimeError: Only a single TORCH_LIBRARY can be used to register
#   the namespace triton
#
# i.e. **torch cannot be re-imported**. Plenty of tests here legitimately
# put a fake torch in `sys.modules` to exercise the "not installed" path.
# That is fine in itself; the damage is done only when the real module is
# left evicted afterwards, because the next honest `import torch`
# anywhere in the suite then re-executes it and dies -- failing a test
# that has nothing to do with whoever swapped the module out.
#
# This bites only when torch is genuinely installed, which is why it went
# unnoticed: it makes the suite red for every contributor who has the ML
# bubble-detection stack set up, and stays invisible to everyone else.
# Rather than rely on each test remembering to restore it, the invariant
# is enforced in one place for the whole suite.
_REAL_MODULES = {}
# torchvision and torchaudio register operators the same way, so they are
# protected too rather than waiting to be discovered the same way.
_PROTECTED = ("torch", "torchvision", "torchaudio")


def _is_real_module(module) -> bool:
    """A genuinely imported module has a file on disk. The stand-ins
    tests install -- plain ModuleType objects, mocks, or None -- do not."""
    return module is not None and bool(getattr(module, "__file__", None))


def remember_real_modules():
    """Records each protected module the first time it is really
    imported, so it can be put back later."""
    for name in _PROTECTED:
        if name not in _REAL_MODULES:
            current = sys.modules.get(name)
            if _is_real_module(current):
                _REAL_MODULES[name] = current
    return _REAL_MODULES


def restore_real_modules() -> list:
    """Puts back any protected module a test replaced or removed.
    Returns the names it had to restore."""
    restored = []
    for name, real in _REAL_MODULES.items():
        if sys.modules.get(name) is not real:
            sys.modules[name] = real
            restored.append(name)
    return restored


@pytest.fixture(autouse=True)
def _keep_real_torch_importable():
    """Restores the real torch (and friends) after every test, so a fake
    left behind can never turn into a re-import crash in a later test."""
    remember_real_modules()
    yield
    restore_real_modules()


@pytest.fixture
def isolated_db():
    """Redirects db.py to a fresh temp directory for the duration of
    one test, then cleans up afterward. Use this fixture in any test
    that touches the database.

    Restores the previous LIBRARY_DIR afterward -- previously left
    pointing at this test's now-deleted temp_dir for every test after
    it in the same pytest process, since configure_library_dir just
    overwrites db.py's module-level globals with no way to undo it.
    Harmless as long as nothing later touched db without its own
    isolation; a real, if latent, footgun the moment something did (a
    profile picker rendered from Settings' sidebar, Step 26e, was the
    first thing to actually hit it: `sqlite3.OperationalError: unable to
    open database file`, from a test with no isolated_db of its own that
    merely happened to run after one that had it, alphabetically).

    The library is a "library" folder inside a private temp directory,
    not a direct child of the shared system temp dir. A library restore
    stages its replacement (".restore_staging_*") and parks the old
    folder ("<library>.pre_restore_*") *next to* the library, so with the
    library sitting straight in /tmp every pytest-xdist worker's restore
    scratch landed in one shared folder, and a test asserting "restore
    left nothing behind" saw another worker's in-flight staging dir."""
    previous = (db.LIBRARY_DIR, db.DRAMAS_DIR, db.DB_PATH, db.BENCHMARK_DIR)
    parent_dir = tempfile.mkdtemp(prefix="baihe_test_")
    temp_dir = os.path.join(parent_dir, "library")
    os.makedirs(temp_dir)
    db.configure_library_dir(temp_dir)
    db.init_db()
    yield db
    db.LIBRARY_DIR, db.DRAMAS_DIR, db.DB_PATH, db.BENCHMARK_DIR = previous
    shutil.rmtree(parent_dir, ignore_errors=True)


def _reset_background_jobs_memory():
    """Drops background_jobs' in-process state: job records, the GPU
    queue, the restore's exclusive hold, the maintenance count and the
    per-job cancel-check cache. In memory only -- clear_all_jobs() would
    also wipe job_records in whatever library db.LIBRARY_DIR points at,
    which outside an isolated_db test can be a real one."""
    import background_jobs as bg
    with bg._lock:
        bg._jobs.clear()
        bg._gpu_queue.clear()
        bg._last_db_cancel_check.clear()
    bg.release_exclusive()
    while bg._maintenance_count:
        bg.exit_maintenance()


@pytest.fixture(autouse=True)
def _isolated_background_jobs():
    """background_jobs keeps its state in module globals for the life of
    the process, so a finished job one test leaves behind is still there
    for whichever test the scheduler runs next in the same worker. Job ids
    are built from drama ids and every isolated_db starts again at id 1,
    so e.g. a leftover "done" novel_glossary_1 made a later test's "no
    finished extraction" refusal not fire -- only under pytest-xdist,
    where the order within a worker differs from a serial run. Reset
    before and after every test so no test depends on another's jobs."""
    _reset_background_jobs_memory()
    yield
    _reset_background_jobs_memory()


@pytest.fixture
def tmp_path_str():
    """A plain temp directory as a string path, cleaned up afterward.
    (pytest has a built-in tmp_path fixture, but it yields a pathlib
    Path; several functions here take str paths, so this avoids
    scattering str() conversions through the tests.)"""
    d = tempfile.mkdtemp(prefix="baihe_tmp_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_lines():
    """A small, representative set of Line objects for testing
    alignment/merging/translation logic without needing real audio."""
    from core import Line
    return [
        Line(idx=0, start=0.0, end=0.5, zh="你", en="You", speaker="A"),
        Line(idx=1, start=0.6, end=1.0, zh="好", en="good", speaker="A"),
        Line(idx=2, start=1.1, end=3.0, zh="今天天气不错", en="The weather is nice today", speaker="A"),
        Line(idx=3, start=5.0, end=5.3, zh="嗯", en="Mm", speaker="B"),
    ]


@pytest.fixture
def sample_drama_meta():
    return {"title_en": "Test Drama", "title_zh": "测试剧", "author": "Test Author"}


@pytest.fixture(autouse=True)
def _testclient_defaults_to_loopback():
    """Step 133: with BAIHE_API_AUTH off the API refuses every request that
    isn't a direct loopback one (api.auth.LoopbackOnlyGate). Starlette's
    TestClient defaults to peer "testclient" / Host "testserver", which
    looks remote, so make an unspecified TestClient look like the owner's
    own browser on 127.0.0.1. Tests that pass base_url/client explicitly
    (remote-request tests) are unaffected.

    Patched and restored by hand rather than through `monkeypatch`: an
    autouse fixture requesting `monkeypatch` would make that shared
    instance outlive `isolated_db`, so a test's own monkeypatch (e.g. of
    shutil.rmtree) would still be active during isolated_db's teardown."""
    try:
        from starlette.testclient import TestClient
    except Exception:
        yield
        return
    original = TestClient.__init__

    def init(self, app, base_url="http://127.0.0.1", *args, client=("127.0.0.1", 50000), **kw):
        original(self, app, base_url, *args, client=client, **kw)
    TestClient.__init__ = init
    try:
        yield
    finally:
        TestClient.__init__ = original
