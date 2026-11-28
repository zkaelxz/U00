"""
tests/conftest.py -- shared pytest fixtures.

Key fixture: `isolated_db` redirects db.py's library path to a fresh
temp directory for each test, so running the test suite never reads,
writes, or deletes anything in your actual library.
"""

import contextlib
import sys
import os
import atexit
import shutil
import stat
import tempfile
import threading
import time
import pytest

# Never start the API's background services (chapter-check scheduler,
# extension endpoint) from a test that enters TestClient's lifespan with
# settings built by load_settings(); see api/background.py.
os.environ["BAIHE_API_BACKGROUND"] = "0"
# Never push a Discord/ntfy notification from a finished test job, even when
# the machine's .env has a webhook configured (services/notification_service).
os.environ["BAIHE_NOTIFY_DISABLED"] = "1"

# Make the project root importable when running `pytest` from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db

# --- never touching the real library ---------------------------------
#
# Every library-rooted path (library.db, dramas/, sources.db, source_cache/,
# profiles/, backups/, logs/, the page-server token...) is derived from
# db.LIBRARY_DIR, which defaults to <repo>/library -- the user's real data.
# A test without isolated_db used to run against it: an autouse fixture's
# clear_all_jobs() wiped job_records there, and the source-adapter tests
# wrote source_health rows into its sources.db. So the whole run is
# pointed at a temp library here, before any test module is imported, and
# isolated_db still gives each test that asks a fresh one on top.
# Set once per process: a test that imports `tests.conftest` runs this file a
# second time after db is already redirected, and the guard would then treat the
# session's temp library as the real one.
REAL_LIBRARY_DIR = os.environ.setdefault("BAIHE_TEST_REAL_LIBRARY_DIR", os.path.abspath(db.LIBRARY_DIR))
def rmtree_retrying(path, tries=5, pause=0.5):
    """Windows can't delete a sqlite file something still has open, and
    ignore_errors=True then leaves the folder in %TEMP% for good. The
    handle is usually released a moment later, so retry before giving up."""
    if os.name != "nt":
        tries = 1   # POSIX unlinks open files, so one pass is enough

    def _clear_readonly(func, p, _exc):
        # A read-only file (a copied model or db file) also blocks the delete on Windows.
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    for attempt in range(tries):
        shutil.rmtree(path, onerror=_clear_readonly)
        if not os.path.exists(path):
            return
        if attempt < tries - 1:
            time.sleep(pause)


_STALE_TEST_DIR_AGE = 2 * 3600


def sweep_stale_test_dirs(root=None, max_age=_STALE_TEST_DIR_AGE):
    """Removes baihe_test_* folders that earlier runs left behind (a killed
    run or a Windows file lock skips the normal cleanup). The age limit
    keeps it from touching the folders of a run that is still going in
    another terminal. Only directories: baihe_tmp_ is also the app's own
    prefix for temp files, so it is not ours to sweep."""
    root = root or tempfile.gettempdir()
    cutoff = time.time() - max_age
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        if not name.startswith("baihe_test_"):
            continue
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path) and os.path.getmtime(path) < cutoff:
                rmtree_retrying(path, tries=2, pause=0.2)
        except OSError:
            pass


# xdist workers import this file too; only the controller sweeps, so a
# worker never deletes a sibling's freshly made folder.
if "PYTEST_XDIST_WORKER" not in os.environ:
    sweep_stale_test_dirs()
_SESSION_ROOT = tempfile.mkdtemp(prefix="baihe_test_session_")
# pytest_unconfigure covers normal, failed and Ctrl+C runs; atexit also
# covers sys.exit and an unhandled error in pytest itself.
atexit.register(rmtree_retrying, _SESSION_ROOT)
db.configure_library_dir(os.path.join(_SESSION_ROOT, "library"))
db.init_db()

import dictionary  # noqa: E402  (the one path not derived from db.LIBRARY_DIR)
dictionary.CEDICT_PATH = os.path.join(db.LIBRARY_DIR, "cedict.txt")

# Belt and braces: anything that still reaches the real library -- a path
# captured before the redirect, a hard-coded one -- raises, and is recorded
# so a caller that swallows the error still fails its test.
_REAL_LIBRARY_HITS = []
_GUARDED_EVENTS = frozenset({
    "open", "sqlite3.connect", "os.mkdir", "os.remove", "os.rmdir",
    "os.rename", "shutil.rmtree", "os.listdir", "os.scandir",
})


def _is_real_library_path(path) -> bool:
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    if not isinstance(path, (str, os.PathLike)):
        return False  # a file descriptor
    # Relative paths are skipped: the audit event doesn't carry dir_fd, so
    # shutil.rmtree's os.open("library", dir_fd=...) inside a temp folder
    # would look like the real one. The app builds its paths from
    # db.LIBRARY_DIR, which is absolute.
    if not os.path.isabs(path):
        return False
    path = os.path.normpath(path)
    return path == REAL_LIBRARY_DIR or path.startswith(REAL_LIBRARY_DIR + os.sep)


def _real_library_audit_hook(event, args):
    if event not in _GUARDED_EVENTS:
        return
    for path in args[:2] if event == "os.rename" else args[:1]:
        if _is_real_library_path(path):
            # A hit from a background job thread that outlived its test lands in
            # whichever test runs next; the thread and test names say whose it is.
            who = (f"{event} {path} (thread {threading.current_thread().name!r}, "
                   f"test {os.environ.get('PYTEST_CURRENT_TEST', 'none')})")
            _REAL_LIBRARY_HITS.append(who)
            raise RuntimeError(f"test touched the real library: {who}")


sys.addaudithook(_real_library_audit_hook)


def pytest_configure(config):
    config._baihe_real_library_hits = _REAL_LIBRARY_HITS


def pytest_unconfigure(config):
    rmtree_retrying(_SESSION_ROOT)


@pytest.fixture(autouse=True)
def _fail_on_real_library_access():
    """Fails the test if anything in it reached the real library, even
    when the guard's error was caught and swallowed on the way."""
    _REAL_LIBRARY_HITS.clear()
    yield
    hits, _REAL_LIBRARY_HITS[:] = list(_REAL_LIBRARY_HITS), []
    assert not hits, f"test touched the real library: {hits}"

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


@pytest.fixture(autouse=True, scope="session")
def _fake_engine_installed():
    """The key-free fake engine every pipeline test translates with. It is
    not part of the app (see tests/fake_engine.py)."""
    from tests import fake_engine
    fake_engine.install()
    fake_engine.install_mt()
    yield
    fake_engine.uninstall_mt()
    fake_engine.uninstall()


@pytest.fixture(autouse=True)
def _no_automatic_cover():
    """Installing a fake video would otherwise launch real ffmpeg for the cover; tests/test_cover_from_video.py
    puts the real call back. Set by hand, not with `monkeypatch`: asking for it here changes the order other
    autouse fixtures patch in, and tests/test_model_pins.py and test_whisper_loading.py depend on that order."""
    from services import cover_art_service
    real = cover_art_service.make_cover_if_missing
    cover_art_service.make_cover_if_missing = lambda drama_id: False
    yield
    cover_art_service.make_cover_if_missing = real


@pytest.fixture(autouse=True)
def _nemotron_packages_present():
    """Nemotron is the default speaker model, and CI has neither librosa nor a new enough
    transformers, so every speaker-detection test would otherwise be refused up front;
    tests that check the missing-package case patch this back. Set by hand for the same
    ordering reason as above."""
    import diarize_nemotron
    real = diarize_nemotron.missing_packages
    diarize_nemotron.missing_packages = lambda: []
    yield
    diarize_nemotron.missing_packages = real


@pytest.fixture(autouse=True)
def _uploads_not_probed():
    """Most upload tests send a few placeholder bytes as audio; tests/test_api_media_upload.py
    puts the real ffprobe check back. Set by hand for the same ordering reason as above."""
    from services import media_upload_service
    real = media_upload_service.refuse_unreadable_audio
    media_upload_service.refuse_unreadable_audio = lambda staged: None
    yield
    media_upload_service.refuse_unreadable_audio = real


@pytest.fixture(autouse=True)
def _ocr_reads_in_this_process():
    """The text reader's helper process can't see a test's fake RapidOCR, so reads stay here;
    tests/test_ocr_worker.py drives the helper bookkeeping and the child's loop with fakes (no
    real spawn). Set by hand for the same ordering reason."""
    import ocr_worker
    ocr_worker.inline = True
    yield
    ocr_worker.inline = False
    ocr_worker._params_cache.clear()


@pytest.fixture
def free_engine():
    """The name of an engine that costs nothing and needs no key. Ollama runs hosted
    tags only and is billed, so tests that need "a free engine" use Echo, which is
    switched off again after the test."""
    from engine_backends import echo
    echo.set_enabled(True)
    return echo.ENGINE_ID


@pytest.fixture(autouse=True)
def _echo_engine_off_after_each_test():
    """Turning Developer Mode on registers the Echo engine in the process-wide
    engine tables; a test that does so (assistant, source-domain and role tests)
    would otherwise leave it there for whichever test the worker runs next, such
    as the free-engine labelling tests."""
    yield
    echo = sys.modules.get("engine_backends.echo")
    if echo is not None:
        echo.set_enabled(False)


@pytest.fixture(autouse=True)
def _faster_whisper_stub_forgotten():
    """Many tests put a plain-module faster_whisper stub straight into sys.modules;
    left there, a later test's `import faster_whisper.vad` fails with "not a package"."""
    before = sys.modules.get("faster_whisper")
    yield
    if before is None:
        sys.modules.pop("faster_whisper", None)
    else:
        sys.modules["faster_whisper"] = before


@pytest.fixture(autouse=True)
def _home_addresses_forgotten():
    """url_guard refuses the home's own public addresses process-wide; one
    test's remote-health check must not make the next test's fetch refused."""
    yield
    from lib import url_guard
    url_guard.set_home_addresses(())


@pytest.fixture(autouse=True)
def _ml_detector_failure_forgotten():
    """The comic detector remembers a failed model load process-wide; a test that
    triggers one must not make the next test's ML detection fail."""
    yield
    from scanlate import scanlate_detect
    scanlate_detect.forget_ml_failure()


@pytest.fixture(autouse=True)
def _quick_read_answers_forgotten():
    """Quick read keeps answers by image bytes and names per title process-wide, and tests
    reuse the same small images and titles, so one test's answer or names must not reach the
    next test's page."""
    yield
    import page_turns
    from services import extension_bridge_service
    with extension_bridge_service._quick_read_cache_lock:
        extension_bridge_service._quick_read_cache.clear()
    with page_turns._context_lock:
        page_turns._chapter_names.clear()
        page_turns._cast.clear()


@pytest.fixture(autouse=True)
def _no_gemma_picture_reader():
    """A comic page's picture goes to Gemma on Ollama's servers whenever the PC's
    Ollama answers, so a test run beside a running Ollama would send real, billed
    requests. The real function stays on `.real` for its own tests; the skip
    window it sets after a failure is process-wide, so it is cleared too."""
    from services import scanlate_run_service
    saved = scanlate_run_service._ollama_picture_reader

    def stub(*args, **kwargs):
        return None
    stub.real = saved
    scanlate_run_service._ollama_picture_reader = stub
    try:
        yield
    finally:
        scanlate_run_service._ollama_picture_reader = saved
        scanlate_run_service._ollama_reader_down_until = 0.0


@pytest.fixture(autouse=True)
def _live_whisper_not_loaded():
    """A Live job loads Whisper before it captures; no test may load a real
    model. The real function stays on `warm_up.real` for its own tests.
    Restores by hand, like the fixture below."""
    from live import live_whisper
    saved = live_whisper.warm_up

    def stub(*args, **kwargs):
        return None
    stub.real = saved
    live_whisper.warm_up = stub
    try:
        yield
    finally:
        live_whisper.warm_up = saved


@pytest.fixture(autouse=True)
def _no_warm_transcription_worker():
    """A GPU transcription runs in a warm worker that outlives the test by 90 s, and a test's
    patched target can't hand that worker objects shared only by inheritance (a spawn Event).
    Every run gets a fresh process instead; tests/test_warm_worker.py puts `claim.real` back."""
    from jobs import warm_worker
    saved = warm_worker.claim

    def stub(*args, **kwargs):
        return None
    stub.real = saved
    warm_worker.claim = stub
    try:
        yield
    finally:
        warm_worker.claim = saved


@pytest.fixture(autouse=True)
def _bridge_models_not_warmed():
    """Starting the extension bridge loads the comic models on a thread; no test may load a
    real model, and a thread left running would log into the next test's library. The real
    function stays on `warm_models_soon.real` for its own tests."""
    from services import page_server
    saved = page_server.warm_models_soon

    def stub():
        return False
    stub.real = saved
    page_server.warm_models_soon = stub
    try:
        yield
    finally:
        page_server.warm_models_soon = saved


@pytest.fixture(autouse=True)
def _live_tools_present():
    """Live's Start refuses when ffmpeg or yt-dlp is missing; the Live tests
    fake both. The real check stays on `missing_tool.real` for its own tests."""
    from services import live_service
    saved = live_service.missing_tool

    def stub():
        return None
    stub.real = saved
    live_service.missing_tool = stub
    try:
        yield
    finally:
        live_service.missing_tool = saved


@pytest.fixture(autouse=True)
def _whisper_sizes_not_downloaded():
    """Loading a Whisper size downloads its pinned revision first; the tests that
    stand in a fake faster_whisper expect the size name to reach WhisperModel, and
    no test may reach the Hub. The real function stays on `_download_model.real`
    for tests/test_model_pins.py."""
    import whisper_models
    saved = whisper_models._download_model

    def stub(model_size, token):
        return model_size
    stub.real = saved
    whisper_models._download_model = stub
    try:
        yield
    finally:
        whisper_models._download_model = saved


@pytest.fixture(autouse=True)
def _private_separator_model_dir(tmp_path_factory):
    """The vocal-separator model folder defaults to ~/.cache; the startup
    sweep and the download guard list and delete files there, so no test
    may see the developer's real one (or race another xdist worker on it).
    Restores by hand: requesting `monkeypatch` here would make the test's
    own patches (os.scandir, shutil.rmtree) outlive isolated_db's teardown."""
    import audio_preprocess
    saved = audio_preprocess.MODEL_DIR
    audio_preprocess.MODEL_DIR = str(tmp_path_factory.mktemp("separator_models"))
    try:
        yield
    finally:
        audio_preprocess.MODEL_DIR = saved


@pytest.fixture(autouse=True)
def _keep_real_torch_importable():
    """Restores the real torch (and friends) after every test, so a fake
    left behind can never turn into a re-import crash in a later test."""
    remember_real_modules()
    yield
    restore_real_modules()


@pytest.fixture(scope="session")
def _template_library(tmp_path_factory):
    """A library with the schema already created, built once per xdist
    worker. Running init_db() for every test was the single largest fixed
    cost in the suite; copying this folder and re-running the (cheap,
    nothing-to-create) init is not."""
    previous = db.LIBRARY_DIR
    template = str(tmp_path_factory.mktemp("template_library") / "library")
    os.makedirs(template)
    db.configure_library_dir(template)
    try:
        db.init_db()
        with contextlib.closing(db.get_conn()) as conn:
            # WAL mode: fold the log into library.db so copying the one file is enough.
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        db.configure_library_dir(previous)
    return template


@pytest.fixture
def isolated_db(_template_library):
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
    left nothing behind" saw another worker's in-flight staging dir.

    The library starts as a copy of the per-worker template, so the
    upgrade tests in test_db.py, which need a schema older than init_db's,
    build their own folders and never use this fixture's starting state."""
    previous = db.LIBRARY_DIR
    parent_dir = tempfile.mkdtemp(prefix="baihe_test_")
    temp_dir = os.path.join(parent_dir, "library")
    shutil.copytree(_template_library, temp_dir, ignore=shutil.ignore_patterns("*-wal", "*-shm"))
    db.configure_library_dir(temp_dir)
    db.init_db()
    yield db
    # Autouse fixtures tear down after this one, so _isolated_background_jobs would join the
    # test's job threads only once the library is gone: a thread still starting up (applog's
    # makedirs/open of logs/app.log) then races the rmtree and dies with FileNotFoundError,
    # which pytest blames on whichever test is tearing down.
    _join_leftover_job_threads()
    db.configure_library_dir(previous)
    rmtree_retrying(parent_dir)


def _reset_background_jobs_memory():
    """Drops background_jobs' in-process state: job records, the GPU
    queue, the restore's exclusive hold, the clean stop's job refusal,
    the maintenance count and the per-job cancel-check cache. In memory only -- clear_all_jobs() would
    also wipe job_records in whatever library db.LIBRARY_DIR points at."""
    import background_jobs as bg
    from jobs import gpu_guard, gpu_queue, job_launch, job_table
    from jobs import job_store
    gpu_guard.reset_for_tests()
    with job_table._lock:
        job_table._jobs.clear()
        job_table._workers.clear()
        gpu_queue._gpu_queue.clear()
        bg._last_db_cancel_check.clear()
        bg._db_cancel_check_failed.clear()
        bg.job_force_stop._abandoned.clear()
        job_store._pending.clear()
        job_store._last_write.clear()
    job_launch.release_exclusive()
    job_launch._stopping = False
    while job_launch._maintenance_count:
        job_launch.exit_maintenance()


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
    _join_leftover_job_threads()
    _reset_background_jobs_memory()


def _join_leftover_job_threads(timeout=5.0):
    """Waits for job threads a test started but didn't wait for. Left
    running, one finishes during the next test's setup in the same worker
    and writes its job record while that test initialises its database
    ("database is locked" in CI, 2026-09-30). Only job runner/watcher
    threads (and the line-rescoring pass a restore starts) are joined; the
    long-lived heartbeat thread is not."""
    deadline = time.monotonic() + timeout
    for t in threading.enumerate():
        if t is threading.current_thread():
            continue
        if t.name.startswith(("job:", "job-watcher:", "confidence-backfill")):
            t.join(max(0.0, deadline - time.monotonic()))


@pytest.fixture
def tmp_path_str():
    """A plain temp directory as a string path, cleaned up afterward.
    (pytest has a built-in tmp_path fixture, but it yields a pathlib
    Path; several functions here take str paths, so this avoids
    scattering str() conversions through the tests.)"""
    d = tempfile.mkdtemp(prefix="baihe_tmp_")
    yield d
    rmtree_retrying(d)


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


_LOOPBACK_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", ""}


def _is_loopback_literal(host) -> bool:
    import ipaddress
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


def _is_ip_literal(host) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(str(host).split("%")[0])
    except ValueError:
        return False
    return True


@pytest.fixture(autouse=True)
def _no_real_network():
    """No test may resolve a public name or connect to a non-loopback address:
    an unfaked fetch would pass on a developer PC and flake or hit a real site
    on CI. A lookup fails as an offline machine's would (unresolvable name),
    which code under test already handles; a connect fails loudly. Loopback stays open for tests that run a local server. A test that
    needs a lookup patches `socket.getaddrinfo` itself (applied after this
    fixture); one that needs a connection uses a fake, not an exemption.

    Patched and restored by hand, not through `monkeypatch`, for the reason
    given on _testclient_defaults_to_loopback."""
    import socket
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host, *args, **kwargs):
        name = host.decode("ascii", "replace") if isinstance(host, bytes) else host
        if name is not None and str(name).lower() not in _LOOPBACK_HOSTS and not _is_ip_literal(name):
            raise socket.gaierror(socket.EAI_NONAME, "no DNS in tests; fake socket.getaddrinfo")
        return real_getaddrinfo(host, *args, **kwargs)

    def refuse_remote(address):
        if (isinstance(address, tuple) and address
                and isinstance(address[0], str) and not _is_loopback_literal(address[0])
                and address[0].lower() not in _LOOPBACK_HOSTS):
            raise AssertionError("test tried a real network connection; use a fake")

    def connect(self, address):
        refuse_remote(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        refuse_remote(address)
        return real_connect_ex(self, address)

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    try:
        yield
    finally:
        socket.getaddrinfo = real_getaddrinfo
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex


@pytest.fixture(autouse=True)
def _workers_do_not_leave_the_test_session(request):
    """Process-job workers call process_worker.start_own_process_group()
    first. Tests that run one in the pytest process would otherwise start a
    new session for it and a watchdog that can end it; only
    test_background_jobs.py and the two files split out of it
    (test_process_jobs.py, test_job_limits.py), which exercise that
    function in real child processes, keep the real one.

    Patched and restored by hand for the reason given on
    _testclient_defaults_to_loopback."""
    from jobs import process_worker
    original = process_worker.start_own_process_group
    if not request.module.__name__.endswith(
            ("test_background_jobs", "test_process_jobs", "test_job_limits")):
        process_worker.start_own_process_group = lambda: None
    try:
        yield
    finally:
        process_worker.start_own_process_group = original


@pytest.fixture(autouse=True)
def _isolated_page_server_config():
    """page_server keeps the extension's translation config and the API's
    config provider (services/extension_service.push_translation_config) in
    module globals, so one test enabling the bridge would leave a provider
    behind that overrides a later test's pushed config. Restored by hand,
    for the same reason as the TestClient fixture above."""
    from services import page_server
    with page_server._config_lock:
        saved = (dict(page_server._config), page_server._config_provider)
    try:
        yield
    finally:
        with page_server._config_lock:
            page_server._config.clear()
            page_server._config.update(saved[0])
            page_server._config_provider = saved[1]


@pytest.fixture(scope="session")
def _default_app():
    """One app with default settings for every `client` and `local_client`: a new app's first request
    builds every route and the middleware stack, about a second each time. The app keeps no state a test
    could leave behind: the library is opened per request and cookies live on each TestClient. A test that
    adds routes or changes `app.state` builds its own app."""
    from api.server import create_app
    from api.api_config import ApiSettings
    return create_app(ApiSettings())


@pytest.fixture
def client(isolated_db, _default_app):
    """API client with a fresh database; a file that needs another setup defines its own `client`."""
    from fastapi.testclient import TestClient
    return TestClient(_default_app, raise_server_exceptions=False)


@pytest.fixture
def local_client(isolated_db, _default_app):
    """Like `client`, but sends X-Baihe-Local on every request, as the React client does."""
    from fastapi.testclient import TestClient
    return TestClient(_default_app, raise_server_exceptions=False, headers={"X-Baihe-Local": "1"})


@pytest.fixture
def whisper_present(monkeypatch):
    """Lab Whisper arms are refused when faster-whisper is missing, as it is on CI; tests of what a
    run does with an arm need the arm accepted."""
    import diagnostics
    real = diagnostics.check_dependency
    monkeypatch.setattr(diagnostics, "check_dependency",
                        lambda name, *a, **k: True if name == "faster_whisper" else real(name, *a, **k))


@pytest.fixture(autouse=True)
def _pydub_present():
    """Listen refuses to start without the opt-in pydub package; the Listen tests
    fake it. The real check stays on `_pydub_problem.real` for its own test."""
    from services import listen_service
    saved = listen_service._pydub_problem

    def stub():
        return None
    stub.real = saved
    listen_service._pydub_problem = stub
    try:
        yield
    finally:
        listen_service._pydub_problem = saved
