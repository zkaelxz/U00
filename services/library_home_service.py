"""services/library_home_service.py -- the Library's Running line: the steps the PC is working on or has waiting.

Read-only. Built from what the PC queue already keeps, so the line and the pages it links to always agree.
"""

from services import library_service, queue_service

# Enough to fill a phone screen; the Jobs page lists the rest.
SHELF_LIMIT = 8
_ACTIVE = ("waiting", "running")


def _title(d: dict) -> str:
    return d.get("title_en") or d.get("title_zh") or f"#{d['id']}"


def _row(d: dict) -> dict:
    return {"drama_id": d["id"], "title": _title(d), "media_type": d.get("media_type"),
            "content_mode": d.get("content_mode"), "has_cover_art": bool(d.get("cover_art_filename")),
            "updated_at": d.get("updated_at")}


def get_home(principal=None) -> dict:
    dramas = library_service.list_library_dramas(principal=principal)
    by_id = {d["id"]: d for d in dramas}
    queue = queue_service.get_queue(principal)

    running = [{**_row(by_id[i["drama_id"]]), "step": i["step"], "status": i["status"],
                "job_id": i["job_id"], "started_at": i["started_at"]}
               for i in queue["items"] if i["status"] in _ACTIVE and i["drama_id"] in by_id]
    running.sort(key=lambda r: (r["status"] != "running",))

    return {"running": running[:SHELF_LIMIT], "running_count": len(running),
            "can_control_queue": bool(queue.get("can_control"))}
