"""services/recap_service.py -- the Library's monthly recap: what one account read, finished and saved in a month.

Read-only. Only numbers the library keeps per account: the reading history (novel and comic pages),
read chapter marks and saved moments. Listening time, words looked up and lines fixed are kept per
title or for the whole PC, so they are left out rather than guessed. Spend is the PC's, so only a
caller who may open Spend history gets it. Months are UTC calendar months, as Spend history cuts them.
"""

import datetime

import db
from lib import clock
from services import ownership_service, spend_history_service

SPEND_PERMISSION = "admin.settings"


def _sees_spend(principal) -> bool:
    return principal is None or SPEND_PERMISSION in principal.get("permissions", ())


def get_recap(month: str | None = None, principal=None, now: datetime.datetime | None = None) -> dict:
    month = month or spend_history_service.previous_month(now or clock.utcnow())
    start, end = spend_history_service.month_window(month)
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
        "spend_usd": spend_history_service.month_cost_usd(month, principal) if _sees_spend(principal) else None,
    }
