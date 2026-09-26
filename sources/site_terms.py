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
