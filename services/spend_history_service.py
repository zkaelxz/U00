"""
services/spend_history_service.py -- read-only paid-API spend by month and
what caused it (Settings > Spend history).

Reads usage_log.estimated_cost_usd, the same column the monthly cap
(db.get_month_spend) and the Library total sum, so a re-cost of past rows
(usage_recost_service) changes this view too. Months are UTC calendar months
cut on the same `created_at >= isoformat` string boundary the cap uses. The
cap counts only rows since a reset, so the month a reset falls in reports how
much of it the cap counts.

Breakdowns for one month: by operation, by engine+model and by title. Each
keeps the top rows and folds the rest into one "Other" row. Operations the app
logs later are shown by their raw name, prettified; nothing is keyed on a
fixed list. A principal who cannot see everything gets only calls logged
against dramas they may see (the rule in db.drama_visible_sql); the admin view
also includes calls whose drama was deleted.

UI-free. Responses hold titles, labels and numbers only.
"""
import contextlib
import csv
import datetime
import io
import re

import db
from services import ownership_service
from services.service_errors import InvalidInputError

MAX_MONTHS = 36
MAX_BREAKDOWN_ROWS = 8
DELETED_TITLE = "deleted title"
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

_SUMS = ("COALESCE(SUM(u.estimated_cost_usd), 0) AS cost, COUNT(*) AS calls, "
         "COALESCE(SUM(u.input_tokens), 0) AS input_tokens, "
         "COALESCE(SUM(u.output_tokens), 0) AS output_tokens, "
         "COALESCE(SUM(u.cache_read_tokens), 0) AS cache_read_tokens")

# Fixed SQL per breakdown; the caller picks a name, never a column.
_GROUPS = {
    "operation": "COALESCE(NULLIF(u.operation, ''), '') AS a, '' AS b",
    "engine_model": "COALESCE(u.engine, '') AS a, COALESCE(u.model, '') AS b",
    "title": "d.id AS a, COALESCE(NULLIF(d.title_en, ''), NULLIF(d.title_zh, ''), '') AS b",
}


def _month_start(now: datetime.datetime) -> datetime.datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(start: datetime.datetime, n: int) -> datetime.datetime:
    index = start.year * 12 + start.month - 1 + n
    return start.replace(year=index // 12, month=index % 12 + 1)


def _prettify(raw: str) -> str:
    words = re.sub(r"[_\-\s]+", " ", raw or "").strip()
    return (words[:1].upper() + words[1:]) if words else "Unknown"


def _scope(visible_to):
    """(extra WHERE sql, params) limiting usage rows to what the caller may see."""
    if visible_to is None:
        return "", []
    clause, params = db.drama_visible_sql("d", visible_to)
    # A call with no live drama has no owner to check, so it is admin-only.
    return f" AND d.id IS NOT NULL AND {clause}", params


def _money(value) -> float:
    return round(float(value or 0), 6)


def _rows(conn, group, start, end, scope):
    where, params = scope
    return conn.execute(
        f"SELECT {_GROUPS[group]}, {_SUMS} FROM usage_log u LEFT JOIN dramas d ON d.id = u.drama_id "
        f"WHERE u.created_at >= ? AND u.created_at < ?{where} GROUP BY a, b "
        "ORDER BY cost DESC, calls DESC, a, b", [start, end, *params]).fetchall()


def _entry(label, row, key=None, other=False):
    return {"key": key, "label": label, "is_other": other, "cost_usd": _money(row["cost"]),
            "calls": int(row["calls"]), "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
            "cache_read_tokens": int(row["cache_read_tokens"])}


def _with_other(entries: list) -> list:
    if len(entries) <= MAX_BREAKDOWN_ROWS:
        return entries
    keep, rest = entries[:MAX_BREAKDOWN_ROWS], entries[MAX_BREAKDOWN_ROWS:]
    other = {f: sum(e[f] for e in rest) for f in
             ("calls", "input_tokens", "output_tokens", "cache_read_tokens")}
    other.update(key=None, label="Other", is_other=True,
                 cost_usd=round(sum(e["cost_usd"] for e in rest), 6))
    return [*keep, other]


def _breakdown(conn, group, start, end, scope) -> list:
    entries = []
    for row in _rows(conn, group, start, end, scope):
        if group == "operation":
            entries.append(_entry(_prettify(row["a"]), row, key=row["a"] or None))
        elif group == "engine_model":
            label = " / ".join(p for p in (row["a"], row["b"]) if p) or "Unknown"
            entries.append(_entry(label, row))
        else:
            label = row["b"] if row["a"] is not None and row["b"] else (
                "Untitled" if row["a"] is not None else DELETED_TITLE)
            entries.append(_entry(label, row))
    if group == "title":
        # Two titles may share a name, and every deleted title groups under id NULL.
        merged = {}
        for e in entries:
            if e["label"] in merged:
                m = merged[e["label"]]
                for f in ("cost_usd", "calls", "input_tokens", "output_tokens", "cache_read_tokens"):
                    m[f] += e[f]
            else:
                merged[e["label"]] = e
        entries = sorted(merged.values(), key=lambda e: (-e["cost_usd"], -e["calls"], e["label"]))
    return _with_other(entries)


def _months(conn, now, scope, reset_at):
    first = _add_months(_month_start(now), -(MAX_MONTHS - 1))
    where, params = scope
    # CASE rather than a second query: one scan gives total and cap-counted.
    counted = "COALESCE(SUM(CASE WHEN u.created_at >= ? THEN u.estimated_cost_usd END), 0)"
    rows = conn.execute(
        f"SELECT substr(u.created_at, 1, 7) AS month, {_SUMS}, {counted} AS counted "
        "FROM usage_log u LEFT JOIN dramas d ON d.id = u.drama_id "
        f"WHERE u.created_at >= ?{where} GROUP BY month ORDER BY month DESC",
        [reset_at or "", first.isoformat(), *params]).fetchall()
    current = _month_start(now).strftime("%Y-%m")
    out = []
    for r in rows:
        if not _MONTH_RE.match(r["month"] or ""):
            continue
        overlaps = bool(reset_at) and r["month"] == current
        out.append({"month": r["month"], "cost_usd": _money(r["cost"]), "calls": int(r["calls"]),
                    "input_tokens": int(r["input_tokens"]), "output_tokens": int(r["output_tokens"]),
                    "cache_read_tokens": int(r["cache_read_tokens"]),
                    "overlaps_reset": overlaps,
                    "cap_counted_usd": _money(r["counted"]) if overlaps else None})
    return out


def get_spend_history(month: str = None, principal=None, now: datetime.datetime = None) -> dict:
    """Month table (newest first, at most MAX_MONTHS) plus the three
    breakdowns for `month` ("YYYY-MM"; default the newest month with spend)."""
    if month is not None and not _MONTH_RE.match(month):
        raise InvalidInputError("A month looks like 2026-10.")
    now = now or datetime.datetime.utcnow()
    scope = _scope(ownership_service.visible_to_filter(principal))
    reset_at = db.get_month_spend_reset_at(now)
    with contextlib.closing(db.get_conn()) as conn:
        months = _months(conn, now, scope, reset_at)
        selected = month or (months[0]["month"] if months else None)
        breakdowns = {"by_operation": [], "by_engine_model": [], "by_title": []}
        if selected:
            start = datetime.datetime(int(selected[:4]), int(selected[5:]), 1)
            lo, hi = start.isoformat(), _add_months(start, 1).isoformat()
            breakdowns = {"by_operation": _breakdown(conn, "operation", lo, hi, scope),
                          "by_engine_model": _breakdown(conn, "engine_model", lo, hi, scope),
                          "by_title": _breakdown(conn, "title", lo, hi, scope)}
    return {"months": months, "selected_month": selected, "cap_reset_at": reset_at,
            "max_months": MAX_MONTHS, **breakdowns}


def export_months_csv(principal=None, now: datetime.datetime = None) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["month", "estimated_cost_usd", "calls", "input_tokens", "output_tokens",
                     "cache_read_tokens", "cap_counted_usd"])
    for m in get_spend_history(None, principal, now)["months"]:
        writer.writerow([m["month"], f"{m['cost_usd']:.6f}", m["calls"], m["input_tokens"],
                         m["output_tokens"], m["cache_read_tokens"],
                         "" if m["cap_counted_usd"] is None else f"{m['cap_counted_usd']:.6f}"])
    return out.getvalue()
