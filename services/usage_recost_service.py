"""
services/usage_recost_service.py -- opt-in re-cost of old usage_log rows.

Some models were costed wrongly before they were priced correctly: the new
Claude 5.x models were unpriced, so estimate_cost fell back to a rate that was
too high, and claude-opus-4-8 sat in the table at $15/$75 although its real
rate is $5/$25. Rows logged before the same-tier fallback in
engine_backends/pricing.py landed were costed at the family maximum (Opus,
$15/$75); rows logged after it at the tier ceiling (Sonnet $3/$15). Both are
above the real rate. db.log_usage stored those figures, so past months still
sum too high.

- Selection is an explicit, reviewed list (RECOST_MODELS), not "every model
  that is unpriced": a row of any other model is never read or written.
  A candidate has a stored cost above zero and a recomputed cost that is above
  zero and lower than the stored one. All four listed corrections only lower
  a cost; raising one is out of scope (a stored 0 may be a free tier, which
  the log does not record, and a cap must never be loosened by a re-cost).
- Batch rows (operation `translate_bulk` or ending `_bulk`) were logged at
  BATCH_PRICE_FACTOR of the list price, so they are recomputed with it too.
- Cache-write tokens are not stored. A row's cost lies between "no cache
  writes" and "every non-cache-read input token billed as a cache write";
  the row is re-costed to the high end so a cap is never undercut, and a row
  already at or below it is left alone.
- Preview never writes. Apply needs confirm=True, keeps each row's first
  original in estimated_cost_usd_before_recost (a later correction can
  re-apply and Undo still returns the logged figure) and writes in one
  transaction. No library snapshot is taken; that column is the way back.

UI-free. Writes are PC-only (the router uses local_only()).
"""
import contextlib
import datetime
import hashlib

import db
import bulk_translate
from engine_backends import pricing
from services.service_errors import ConflictError, InvalidInputError

RECOST_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1", "claude-opus-4-8")


def _month_start() -> str:
    now = datetime.datetime.utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _high_cost(row: dict) -> float:
    """Upper end of the row's possible cost: all input that was not read from
    the cache is treated as cache-write, then the batch discount if it was one."""
    inp = row["input_tokens"] or 0
    cache_read = row["cache_read_tokens"] or 0
    cost = pricing.estimate_cost(row["model"], inp, row["output_tokens"] or 0,
                                 cache_read, max(0, inp - cache_read))
    if (row["operation"] or "").endswith("_bulk"):
        cost *= bulk_translate.BATCH_PRICE_FACTOR
    return cost


def _plan() -> list:
    """(row, recomputed cost) for every row a re-cost would change."""
    plan = []
    for row in db.usage_recost_candidates(RECOST_MODELS):
        new = _high_cost(row)
        if 0 < new < row["estimated_cost_usd"]:
            plan.append((row, new))
    return plan


def _recosted_rows() -> int:
    with contextlib.closing(db.get_conn()) as conn:
        return conn.execute("SELECT COUNT(*) FROM usage_log "
                            "WHERE estimated_cost_usd_before_recost IS NOT NULL").fetchone()[0]


def _fingerprint(plan: list) -> str:
    # Row ids and recomputed costs, so a different set with the same count differs.
    pairs = sorted((row["id"], round(new, 6)) for row, new in plan)
    return hashlib.sha256(repr(pairs).encode()).hexdigest()[:16]


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
    month_total = db.get_month_spend(since_reset=False)
    return {
        "rows": len(plan),
        "models": sorted(by_model.values(), key=lambda m: -m["stored_usd"]),
        "stored_usd": stored,
        "recomputed_usd": recomputed,
        "difference_usd": stored - recomputed,
        "month_stored_usd": month_total,
        "month_recomputed_usd": month_total - month_stored + month_new,
        "recosted_rows": _recosted_rows(),
        "fingerprint": _fingerprint(plan),
    }


def apply(confirm: bool, previewed: int, fingerprint: str) -> dict:
    if confirm is not True:
        raise InvalidInputError("Confirm the re-cost before it is applied.")
    plan = _plan()
    if previewed != len(plan) or fingerprint != _fingerprint(plan):
        raise ConflictError("The past costs changed since you checked them. Check again before applying.")
    updates = [(row["id"], row["estimated_cost_usd"], new) for row, new in plan]
    changed = db.apply_usage_recost(updates) if updates else 0
    return {"rows": changed, "previewed": previewed, "changed": changed,
            "month_spend_usd": db.get_month_spend(since_reset=False), "recosted_rows": _recosted_rows()}


def undo() -> dict:
    rows = db.undo_usage_recost()
    return {"rows": rows, "month_spend_usd": db.get_month_spend(since_reset=False),
            "recosted_rows": _recosted_rows()}
