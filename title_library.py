"""
title_library.py -- a searchable "known titles" discovery library,
separate from your working drama catalog. Two ways to populate it:

  1. Import from a public listing page URL (baihehub.com or any other
     public catalog/wiki) -- reuses the same bibliographic-metadata
     extraction as metadata_lookup.py. Only cataloging info (title,
     author, tags, a short synopsis) is stored, never the actual work.
  2. Add manually -- useful for Japanese/Korean titles, since baihehub
     is Chinese-focused and there's no single equivalent source wired
     up here yet.

Search works in any language: an English query gets translated to
Chinese before searching a Chinese-language database like baihehub,
so "give me the wandering princess story" can still find "流浪的公主"
-- results keep both the original title and an English rendering.

NOTE on baihehub.com specifically: its pages are a client-rendered
(Nuxt) app, but its search page reads from a public JSON API, which
`search_baihehub()` calls directly -- no browser rendering needed. If
that ever stops returning results, it falls back to handing you the
human-browsable search URL -- open that, copy the URL of anything
interesting, and import it from that URL in Discover instead.
"""

def translate_query_to_zh(query: str, engine) -> str:
    """Translates a search query into Chinese so it can be matched
    against a Chinese-language catalog. Returns the query unchanged if
    it looks like it's already Chinese, or if no engine is available."""
    if any("\u4e00" <= ch <= "\u9fff" for ch in query):
        return query  # already contains Chinese characters
    if engine is None or not getattr(engine, "supports_reference", False):
        return query
    from translate_engines import call_with_backoff
    translated = call_with_backoff(lambda: engine.translate_batch([query], {}))
    return translated[0] if translated else query


# baihehub.com's own search page queries this public Strapi API directly
# from the browser (verified 2026-09-26 by reading the site's search-page
# bundle and calling it): each collection is filtered with Strapi's
# `filters[$or][n][field][$contains]` syntax, the response is
# {"data": [...], "meta": {...}}, and each item's detail page on the site
# is /<collection>/<documentId>.
BAIHEHUB_API = "https://strapi.zhufree.fun/api"
BAIHEHUB_MAX_BYTES = 2_000_000

# (collection, fields the site's own search matches on, title field)
_BAIHEHUB_COLLECTIONS = [
    ("books", ["title", "searchKeyword"], "title"),
    ("audio-dramas", ["name", "intro"], "name"),
    ("manhuas", ["name", "intro"], "name"),
]


def _baihehub_items(data) -> list:
    """The item list out of a baihehub API response: a Strapi
    {"data": [...]} dict, or a bare list."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("data") or data.get("results") or []
        return items if isinstance(items, list) else []
    return []


def search_baihehub(query: str, timeout: int = 15, limit: int = 10):
    """Searches baihehub.com's books, audio dramas and manhua the same
    way the site's own search page does. Returns a list of {title, url,
    snippet} dicts, or None if nothing came back (caller should fall
    back to search_url_fallback() below)."""
    import json
    import requests
    from lib import capped_body
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TitleLibrary/1.0)", "Accept": "application/json"}
    results = []
    for collection, fields, title_field in _BAIHEHUB_COLLECTIONS:
        # requests URL-encodes the query (and the $/[] in the keys).
        params = {f"filters[$or][{i}][{f}][$contains]": query for i, f in enumerate(fields)}
        params["pagination[limit]"] = limit
        try:
            resp = requests.get(f"{BAIHEHUB_API}/{collection}", params=params,
                                headers=headers, timeout=timeout, stream=True)
            if resp.status_code != 200:
                resp.close()
                continue
            items = _baihehub_items(json.loads(capped_body.read_capped(
                resp, BAIHEHUB_MAX_BYTES, timeout * 3,
                lambda: ValueError("BaiheHub response too large"))))
        except (requests.RequestException, ValueError):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            doc_id = item.get("documentId") or item.get("id")
            if not doc_id:
                continue
            snippet = item.get("description") or item.get("intro") or item.get("summary") or ""
            results.append({
                "title": item.get(title_field) or item.get("title") or item.get("name") or "",
                "url": f"https://baihehub.com/{collection}/{doc_id}",
                "snippet": snippet[:200],
            })
    return results or None


def search_url_fallback(query: str) -> str:
    """The reliable fallback: a human-browsable search URL. Always
    works since it doesn't depend on guessing the API shape."""
    from urllib.parse import quote
    return f"https://baihehub.com/search?keyword={quote(query)}"


SEED_TITLES = [
    {
        "title_original": "女将军和长公主", "title_en": "The General and the Princess",
        "author": "请君莫笑", "tags": "military, romance, power couple",
        "summary_en": "A female general and a long princess navigate court politics and a growing "
                       "romance; adapted into an audio drama available on Fanjiao.",
        "summary_original": "", "source_name": "manual", "source_url": "",
        "language": "zh", "media_type": "audio_drama",
    },
    {
        "title_original": "余情可待", "title_en": "Lingering Affection",
        "author": "闵然", "tags": "fantasy, suspense, mystery, power couple",
        "summary_en": "A modern-setting arc (探虚陵现代篇) involving an ancient tomb discovery and a "
                       "developing relationship between the two leads; has a manhua adaptation.",
        "summary_original": "", "source_name": "manual", "source_url": "",
        "language": "zh", "media_type": "audio_drama",
    },
    {
        "title_original": "顾小姐和曲小姐", "title_en": "Miss Gu and Miss Qu",
        "author": "晚之", "tags": "divorce, futuristic, game elements, quarrelsome lovers, fluff",
        "summary_en": "Set in a near-future where a mid-life-crisis lead builds a dating game and "
                       "unexpectedly falls for a rival within it (中年恋爱补丁 arc).",
        "summary_original": "", "source_name": "manual", "source_url": "",
        "language": "zh", "media_type": "audio_drama",
    },
    {
        "title_original": "一叶孤舟", "title_en": "A Boat Adrift",
        "author": "牛耳耳 (Niu Erer)", "tags": "nonprofit, fan-translated, JJWXC original",
        "summary_en": "A single-season fan-translated audio drama based on a Jinjiang Literature City "
                       "original; framed around two people needing each other to cross a river, literally "
                       "and emotionally.",
        "summary_original": "", "source_name": "manual", "source_url": "",
        "language": "zh", "media_type": "audio_drama",
    },
    {
        "title_original": "春夏秋冬", "title_en": "Spring, Summer, Autumn, Winter",
        "author": "一盏夜灯", "tags": "slow burn, seasons, character-driven",
        "summary_en": "Follows the gradually deepening relationship between two leads, Shi Ci and "
                       "Tang Zhou, structured around the passage of the four seasons.",
        "summary_original": "", "source_name": "manual", "source_url": "",
        "language": "zh", "media_type": "audio_drama",
    },
    {
        "title_original": "狐媚惑主", "title_en": "Fox Charm Bewitches the Ruler",
        "author": "", "tags": "historical, palace intrigue, multi-arc, ensemble cast",
        "summary_en": "A multi-arc historical/palace-setting audio drama told across several "
                       "character pairings (芍洛, 燕棠, 绯潋, 宛芋 arcs), each a largely self-contained story.",
        "summary_original": "", "source_name": "manual", "source_url": "",
        "language": "zh", "media_type": "audio_drama",
    },
    {
        "title_original": "破雪", "title_en": "Breaking Snow",
        "author": "", "tags": "entertainment industry, celebrity, rivals-to-lovers",
        "summary_en": "Set in the entertainment industry -- an established actress and a rising star "
                       "navigate public rivalry while secretly together, playing against a tabloid-fueled "
                       "fandom war.",
        "summary_original": "", "source_name": "manual", "source_url": "",
        "language": "zh", "media_type": "audio_drama",
    },
    {
        "title_original": "白月光Omega总想拥有我", "title_en": "The White Moonlight Omega Always Wants Me",
        "author": "", "tags": "ABO, transmigration, modern setting, explicitly tagged baihe",
        "summary_en": "A modern transmigration-into-a-book ABO-setting baihe audio drama, currently "
                       "in its second season on the Fanjiao app.",
        "summary_original": "", "source_name": "manual", "source_url": "",
        "language": "zh", "media_type": "audio_drama",
    },
]


def seed_known_titles(db_module):
    """Inserts the starter title set if not already present (checked by
    title_original to avoid duplicates on repeat runs). Returns how
    many were newly added."""
    existing_titles = {t["title_original"] for t in db_module.list_known_titles()}
    added = 0
    for entry in SEED_TITLES:
        if entry["title_original"] not in existing_titles:
            db_module.create_known_title(**entry)
            added += 1
    return added
