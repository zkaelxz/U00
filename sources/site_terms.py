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
        # roadmap Step 91 investigation. No adapter: real chapter text isn't
        # in the server-rendered HTML at all -- confirmed directly, not
        # assumed -- and a real karma-based paywall covers a meaningful part
        # of the catalog regardless.
        "domains": ("wuxiaworld.com",),
        "platform": "Wuxiaworld",
        "automation_permission": AutomationPermission.UNKNOWN.value,
        "ai_ml_use": AiMlUse.UNKNOWN.value,
        "terms": {
            "read": "www.wuxiaworld.com/about/terms-of-service, fetched directly (2026-09-28).",
            "unverified": "The page is a client-rendered React app; the served HTML's <div "
                          "id=\"root\"> does carry real server-rendered markup for this route "
                          "(confirmed directly, unlike the bare '/' homepage, whose root div is "
                          "empty), but the actual terms-of-service clause text could not be "
                          "reliably isolated from the surrounding app chrome without a real "
                          "browser render, which this environment could not run (the `playwright` "
                          "package install needed to drive the pre-installed Chromium binary was "
                          "blocked by this session's own tool-permission policy) -- recorded "
                          "honestly as unverified rather than guessed.",
            "extraction_method": "Series/chapter *metadata* pages (title, chapter count, "
                                 "pricing info) are genuinely server-rendered and readable over "
                                 "plain HTTP -- confirmed directly against a real novel "
                                 "(dragon-prince-yuan) and its chapter 1. But the chapter body "
                                 "itself is not: the same server-rendered payload embeds each "
                                 "chapter's real pricing/karma metadata but an explicitly empty "
                                 "`\"paragraphs\":[]` for the text, meaning the actual prose is "
                                 "fetched by the page's own client-side JavaScript after load, "
                                 "not present in the response this app would receive. "
                                 "RENDERED_BROWSER tier (this project's existing "
                                 "`page_fetch.fetch_rendered`) might retrieve it, but that could "
                                 "not be verified from this environment for the same Playwright- "
                                 "install reason above -- recorded as UNRESOLVED, not assumed "
                                 "working.",
            "paywall": "A real karma (premium-currency) system gates a meaningful part of the "
                      "catalog, confirmed directly in a real chapter's own embedded pricing "
                      "data: `\"karmaInfo\":{\"isActive\":true,...}`, per-chapter "
                      "`\"karmaPrice\"` values, and a real timed free-unlock mechanic "
                      "(\"2 Free Chapters Every 23 Hrs\", a `waitTime` of 82800 seconds). Some "
                      "early chapters observed as currently free (`isKarmaRequired: false`), "
                      "but this is not a blanket free-text site.",
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
