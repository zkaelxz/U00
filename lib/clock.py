"""lib/clock.py -- the app's one source of "now" in UTC.

Timestamps are stored as naive UTC ISO strings (no offset), so the helper
returns a naive datetime: datetime.utcnow() did the same and is deprecated
since Python 3.12; datetime.now(timezone.utc) would add "+00:00" to every
stored value and break comparisons with the rows already written."""
import datetime


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
