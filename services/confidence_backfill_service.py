"""services/confidence_backfill_service.py -- one pass at startup that gives lines saved before per-sentence
scoring their own Whisper confidence, from the word probabilities stored with them."""
import threading
from types import SimpleNamespace

import db
import segment_splitting

_MARKER = "backfills.per_line_confidence"
_thread = None


def start_in_background() -> None:
    """run_once on a daemon thread: on a large library it takes seconds, and the launcher rolls an
    update back if the server misses its health-check window."""
    global _thread
    _thread = threading.Thread(target=_run_logged, name="confidence-backfill", daemon=True)
    _thread.start()


def wait(timeout: float) -> bool:
    """True once no background pass is running, so a restore never swaps the database under one."""
    t = _thread
    if t is not None:
        t.join(timeout)
    return t is None or not t.is_alive()


def _run_logged() -> None:
    try:
        run_once()
    except Exception:   # noqa: BLE001 -- the marker stays unset, so the next start tries again
        try:
            import applog
            applog.get_logger().warning("Line scores were not rescored", exc_info=True)
        except OSError:
            pass


def run_once() -> int:
    """Rescores every title once and returns how many lines changed. A title that can't be read is
    skipped and logged, never a reason the app fails to start; the marker is set regardless, since a
    malformed row fails the same way every time and a second pass would rescale finished titles again."""
    if db.get_app_setting(_MARKER):
        return 0
    changed = 0
    for drama_id in db.drama_ids_with_lines():
        try:
            lines = [SimpleNamespace(**{k: row[k] for k in ("id", "start", "end", "zh", "confidence", "word_timings")})
                     for row in db.load_lines(drama_id, with_words=True)]
            scores = segment_splitting.rescore_shared_confidence(lines)
            if scores:
                before = {ln.id: ln.confidence for ln in lines}
                db.set_line_confidences({i: (new, before[i]) for i, new in scores.items()})
                changed += len(scores)
        except Exception as exc:   # noqa: BLE001 -- one odd title must not block the rest or startup
            try:
                import applog
                applog.get_logger().warning(f"Line scores not rescored for title {drama_id}: {type(exc).__name__}")
            except OSError:
                pass   # no log folder is no reason to stop starting
    db.set_app_setting(_MARKER, True)
    return changed
