"""ocr_worker.py -- the helper process RapidOCR reads text in, on the GPU when there is room.

Reading ran inside the server before: on the V100 PC a Quick read held the app at
about 460% CPU, a health check took 3 s and a long Review page would not load. In
its own process the reader can't take the server's GIL or cores, it runs at a
lower priority, and a crash in a native library ends only the helper.

The parent decides everything (which engine, GPU or CPU, the settings); the child
builds the engine it is handed and reads. The GPU path uses the app's own PyTorch
(see ocr.RAPIDOCR_TORCH_MEDIUM_*). It gives way like the balloon detector and
LaMa do: only while Settings allows the GPU, the card has room and the GPU guard
isn't holding jobs; an out-of-memory read is redone on the CPU, and any other GPU
fault keeps the CPU for the rest of the process. The helper exits after
IDLE_EXIT_SECONDS without a read, and ocr.release_models() stops it, so its GPU
memory goes back whenever the app frees its models (before Whisper loads, and
when the GPU guard finds the card short).
"""

import logging
import multiprocessing
import os
import sys
import threading
import time
from types import SimpleNamespace

log = logging.getLogger(__name__)

# Only the medium pair has PyTorch weights here; balloons and pages use it.
GPU_KEYS = frozenset({"medium"})
# The two models, CUDA's own share and one page's working memory: generous, like
# the balloon detector's, so the reader only goes on a card with clear room.
GPU_NEED_MB = 1500
# A card without room is asked again at most this often: each nvidia-smi
# reading can take seconds, and a Quick read makes several reads a page.
GPU_RECHECK_SECONDS = 60
IDLE_EXIT_SECONDS = 600
# The first read also imports torch and loads the models.
READ_TIMEOUT_SECONDS = 300
_PARENT_CHECK_SECONDS = 5

_lock = threading.Lock()
_child = None
_gpu_loaded = False
_gpu_off_reason = None
_gpu_checked_at = None
_params_cache: dict = {}
# Tests read in this process with a fake RapidOCR (tests/conftest.py).
inline = False


def on_gpu() -> bool:
    return _gpu_loaded and _child is not None


def read(key: str, source):
    """One RapidOCR pass (image path or BGR array) as an object with txts, scores and boxes."""
    import ocr
    gpu = _gpu_allowed(key)
    params = None
    if gpu:
        try:
            params = _params(key, True)
        except ocr.RapidOcrModelUnavailable as exc:
            _gpu_off(f"its GPU models could not be fetched ({exc})")
            gpu = False
    if params is None:
        params = _params(key, False)
    with _lock:
        reply = _ask(("read", f"{key}:{'gpu' if gpu else 'cpu'}", params, source))
        if reply[0] in ("gpu_oom", "gpu_failed"):
            if reply[0] == "gpu_failed":
                _gpu_off(reply[1])
            reply = _ask(("read", f"{key}:cpu", _params(key, False), source))
        elif gpu:
            globals()["_gpu_loaded"] = True
    if reply[0] == "error":
        raise RuntimeError(reply[1])
    _, txts, scores, boxes = reply
    return SimpleNamespace(txts=txts, scores=scores, boxes=boxes)


def stop() -> None:
    """Ends the helper and frees what it holds; the next read starts a new one."""
    global _child, _gpu_loaded
    with _lock:
        child, _child, _gpu_loaded = _child, None, False
        _inline_engines().clear()
    if child is not None:
        _end(child)


def _params(key: str, gpu: bool) -> dict:
    # Building them checks the model files on disk; once per process is enough.
    if (key, gpu) not in _params_cache:
        import ocr
        _params_cache[(key, gpu)] = ocr._rapidocr_params(key, gpu=gpu)
    return _params_cache[(key, gpu)]


def _gpu_off(reason: str) -> None:
    global _gpu_off_reason
    if _gpu_off_reason is None:
        log.warning("Text reader: using the CPU for the rest of this run: %s", reason)
    _gpu_off_reason = reason


def _gpu_allowed(key: str) -> bool:
    global _gpu_checked_at
    if key not in GPU_KEYS or _gpu_off_reason is not None:
        return False
    try:
        import stored_settings
        if not stored_settings.read("use_gpu"):
            return False
        from jobs import gpu_guard
        if gpu_guard.hold_reason() is not None:
            return False
    except Exception:
        return False
    if on_gpu():
        return True
    now = time.monotonic()
    if _gpu_checked_at is not None and now - _gpu_checked_at < GPU_RECHECK_SECONDS:
        return False
    _gpu_checked_at = now
    try:
        import vram
        free = vram.free_vram_mb()
        if free is None:
            return False     # no readable NVIDIA card
        # Also honours Settings > Keep free memory for the GPU.
        vram.check_fits("RapidOCR text reader", required_mb=GPU_NEED_MB, free_mb=free)
    except Exception:
        return False
    _gpu_checked_at = None
    return True


def _ask(message) -> tuple:
    """Sends one request and waits for its reply, starting the helper when needed. A helper
    that went away (its idle exit, or a crash) is started again once."""
    global _child
    # A job already running in its own process (a daemon, which may not start one) reads there.
    if inline or multiprocessing.current_process().daemon:
        return _serve(_inline_engines(), *message[1:])
    for attempt in (1, 2):
        if _child is None or not _child[0].is_alive():
            if _child is not None:
                _end(_child)
            globals()["_gpu_loaded"] = False
            _child = _start()
        process, conn = _child
        try:
            conn.send(message)
            if not conn.poll(READ_TIMEOUT_SECONDS):
                _child = None
                _end((process, conn))
                return ("error", "Reading the text took too long and was stopped.")
            return conn.recv()
        except (EOFError, OSError, BrokenPipeError):
            _child = None
            _end((process, conn))
            if attempt == 2:
                return ("error", "The text reader stopped unexpectedly.")
    return ("error", "The text reader stopped unexpectedly.")


def _start():
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe()
    threads = max(1, (os.cpu_count() or 2) // 2)
    process = ctx.Process(target=_child_main, args=(child_conn, threads),
                          name="baihe-ocr-worker", daemon=True)
    process.start()
    child_conn.close()
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
    last = time.monotonic()
    while True:
        if conn.poll(_PARENT_CHECK_SECONDS):
            try:
                message = conn.recv()
            except EOFError:
                return
            if message[0] == "stop":
                return
            conn.send(_serve(engines, *message[1:], threads=threads))
            last = time.monotonic()
        elif parent is not None and not parent.is_alive():
            return
        elif time.monotonic() - last > IDLE_EXIT_SECONDS:
            return


def _serve(engines: dict, engine_id: str, params: dict, source, threads: int | None = None) -> tuple:
    on_gpu = engine_id.endswith(":gpu")
    try:
        if engine_id not in engines:
            if on_gpu and threads:
                import torch
                torch.set_num_threads(threads)
            from rapidocr import RapidOCR
            engines[engine_id] = RapidOCR(params=params)
        result = engines[engine_id](source)
        return ("ok", result.txts, getattr(result, "scores", None), getattr(result, "boxes", None))
    except Exception as exc:
        from engine_backends.redaction import redact_secrets
        if not on_gpu:
            return ("error", redact_secrets(f"{type(exc).__name__}: {exc}"))
        torch = sys.modules.get("torch")
        oom = torch is not None and isinstance(exc, getattr(torch.cuda, "OutOfMemoryError", ()))
        if not oom:
            engines.pop(engine_id, None)
        if torch is not None:
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
        if oom:
            return ("gpu_oom",)
        return ("gpu_failed", redact_secrets(f"{type(exc).__name__}: {exc}"))


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
