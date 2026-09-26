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

NOTE on baihehub.com specifically: its search page is a client-rendered
app (React/Vue), so I can't verify its live JSON API from a static
fetch in the environment this was built in. `search_baihehub()` tries
a best-effort guess at the API and falls back cleanly to just handing
you the human-browsable search URL if the guess is wrong -- open that,
copy the URL of anything interesting, and use `import_title_from_url()`
on it instead. That fallback path always works regardless of the API.
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


def search_baihehub(query: str, timeout: int = 15):
    """Best-effort search against baihehub.com. Tries a couple of
    plausible API endpoint shapes; returns a list of {title, url,
    snippet} dicts on success, or None if none of the guesses worked
    (caller should fall back to search_url_fallback() below)."""
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TitleLibrary/1.0)", "Accept": "application/json"}
    candidate_endpoints = [
        f"https://baihehub.com/api/search?keyword={query}",
        f"https://baihehub.com/api/v1/search?q={query}",
        f"https://baihehub.com/api/search?q={query}",
    ]
    for url in candidate_endpoints:
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code != 200:
                continue
            data = resp.json()
            items = data.get("data") or data.get("results") or data if isinstance(data, list) else []
            if not items:
                continue
            results = []
            for item in items[:20]:
                if not isinstance(item, dict):
                    continue
                title = item.get("title") or item.get("name") or ""
                slug = item.get("id") or item.get("slug") or ""
                results.append({
                    "title": title,
                    "url": item.get("url") or f"https://baihehub.com/audio-dramas/{slug}",
                    "snippet": item.get("summary") or item.get("description") or "",
                })
            if results:
                return results
        except Exception:
            continue
    return None


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


def import_title_from_url(url: str, engine, media_type_hint: str = None,
                           allow_render: bool = True):
    """Fetches a listing page and extracts catalog metadata for the
    known_titles library. Returns (entry_dict, status) so the caller can
    distinguish "this page had no metadata" from "we couldn't actually
    read this page" -- which used to look identical."""
    import page_fetch, metadata_lookup
    result = page_fetch.smart_fetch(url, allow_render=allow_render)
    if not result["text"].strip():
        return {}, {"ok": False, "message": result["message"],
                    "needs_manual": result["needs_manual"]}

    meta = metadata_lookup.extract_metadata_llm(result["text"], engine)
    if not meta:
        return {}, {"ok": False, "needs_manual": result["needs_manual"],
                    "message": "Read the page but found no title metadata. " + result["message"]}

    media_type = media_type_hint
    if not media_type:
        if "/audio-dramas/" in url or "audio" in url:
            media_type = "audio_drama"
        elif "/books/" in url or "novel" in url:
            media_type = "novel"
        elif "/manhuas/" in url or "manga" in url or "manhwa" in url:
            media_type = "manhua"
        else:
            media_type = "other"

    return {
        "title_original": meta.get("title_zh") or meta.get("title_en") or "",
        "title_en": meta.get("title_en") or "",
        "author": meta.get("author") or "",
        "tags": "",
        "summary_en": meta.get("summary") or "",
        "summary_original": "",
        "source_name": "baihehub" if "baihehub.com" in url else "imported",
        "source_url": url,
        "language": "zh",
        "media_type": media_type,
    }, {"ok": True, "needs_manual": False, "message": result["message"]}
