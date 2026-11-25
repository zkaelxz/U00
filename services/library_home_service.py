"""services/library_home_service.py -- the Library's three shelves: what needs the person, what the PC is running, and what is ready to watch or read.

Read-only. Built from what the Library list, the PC queue and the series auto-run already keep, so the shelves and
the pages they link to always agree. Risky-line counts are the expensive part (every line of a title is scored), so
only the most recently changed titles that wait for a check are counted.
"""

from services import library_service, queue_service, review_lines_service, series_auto_service

_COMIC_MEDIA_TYPES = ("manga", "manhwa", "manhua")
# Enough to fill a phone screen per shelf; the full grid is below them.
SHELF_LIMIT = 8
# Each counted title reads all its lines; past this many the shelf would cost more than the rest of the page.
RISK_TITLES = 20
_FAILED = ("failed", "interrupted")
_ACTIVE = ("waiting", "running")
_FINISHED_PERCENT = 99


def _is_comic(media_type) -> bool:
    return (media_type or "").lower() in _COMIC_MEDIA_TYPES


def _title(d: dict) -> str:
    return d.get("title_en") or d.get("title_zh") or f"#{d['id']}"


def _row(d: dict) -> dict:
    return {"drama_id": d["id"], "title": _title(d), "media_type": d.get("media_type"),
            "content_mode": d.get("content_mode"), "has_cover_art": bool(d.get("cover_art_filename")),
            "updated_at": d.get("updated_at")}


def _lines_done(d: dict) -> bool:
    total = d.get("line_count") or 0
    return total > 0 and (d.get("translated_count") or 0) >= total


def _pages_done(d: dict) -> bool:
    total = d.get("page_count") or 0
    return total > 0 and (d.get("pages_translated") or 0) >= total


def _newest_first(dramas: list) -> list:
    return sorted(dramas, key=lambda d: (d.get("updated_at") or d.get("created_at") or "", d["id"]), reverse=True)


def _failed_items(items: list) -> list:
    """The newest failed step per title, unless a later step of that title was added since (a retry or the next step)."""
    latest: dict[int, dict] = {}
    for item in items:
        if item["drama_id"] not in latest or item["id"] > latest[item["drama_id"]]["id"]:
            latest[item["drama_id"]] = item
    return [i for i in sorted(latest.values(), key=lambda i: i["id"], reverse=True) if i["status"] in _FAILED]


def get_home(principal=None) -> dict:
    dramas = library_service.list_library_dramas(principal=principal)
    by_id = {d["id"]: d for d in dramas}
    items = queue_service.get_queue(principal)["items"]

    needs: list = []
    taken: set = set()
    for item in _failed_items(items):
        if item["drama_id"] in by_id:
            needs.append({**_row(by_id[item["drama_id"]]), "kind": "failed", "step": item["step"],
                          "reason": item["reason"], "risk_count": None, "series_id": None})
            taken.add(item["drama_id"])
    for hold in series_auto_service.held_series(principal):
        needs.append({"drama_id": None, "title": hold["name"], "media_type": None, "content_mode": None,
                      "has_cover_art": False, "updated_at": None, "kind": "series_hold", "step": None,
                      "reason": hold["hold"], "risk_count": None, "series_id": hold["series_id"]})

    to_check = [d for d in _newest_first(dramas)
                if d["id"] not in taken and _lines_done(d) and d.get("status") != "exported"
                and not _is_comic(d.get("media_type"))][:RISK_TITLES]
    checked_clean: set = set()
    for d in to_check:
        count = review_lines_service.risk_count(d["id"])
        if count > 0:
            needs.append({**_row(d), "kind": "check", "step": None, "reason": None, "risk_count": count,
                          "series_id": None})
            taken.add(d["id"])
        else:
            checked_clean.add(d["id"])

    running = [{**_row(by_id[i["drama_id"]]), "step": i["step"], "status": i["status"],
                "job_id": i["job_id"], "started_at": i["started_at"]}
               for i in items if i["status"] in _ACTIVE and i["drama_id"] in by_id]
    running.sort(key=lambda r: (r["status"] != "running",))
    busy = {r["drama_id"] for r in running}

    ready = [{**_row(d), "read_percent": d.get("read_percent") or 0.0}
             for d in _newest_first(dramas)
             if d["id"] not in taken and d["id"] not in busy
             and (d.get("read_percent") or 0.0) < _FINISHED_PERCENT
             and (_pages_done(d) if _is_comic(d.get("media_type"))
                  else _lines_done(d) and (d.get("status") == "exported" or d["id"] in checked_clean))]

    return {"needs_you": needs[:SHELF_LIMIT], "needs_you_count": len(needs),
            "running": running[:SHELF_LIMIT], "running_count": len(running),
            "ready": ready[:SHELF_LIMIT], "ready_count": len(ready)}
