"""
services/assistant_backlog_service.py -- the maintenance assistant's backlog
(bug / feature / note items). Written only by the user through its own
routes; the assistant can only suggest items. UI-free.
"""

import contextlib
import time

import db
from services.assistant_repo_access import _redact
from lib.errors import ConflictError, InvalidInputError, NotFoundError


MAX_BACKLOG_TEXT = 1000
MAX_BACKLOG_ITEMS = 500
BACKLOG_KINDS = ("bug", "feature", "note")


def list_backlog() -> dict:
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT id, kind, text, created_at FROM assistant_backlog "
                            "ORDER BY id DESC").fetchall()
    return {"items": [{"id": r["id"], "kind": r["kind"], "text": r["text"],
                       "created_at": r["created_at"]} for r in rows]}


def add_backlog_item(kind: str, text: str) -> dict:
    if kind not in BACKLOG_KINDS:
        raise InvalidInputError(f"kind must be one of {', '.join(BACKLOG_KINDS)}.")
    if not isinstance(text, str) or not text.strip():
        raise InvalidInputError("text is required.")
    if len(text) > MAX_BACKLOG_TEXT:
        raise InvalidInputError(f"text must be at most {MAX_BACKLOG_TEXT} characters.")
    text = _redact(text.strip())
    created = time.strftime("%Y-%m-%dT%H:%M:%S")
    with contextlib.closing(db.get_conn()) as conn:
        count = conn.execute("SELECT COUNT(*) FROM assistant_backlog").fetchone()[0]
        if count >= MAX_BACKLOG_ITEMS:
            raise ConflictError(f"The backlog is full ({MAX_BACKLOG_ITEMS} items). Delete some first.")
        cur = conn.execute("INSERT INTO assistant_backlog (kind, text, created_at) VALUES (?, ?, ?)",
                           (kind, text, created))
        conn.commit()
        item_id = cur.lastrowid
    return {"id": item_id, "kind": kind, "text": text, "created_at": created}


def delete_backlog_item(item_id: int, confirm: bool = False) -> dict:
    if confirm is not True:
        raise InvalidInputError("Deleting a backlog item needs confirm=true.")
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("DELETE FROM assistant_backlog WHERE id = ?", (int(item_id),))
        conn.commit()
    if cur.rowcount == 0:
        raise NotFoundError("No such backlog item.")
    return {"deleted": True}


def clear_backlog(confirm: bool = False) -> dict:
    if confirm is not True:
        raise InvalidInputError("Clearing the backlog needs confirm=true.")
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("DELETE FROM assistant_backlog")
        conn.commit()
    return {"deleted": cur.rowcount}
