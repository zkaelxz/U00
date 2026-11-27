"""ocr_worker.py -- the helper process RapidOCR reads text in, on the GPU when there is room.

Reading ran inside the server before: on the V100 PC a Quick read held the app at
about 460% CPU, a health check took 3 s and a long Review page would not load. In
its own process the reader can't take the server's GIL or cores, it runs at a
lower priority, and a crash in a native library ends only the helper.

Two helpers: one reads on the CPU, the other loads the GPU engine and reads once it is
ready, so neither start-up holds the other up. The parent decides which reads where.
The GPU path uses the app's own PyTorch (see ocr.RAPIDOCR_TORCH_MEDIUM_*). It gives
way like the balloon detector and LaMa do: only while Settings allows the GPU, the card has room and the GPU guard
isn't holding jobs; an out-of-memory read is redone on the CPU, and any other GPU
fault keeps the CPU for the rest of the process. A helper exits after
IDLE_EXIT_SECONDS without a read (the GPU one sooner, GPU_IDLE_EXIT_SECONDS), and
ocr.release_models() stops both, so the GPU memory also goes back whenever this
process frees its models (the GPU guard finding the card short, a Whisper run in
this process). Turning GPU use off, or the guard holding jobs, stops the GPU one.
"""

import multiprocessing
import os
import sys
import threading
import time
from types import SimpleNamespace

from lib import device_use

def _log(text: str, warning: bool = False) -> None:
    """To the app's log file (applog); a log that can't be written never stops a read."""
    try:
        import applog
        logger = applog.get_logger()
        (logger.warning if warning else logger.info)(text)
    except Exception:
        pass

# Only the medium pair has PyTorch weights here; balloons and pages use it.
GPU_KEYS = frozenset({"medium"})
# The helper held 2.25 GB on the V100 while reading Chinese pages (two models, CUDA's own
# share, a page's working memory); a little over that so it only goes on a card with room.
GPU_NEED_MB = 2500
# A card without room is asked again at most this often: each nvidia-smi
# reading can take seconds, and a Quick read makes several reads a page.
GPU_RECHECK_SECONDS = 60
IDLE_EXIT_SECONDS = 600
# The GPU helper gives its memory back sooner: a Whisper job runs in its own process, where
# freeing the app's models can't reach this one. Loading again takes about 11 s on the V100,
# during which reads go to the CPU helper, so a short wait costs little.
GPU_IDLE_EXIT_SECONDS = 120
# The first CPU read also loads its models.
READ_TIMEOUT_SECONDS = 300
_PARENT_CHECK_SECONDS = 5

class _Helper:
    """One helper process: the CPU one reads with onnxruntime, the GPU one with torch."""

    def __init__(self, device: str):
        self.device = device
        self.lock = threading.Lock()
        self.child: tuple | None = None
        # GPU only: the engine was asked to load and has (ready) or hasn't answered yet.
        self.loading = False
        self.ready = False
        self.load_started = 0.0

    def alive(self) -> bool:
        return self.child is not None and self.child[0].is_alive()


_cpu = _Helper("cpu")
_gpu = _Helper("gpu")
_gpu_off_reason = None
_gpu_off_at = 0.0
# A GPU fault turns the GPU reader off, but for this long only: one fault (a card that was
# briefly full) otherwise left every later read on the CPU until the app restarted.
GPU_OFF_SECONDS = 300
_gpu_checked_at = None
_params_cache: dict = {}
# Tests read in this process with a fake RapidOCR (tests/conftest.py).
inline = False


def on_gpu() -> bool:
    return _gpu.ready and _gpu.alive()


def read(key: str, source, allow_download: bool = True):
    """One RapidOCR pass (image path or BGR array) as an object with txts, scores and boxes.

    The GPU engine loads in its own helper while reads go to the CPU one: loaded in the
    same process, torch's import held the CPU reads up (V100: a 63 s first page)."""
    reply = None
    if _gpu_allowed(key) and (allow_download or _gpu_weights_on_disk()) and _gpu_ready(key):
        reply = _ask(_gpu, ("read", f"{key}:gpu", None, source))
        if reply[0] == "gpu_failed":
            _gpu_off(reply[1])
            _stop_helper(_gpu)
        elif reply[0] == "gpu_later":
            _gpu_retry_later()
        elif reply[0] == "gpu_timeout":
            _gpu_off("a GPU read took too long")
            _stop_helper(_gpu)
        if reply[0] not in ("ok", "error"):
            reply = None
    if reply is None:
        reply = _ask(_cpu, ("read", f"{key}:cpu", _params(key), source))
        device_use.note(f"text reader ({key})", "CPU")
    else:
        device_use.note(f"text reader ({key})", "GPU")
    if reply[0] == "error":
        raise RuntimeError(reply[1])
    _, txts, scores, boxes = reply
    return SimpleNamespace(txts=txts, scores=scores, boxes=boxes)


def stop() -> None:
    """Ends the helpers and frees what they hold; the next read starts them again."""
    _stop_helper(_gpu)
    _stop_helper(_cpu)
    _params_cache.clear()
    with _inline_lock():
        _inline_engines().clear()


def _stop_helper(helper: _Helper) -> None:
    with helper.lock:
        child, helper.child = helper.child, None
        helper.loading = helper.ready = False
    if child is not None:
        _end(child)


def _params(key: str) -> dict:
    # Building them checks the model files on disk; once per process is enough.
    if key not in _params_cache:
        import ocr
        _params_cache[key] = ocr._rapidocr_params(key)
    return _params_cache[key]


def _gpu_off(reason: str) -> None:
    global _gpu_off_reason, _gpu_off_at
    if _gpu_off_reason is None:
        _log(f"Text reader: using the CPU for the next {GPU_OFF_SECONDS // 60} minutes: {reason}",
             warning=True)
    _gpu_off_reason = reason
    _gpu_off_at = time.monotonic()


def _gpu_retry_later() -> None:
    global _gpu_checked_at
    _stop_helper(_gpu)
    _gpu_checked_at = time.monotonic()


def _gpu_weights_on_disk() -> bool:
    import os
    import ocr
    return all(os.path.isfile(ocr.rapidocr_model_path(spec)) for spec in (
        ocr.RAPIDOCR_TORCH_MEDIUM_DET_MODEL, ocr.RAPIDOCR_TORCH_MEDIUM_REC_MODEL, ocr.RAPIDOCR_MEDIUM_DICT))


def _gpu_ready(key: str) -> bool:
    """True once the GPU helper has its engine; otherwise starts or checks on its load without
    waiting, so the caller reads on the CPU meanwhile."""
    if inline or multiprocessing.current_process().daemon:
        return True
    with _gpu.lock:
        if _gpu.ready and _gpu.alive():
            return True
        if not (_gpu.loading and _gpu.alive()):
            if _gpu.child is not None:
                _end(_gpu.child)
            child = _start()
            _gpu.child, _gpu.ready, _gpu.load_started = child, False, time.monotonic()
            child[1].send(("load", f"{key}:gpu", key, GPU_IDLE_EXIT_SECONDS))
            _gpu.loading = True
            return False
        assert _gpu.child is not None
        conn = _gpu.child[1]
        try:
            if not conn.poll(0):
                return False
            reply = conn.recv()
        except (EOFError, OSError):
            reply = ("gpu_failed", "the GPU helper stopped while loading")
        _gpu.loading = False
        if reply[0] == "ready":
            _gpu.ready = True
            _log(f"Text reader: GPU engine ready {time.monotonic() - _gpu.load_started:.1f} s "
                 "after its load started")
            _gpu.load_started = 0.0
            return True
    if reply[0] == "gpu_later":
        _gpu_skipped("the GPU engine found no room while loading")
        _gpu_retry_later()
    else:
        _gpu_off(reply[1] if len(reply) > 1 else "the GPU engine did not load")
        _stop_helper(_gpu)
    return False


_last_gpu_skip = None


def _gpu_skipped(reason: str) -> bool:
    """False, after logging why reads stay on the CPU the first time that reason is seen: a
    run on the CPU is ten times slower and was otherwise silent."""
    global _last_gpu_skip
    if reason != _last_gpu_skip:
        _last_gpu_skip = reason
        _log(f"Text reader: not using the GPU: {reason}", warning=True)
    return False


def _gpu_allowed(key: str) -> bool:
    global _gpu_checked_at, _gpu_off_reason
    if _gpu_off_reason is not None and time.monotonic() - _gpu_off_at >= GPU_OFF_SECONDS:
        _gpu_off_reason = None
        _log("Text reader: trying the GPU again")
    if key not in GPU_KEYS or _gpu_off_reason is not None:
        return False
    try:
        import stored_settings
        from jobs import gpu_guard
        refused = not stored_settings.read("use_gpu") or gpu_guard.hold_reason() is not None
    except Exception:
        refused = True
    if refused:
        if _gpu.alive():
            _stop_helper(_gpu)     # Settings or the guard turned it off: give the memory back now
        return _gpu_skipped("turned off in Settings or held by the GPU guard")
    # Its load or reads already passed the check below (a job's own process keeps its
    # engine in this process instead, where the check would count its own memory).
    if _gpu.alive() or ((inline or multiprocessing.current_process().daemon)
                        and f"{key}:gpu" in _inline_engines()):
        return True
    now = time.monotonic()
    if _gpu_checked_at is not None and now - _gpu_checked_at < GPU_RECHECK_SECONDS:
        return False
    _gpu_checked_at = now
    try:
        import vram
        free = vram.free_vram_mb()
        if free is None:
            return _gpu_skipped("the free GPU memory could not be read (no NVIDIA card, or "
                                "nvidia-smi did not answer)")
        # Also honours Settings > Keep free memory for the GPU.
        vram.check_fits("RapidOCR text reader", required_mb=GPU_NEED_MB, free_mb=free)
    except Exception as exc:
        return _gpu_skipped(f"not enough free GPU memory ({type(exc).__name__}: {exc})")
    _gpu_checked_at = None
    global _last_gpu_skip
    _last_gpu_skip = None
    return True


def _ask(helper: _Helper, message) -> tuple:
    """Sends one request to `helper` and waits for its reply, starting it when needed. A helper
    that went away (its idle exit, or a crash) is started again once; the GPU one is not,
    since its engine would have to load again (the caller then reads on the CPU)."""
    # A job already running in its own process (a daemon, which may not start one) reads there.
    if inline or multiprocessing.current_process().daemon:
        with _inline_lock():
            return _serve(_inline_engines(), *message[1:])
    with helper.lock:
        for attempt in (1, 2):
            if not helper.alive():
                if helper.device == "gpu":
                    if helper.child is not None:
                        _end(helper.child)
                    helper.child, helper.ready, helper.loading = None, False, False
                    return ("gpu_lost",)
                if helper.child is not None:
                    _end(helper.child)
                helper.child = _start()
                helper.load_started = time.monotonic()
            child = helper.child
            assert child is not None
            process, conn = child
            try:
                conn.send(message)
                if not _answered(process, conn):
                    helper.child = None
                    helper.ready = False
                    _end((process, conn))
                    if helper.device == "gpu":
                        return ("gpu_timeout",)
                    return ("error", "Reading the text took too long and was stopped.")
                reply = conn.recv()
                if helper.load_started:
                    _log(f"Text reader: first {helper.device} read answered "
                         f"{time.monotonic() - helper.load_started:.1f} s after its helper started")
                    helper.load_started = 0.0
                return reply
            except (EOFError, OSError):
                helper.child = None
                helper.ready = False
                _end((process, conn))
                if attempt == 2 or helper.device == "gpu":
                    break
    return ("error", "The text reader stopped unexpectedly.") if helper.device == "cpu" else ("gpu_lost",)


def _answered(process, conn) -> bool:
    """Whether `conn` has a reply, waiting up to READ_TIMEOUT_SECONDS. A helper that died
    without closing its end (a crash while it started, or a spawned copy that failed to import)
    is noticed within a second instead of after the whole timeout, and counts as an EOF."""
    deadline = time.monotonic() + READ_TIMEOUT_SECONDS
    while True:
        if conn.poll(min(1, max(0, deadline - time.monotonic()))):
            return True
        if not process.is_alive():
            if conn.poll(0):
                return True
            raise EOFError("the text reader helper stopped")
        if time.monotonic() >= deadline:
            break
    return False


def _start():
    started = time.monotonic()
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe()
    threads = max(1, (os.cpu_count() or 2) // 2)
    process = ctx.Process(target=_child_main, args=(child_conn, threads),
                          name="baihe-ocr-worker", daemon=True)
    process.start()
    child_conn.close()
    _log(f"Text reader: helper process started in {time.monotonic() - started:.1f} s")
    return process, parent_conn


def _end(child) -> None:
    process, conn = child
    try:
        conn.send(("stop",))
    except Exception:
        pass
    process.join(5)
    if process.is_alive():
        process.kill()
        process.join(5)
    try:
        conn.close()
    except Exception:
        pass


def _inline_lock():
    # The shared in-process OCR models' lock, as these reads took before the helper existed.
    import ocr
    return ocr._MODEL_LOCK


def _inline_engines() -> dict:
    import ocr
    engines = getattr(ocr, "_rapidocr_instances", None)
    if engines is None:
        engines = {}
        setattr(ocr, "_rapidocr_instances", engines)
    return engines


def _child_main(conn, threads: int) -> None:
    # Before numpy, cv2 or torch load, since they read these once.
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[name] = str(threads)
    _lower_priority()
    parent = multiprocessing.parent_process()
    engines: dict = {}
    idle_exit = IDLE_EXIT_SECONDS
    last = time.monotonic()
    while True:
        if conn.poll(_PARENT_CHECK_SECONDS):
            try:
                message = conn.recv()
            except EOFError:
                return
            if message[0] == "stop":
                return
            if message[0] == "load":
                idle_exit = message[3]
                conn.send(_load_gpu_engine(engines, message[1], message[2], threads))
            else:
                conn.send(_serve(engines, *message[1:]))
            last = time.monotonic()
        elif parent is not None and not parent.is_alive():
            return
        elif time.monotonic() - last > idle_exit:
            return


def _serve(engines: dict, engine_id: str, params, source) -> tuple:
    """One read with the engine `engine_id`, built from `params` on first use (a GPU engine
    without params builds its own, as in tests and in a job's own process)."""
    on_gpu = engine_id.endswith(":gpu")
    try:
        if engine_id not in engines:
            if params is None:
                import ocr
                params = ocr._rapidocr_params(engine_id.split(":")[0], gpu=on_gpu)
            from rapidocr import RapidOCR
            engines[engine_id] = RapidOCR(params=params)
        result = engines[engine_id](source)
        return ("ok", result.txts, getattr(result, "scores", None), getattr(result, "boxes", None))
    except Exception as exc:
        if not on_gpu:
            from engine_backends.redaction import redact_secrets
            return ("error", redact_secrets(f"{type(exc).__name__}: {exc}"))
        return _gpu_fault(engines, engine_id, exc)


def _gpu_fault(engines: dict, engine_id: str, exc: Exception) -> tuple:
    """The reply for a GPU failure. A full card, or weights that could not be fetched this
    time, is tried again later (an OOM read keeps the engine); anything else drops it and
    keeps the CPU."""
    import ocr
    from engine_backends.redaction import redact_secrets
    torch = sys.modules.get("torch")
    oom = torch is not None and isinstance(exc, getattr(torch.cuda, "OutOfMemoryError", ()))
    if isinstance(exc, ocr.RapidOcrModelUnavailable):
        return ("gpu_later",)
    if not oom:
        engines.pop(engine_id, None)
    if torch is not None:
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
    return ("gpu_later",) if oom else ("gpu_failed", redact_secrets(f"{type(exc).__name__}: {exc}"))


def _load_gpu_engine(engines: dict, engine_id: str, key: str, threads) -> tuple:
    """Builds the GPU engine and loads both models now (RapidOCR loads them on first use). The
    PyTorch weights are fetched here on first use, so that download never holds a read up."""
    try:
        import torch
        torch.set_num_threads(threads)
        import ocr
        params = ocr._rapidocr_params(key, gpu=True)
        from rapidocr import RapidOCR
        engine = RapidOCR(params=params)
        for name in ("_load_det_model", "_load_rec_model"):
            getattr(engine, name, lambda: None)()
        engines[engine_id] = engine
        return ("ready",)
    except Exception as exc:
        return _gpu_fault(engines, engine_id, exc)


def _lower_priority() -> None:
    try:
        if os.name == "nt":
            import ctypes
            below_normal = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),
                                                    below_normal)
        else:
            os.nice(5)
    except Exception:
        pass
