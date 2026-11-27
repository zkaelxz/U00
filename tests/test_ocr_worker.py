"""The text reader's helper process: GPU choice, fallbacks to the CPU, restarts and the child's loop
(mocked; no models, no GPU)."""

import multiprocessing
import sys
import threading
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
    _fresh(monkeypatch)
    monkeypatch.setattr(ocr_worker, "_params_cache", {})
    monkeypatch.delitem(vars(ocr), "_rapidocr_instances", raising=False)
    monkeypatch.setattr(ocr, "_rapidocr_params", lambda key, gpu=False: {"gpu": gpu})
    monkeypatch.setattr(ocr_worker, "_gpu_allowed", lambda key: key in ocr_worker.GPU_KEYS)
    yield monkeypatch
    if hasattr(ocr, "_rapidocr_instances"):
        delattr(ocr, "_rapidocr_instances")


def _fresh(monkeypatch):
    monkeypatch.setattr(ocr_worker, "_cpu", ocr_worker._Helper("cpu"))
    monkeypatch.setattr(ocr_worker, "_gpu", ocr_worker._Helper("gpu"))
    monkeypatch.setattr(ocr_worker, "_gpu_off_reason", None)
    monkeypatch.setattr(ocr_worker, "_gpu_checked_at", None)


def _fake_rapidocr(monkeypatch, engine):
    fake = types.SimpleNamespace(RapidOCR=lambda params=None: (lambda source: engine(params, source)))
    monkeypatch.setitem(sys.modules, "rapidocr", fake)


def test_medium_reads_on_the_gpu_and_other_keys_on_the_cpu(worker):
    seen = []
    _fake_rapidocr(worker, lambda params, source: seen.append(params["gpu"]) or _Result("ok"))
    assert ocr_worker.read("medium", "/fake/page.png").txts == ("ok",)
    ocr_worker.read("small", "/fake/page.png")
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
        ocr_worker.read("small", "/fake/a.png")
    assert "ValueError" in str(err.value) and "x" * 40 not in str(err.value)


def test_gpu_models_that_cannot_be_fetched_read_on_the_cpu_for_now(worker):
    worker.setattr(ocr_worker, "_gpu_allowed", lambda key: ocr_worker._gpu_off_reason is None)

    def params(key, gpu=False):
        if gpu:
            raise ocr.RapidOcrModelUnavailable("Could not download the RapidOCR model.")
        return {"gpu": False}
    worker.setattr(ocr, "_rapidocr_params", params)
    _fake_rapidocr(worker, lambda p, source: _Result("cpu"))
    assert ocr_worker.read("medium", "/fake/a.png").txts == ("cpu",)
    assert ocr_worker._gpu_off_reason is None and ocr_worker._gpu_checked_at is not None, \
        "a failed download is tried again after the recheck interval, not given up on"


class TestGpuAllowed:
    @pytest.fixture(autouse=True)
    def _state(self, monkeypatch):
        import stored_settings
        import vram
        from jobs import gpu_guard
        _fresh(monkeypatch)
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
        assert ocr_worker._gpu_allowed("ko")
        assert not ocr_worker._gpu_allowed("small"), "the small pair has no PyTorch weights"

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

    def test_a_fault_turns_the_gpu_off_for_a_while_not_for_the_rest_of_the_process(self):
        ocr_worker._gpu_off("CUDA error")
        assert not ocr_worker._gpu_allowed("medium")
        ocr_worker._gpu_off_at -= ocr_worker.GPU_OFF_SECONDS + 1
        assert ocr_worker._gpu_allowed("medium")

    def test_why_the_cpu_is_used_is_logged_once_per_reason(self, monkeypatch):
        lines = []
        monkeypatch.setattr(ocr_worker, "_log", lambda text, warning=False: lines.append(text))
        monkeypatch.setattr(ocr_worker, "_last_gpu_skip", None)
        self.free = None
        for _ in range(3):
            ocr_worker._gpu_allowed("medium")
            ocr_worker._gpu_checked_at = None
        assert len(lines) == 1 and "could not be read" in lines[0]


class _FakeProcess:
    def __init__(self):
        self.alive = True
        self.killed = False

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass

    def kill(self):
        self.killed = True
        self.alive = False


class _FakeConn:
    """Answers each message from its script in order; `held` replies wait for release()."""

    def __init__(self, replies, held=()):
        self.replies = list(replies)
        self.held = set(held)
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def poll(self, timeout=None):
        return bool(self.replies) and 0 not in self.held

    def release(self):
        self.held.clear()

    def recv(self):
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def close(self):
        pass


@pytest.fixture
def helpers(worker):
    """Real helper bookkeeping with scripted processes: `starts` lists the conns handed out."""
    worker.setattr(ocr_worker, "inline", False)
    scripts = []
    starts = []

    def start():
        conn = scripts.pop(0)
        starts.append(conn)
        return _FakeProcess(), conn
    worker.setattr(ocr_worker, "_start", start)
    return types.SimpleNamespace(scripts=scripts, starts=starts, monkeypatch=worker)


def test_pages_are_read_on_the_cpu_while_the_gpu_helper_loads(helpers):
    gpu = _FakeConn([("ready",), ("ok", ("gpu",), None, None)], held={0})
    cpu = _FakeConn([("ok", ("cpu",), None, None)])
    helpers.scripts += [gpu, cpu]
    assert ocr_worker.read("medium", "/fake/a.png").txts == ("cpu",)
    assert gpu.sent == [("load", "medium:gpu", "medium", ocr_worker.GPU_IDLE_EXIT_SECONDS)], "the GPU load starts in its own helper"
    assert not ocr_worker.on_gpu()
    gpu.release()
    assert ocr_worker.read("medium", "/fake/b.png").txts == ("gpu",)
    assert ocr_worker.on_gpu()
    assert gpu.sent[-1][:2] == ("read", "medium:gpu") and len(cpu.sent) == 1


def test_the_small_pair_never_starts_the_gpu_helper(helpers):
    helpers.scripts.append(_FakeConn([("ok", ("small",), None, None)]))
    assert ocr_worker.read("small", "/fake/a.png").txts == ("small",)
    assert len(helpers.starts) == 1 and not ocr_worker._gpu.alive()


def test_a_gpu_engine_that_cannot_load_keeps_the_cpu(helpers):
    gpu = _FakeConn([("gpu_failed", "DeviceConfigError: CUDA is not available.")])
    cpu = _FakeConn([("ok", ("cpu",), None, None)] * 3)
    helpers.scripts += [gpu, cpu]
    helpers.monkeypatch.setattr(ocr_worker, "_gpu_allowed", lambda key: ocr_worker._gpu_off_reason is None)
    for _ in range(3):
        assert ocr_worker.read("medium", "/fake/a.png").txts == ("cpu",)
    assert "CUDA is not available" in ocr_worker._gpu_off_reason
    assert not ocr_worker._gpu.alive() and len(helpers.starts) == 2


def test_a_card_that_fills_while_loading_is_asked_again_later(helpers):
    helpers.scripts += [_FakeConn([("gpu_later",)]), _FakeConn([("ok", ("cpu",), None, None)] * 2)]
    ocr_worker.read("medium", "/fake/a.png")
    ocr_worker.read("medium", "/fake/b.png")
    assert ocr_worker._gpu_checked_at is not None and ocr_worker._gpu_off_reason is None
    assert not ocr_worker._gpu.alive(), "the GPU helper is stopped so its memory goes back"


def test_a_gpu_helper_that_went_away_hands_the_read_to_the_cpu(helpers):
    reload = _FakeConn([], held={0})
    helpers.scripts += [_FakeConn([("ready",)]), _FakeConn([("ok", ("cpu",), None, None)] * 2), reload]
    ocr_worker.read("medium", "/fake/a.png")       # starts the load, reads on the CPU
    assert ocr_worker._gpu_ready("medium")         # the load has answered
    child = ocr_worker._gpu.child
    assert child is not None
    child[0].alive = False                         # its idle exit
    assert ocr_worker.read("medium", "/fake/b.png").txts == ("cpu",)
    assert reload.sent == [("load", "medium:gpu", "medium", ocr_worker.GPU_IDLE_EXIT_SECONDS)], "the next read loads it again"


def test_a_cpu_helper_that_went_away_is_started_again_once(helpers):
    helpers.scripts += [_FakeConn([EOFError()]), _FakeConn([("ok", ("again",), None, None)])]
    assert ocr_worker.read("small", "/fake/a.png").txts == ("again",)
    assert len(helpers.starts) == 2


def test_a_cpu_helper_that_keeps_dying_is_an_error(helpers):
    helpers.scripts += [_FakeConn([EOFError()]), _FakeConn([EOFError()])]
    with pytest.raises(RuntimeError, match="stopped unexpectedly"):
        ocr_worker.read("small", "/fake/a.png")


def test_a_stuck_read_is_stopped(helpers):
    helpers.scripts.append(_FakeConn([]))
    helpers.monkeypatch.setattr(ocr_worker, "READ_TIMEOUT_SECONDS", 0)
    with pytest.raises(RuntimeError, match="too long"):
        ocr_worker.read("small", "/fake/a.png")
    assert ocr_worker._cpu.child is None


def test_freeing_the_apps_models_stops_both_helpers(helpers):
    gpu = _FakeConn([("ready",), ("ok", ("gpu",), None, None)])
    cpu = _FakeConn([("ok", ("cpu",), None, None)])
    helpers.scripts += [gpu, cpu]
    ocr_worker.read("medium", "/fake/a.png")
    ocr_worker.read("medium", "/fake/b.png")
    assert ocr_worker.on_gpu()
    assert ("RapidOCR text reader", "cuda") in ocr.loaded_gpu_models()
    ocr.release_models(empty_cuda=False)
    assert not ocr_worker.on_gpu() and ocr_worker._cpu.child is None
    assert gpu.sent[-1] == ("stop",) and cpu.sent[-1] == ("stop",)
    assert ("RapidOCR text reader", "cuda") not in ocr.loaded_gpu_models()


def _fake_torch(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        set_num_threads=lambda n: None,
        cuda=types.SimpleNamespace(OutOfMemoryError=MemoryError, empty_cache=lambda: None)))


def test_the_childs_loop_loads_reads_and_stops(monkeypatch):
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        monkeypatch.setenv(name, "0")
    monkeypatch.setattr(ocr_worker, "_lower_priority", lambda: None)
    monkeypatch.setattr(ocr, "_rapidocr_params", lambda key, gpu=False: {"gpu": gpu})
    _fake_torch(monkeypatch)
    built = []

    class Engine:
        def __init__(self, params=None):
            built.append(params)

        def _load_det_model(self):
            built.append("det")

        def __call__(self, source):
            return _Result(source)
    monkeypatch.setitem(sys.modules, "rapidocr", types.SimpleNamespace(RapidOCR=Engine))
    parent, child = multiprocessing.Pipe()
    loop = threading.Thread(target=ocr_worker._child_main, args=(child, 2), daemon=True)
    loop.start()
    parent.send(("load", "medium:gpu", "medium", 120))
    assert parent.recv() == ("ready",)
    assert built == [{"gpu": True}, "det"], "the GPU engine builds its own params and loads now"
    parent.send(("read", "medium:gpu", None, "a.png"))
    assert parent.recv()[1] == ("a.png",)
    parent.send(("read", "ko:cpu", {"gpu": False}, "b.png"))
    parent.send(("read", "ko:cpu", {"gpu": False}, "c.png"))
    assert parent.recv()[1] == ("b.png",) and parent.recv()[1] == ("c.png",)
    assert len(built) == 3, "each engine is built once and kept"
    parent.send(("stop",))
    loop.join(10)
    assert not loop.is_alive()
    import os
    assert os.environ["OMP_NUM_THREADS"] == "2"


def test_a_failed_gpu_load_says_why(monkeypatch):
    def broken(params=None):
        raise RuntimeError("CUDA is not available.")
    monkeypatch.setattr(ocr, "_rapidocr_params", lambda key, gpu=False: {})
    monkeypatch.setitem(sys.modules, "rapidocr", types.SimpleNamespace(RapidOCR=broken))
    _fake_torch(monkeypatch)
    reply = ocr_worker._load_gpu_engine({}, "medium:gpu", "medium", 2)
    assert reply[0] == "gpu_failed" and "CUDA is not available" in reply[1]
    def oom(params=None):
        raise MemoryError()
    monkeypatch.setitem(sys.modules, "rapidocr", types.SimpleNamespace(RapidOCR=oom))
    assert ocr_worker._load_gpu_engine({}, "medium:gpu", "medium", 2) == ("gpu_later",)


def test_a_failed_weights_download_is_tried_again_later(monkeypatch):
    def unavailable(key, gpu=False):
        raise ocr.RapidOcrModelUnavailable("Could not download the RapidOCR model.")
    monkeypatch.setattr(ocr, "_rapidocr_params", unavailable)
    _fake_torch(monkeypatch)
    assert ocr_worker._load_gpu_engine({}, "medium:gpu", "medium", 2) == ("gpu_later",)


def test_a_stuck_gpu_read_is_redone_on_the_cpu(helpers):
    gpu = _FakeConn([("ready",)])
    helpers.scripts += [gpu, _FakeConn([("ok", ("cpu",), None, None)] * 2)]
    ocr_worker.read("medium", "/fake/a.png")
    assert ocr_worker._gpu_ready("medium")
    helpers.monkeypatch.setattr(ocr_worker, "READ_TIMEOUT_SECONDS", 0)
    assert ocr_worker.read("medium", "/fake/b.png").txts == ("cpu",)
    assert ocr_worker._gpu_off_reason and not ocr_worker._gpu.alive()


def test_a_helper_that_died_without_answering_is_replaced_at_once(helpers):
    helpers.monkeypatch.setattr(ocr_worker, "READ_TIMEOUT_SECONDS", 3600)
    dead = _FakeConn([])
    helpers.scripts += [dead, _FakeConn([("ok", ("cpu",), None, None)])]
    real_start = ocr_worker._start

    def start():
        process, conn = real_start()
        process.alive = conn is not dead
        return process, conn
    helpers.monkeypatch.setattr(ocr_worker, "_start", start)
    assert ocr_worker.read("small", "/fake/a.png").txts == ("cpu",)
    assert len(helpers.starts) == 2


def test_warming_does_not_start_the_gpu_load_without_its_weights(helpers):
    helpers.monkeypatch.setattr(ocr_worker, "_gpu_weights_on_disk", lambda key="medium": False)
    helpers.scripts.append(_FakeConn([("ok", ("cpu",), None, None)]))
    assert ocr_worker.read("medium", "/fake/a.png", allow_download=False).txts == ("cpu",)
    assert len(helpers.starts) == 1 and not ocr_worker._gpu.alive()


class TestGivingWay:
    @pytest.fixture(autouse=True)
    def _guard(self, monkeypatch):
        import stored_settings
        from jobs import gpu_guard
        _fresh(monkeypatch)
        self.settings = {"use_gpu": True}
        self.hold = None
        monkeypatch.setattr(stored_settings, "read", lambda key: self.settings[key])
        monkeypatch.setattr(gpu_guard, "hold_reason", lambda: self.hold)
        self.conn = _FakeConn([])
        ocr_worker._gpu.child = (_FakeProcess(), self.conn)
        ocr_worker._gpu.ready = True
        ocr_worker._gpu.key = "medium"
        yield
        ocr_worker._gpu.child = None
        ocr_worker._gpu.key = ""

    def test_turning_gpu_use_off_stops_the_gpu_helper(self):
        self.settings["use_gpu"] = False
        assert not ocr_worker._gpu_allowed("medium")
        assert not ocr_worker._gpu.alive() and self.conn.sent == [("stop",)]

    def test_the_gpu_guard_holding_jobs_stops_the_gpu_helper(self):
        self.hold = "GPU jobs are waiting: the GPU is short of memory."
        assert not ocr_worker._gpu_allowed("medium")
        assert not ocr_worker._gpu.alive()

    def test_a_loaded_gpu_helper_is_kept_without_asking_the_card_again(self, monkeypatch):
        import vram
        monkeypatch.setattr(vram, "free_vram_mb", lambda: pytest.fail("measured again"))
        assert ocr_worker._gpu_allowed("medium")


def test_a_jobs_own_process_keeps_using_its_loaded_gpu_engine(monkeypatch):
    import stored_settings
    import vram
    from jobs import gpu_guard
    _fresh(monkeypatch)
    monkeypatch.setattr(stored_settings, "read", lambda key: True)
    monkeypatch.setattr(gpu_guard, "hold_reason", lambda: None)
    monkeypatch.setattr(vram, "free_vram_mb", lambda: pytest.fail("measured again"))
    monkeypatch.setitem(ocr_worker._inline_engines(), "medium:gpu", object())
    assert ocr_worker._gpu_allowed("medium")


def test_start_gpu_load_waits_for_the_engine_only_when_the_gpu_is_allowed(monkeypatch):
    import ocr_worker
    monkeypatch.setattr(ocr_worker, "_gpu_weights_on_disk", lambda key="medium": True)
    monkeypatch.setattr(ocr_worker, "_gpu_allowed", lambda key: False)
    assert ocr_worker.start_gpu_load("medium", wait=1) is False
    answers = iter([False, False, True])
    monkeypatch.setattr(ocr_worker._gpu, "alive", lambda: True)
    monkeypatch.setattr(ocr_worker, "_gpu_allowed", lambda key: True)
    monkeypatch.setattr(ocr_worker, "_gpu_ready", lambda key: next(answers))
    assert ocr_worker.start_gpu_load("medium", wait=5) is True
