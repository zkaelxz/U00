"""
sources/cache.py -- the configurable raw-content cache (Step 23 item 4).

The point isn't saving Baihe money on re-OCR (that costs next to
nothing); it's not re-hitting a rate-limited external source for content
that hasn't changed. Content is stored once per SHA-256, so the same
image reached through two URLs is kept on disk once.

Modes:
  none            -- nothing is written; every fetch goes to the source.
  temporary       -- kept for the length of one import, then cleared.
  keep_originals  -- raw downloads kept until cleared by hand.
  keep_translated -- raw downloads cleared after use like `temporary`;
                     the translated output already lives in Scanlate's
                     own pages folder, so nothing extra is kept here.
  keep_both       -- raw downloads kept (translated output lives in
                     Scanlate either way).
"""

import hashlib
import os
import time

from . import store

MODES = ["none", "temporary", "keep_originals", "keep_translated", "keep_both"]
MODE_LABELS = {
    "none": "Don't retain anything",
    "temporary": "Temporary (cleared after each import)",
    "keep_originals": "Keep originals",
    "keep_translated": "Keep translated only (originals cleared after each import)",
    "keep_both": "Keep originals and translated",
}
_PERSISTENT = {"keep_originals", "keep_both"}


class RawCache:
    def __init__(self, mode: str = None):
        self.mode = mode or store.get_setting("cache_mode") or "temporary"
        if self.mode not in MODES:
            raise ValueError(f"Unknown cache mode {self.mode!r}; expected one of {MODES}")
        self.hits = 0

    @property
    def root(self) -> str:
        return store.cache_dir()

    def _path(self, sha: str) -> str:
        return os.path.join(self.root, sha[:2], sha)

    def get(self, url: str):
        if self.mode == "none":
            return None
        with store.connect() as conn:
            row = conn.execute("SELECT sha256 FROM cache_index WHERE url=?", (url,)).fetchone()
        if not row:
            return None
        path = self._path(row["sha256"])
        if not os.path.exists(path):
            with store.connect() as conn:
                conn.execute("DELETE FROM cache_index WHERE url=?", (url,))
            return None
        self.hits += 1
        with open(path, "rb") as f:
            data = f.read()
        # A hit counts as use for the size ceiling (enforce_ceiling):
        # created_at doubles as "last used". put() already rewrites it on
        # every store and nothing else reads it, so this needs no new
        # column in a schema that has no migration path.
        with store.connect() as conn:
            conn.execute("UPDATE cache_index SET created_at=? WHERE url=?", (time.time(), url))
        return data

    def put(self, url: str, content: bytes):
        if self.mode == "none" or content is None:
            return
        sha = hashlib.sha256(content).hexdigest()
        path = self._path(sha)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(content)
            os.replace(tmp, path)
        retention = "keep" if self.mode in _PERSISTENT else "temporary"
        with store.connect() as conn:
            conn.execute("INSERT INTO cache_index(url, sha256, size, retention, created_at) "
                         "VALUES(?, ?, ?, ?, ?) ON CONFLICT(url) DO UPDATE SET "
                         "sha256=excluded.sha256, size=excluded.size, "
                         "retention=excluded.retention, created_at=excluded.created_at",
                         (url, sha, len(content), retention, time.time()))

    def release(self):
        """End of an import: drop everything this cache holds only
        temporarily. Content still referenced by a `keep` entry survives."""
        self._delete_where("retention='temporary'")

    def clear_all(self):
        self._delete_where("1=1")

    def _delete_where(self, clause: str):
        with store.connect() as conn:
            doomed = [r["sha256"] for r in conn.execute(
                f"SELECT DISTINCT sha256 FROM cache_index WHERE {clause}")]
            conn.execute(f"DELETE FROM cache_index WHERE {clause}")
            still_used = {r["sha256"] for r in conn.execute("SELECT DISTINCT sha256 FROM cache_index")}
        for sha in doomed:
            if sha in still_used:
                continue
            try:
                os.remove(self._path(sha))
            except FileNotFoundError:
                pass

    def enforce_ceiling(self, max_mb: float = None):
        """Roadmap 111: shrink what the keep modes retain to the
        `cache_max_mb` setting (0 = no limit). Works per distinct content
        (one file can back several URLs), least recently used first, and
        only removes content every row of which is `keep` -- content an
        import in progress holds as `temporary` is left alone. Also drops
        `.part` files a crashed put() left behind more than a day ago.
        Returns the number of files removed."""
        self._drop_stale_parts()
        if max_mb is None:
            max_mb = store.get_setting("cache_max_mb") or 0
        if max_mb <= 0:
            return 0
        ceiling = int(max_mb * 1024 * 1024)
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT sha256, MAX(size) AS size, MAX(created_at) AS used, "
                "SUM(retention != 'keep') AS pinned FROM cache_index "
                "GROUP BY sha256 ORDER BY used ASC").fetchall()
            total = sum(r["size"] for r in rows)
            doomed = []
            for r in rows:
                if total <= ceiling:
                    break
                if r["pinned"]:
                    continue
                doomed.append(r["sha256"])
                total -= r["size"]
            conn.executemany("DELETE FROM cache_index WHERE sha256=?", [(s,) for s in doomed])
            still_used = {r["sha256"] for r in conn.execute(
                "SELECT DISTINCT sha256 FROM cache_index")}
        removed = 0
        for sha in doomed:
            if sha in still_used:   # re-stored by a concurrent put()
                continue
            try:
                os.remove(self._path(sha))
                removed += 1
            except FileNotFoundError:
                pass
        return removed

    def _drop_stale_parts(self, max_age: float = 86400):
        cutoff = time.time() - max_age
        for dirpath, _, files in os.walk(self.root):
            for name in files:
                if not name.endswith(".part"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                except OSError:
                    pass

    def stats(self) -> dict:
        with store.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes "
                               "FROM cache_index").fetchone()
        return {"entries": row["n"], "bytes": row["bytes"], "mode": self.mode}
