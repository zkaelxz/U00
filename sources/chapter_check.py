"""
sources/chapter_check.py -- checks one tracked series for new chapters, the
same simple shape as Mihon's library update: re-fetch the chapter list, diff
by chapter id against what's already known, and record anything new as a
notification. The cycle over every tracked series, its auto-import and CBZ
saving, and the scheduler are services/chapter_check_service.py.

It notifies; it does not download. Every check goes through the adapter's
own paced client, so a check hits a source no harder than a manual
chapter-list fetch would.
"""

from . import http, ladder, store
from .models import FailureReason, FetchFailed

# Saved chapter-list validators older than this are ignored and the list
# is fetched in full, so a server that wrongly keeps answering 304 can't
# hide new chapters for longer than this.
VALIDATOR_MAX_AGE_SECONDS = 7 * 24 * 3600


def check_series(adapter, row: dict) -> list:
    """Returns the ChapterInfo list of chapters that are new since the last
    check, and records them (known + a notification each).

    The chapter list is fetched as a conditional re-poll. When
    the last poll was one plain GET that returned an ETag or Last-Modified,
    this poll sends them back; a 304 means nothing changed, so the list is
    neither downloaded nor parsed."""
    ladder.check_terms(adapter.name, adapter.capabilities())
    source, series_id = row["source"], row["series_id"]
    saved = store.poll_validators(source, series_id, max_age=VALIDATOR_MAX_AGE_SECONDS)
    with http.conditional_poll(**saved) as poll:
        try:
            chapters = adapter.get_chapters(series_id)
        except http.NotModified:
            chapters = None
        finally:
            if poll.untrusted_304:
                # The validated URL now redirects somewhere that answered
                # 304: forget the validators so the next poll is a full fetch.
                try:
                    store.save_poll_validators(source, series_id, None)
                except Exception:
                    pass
    if poll.not_modified:
        # Even if an adapter swallowed NotModified and returned something,
        # the one request it made said "unchanged".
        store.mark_checked(source, series_id)
        return []
    if chapters is None:
        raise TypeError("NotModified was raised outside this poll's own request")
    partial = getattr(adapter, "chapter_notice", "")
    if partial:
        # Nothing is recorded from a short list: the volumes it left out would
        # later come back as "new" chapters. The validators stay unsaved too,
        # so the next poll is a full fetch.
        raise FetchFailed(f"Not checked: {partial}", FailureReason.HTTP_ERROR)
    known = store.known_chapter_ids(source, series_id)
    new = [c for c in chapters if c.chapter_id not in known]
    if new:
        # Only what this call actually recorded: another process checking
        # the same series at the same moment gets the rest.
        new = store.record_new_chapters(source, series_id, new)
    # Saved only after the new chapters are recorded, so a 304 next time
    # can never hide a chapter this poll saw. Best effort: failing here
    # must not stop the auto-import of chapters already recorded (the old
    # validators then just get a 200 next time).
    try:
        store.save_poll_validators(source, series_id, poll.validators())
    except Exception:
        pass
    store.mark_checked(source, series_id)
    return new
