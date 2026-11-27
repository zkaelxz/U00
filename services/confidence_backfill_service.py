"""services/confidence_backfill_service.py -- one pass at startup that gives lines saved before per-sentence
scoring their own Whisper confidence, from the word probabilities stored with them."""
from types import SimpleNamespace

import db
import segment_splitting

_MARKER = "backfills.per_line_confidence"


def run_once() -> int:
    """Rescores every title once and returns how many lines changed. The marker is set last: a pass
    cut short runs again, and a title it already finished has no shared scores left to rescale.
    A title that can't be read is skipped and logged, never a reason the app fails to start."""
    if db.get_app_setting(_MARKER):
        return 0
    changed, failed = 0, False
    for drama_id in db.drama_ids_with_lines():
        try:
            lines = [SimpleNamespace(**{k: row[k] for k in ("id", "start", "end", "zh", "confidence", "word_timings")})
                     for row in db.load_lines(drama_id, with_words=True)]
            scores = segment_splitting.rescore_shared_confidence(lines)
            if scores:
                db.set_line_confidences(scores)
                changed += len(scores)
        except Exception as exc:   # noqa: BLE001 -- one odd title must not block the rest or startup
            failed = True
            import applog
            applog.get_logger().warning(f"Line scores not rescored for title {drama_id}: {type(exc).__name__}")
    if not failed:
        db.set_app_setting(_MARKER, True)
    return changed
