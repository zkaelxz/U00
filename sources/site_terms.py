"""
sources/site_terms.py -- terms findings for sites that have no adapter
but whose terms have been read directly (roadmap Step 23k item 4).

A pasted URL with no adapter is fetched as the shared "generic" source,
whose capability record can't carry one particular site's terms. This
table is where those per-site findings live, so the capability check
refuses a restricted site before anything is sent -- including before a
sign-in window is ever opened for it.

Signing in answers "can the person see this", never "does the site
permit automated extraction of it". Nothing in this table changes because
a session is authenticated, and nothing here is ever cleared by default:
a site whose terms couldn't be read stays UNKNOWN, not PERMITTED.

Every entry cites what was actually read (the roadmap's §6 vetting notes).
Add a site only with a directly-read finding, quoted where possible.
"""

from urllib.parse import urlsplit

from .models import AiMlUse, AutomationPermission, SourceCapabilities

SITE_TERMS = [
    {
        "domains": ("naver.com",),
        "platform": "Naver (Series / Webtoon)",
        "automation_permission": AutomationPermission.EXPLICITLY_RESTRICTED.value,
        "ai_ml_use": AiMlUse.UNKNOWN.value,
        "terms": {
            "read": "Naver's umbrella terms of service, read in full (2026-09-27).",
            "clause": "Bans \"automated means (e.g. macro programs, robots/bots, spiders, "
                      "scrapers)\" for collecting posted content; a separate clause bans "
                      "circumventing anti-abuse measures.",
            "unverified": "comic.naver.com (Naver Webtoon) is covered by inference from the "
                          "umbrella terms, not a separate direct read of that subdomain.",
        },
    },
    {
        "domains": ("novelpia.com",),
        "platform": "Novelpia",
        "automation_permission": AutomationPermission.EXPLICITLY_RESTRICTED.value,
        "ai_ml_use": AiMlUse.UNKNOWN.value,
        "terms": {
            "read": "Novelpia's terms of service and robots.txt, read directly (2026-09-27).",
            "clause": "Bans \"computer programs, automated means, scripts, bots\" for abnormal "
                      "access/collection, separately bans content-extraction/screenshot tools, "
                      "and names unauthorized extraction as grounds for account termination.",
            "robots_txt": "Disallow: / for all clients except a few named search engines.",
        },
    },
    {
        "domains": ("jjwxc.net",),
        "platform": "JJWXC (晋江文学城)",
        "automation_permission": AutomationPermission.EXPLICITLY_RESTRICTED.value,
        "ai_ml_use": AiMlUse.UNKNOWN.value,
        "terms": {
            "read": "my.jjwxc.net/register/registerRule.php, read directly.",
            "clause": "§4.3 bans any manner of crawling or scraping (爬取/抓取) of its database "
                      "materials; §4.9 invokes civil and criminal liability for serious "
                      "violations. Never attempted, whatever access the app has.",
        },
    },
    {
        "domains": ("page.kakao.com",),
        "platform": "KakaoPage",
        "automation_permission": AutomationPermission.UNKNOWN.value,
        "ai_ml_use": AiMlUse.UNKNOWN.value,
        "terms": {
            "read": "page.kakao.com/policy/terms exists but is a client-rendered shell; the "
                    "clause text could not be retrieved.",
            "unverified": "Not cleared: UNKNOWN is not PERMITTED.",
        },
    },
    {
        # roadmap Step 91 investigation. No adapter -- not because the
        # technique couldn't be found (it was, in full: see below), but
        # because the site's own real, directly-read Terms of Service
        # explicitly prohibit exactly this. Automation_permission is
        # EXPLICITLY_RESTRICTED, not UNKNOWN -- a real found-and-corrected
        # mistake from this same investigation: an earlier pass guessed the
        # wrong ToS URL (`/about/terms-of-service`, which 404s), read that
        # 404 page's near-empty content, and recorded UNKNOWN rather than
        # finding the real page. The real path is `/terms-of-service`
        # (found from the site's own footer link), fetched and read in full
        # directly (2026-09-28).
        #
        # ai_ml_use corrected 2026-09-28 (a second pass re-reading this same
        # page while investigating jjwxc): the "Automated Access/Extraction"
        # and "Automated Data Collection" bullets quoted below sit right
        # next to a separate, distinct "AI/ML Use" bullet in the same list
        # -- present in the same full-text read that already quoted its two
        # neighbors, just not itself recorded. EXPLICITLY_RESTRICTED, not
        # UNKNOWN, per this file's own rule that only a directly-read clause
        # sets it either way.
        "domains": ("wuxiaworld.com",),
        "platform": "Wuxiaworld",
        "automation_permission": AutomationPermission.EXPLICITLY_RESTRICTED.value,
        "ai_ml_use": AiMlUse.EXPLICITLY_RESTRICTED.value,
        "terms": {
            "read": "www.wuxiaworld.com/terms-of-service, fetched and read in full directly "
                    "(2026-09-28) -- the real path, found via the site's own footer link, not "
                    "the guessed `/about/terms-of-service` path (which 404s).",
            "clause": "\"Automated Access/Extraction: Use any robot, bot, scraper, crawler, or "
                      "other automated means to access or extract content from the Platform "
                      "except as expressly permitted.\" And separately: \"Automated Data "
                      "Collection: Do not use any robot, spider, scraper, crawler, or any "
                      "automated means to access or extract data from Wuxiaworld without our "
                      "prior written permission. The only exception is public search engine "
                      "indexing... Any other automated reading or downloading of our content "
                      "(for instance, to create an archive) is prohibited.\" And, the same list's "
                      "own separate AI/ML clause: \"AI/ML Use: Use the Platform content to "
                      "develop, train, or improve artificial intelligence or machine learning "
                      "models without our prior written consent.\"",
            "extraction_method": "Recorded for completeness, even though the ToS clause above "
                                 "is what actually rules this site out: chapter text is fully "
                                 "retrievable over plain HTTP without any browser/JS execution. "
                                 "Series and chapter *pages* both embed a "
                                 "`window.__REACT_QUERY_STATE__` JSON cache server-side; its "
                                 "`['chapter', novel_slug, chapter_slug, None]` query holds the "
                                 "real chapter HTML at `item.content.value` and a reliable "
                                 "`item.pricingInfo.isFree` flag -- confirmed true for an early "
                                 "chapter and false for a late one on the same real novel "
                                 "(dragon-prince-yuan). A real karma (premium-currency) paywall "
                                 "also covers most of the catalog (per-chapter `karmaInfo`, a "
                                 "timed free-unlock mechanic), but even a free, unlocked "
                                 "chapter's text is still off-limits under the clause above -- "
                                 "the paywall status doesn't change the ToS answer. No "
                                 "server-side endpoint for the *full* chapter list was found "
                                 "(a novel page's own `chapterInfo.chapterGroups[].chapterList` "
                                 "is server-rendered empty; the real list is fetched by "
                                 "client-side JS this session didn't reverse-engineer), which "
                                 "would have been a second, independent reason to leave "
                                 "`get_chapters()` unbuilt even absent the ToS finding.",
        },
    },
    {
        # roadmap Step 91 investigation. No adapter: an active Cloudflare
        # interactive challenge blocks even the homepage and the terms
        # pages over plain HTTP -- this project's own architecture (see
        # sources/models.py's CHALLENGE_REASONS / sources/ladder.py) always
        # hands an active challenge to the person rather than trying to
        # solve or automate past it, so no tier below USER_ASSISTED_BROWSER
        # is buildable here regardless of what a real browser might do.
        "domains": ("webnovel.com",),
        "platform": "Webnovel (Qidian International / WebNovel/YueWen)",
        "automation_permission": AutomationPermission.UNKNOWN.value,
        "ai_ml_use": AiMlUse.UNKNOWN.value,
        "terms": {
            "read": "www.webnovel.com/about/termOfUse and /about/tos both returned an active "
                    "Cloudflare \"Just a moment...\" interactive challenge page (HTTP 403) on a "
                    "direct fetch (2026-09-28), same as the site's own homepage -- the real "
                    "clause text was never reachable to read.",
            "unverified": "Not cleared: UNKNOWN is not PERMITTED. Automated challenge-solving "
                          "was not attempted, per this project's standing rule that an active "
                          "anti-automation challenge is always handed to the person, never "
                          "solved automatically.",
            "extraction_method": "UNAVAILABLE at STATIC_HTTP -- an active Cloudflare "
                                 "interactive challenge (not just a CDN passthrough; confirmed "
                                 "by the literal \"Just a moment...\" challenge page and HTTP "
                                 "403) fires on the bare homepage itself, before any book/chapter "
                                 "path is even reached.",
        },
    },
]


def _host(url: str) -> str:
    return (urlsplit(url or "").hostname or "").lower()


def entry_for(url: str):
    host = _host(url)
    for entry in SITE_TERMS:
        if any(host == d or host.endswith("." + d) for d in entry["domains"]):
            return entry
    return None


def capabilities_for(url: str):
    """A SourceCapabilities record carrying this site's terms findings, or
    None if the site has no entry."""
    entry = entry_for(url)
    if entry is None:
        return None
    caps = SourceCapabilities(platform=entry["platform"],
                              automation_permission=entry["automation_permission"],
                              ai_ml_use=entry["ai_ml_use"], terms=dict(entry["terms"]))
    caps.terms["tos_prohibited"] = bool(caps.terms_restrictions())
    return caps
