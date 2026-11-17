"""A household member's own monthly limit for paid engines, and whether it is used up."""

import db


def paid_limit_usd(user_id) -> float | None:
    """A member's own monthly limit for paid engines, or None when none applies."""
    row = db.auth_get_user(user_id) if isinstance(user_id, int) else None
    if not row or row["is_admin"] or row.get("monthly_paid_cap_usd") is None:
        return None
    return float(row["monthly_paid_cap_usd"])


def paid_limit_reached(user_id) -> bool:
    """A member whose own monthly limit for paid engines is used up (spend
    counted like the library-wide cap, from an active reset, with their
    pending bulk batches)."""
    limit = paid_limit_usd(user_id)
    return limit is not None and db.get_user_month_spend(user_id) >= limit


PAID_LIMIT_MESSAGE = ("You've used this month's limit for paid engines, so they're off for you "
                      "until next month or until the PC owner raises your limit. Free engines "
                      "such as Ollama still work.")
