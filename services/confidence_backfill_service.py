"""services/confidence_backfill_service.py -- one pass at startup that gives lines saved before per-sentence
scoring their own Whisper confidence, from the word probabilities stored with them."""
from types import SimpleNamespace

import db
import segment_splitting

_MARKER = "backfills.per_line_confidence"


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
