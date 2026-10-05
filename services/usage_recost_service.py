"""
services/usage_recost_service.py -- opt-in re-cost of old usage_log rows.

Before models missing from PRICING_PER_MILLION_TOKENS were priced at their own
tier, estimate_cost priced them at the whole family's highest rate and
db.log_usage stored that figure, so past months still sum too high. This
recomputes those rows with the current estimate_cost.

- Candidates: a stored cost above zero, a model that is not a key of
  PRICING_PER_MILLION_TOKENS, not re-costed already, and a recomputed cost
  that is above zero and lower than the stored one. Priced models are never
  touched; a cost is never raised (a stored 0 may be a free tier, which the
  log does not record).
- Approximate: cache-write tokens are not stored, so they are costed as plain
  input.
- Preview never writes. Apply needs confirm=True, copies each old cost into
  estimated_cost_usd_before_recost in one transaction, and Undo restores it.
  No library snapshot is taken; that saved column is the way back.

UI-free. Writes are PC-only (the router uses local_only()).
"""

import contextlib
import datetime

import db
from engine_backends import pricing
from services.service_errors import InvalidInputError


def _month_start() -> str:
    now = datetime.datetime.utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _plan() -> list:
    """(row, recomputed cost) for every row a re-cost would change."""
    plan = []
    for row in db.usage_recost_candidates(tuple(pricing.PRICING_PER_MILLION_TOKENS)):
        new = pricing.estimate_cost(row["model"], row["input_tokens"] or 0,
                                    row["output_tokens"] or 0, row["cache_read_tokens"] or 0)
        if 0 < new < row["estimated_cost_usd"]:
            plan.append((row, new))
    return plan


def _recosted_rows() -> int:
    with contextlib.closing(db.get_conn()) as conn:
        return conn.execute("SELECT COUNT(*) FROM usage_log "
                            "WHERE estimated_cost_usd_before_recost IS NOT NULL").fetchone()[0]


def preview() -> dict:
    plan = _plan()
    month = _month_start()
    by_model = {}
    month_stored = month_new = 0.0
    for row, new in plan:
        m = by_model.setdefault(row["model"], {"model": row["model"], "rows": 0,
                                               "stored_usd": 0.0, "recomputed_usd": 0.0})
        m["rows"] += 1
        m["stored_usd"] += row["estimated_cost_usd"]
        m["recomputed_usd"] += new
        if (row["created_at"] or "") >= month:
            month_stored += row["estimated_cost_usd"]
            month_new += new
    stored = sum(r["estimated_cost_usd"] for r, _ in plan)
    recomputed = sum(n for _, n in plan)
    month_total = db.get_month_spend()
    return {
        "rows": len(plan),
        "models": sorted(by_model.values(), key=lambda m: -m["stored_usd"]),
        "stored_usd": stored,
        "recomputed_usd": recomputed,
        "difference_usd": stored - recomputed,
        "month_stored_usd": month_total,
        "month_recomputed_usd": month_total - month_stored + month_new,
        "recosted_rows": _recosted_rows(),
    }


def apply(confirm: bool) -> dict:
    if confirm is not True:
        raise InvalidInputError("Confirm the re-cost before it is applied.")
    updates = [(row["id"], row["estimated_cost_usd"], new) for row, new in _plan()]
    written = db.apply_usage_recost(updates) if updates else 0
    return {"rows": written, "month_spend_usd": db.get_month_spend()}


def undo() -> dict:
    return {"rows": db.undo_usage_recost(), "month_spend_usd": db.get_month_spend()}
