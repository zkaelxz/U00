# Known working sources — quick reference

A short status board. `docs/content-sources.md` has the full
technical story (selectors, protocols, fixes) for every registered
adapter; this file is just "can I point the app at this site" at a
glance, plus the ad hoc sites checked outside the adapter list. The
tables are generated: edit `docs/source-status.json` whenever a new site
is vetted, then run `python scripts/source_status.py` (CI runs the check).
Update a row's status and date the next time it's re-verified rather than
trusting an old date forever — sites change, break, and get throttled
(manhuaku). `python scripts/source_probe.py` checks by hand which
hosts still answer.

**Status key:** ✅ confirmed live · ⚠️ confirmed but with a real caveat ·
🔧 reachable, needs a fix/adapter work · ⛔ checked and declined (not a
technical failure — a permission/legitimacy reason) · 🚫 refused outright,
not investigated further · ❔ not vetted (unreachable from the vetting
network, or no status recorded yet)

<!-- BEGIN GENERATED: scripts/source_status.py (edit docs/source-status.json and re-run the script; do not edit by hand) -->

## Registered adapters (`sources/adapters/*.py`)

Site, language, type, hosts and flags come from the adapter itself; status, date and notes from `docs/source-status.json`. The Sources tab's 🟢/🟡/🔴 health light is a runtime signal and is not recorded here.

| Site | Adapter | Language | Type | Hosts | Flags | Status | Verified | Notes |
|---|---|---|---|---|---|---|---|---|
| 漫画柜 ManHuaGui | `manhuagui` | zh | manhua | `manhuagui.com`, `mhgui.com` | adult toggle, chapters sorted by number | ✅ | 2026-09-26 | Full chain confirmed live 2026-09-26. **Pace (2026-10-09):** Fast off: robots.txt Crawl-delay 10 equals the floor, so fast could not be faster; the footer says downloading is prohibited. |
| Bilibili | `bilibili` | zh | video | `bilibili.com`, `b23.tv` | sign-in optional | ✅ | unverified date | Backed by yt-dlp's maintained extractor. **Pace (2026-10-09):** Fast off: user agreement 4.3.15 forbids automated access. Pacing is not enforced for video anyway because yt-dlp bypasses SourceClient. |
| 52shuku.net | `52shuku` | zh | novel | `52shuku.net` |  | ✅ | unverified date | URL-shape + container-selector bugs found and fixed. **Pace (2026-10-09):** Fast allowed (1.0/2.0/2): robots.txt has no Crawl-delay and disallows /e/ /d/ /so/ (adapter avoids them); the copyright notice has no automated-access or rate clause. A search under /so/ must stay at normal pace. |
| xbanxia.cc | `xbanxia` | zh | novel | `www.xbanxia.cc` |  | ✅ | unverified date | Domain + 3 routing bugs found and fixed; `www.xbanxia.cc` is the real target. **Pace (2026-10-09):** Fast allowed (1.0/2.0/2): robots.txt has no Disallow or Crawl-delay; /copyright.html has no automated-access or rate clause; no terms page. |
| 哔哩哔哩漫画 Bilibili Manga | `bilibili_manga` | zh | manhua | `manga.bilibili.com` |  | ⚠️ | unverified date | Untested, not disqualified — needs a real signed-in account nobody has supplied. **Pace (2026-10-09):** Fast off: robots.txt returns 404 and the agreement pages are unreadable SPA shells; bilibili agreement 4.3.15 likely applies. |
| 툰코 ToonKor | `toonkor` | ko | manhwa | `toonkor0.org` |  | ✅ | unverified date | `toonkor0.org` confirmed correct/live domain. **Pace (2026-10-09):** Fast allowed (1.0/2.5/2): robots.txt is User-agent * Allow /, no Crawl-delay; no terms page found on the home page. |
| 瓜子漫画 Guazimanhua | `guazimanhua` | zh | manhua | `guazimanhua.com` |  | ✅ | unverified date | **Pace (2026-10-09):** Fast off: robots.txt disallows /category.php?*keyword= which the adapter's search uses; user agreement not read. |
| 妙趣漫画 Miaoqumh | `miaoqumh` | zh | manhua | `miaoqumh.org` |  | ✅ | unverified date | `search()` unsupported — real endpoint, empty for every query tried. **Pace (2026-10-09):** Fast off: robots.txt returns 403. |
| 包子漫画 Baozimh/GoDaManhua | `baozimh` | zh | manhua | `baozimh.org`, `godamh.com`, `baozimh.one`, `bzmh.org`, `g-mh.org` |  | ✅ | unverified date | Mirrors: `baozimh.org`, `godamh.com`, `baozimh.one`, `bzmh.org`, `g-mh.org`. `www.twmanga.com` and `www.twbzmg.com` are regional mirrors of the blocked `baozimh.com` brand, not part of this family (different markup, no JSON API); they have their own adapter, `twmanga`. **Pace (2026-10-09):** Fast off pending a terms read: robots.txt is clean on all mirrors but the Goda usage agreement was not located. Proposed fast 0.5/1.5/2 once cleared. |
| 快看漫画 Kuaikan Manhua | `kuaikan` | zh | manhua | `kuaikanmanhua.com` |  | ✅ | unverified date | Was completely broken (shared `requests` cookie bug); fixed in `sources/http.py`, benefits every source. **Pace (2026-10-09):** Fast off: service agreement not read and content is partly paywalled; robots.txt allows the paths we use. |
| 漫画库 Manhuaku | `manhuaku` | zh | manhua | `manhuaku.net` |  | ⚠️ | 2026-09-26 | Both content paths (baozimh-aggregated + native `blob:`) confirmed working 2026-09-26 with real page bytes. `search()` unsupported (real endpoint, empty for every query tried). `robots.txt` inaccessible (403), not reviewed for ToS. **2026-09-27: two consecutive live end-to-end runs hung — one past 120s, a retry past 240s. The second produced no output at all, not even the first progress marker printed before any network call, suggesting the stall (or a background-capture buffering issue) may sit earlier than the actual site request. Not re-confirmed working today; cause still unknown (site slowdown, a regression, or an artifact of this environment). Stopped at two attempts deliberately, not retried further.** **Pace (2026-10-09):** Fast off: robots.txt returns 403; the site throttles and serves AES/blob content. |
| ゼロサムオンライン Zero-Sum Online | `zerosumonline` | ja | manga | `zerosumonline.com` |  | ✅ | unverified date | Own protobuf reader for the real API. **Pace (2026-10-09):** Fast allowed (1.0/2.0/1): robots.txt returns 404 on zerosumonline.com and api.zerosumonline.com and no terms page was found. |
| ranobes.net | `ranobes` | en | novel | `ranobes.net` |  | ❔ | unverified date | Content status not recorded here; only the pace note is. **Pace (2026-10-09):** Fast off: robots.txt disallows /chapters/*/page/* which the adapter fetches for pages 2+; rules.html is silent on automated access. |
| 猫耳FM MissEvan | `missevan` | zh | audio_drama | `missevan.com` | sign-in optional | ❔ | unverified date | Content status not recorded here; only the pace note is. **Pace (2026-10-09):** Fast off: terms of service 4.2.11 forbid automated access. |
| 饭角 Fanjiao | `fanjiao` | zh | audio_drama | `fanjiao.co` |  | ❔ | unverified date | Content status not recorded here; only the pace note is. **Pace (2026-10-09):** Fast off: robots.txt and terms could not be retrieved. |
| 轻之国度 (lightnovel.fun) | `lightnovel_fun` | zh | novel | `www.lightnovel.fun` |  | ✅ | 2026-09-30 | Search, series, chapters (across volumes) and chapter text confirmed live 2026-09-30. Public `/book` and `/reader` pages only; locked 轻币 chapters are reported, never unlocked; EPUB/file-locker links never followed. `lightnovel.us` returned 503. **Pace (2026-10-09):** Fast off pending a terms read: robots.txt is clean but the site rules (LK站规) were not read. Proposed fast 0.5/1.5/2 once cleared. |
| 小説家になろう (Syosetu) | `syosetu` | ja | novel | `ncode.syosetu.com` |  | ⚠️ | 2026-09-30 | Page structure read live 2026-09-30 and the adapter run offline against saved pages; no import run through the app. **Terms: the ToS (第14条 23) forbid automated access except via the official API** (recorded, not enforced; shipped by owner decision 2026-09-30). 18+ works (`novel18.syosetu.com`) unsupported; 18+/login/removed-work handling unverified live. **Pace (2026-10-09):** Fast off: terms (Art. 14 item 23) allow automated access only through the official なろうデベロッパー API. robots.txt Crawl-delay 1; the 2.0 s host_min_interval floor stays. |
| 飘天文学 (Piaotian) | `piaotian` | zh | novel | `www.piaotia.com`, `ptwxz.com` |  | ⚠️ | 2026-09-30 | Page structure read live 2026-09-30 (book page, whole-book list, chapter, 404 and 200 error pages, search) and the adapter run offline against those saved pages; no chapter imported through the app. Search page 2, login/age interstitials and the site's own search throttle unverified live. No terms page found (UNKNOWN, not permitted); the site hosts reposted copyrighted fiction. **Pace (2026-10-09):** Fast allowed (2.5/3.5/1): robots.txt has no Crawl-delay and disallows only attachment/login/packshow/admin; no terms page found; the 2.5 s floor stays for the site's own search throttle. |
| MangaK | `mangak` | en | manga, manhwa, manhua | `mangak.io`, `api.mangak.io` |  | ⚠️ | 2026-10-03 | Search, series, the full chapter list, page list and an image download run live against mangak.io 2026-10-03; no import run through the app. **Terms: the ToS (section 4) forbid "automated tools, bots, or scrapers"** (recorded, not enforced; shipped by owner decision 2026-10-03). The site says it hosts content "sourced from third-party providers". **Pace (2026-10-09):** Fast off: terms of service section 4 forbid automated access. |
| 包子漫畫 Twmanga/Twbzmg | `twmanga` | zh | manhua | `www.twmanga.com`, `www.twbzmg.com` |  | ⚠️ | 2026-10-08 | 2026-10-08, static HTTP, no browser: the adapter itself fetched a series page, its chapter list (oldest first), a chapter's 47 image URLs on `s1.bzcdn.net` (only s1 was seen; s2-s9 is an assumption), and downloaded one image (no Referer or cookie). Search returns server-rendered cards (page 1 only). The chapter link is a `page_direct` 302 to the other mirror; the adapter builds the chapter URL from its slots instead and refuses a page response that ends on any host other than `www.twmanga.com` / `www.twbzmg.com` (the apex `baozimh.com` is never requested by the adapter; a redirect there is refused after the fact, because the client follows redirects itself) and any image whose final URL is not on `s1`-`s9.bzcdn.net`. No robots.txt (the path answers with the 404 page). The apex `baozimh.com` answers 403 with a gatekeeper challenge and is never requested. Pacing floor 5 s per host (page hosts and `s1`-`s9.bzcdn.net`), one request at a time. Vetting: `docs/research/twmanga-vetting.md`. Checked from the cloud sandbox only, on one two-chapter series and a second, longer one: not yet run on the owner's machine. The site's terms of service were not found, so ToS is unreviewed. Series with more than one section, chapters whose images span several pages, and search beyond page 1 were not checked. **Pace (2026-10-09):** No fast profile: robots.txt is absent (404 page) and no terms page was located, so fast is allowed in principle, but the 5.0 s host_min_interval floor already equals the most that is defensible. |

## Generic paste-a-URL (no adapter) — confirmed on real, specific sites

These went through `sources.preflight.preflight()` / the generic importer
directly, not a dedicated adapter. Re-check before relying on them for a
different site with the same template — "generic works" doesn't mean
every WordPress/reader-template site does.

| Site | Language | Type | Status | Verified | Notes |
|---|---|---|---|---|---|
| `m.zgzl.net` ("文海小说") | zh | novel | ✅ | unverified date | Real chapter, HIGH confidence via trafilatura. Multi-page chapters: "next page" ≠ "next chapter" link, correctly not auto-followed. |
| `www.51manga.com` | zh | manhua | ✅ | unverified date | Real chapter page, 13 real page images, `STATIC_HTTP`. |
| `www.mh160mh.com` | zh | manhua | ✅ | unverified date | Real chapter needs `RENDERED_BROWSER` (JS-loaded); homepage alone gives a false-positive image count (thumbnail grid) — always test an actual chapter URL, not the homepage. |
| `www.goodtoon005.com` | ko | manhwa | ✅ | unverified date | A real chapter URL (`/manga/gt-<id>/<chapter#>/`) needs `RENDERED_BROWSER`, 56 real page images. **The series-landing URL (`/manga/gt-<id>/`, no chapter number) is NOT a real chapter — it returned a much smaller, misleading image count (cover + episode-list thumbnails) via `STATIC_HTTP`. Always test a URL with an explicit chapter segment.** Homepage links to a cluster of low-reputation ad/redirect domains — real exposure risk when rendering its pages in a browser. |

## Checked and set aside — not a site problem, a fit/legitimacy problem

| Site | Status | Verified | Reason |
|---|---|---|---|
| `dl-raw.si` | ⛔ | unverified date | Not a reading aggregator — its own framing is bulk ZIP/RAR volume downloads sourced from third-party file lockers (Rapidgator). Declined to build automated fetch+translate for this: it's volume-scale distribution of commercially published work via a piracy channel, not a page someone is already reading. robots.txt itself is permissive; the refusal is about what the site *is*, not a technical/ToS block. |
| `fucknovelpia.com` | ⛔ | unverified date | A mirror redistributing translated content from Novelpia, a real paid Korean platform, without anything establishing that's authorized. Not vetted further. |
| `mh03.com` | 🚫 | unverified date | `robots.txt` names `ClaudeBot` and `Claude-SearchBot` with `Disallow: /` (alongside GPTBot, Bytespider, etc.) — refused outright, not investigated past reading that one file. |
| Novgo | ⛔ | 2026-09-30 | Mirrors Wuxiaworld's licensed *Against the Gods* word for word, including a chapter Wuxiaworld itself serves only as a teaser. Not an independent source. (Vetted 2026-09-30.) |
| NovelFull | ❔ | 2026-09-30 | English sites, unreachable from the vetting network (403 / DNS failure) on 2026-09-30. Not vetted further. |
| NovelBin | ❔ | 2026-09-30 | English sites, unreachable from the vetting network (403 / DNS failure) on 2026-09-30. Not vetted further. |
| wenku8 | ❔ | 2026-09-30 | 403 on every page tried on 2026-09-30. Unvetted. |

<!-- END GENERATED -->

## SFACG (idea reviewed, site itself out of scope)

Not vetted or built against — the user only wanted the *technique*
(a site protects its API with a per-request signature computed
client-side) applied generically. Built as `page_fetch.api_capture_session()`:
opens a page in a real browser, records the responses its own JS makes
against a URL pattern, so a signature never needs to be known or ported.
Doesn't apply to manhuaku's `blob:` problem — see that row above; that's
a different mechanism (`fetch_rendered_resolving_blobs`), already built
and already in use.
