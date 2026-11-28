"""Quick read runs in flight (pages received, pages finished) so Jobs can list and drop them."""

import threading
import time
from urllib.parse import urlsplit

from applog import get_logger
from engine_backends.shared import display_url
from lib.errors import NotFoundError
from services import ownership_service

# A finished run stays known for a while so a late batch of the same run is not mistaken for a
# new run (a new run drops the same person's older ones).
_KEEP_FINISHED_SECONDS = 3600
_MAX_RUNS = 200

_lock = threading.Lock()
_runs: dict[str, dict] = {}
# Why a run was stopped on the PC, so the phone can say so instead of showing a short page count.
_dropped: dict[str, str] = {}


def dropped_reason(run_id: str) -> str:
    return _dropped.get(run_id, "")


def _label(source_url: str) -> tuple[str, str]:
    """(site, chapter): the host and the last path part of the page link, nothing else."""
    shown = display_url(source_url or "") or ""
    parts = urlsplit(shown)
    tail = [p for p in parts.path.split("/") if p][-2:]
    return parts.hostname or "", "/".join(tail)


def _forget_old(now: float) -> None:
    for run_id in [k for k, r in _runs.items() if r["finished_at"] and now - r["finished_at"] > _KEEP_FINISHED_SECONDS]:
        del _runs[run_id]
        _dropped.pop(run_id, None)
    while len(_runs) > _MAX_RUNS:
        oldest = min(_runs, key=lambda k: _runs[k]["started"])
        del _runs[oldest]
        _dropped.pop(oldest, None)


def begin(run_id: str, owner, source_url: str, pages: int, cancel, device=None) -> None:
    """Counts `pages` more received pages for the run. The first batch of a run drops this reader's
    other runs still working (the reader moved on); `cancel(run_id)` stops one. A reader is the same
    account on the same device; where the device is unknown (the PC's own token) only a run on the
    same page address counts, so a second phone or the PC's own extension is never stopped."""
    if not run_id or pages <= 0:
        return
    now = time.monotonic()
    dropped = []
    with _lock:
        _forget_old(now)
        run = _runs.get(run_id)
        if run is not None and run["owner"] != owner:
            return   # a run id is its first sender's; another account cannot add to it
        if run is None:
            dropped = [k for k, r in _runs.items() if r["owner"] == owner and r["received"] > r["done"]
                       and (r["device"] == device if device is not None else r["source"] == source_url)]
            site, chapter = _label(source_url)
            run = _runs[run_id] = {"owner": owner, "device": device, "source": source_url, "site": site,
                                   "chapter": chapter, "received": 0, "done": 0, "started": now,
                                   "finished_at": 0.0, "first_done_at": 0.0, "cached": 0}
        run["received"] += pages
        run["finished_at"] = 0.0
    get_logger().info("Quick read run %s: %d more pages (%s)", run_id[:8], pages, run["site"] or "no site")
    for old in dropped:
        _dropped[old] = "Stopped on the PC: a newer Quick read started from this phone."
        cancel(old)
        finish(old)
        get_logger().info("Quick read run %s dropped: run %s started for the same reader", old[:8], run_id[:8])


def page_finished(run_id: str, cached: bool = False) -> None:
    summary = None
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        now = time.monotonic()
        run["done"] = min(run["done"] + 1, run["received"])
        run["cached"] += bool(cached)
        run["first_done_at"] = run["first_done_at"] or now
        if run["done"] >= run["received"]:
            run["finished_at"] = now
            summary = (run["received"], run["cached"], run["first_done_at"] - run["started"], now - run["started"])
    if summary:
        get_logger().info("Quick read run %s finished: %d pages (%d from cache), first page after %.1fs, "
                          "all pages after %.1fs", run_id[:8], *summary)


def finish(run_id: str) -> None:
    """The run's remaining pages are dropped, so it no longer counts as working."""
    with _lock:
        run = _runs.get(run_id)
        if run is not None:
            run["done"] = run["received"]
            run["finished_at"] = time.monotonic()


def _visible(principal, run: dict) -> bool:
    if ownership_service.sees_every_job(principal):
        return True
    return run["owner"] is not None and run["owner"] == ownership_service.user_id(principal)


def list_runs(principal=None) -> list[dict]:
    now = time.monotonic()
    with _lock:
        rows = [(k, dict(r)) for k, r in _runs.items() if r["received"] > r["done"] and _visible(principal, r)]
    return [{"run_id": k, "site": r["site"], "chapter": r["chapter"],
             "pages_received": r["received"], "pages_left": r["received"] - r["done"],
             "running_seconds": int(now - r["started"])}
            for k, r in sorted(rows, key=lambda kv: kv[1]["started"])]


def drop_run(run_id: str, principal=None) -> dict:
    """Stops the run's pages that have not started and tells started ones to stop at their next
    step. A run the caller may not see is a 404, like a job they may not see."""
    from services import extension_bridge_service
    with _lock:
        run = _runs.get(run_id)
        if run is None or run["received"] <= run["done"] or not _visible(principal, run):
            raise NotFoundError("No such Quick read run")
        left = run["received"] - run["done"]
    _dropped[run_id] = "Stopped on the PC: dropped from Jobs."
    extension_bridge_service.mark_cancelled(run_id)
    finish(run_id)
    get_logger().info("Quick read run %s dropped from Jobs (%d pages left)", run_id[:8], left)
    return {"run_id": run_id, "dropped_pages": left}


def drop_unheard(run_id: str, cancel) -> None:
    """Stops a run whose reader has gone (no answer stream open for a while), unless nothing of it
    is left to do: a later batch of a finished run must not be refused."""
    with _lock:
        run = _runs.get(run_id)
        if run is None or run["received"] <= run["done"]:
            return
        left = run["received"] - run["done"]
    _dropped[run_id] = "Stopped on the PC: the phone stopped listening for these pages."
    cancel(run_id)
    finish(run_id)
    get_logger().info("Quick read run %s dropped: nobody listening (%d pages left)", run_id[:8], left)


def drop_all(principal=None) -> dict:
    runs = pages = 0
    for run in list_runs(principal):
        try:
            pages += drop_run(run["run_id"], principal)["dropped_pages"]
            runs += 1
        except NotFoundError:
            continue   # it finished between the list and the drop
    return {"dropped_runs": runs, "dropped_pages": pages}
