"""The text reader's helper process: GPU choice, fallbacks to the CPU, restarts and the child's loop
(mocked; no models, no GPU)."""

import multiprocessing
import sys
import threading
import time
import types

import pytest

import ocr
import ocr_worker


class _Result:
    def __init__(self, text):
        self.txts = (text,)
        self.scores = (0.9,)
        self.boxes = [[[0, 0], [1, 0], [1, 1], [0, 1]]]


@pytest.fixture
def worker(monkeypatch):
    """A fresh worker state with the GPU allowed and params that only say which device they are for."""
    for name, value in (("_child", None), ("_gpu_loaded", False), ("_gpu_pending", False), ("_gpu_off_reason", None),
                        ("_gpu_checked_at", None)):
        monkeypatch.setattr(ocr_worker, name, value)
    monkeypatch.setattr(ocr_worker, "_params_cache", {})
    monkeypatch.delitem(vars(ocr), "_rapidocr_instances", raising=False)
    monkeypatch.setattr(ocr, "_rapidocr_params", lambda key, gpu=False: {"gpu": gpu})
    monkeypatch.setattr(ocr_worker, "_gpu_allowed", lambda key: key in ocr_worker.GPU_KEYS)
    yield monkeypatch
    if hasattr(ocr, "_rapidocr_instances"):
        delattr(ocr, "_rapidocr_instances")


def _fake_rapidocr(monkeypatch, engine):
    fake = types.SimpleNamespace(RapidOCR=lambda params=None: (lambda source: engine(params, source)))
    monkeypatch.setitem(sys.modules, "rapidocr", fake)


def test_medium_reads_on_the_gpu_and_other_keys_on_the_cpu(worker):
    seen = []
    _fake_rapidocr(worker, lambda params, source: seen.append(params["gpu"]) or _Result("ok"))
    assert ocr_worker.read("medium", "/fake/page.png").txts == ("ok",)
    ocr_worker.read("ko", "/fake/page.png")
    assert seen == [True, False]


def test_a_gpu_fault_moves_the_rest_of_the_run_to_the_cpu(worker):
    worker.setattr(ocr_worker, "_gpu_allowed", lambda key: ocr_worker._gpu_off_reason is None)
    seen = []

    def engine(params, source):
        seen.append(params["gpu"])
        if params["gpu"]:
            raise RuntimeError("CUDA is not available.")
        return _Result("cpu")
    _fake_rapidocr(worker, engine)
    assert ocr_worker.read("medium", "/fake/a.png").txts == ("cpu",)
    assert ocr_worker.read("medium", "/fake/b.png").txts == ("cpu",)
    assert seen == [True, False, False], "a broken GPU is tried once, not on every read"
    assert "CUDA is not available" in ocr_worker._gpu_off_reason


def test_an_out_of_memory_read_is_redone_on_the_cpu_and_the_gpu_kept(worker):
    class OutOfMemoryError(Exception):
        pass
    worker.setitem(sys.modules, "torch", types.SimpleNamespace(
        cuda=types.SimpleNamespace(OutOfMemoryError=OutOfMemoryError, empty_cache=lambda: None)))
    full = [True]
    seen = []

    def engine(params, source):
        seen.append(params["gpu"])
        if params["gpu"] and full:
            full.clear()
            raise OutOfMemoryError()
        return _Result("gpu" if params["gpu"] else "cpu")
    _fake_rapidocr(worker, engine)
    assert ocr_worker.read("medium", "/fake/a.png").txts == ("cpu",)
    assert ocr_worker.read("medium", "/fake/b.png").txts == ("gpu",)
    assert seen == [True, False, True] and ocr_worker._gpu_off_reason is None


def test_a_cpu_failure_is_an_error_with_secrets_removed(worker):
    def engine(params, source):
        raise ValueError("bad image sk-ant-api03-" + "x" * 40)
    _fake_rapidocr(worker, engine)
    with pytest.raises(RuntimeError) as err:
        ocr_worker.read("ko", "/fake/a.png")
    assert "ValueError" in str(err.value) and "x" * 40 not in str(err.value)


def test_gpu_models_that_cannot_be_fetched_leave_reading_on_the_cpu(worker):
    worker.setattr(ocr_worker, "_gpu_allowed", lambda key: ocr_worker._gpu_off_reason is None)

    def params(key, gpu=False):
        if gpu:
            raise ocr.RapidOcrModelUnavailable("Could not download the RapidOCR model.")
        return {"gpu": False}
    worker.setattr(ocr, "_rapidocr_params", params)
    _fake_rapidocr(worker, lambda p, source: _Result("cpu"))
    assert ocr_worker.read("medium", "/fake/a.png").txts == ("cpu",)
    assert ocr_worker._gpu_off_reason


class TestGpuAllowed:
    @pytest.fixture(autouse=True)
    def _state(self, monkeypatch):
        import stored_settings
        import vram
        from jobs import gpu_guard
        for name, value in (("_child", None), ("_gpu_loaded", False), ("_gpu_pending", False), ("_gpu_off_reason", None),
                            ("_gpu_checked_at", None)):
            monkeypatch.setattr(ocr_worker, name, value)
        self.settings = {"use_gpu": True}
        monkeypatch.setattr(stored_settings, "read", lambda key: self.settings[key])
        self.hold = None
        monkeypatch.setattr(gpu_guard, "hold_reason", lambda: self.hold)
        self.free = 16000
        self.readings = 0

        def free_vram_mb():
            self.readings += 1
            return self.free
        monkeypatch.setattr(vram, "free_vram_mb", free_vram_mb)
        monkeypatch.setattr(vram.memory_headroom, "check_need", lambda *a, **k: None)

    def test_a_card_with_room_is_used(self):
        assert ocr_worker._gpu_allowed("medium")
        assert not ocr_worker._gpu_allowed("ko"), "only the medium pair has PyTorch weights"

    def test_settings_the_gpu_guard_or_no_card_keep_the_cpu(self):
        self.settings["use_gpu"] = False
        assert not ocr_worker._gpu_allowed("medium")
        self.settings["use_gpu"] = True
        self.hold = "GPU jobs are waiting: the GPU is short of memory."
        assert not ocr_worker._gpu_allowed("medium")
        self.hold = None
        self.free = None
        assert not ocr_worker._gpu_allowed("medium")

    def test_a_full_card_is_not_read_again_on_every_page(self):
        self.free = 500
        assert not ocr_worker._gpu_allowed("medium")
        assert not ocr_worker._gpu_allowed("medium")
        assert self.readings == 1
        assert ocr_worker._gpu_checked_at is not None
        ocr_worker._gpu_checked_at -= ocr_worker.GPU_RECHECK_SECONDS + 1
        self.free = 16000
        assert ocr_worker._gpu_allowed("medium") and self.readings == 2


class _FakeProcess:
    def __init__(self, alive=True):
        self.alive = alive
        self.killed = False

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass

    def kill(self):
        self.killed = True
        self.alive = False


class _FakeConn:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def poll(self, timeout=None):
        return bool(self.replies)

    def recv(self):
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def close(self):
        pass


def test_a_helper_that_went_away_is_started_again_once(worker):
    worker.setattr(ocr_worker, "inline", False)
    started = []
    conns = [_FakeConn([EOFError()]), _FakeConn([("ok", ("again",), None, None)])]

    def start():
        started.append(1)
        return _FakeProcess(), conns[len(started) - 1]
    worker.setattr(ocr_worker, "_start", start)
    assert ocr_worker.read("ko", "/fake/a.png").txts == ("again",)
    assert len(started) == 2


def test_a_helper_that_keeps_dying_is_an_error(worker):
    worker.setattr(ocr_worker, "inline", False)
    worker.setattr(ocr_worker, "_start", lambda: (_FakeProcess(), _FakeConn([EOFError()])))
    with pytest.raises(RuntimeError, match="stopped unexpectedly"):
        ocr_worker.read("ko", "/fake/a.png")


def test_a_stuck_read_is_stopped(worker):
    worker.setattr(ocr_worker, "inline", False)
    process = _FakeProcess()
    worker.setattr(ocr_worker, "_start", lambda: (process, _FakeConn([])))
    worker.setattr(ocr_worker, "READ_TIMEOUT_SECONDS", 0)
    with pytest.raises(RuntimeError, match="too long"):
        ocr_worker.read("ko", "/fake/a.png")
    assert process.killed and ocr_worker._child is None


def test_freeing_the_apps_models_stops_the_helper(worker):
    worker.setattr(ocr_worker, "inline", False)
    conn = _FakeConn([("ok", ("x",), None, None)])
    worker.setattr(ocr_worker, "_start", lambda: (_FakeProcess(), conn))
    ocr_worker.read("medium", "/fake/a.png")
    assert ocr_worker.on_gpu()
    ocr.release_models(empty_cuda=False)
    assert ocr_worker._child is None and not ocr_worker.on_gpu()
    assert conn.sent[-1] == ("stop",)
    assert ("RapidOCR text reader", "cuda") not in ocr.loaded_gpu_models()


def test_the_childs_loop_reads_until_told_to_stop(monkeypatch):
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        monkeypatch.setenv(name, "0")
    monkeypatch.setattr(ocr_worker, "_lower_priority", lambda: None)
    built = []
    monkeypatch.setitem(sys.modules, "rapidocr", types.SimpleNamespace(
        RapidOCR=lambda params=None: built.append(params) or (lambda source: _Result(source))))
    parent, child = multiprocessing.Pipe()
    loop = threading.Thread(target=ocr_worker._child_main, args=(child, 2), daemon=True)
    loop.start()
    parent.send(("read", "ko:cpu", {"gpu": False}, "a.png"))
    assert parent.recv()[1] == ("a.png",)
    parent.send(("read", "ko:cpu", {"gpu": False}, "b.png"))
    assert parent.recv()[1] == ("b.png",)
    assert len(built) == 1, "the engine is built once and kept"
    parent.send(("stop",))
    loop.join(10)
    assert not loop.is_alive()
    import os
    assert os.environ["OMP_NUM_THREADS"] == "2"


def test_pages_are_read_on_the_cpu_while_the_gpu_engine_loads(worker):
    worker.setattr(ocr_worker, "inline", False)
    checks = []
    worker.setattr(ocr_worker, "_gpu_allowed", lambda key: checks.append(key) or True)
    conn = _FakeConn([("gpu_loading",), ("ok", ("cpu",), None, None),
                      ("ok", ("gpu",), None, None)])
    worker.setattr(ocr_worker, "_start", lambda: (_FakeProcess(), conn))
    assert ocr_worker.read("medium", "/fake/a.png").txts == ("cpu",)
    assert not ocr_worker.on_gpu()
    assert ocr_worker.read("medium", "/fake/b.png").txts == ("gpu",)
    assert ocr_worker.on_gpu()
    assert [m[1] for m in conn.sent] == ["medium:gpu", "medium:cpu", "medium:gpu"]


def test_a_card_that_fills_while_loading_is_asked_again_later(worker):
    worker.setattr(ocr_worker, "inline", False)
    conn = _FakeConn([("gpu_oom",), ("ok", ("cpu",), None, None)])
    worker.setattr(ocr_worker, "_start", lambda: (_FakeProcess(), conn))
    assert ocr_worker.read("medium", "/fake/a.png").txts == ("cpu",)
    assert ocr_worker._gpu_pending is False and ocr_worker._gpu_checked_at is not None
    assert ocr_worker._gpu_off_reason is None


def test_the_helper_loads_its_gpu_engine_on_a_thread(monkeypatch):
    gate = threading.Event()
    built = []

    class Engine:
        def __init__(self, params=None):
            gate.wait(10)
            built.append(params)

        def __call__(self, source):
            return _Result("gpu")
    monkeypatch.setitem(sys.modules, "rapidocr", types.SimpleNamespace(RapidOCR=Engine))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        set_num_threads=lambda n: None,
        cuda=types.SimpleNamespace(OutOfMemoryError=MemoryError, empty_cache=lambda: None)))
    engines, loading = {}, {}
    assert ocr_worker._serve(engines, "medium:gpu", {}, "a", threads=2, loading=loading) == ("gpu_loading",)
    assert ocr_worker._serve(engines, "medium:gpu", {}, "b", threads=2, loading=loading) == ("gpu_loading",)
    gate.set()
    for _ in range(200):
        if "medium:gpu" in engines:
            break
        time.sleep(0.01)
    assert ocr_worker._serve(engines, "medium:gpu", {}, "c", threads=2, loading=loading)[1] == ("gpu",)
    assert len(built) == 1


def test_a_gpu_engine_that_cannot_load_keeps_the_cpu(monkeypatch):
    def broken(params=None):
        raise RuntimeError("CUDA is not available.")
    monkeypatch.setitem(sys.modules, "rapidocr", types.SimpleNamespace(RapidOCR=broken))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        set_num_threads=lambda n: None,
        cuda=types.SimpleNamespace(OutOfMemoryError=MemoryError, empty_cache=lambda: None)))
    loading: dict = {}
    ocr_worker._load_gpu_engine({}, loading, "medium:gpu", {}, 2)
    reply = ocr_worker._serve({}, "medium:gpu", {}, "a", loading=loading)
    assert reply[0] == "gpu_failed" and "CUDA is not available" in reply[1]
