"""sources/store_domains.py -- Image-hash memory, the extraction-result cache and per-source domain lists and proposals.

Moved out of sources/store.py to keep it under the size ceiling. Import these
names from `sources.store`, which re-exports them at its end; nothing imports
this module first, because it needs `sources.store` fully defined.
"""

import json
import time


from sources.store import (connect, get_setting, set_setting)


# ---------------------------------------------------------------------------
# Generic-import image memory (cross-chapter duplicate filter)
# ---------------------------------------------------------------------------

def remember_images(domain: str, chapter_url: str, hashes):
    with connect() as conn:
        conn.executemany("INSERT OR IGNORE INTO seen_images(domain, sha256, chapter_url) "
                         "VALUES(?, ?, ?)", [(domain, h, chapter_url) for h in hashes])


def hashes_seen_elsewhere(domain: str, chapter_url: str, hashes) -> set:
    """Of `hashes`, the ones already seen on a *different* chapter URL of
    the same site -- a logo/ad/nav image, not a real page."""
    hashes = list(hashes)
    if not hashes:
        return set()
    marks = ",".join("?" * len(hashes))
    with connect() as conn:
        rows = conn.execute(f"SELECT DISTINCT sha256 FROM seen_images WHERE domain=? "
                            f"AND chapter_url<>? AND sha256 IN ({marks})",
                            (domain, chapter_url, *hashes)).fetchall()
    return {r["sha256"] for r in rows}


# ---------------------------------------------------------------------------
# Extraction-result cache -- keyed by a hash of what the
# model read, so an unchanged page never costs a second LLM call. Separate
# from the raw-content cache (cache_index), which is about not re-fetching.
# ---------------------------------------------------------------------------

def get_extraction(kind: str, content_hash: str):
    with connect() as conn:
        row = conn.execute("SELECT data FROM extraction_cache WHERE kind=? AND content_hash=?",
                           (kind, content_hash)).fetchone()
    return json.loads(row["data"]) if row else None


def put_extraction(kind: str, content_hash: str, url: str, entry: dict):
    with connect() as conn:
        conn.execute("INSERT INTO extraction_cache(kind, content_hash, url, data, created_at) "
                     "VALUES(?, ?, ?, ?, ?) ON CONFLICT(kind, content_hash) DO UPDATE SET "
                     "url=excluded.url, data=excluded.data, created_at=excluded.created_at",
                     (kind, content_hash, url, json.dumps(entry, ensure_ascii=False), time.time()))


def delete_extraction(kind: str, content_hash: str):
    with connect() as conn:
        conn.execute("DELETE FROM extraction_cache WHERE kind=? AND content_hash=?",
                     (kind, content_hash))


# ---------------------------------------------------------------------------
# Domain lists (sources/domains.py). The list and the last domain that
# worked are settings, one pair of keys per source; a host found by
# a redirect is only a proposal until the owner confirms it.
# ---------------------------------------------------------------------------

def _domains_key(source: str) -> str:
    return f"source_domains.{source}"


def _last_good_key(source: str) -> str:
    return f"source_domain_last_good.{source}"


def domain_list(source: str):
    """The owner's saved domain list (https origins), or None when the
    adapter's own `base_urls` apply."""
    value = get_setting(_domains_key(source))
    return [str(v) for v in value] if isinstance(value, list) and value else None


def set_domain_list(source: str, origins):
    set_setting(_domains_key(source), list(origins) if origins else None)


def last_good_domain(source: str):
    value = get_setting(_last_good_key(source))
    return str(value) if value else None


def set_last_good_domain(source: str, origin):
    set_setting(_last_good_key(source), origin or None)


MAX_PENDING_PROPOSALS = 5


def propose_domain(source: str, host: str, now: float = None) -> bool:
    """Records a pending proposal (`host` is host[:port]). False when the
    host was already proposed, was dismissed before (a dismissed host is not
    proposed again), or the source already has MAX_PENDING_PROPOSALS
    waiting."""
    # One statement: the count and the insert run under the same write lock.
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO domain_proposals(source, host, found_at) SELECT ?, ?, ? "
            "WHERE (SELECT COUNT(*) FROM domain_proposals WHERE source=? AND dismissed=0) < ?",
            (source, host, time.time() if now is None else now, source, MAX_PENDING_PROPOSALS))
    return cur.rowcount > 0


def domain_proposals(source: str = None) -> list:
    """Pending (not dismissed) proposals, newest first."""
    sql = "SELECT source, host, found_at FROM domain_proposals WHERE dismissed=0"
    args = ()
    if source is not None:
        sql += " AND source=?"
        args = (source,)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY found_at DESC", args)]


def dismiss_domain_proposal(source: str, host: str) -> bool:
    with connect() as conn:
        cur = conn.execute("UPDATE domain_proposals SET dismissed=1 "
                           "WHERE source=? AND host=? AND dismissed=0", (source, host))
    return cur.rowcount > 0


def delete_domain_proposal(source: str, host: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM domain_proposals WHERE source=? AND host=? AND dismissed=0",
                           (source, host))
    return cur.rowcount > 0
