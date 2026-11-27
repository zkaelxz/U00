"""services/recap_service.py -- the Library's monthly recap: what one account read, finished and saved in a month.

Read-only. Only numbers the library keeps per account: the reading history (novel and comic pages),
read chapter marks and saved moments. Listening time, words looked up and lines fixed are kept per
title or for the whole PC, so they are left out rather than guessed. Spend is the PC's, so only a
caller who may open Spend history gets it. Months are UTC calendar months, as Spend history cuts them.
"""

import datetime
import re

import db
from lib import clock
from lib.errors import InvalidInputError
from services import ownership_service, spend_history_service

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
# The window ends at the following month, which datetime can't reach past 9999-12.
_MIN_YEAR, _MAX_YEAR = 2000, 9998
SPEND_PERMISSION = "admin.settings"


def _last_month(now: datetime.datetime) -> str:
    first = now.replace(day=1)
    return (first - datetime.timedelta(days=1)).strftime("%Y-%m")


def _window(month: str) -> tuple[str, str]:
    m = _MONTH_RE.match(month)
    if not m or not _MIN_YEAR <= int(m.group(1)) <= _MAX_YEAR:
        raise InvalidInputError(f"A month looks like 2026-10, from {_MIN_YEAR} to {_MAX_YEAR}.")
    year, mon = int(m.group(1)), int(m.group(2))
    start = datetime.datetime(year, mon, 1)
    end = datetime.datetime(year + mon // 12, mon % 12 + 1, 1)
    return start.isoformat(), end.isoformat()


def _sees_spend(principal) -> bool:
    return principal is None or SPEND_PERMISSION in principal.get("permissions", ())


def _month_spend(month: str, principal) -> float:
    rows = spend_history_service.get_spend_history(month, principal)["months"]
    return next((r["cost_usd"] for r in rows if r["month"] == month), 0.0)


def get_recap(month: str | None = None, principal=None, now: datetime.datetime | None = None) -> dict:
    month = month or _last_month(now or clock.utcnow())
    start, end = _window(month)
    marks = db.month_recap(ownership_service.user_id(principal) or 0, start, end,
                           visible_to=ownership_service.visible_to_filter(principal))
    titles = marks["titles"]
    top = max(titles, key=lambda t: (t["days"], t["last_at"]), default=None)
    return {
        "month": month,
        "titles_read": len(titles),
        "titles_finished": sum(1 for t in titles if t["first_finished"]),
        "chapters_read": marks["chapters_read"],
        "moments_saved": marks["moments_saved"],
        "active_days": marks["active_days"],
        "most_days": top and {"drama_id": top["drama_id"], "days": top["days"],
                              "title": top["title_en"] or top["title_zh"] or "Untitled"},
        "spend_usd": _month_spend(month, principal) if _sees_spend(principal) else None,
    }
