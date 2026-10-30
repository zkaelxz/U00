"""Known titles: the searchable discovery library of titles, kept apart from the dramas
you are working on until one is imported into `dramas`."""

import contextlib
from lib import clock

import db


def create_known_title(**fields) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        fields.setdefault("source_name", "manual")
        fields["created_at"] = clock.utcnow().isoformat()
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(f"INSERT INTO known_titles ({cols}) VALUES ({placeholders})", list(fields.values()))
        conn.commit()
        new_id = cur.lastrowid
    return new_id


def list_known_titles(search: str = "", language: str = "", media_type: str = "", source_name: str = ""):
    with contextlib.closing(db.get_conn()) as conn:
        query = "SELECT * FROM known_titles WHERE 1=1"
        params = []
        if search:
            query += " AND (title_original LIKE ? OR title_en LIKE ? OR tags LIKE ? OR author LIKE ?)"
            like = f"%{search}%"
            params += [like, like, like, like]
        if language:
            query += " AND language = ?"
            params.append(language)
        if media_type:
            query += " AND media_type = ?"
            params.append(media_type)
        if source_name:
            query += " AND source_name = ?"
            params.append(source_name)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def delete_known_title(title_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM known_titles WHERE id = ?", (title_id,))
        conn.commit()
