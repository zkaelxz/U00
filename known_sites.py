"""
known_sites.py -- a curated reference list of well-known, official/
licensed platforms for baihe (Chinese GL), Korean GL, and Japanese
yuri content, across audio drama, novel, and comic formats.

This is purely a directory of legitimate publisher/platform names and
homepage URLs -- the same kind of information found in library
research guides or "where to read legally" articles. It doesn't
include scanlation/aggregator sites, and it's used only to pre-fill
the Navigator and metadata-lookup tools with a starting point; you
still need your own account/access on whichever platform you use.

Entries intentionally kept high-level (platform name, homepage,
region, formats, language, a one-line note) -- no scraping logic or
per-title URLs live here.
"""

KNOWN_SITES = [
    # ---- Chinese baihe ----
    {
        "name": "JJWXC (晋江文学城)",
        "url": "https://www.jjwxc.net",
        "region": "China", "language": "zh",
        "content_types": ["novel"],
        "notes": "Major Chinese web novel platform; large baihe/GL catalog, paid + free chapters.",
    },
    {
        "name": "Fanjiao (饭角)",
        "url": "https://www.fanjiao.cc",
        "region": "China", "language": "zh",
        "content_types": ["audio_drama"],
        "notes": "Dedicated baihe/GL audio drama app.",
    },
    {
        "name": "MissEvan / Maoer FM (猫耳FM)",
        "url": "https://www.missevan.com",
        "region": "China", "language": "zh",
        "content_types": ["audio_drama"],
        "notes": "Broad Chinese audio drama platform, not baihe-specific but hosts many titles.",
    },
    {
        "name": "Kuaikan Manhua (快看漫画)",
        "url": "https://www.kuaikanmanhua.com",
        "region": "China", "language": "zh",
        "content_types": ["manhua"],
        "notes": "One of the largest Chinese manhua platforms.",
    },
    {
        "name": "Bilibili Comics (哔哩哔哩漫画)",
        "url": "https://manga.bilibili.com",
        "region": "China", "language": "zh",
        "content_types": ["manhua"],
        "notes": "Manhua/manga platform with an official English app for some titles.",
    },

    # ---- Korean GL ----
    {
        "name": "Naver Webtoon / Naver Series",
        "url": "https://series.naver.com",
        "region": "Korea", "language": "ko",
        "content_types": ["manhwa", "novel"],
        "notes": "Largest Korean webtoon/web-novel platform; global arm is WEBTOON.",
    },
    {
        "name": "Lezhin Comics",
        "url": "https://www.lezhin.com",
        "region": "Korea", "language": "ko",
        "content_types": ["manhwa"],
        "notes": "Known for the strongest licensed GL catalog among Korean platforms; also has an English storefront.",
    },
    {
        "name": "KakaoPage / Kakao Webtoon",
        "url": "https://page.kakao.com",
        "region": "Korea", "language": "ko",
        "content_types": ["manhwa", "novel"],
        "notes": "Major Korean platform, wait-or-pay chapter model.",
    },
    {
        "name": "Ridibooks (리디북스)",
        "url": "https://ridibooks.com",
        "region": "Korea", "language": "ko",
        "content_types": ["manhwa", "novel"],
        "notes": "Korean ebook/webtoon store, buy-to-own; go-to for a lot of licensed BL/GL.",
    },
    {
        "name": "Bomtoon",
        "url": "https://www.bomtoon.com",
        "region": "Korea", "language": "ko",
        "content_types": ["manhwa"],
        "notes": "Mature webtoon platform, primarily BL-focused but carries some GL.",
    },
    {
        "name": "Tappytoon",
        "url": "https://www.tappytoon.com",
        "region": "Korea (global storefront)", "language": "ko",
        "content_types": ["manhwa"],
        "notes": "English-language storefront for licensed Korean manhwa, notable BL/GL section.",
    },
    {
        "name": "Munpia (문피아)",
        "url": "https://www.munpia.com",
        "region": "Korea", "language": "ko",
        "content_types": ["novel"],
        "notes": "Major Korean web novel platform (general catalog, not GL-specific).",
    },

    # ---- Japanese yuri ----
    {
        "name": "BookWalker",
        "url": "https://bookwalker.jp",
        "region": "Japan (JP + Global storefronts)", "language": "ja",
        "content_types": ["manga", "novel"],
        "notes": "Kadokawa's ebook store for manga/light novels; JP site has the full original catalog, "
                 "Global site only has titles licensed for English release.",
    },
    {
        "name": "ComicWalker",
        "url": "https://comic-walker.com",
        "region": "Japan", "language": "ja",
        "content_types": ["manga"],
        "notes": "Kadokawa's webtoon/manga reading platform, some free chapters.",
    },
    {
        "name": "DLsite",
        "url": "https://www.dlsite.com",
        "region": "Japan", "language": "ja",
        "content_types": ["audio_drama", "manga", "novel"],
        "notes": "Doujin digital storefront; 'DLsite Sound' covers audio dramas/situation CDs "
                 "(a major source of independently-produced yuri audio drama), 'DLsite Play' covers manga.",
    },
    {
        "name": "Comic Yuri Hime (コミック百合姫)",
        "url": "https://yurihime.jp",
        "region": "Japan", "language": "ja",
        "content_types": ["manga"],
        "notes": "Ichijinsha's dedicated yuri manga anthology magazine -- the flagship official yuri publication.",
    },
    {
        "name": "Fantia",
        "url": "https://fantia.jp",
        "region": "Japan", "language": "ja",
        "content_types": ["audio_drama"],
        "notes": "Creator subscription platform; some voice/audio-drama circles distribute yuri work here.",
    },
]


def list_sites(content_type: str = None, language: str = None):
    results = KNOWN_SITES
    if content_type:
        results = [s for s in results if content_type in s["content_types"]]
    if language:
        results = [s for s in results if s["language"] == language]
    return results


# ---------------------------------------------------------------------------
# Finding where a work exists
# ---------------------------------------------------------------------------

def _q(text: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(text)


def build_search_links(query: str, language: str = None, content_type: str = None,
                        genre_hint: str = "百合"):
    """
    Builds real search URLs for finding a title across the official
    platforms, rather than pretending to search them from inside the app.

    Uses site-scoped web search rather than each platform's internal
    search URL. That's deliberate: internal URL schemes differ per site,
    change without notice, and several of these platforms render results
    with JavaScript so a scraped query returns nothing anyway. A
    site-scoped search works for every one of them and stays working.

    Returns [{"site", "url", "kind"}] -- 'kind' is "web" for a scoped web
    search, "direct" where the platform has a stable search URL worth
    using directly.
    """
    if not (query or "").strip():
        return []
    q = query.strip()
    links = []

    for site in list_sites(content_type=content_type, language=language):
        domain = site["url"].split("//")[-1].split("/")[0]
        terms = f"site:{domain} {q}"
        if genre_hint and site["language"] == "zh":
            terms += f" {genre_hint}"
        links.append({
            "site": site["name"],
            "url": f"https://www.google.com/search?q={_q(terms)}",
            "kind": "web",
            "note": f"Search {site['name']} via web search",
        })

    # A couple of platforms have stable, simple search URLs worth hitting directly.
    if not language or language == "zh":
        links.append({
            "site": "JJWXC (direct search)",
            "url": f"https://www.jjwxc.net/search.php?kw={_q(q)}",
            "kind": "direct",
            "note": "JJWXC's own search page",
        })
        links.append({
            "site": "baihehub (direct search)",
            "url": f"https://baihehub.com/search?keyword={_q(q)}",
            "kind": "direct",
            "note": "baihehub's own search -- renders with JavaScript, so open it in a browser",
        })

    # A general search is often the fastest way to find which platform carries a work.
    links.insert(0, {
        "site": "General web search",
        "url": f"https://www.google.com/search?q={_q(f'{q} {genre_hint} 广播剧 OR 小说')}",
        "kind": "web",
        "note": "Broad search across all sites -- usually the quickest way to find who has it",
    })
    return links


def jjwxc_tag_url(tag: str = "百合", page: int = 1) -> str:
    """
    URL for browsing a JJWXC tag listing (their Lily/baihe tag being the
    obvious one). Returned for you to open in a browser -- the listing is
    behind their own search interface, so this is a starting point for
    browsing, not something the app reads automatically.
    """
    return f"https://www.jjwxc.net/bookbase.php?bq={_q(tag)}&page={page}"
