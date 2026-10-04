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
import threading
import time
import weakref

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

# Imports run as threads in one process, each with its own RawCache, and
# share one cache_index. An import's release() drops only the temporary
# rows it stored itself and no other live cache still holds; _lock keeps
# a put() from landing between a release's read and its file removal.
_lock = threading.Lock()
_live = weakref.WeakSet()
_STALE_TEMPORARY_SECONDS = 86400


class RawCache:
    def __init__(self, mode: str = None):
        self.mode = mode or store.get_setting("cache_mode") or "temporary"
        if self.mode not in MODES:
            raise ValueError(f"Unknown cache mode {self.mode!r}; expected one of {MODES}")
        self.hits = 0
        self._temporary_urls = set()

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
        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:   # cleared or trimmed (enforce_ceiling) meanwhile
            with store.connect() as conn:
                conn.execute("DELETE FROM cache_index WHERE url=?", (url,))
            return None
        self.hits += 1
        # A hit counts as use for the size ceiling (enforce_ceiling):
        # created_at doubles as "last used". put() already rewrites it on
        # every store and nothing else reads it, so this needs no new
        # cache_index column (and no store._ADDED_COLUMNS entry).
        with store.connect() as conn:
            conn.execute("UPDATE cache_index SET created_at=? WHERE url=?", (time.time(), url))
        return data

    def put(self, url: str, content: bytes):
        if self.mode == "none" or content is None:
            return
        sha = hashlib.sha256(content).hexdigest()
        path = self._path(sha)
        retention = "keep" if self.mode in _PERSISTENT else "temporary"
        with _lock:
            if retention == "temporary":
                self._temporary_urls.add(url)
                _live.add(self)
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    f.write(content)
                os.replace(tmp, path)
            with store.connect() as conn:
                conn.execute("INSERT INTO cache_index(url, sha256, size, retention, created_at) "
                             "VALUES(?, ?, ?, ?, ?) ON CONFLICT(url) DO UPDATE SET "
                             "sha256=excluded.sha256, size=excluded.size, "
                             "retention=excluded.retention, created_at=excluded.created_at",
                             (url, sha, len(content), retention, time.time()))

    def release(self):
        """End of an import: drop the temporary rows this cache stored,
        except URLs another live cache (a concurrent import) also stored,
        plus day-old temporary rows nothing live holds (left by a crashed
        import). Content still referenced by any remaining row survives."""
        with _lock:
            mine, self._temporary_urls = self._temporary_urls, set()
            _live.discard(self)
            others = set().union(*(c._temporary_urls for c in list(_live)))
            cutoff = time.time() - _STALE_TEMPORARY_SECONDS
            with store.connect() as conn:
                doomed = [(r["url"], r["sha256"]) for r in conn.execute(
                    "SELECT url, sha256, created_at FROM cache_index WHERE retention='temporary'")
                    if r["url"] not in others
                    and (r["url"] in mine or r["created_at"] < cutoff)]
                conn.executemany("DELETE FROM cache_index WHERE url=? AND retention='temporary'",
                                 [(u,) for u, _ in doomed])
                still_used = {r["sha256"] for r in conn.execute(
                    "SELECT DISTINCT sha256 FROM cache_index")}
            for sha in {s for _, s in doomed} - still_used:
                try:
                    os.remove(self._path(sha))
                except FileNotFoundError:
                    pass

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
        files more than a day old that nothing indexes: `.part` files a
        crashed put() left behind, and files an earlier trim couldn't
        remove. Returns the number of files removed by the ceiling."""
        self._drop_stale_orphans()
        if max_mb is None:
            max_mb = store.get_setting("cache_max_mb") or 0
        if max_mb <= 0:
            return 0
        ceiling = int(max_mb * 1024 * 1024)
        with store.connect() as conn:
            # One write transaction from the read to the delete, so a row
            # another import stores meanwhile is seen or waits.
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT sha256, MAX(size) AS size, MAX(created_at) AS used, "
                "SUM(retention != 'keep') AS pinned FROM cache_index "
                "GROUP BY sha256 ORDER BY used ASC").fetchall()
            # Only kept content counts: an import's temporary rows are
            # released when it ends and can't be trimmed before then.
            rows = [r for r in rows if not r["pinned"]]
            total = sum(r["size"] for r in rows)
            doomed = []
            for r in rows:
                if total <= ceiling:
                    break
                doomed.append(r["sha256"])
                total -= r["size"]
            conn.executemany("DELETE FROM cache_index WHERE sha256=? AND retention='keep'",
                             [(s,) for s in doomed])
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
            except OSError:   # e.g. open elsewhere on Windows; keep trimming the rest
                import applog
                applog.get_logger().warning("Could not remove a cached file", exc_info=True)
        return removed

    def _drop_stale_orphans(self, max_age: float = 86400):
        cutoff = time.time() - max_age
        with store.connect() as conn:
            indexed = {r["sha256"] for r in conn.execute(
                "SELECT DISTINCT sha256 FROM cache_index")}
        for dirpath, _, files in os.walk(self.root):
            for name in files:
                if name in indexed:
                    continue
                path = os.path.join(dirpath, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                except OSError:
                    pass

    def stats(self) -> dict:
        with store.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM cache_index").fetchone()
            # Bytes on disk: content shared by several URLs is stored once,
            # and the size ceiling (enforce_ceiling) measures it the same way.
            size = conn.execute("SELECT COALESCE(SUM(size), 0) AS bytes FROM "
                                "(SELECT MAX(size) AS size FROM cache_index GROUP BY sha256)"
                                ).fetchone()
        return {"entries": row["n"], "bytes": size["bytes"], "mode": self.mode}
