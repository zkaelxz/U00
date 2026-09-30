# Known working sources — quick reference

A short status board. `docs/content-sources.md` has the full
technical story (selectors, protocols, fixes) for every registered
adapter; this file is just "can I point the app at this site" at a
glance, plus the ad hoc sites checked outside the adapter list. The
tables are generated: edit `docs/source-status.json` whenever a new site
is vetted, then run `python scripts/source_status.py` (CI runs the check).
Update a row's status and date the next time it's re-verified rather than
trusting an old date forever — sites change, break, and get throttled
(mangaz, manhuaku). `python scripts/source_probe.py` checks by hand which
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
| 漫画柜 ManHuaGui | `manhuagui` | zh | manhua | `manhuagui.com`, `mhgui.com` | adult toggle, chapters sorted by number | ✅ | 2026-09-26 | Full chain confirmed live 2026-09-26. |
| Bilibili | `bilibili` | zh | video | `bilibili.com`, `b23.tv` | sign-in optional | ✅ | unverified date | Backed by yt-dlp's maintained extractor. |
| 52shuku.net | `52shuku` | zh | novel | `52shuku.net` |  | ✅ | unverified date | URL-shape + container-selector bugs found and fixed. |
| xbanxia.cc | `xbanxia` | zh | novel | `www.xbanxia.cc` |  | ✅ | unverified date | Domain + 3 routing bugs found and fixed; `www.xbanxia.cc` is the real target. |
| 哔哩哔哩漫画 Bilibili Manga | `bilibili_manga` | zh | manhua | `manga.bilibili.com` |  | ⚠️ | unverified date | Untested, not disqualified — needs a real signed-in account nobody has supplied. |
| 툰코 ToonKor | `toonkor` | ko | manhwa | `toonkor0.org` |  | ✅ | unverified date | `toonkor0.org` confirmed correct/live domain. |
| 瓜子漫画 Guazimanhua | `guazimanhua` | zh | manhua | `guazimanhua.com` |  | ✅ | unverified date |  |
| 妙趣漫画 Miaoqumh | `miaoqumh` | zh | manhua | `miaoqumh.org` |  | ✅ | unverified date | `search()` unsupported — real endpoint, empty for every query tried. |
| 包子漫画 Baozimh/GoDaManhua | `baozimh` | zh | manhua | `baozimh.org`, `godamh.com`, `baozimh.one`, `bzmh.org`, `g-mh.org` |  | ✅ | unverified date | Mirrors: `baozimh.org`, `godamh.com`, `baozimh.one`, `bzmh.org`, `g-mh.org`. **`www.twbzmg.com` is the same brand (Taiwan-region deployment) but not yet added/verified as a mirror — don't assume it shares selectors.** |
| 快看漫画 Kuaikan Manhua | `kuaikan` | zh | manhua | `kuaikanmanhua.com` |  | ✅ | unverified date | Was completely broken (shared `requests` cookie bug); fixed in `sources/http.py`, benefits every source. |
| 漫画库 Manhuaku | `manhuaku` | zh | manhua | `manhuaku.net` |  | ⚠️ | 2026-09-26 | Both content paths (baozimh-aggregated + native `blob:`) confirmed working 2026-09-26 with real page bytes. `search()` unsupported (real endpoint, empty for every query tried). `robots.txt` inaccessible (403), not reviewed for ToS. **2026-09-27: two consecutive live end-to-end runs hung — one past 120s, a retry past 240s. The second produced no output at all, not even the first progress marker printed before any network call, suggesting the stall (or a background-capture buffering issue) may sit earlier than the actual site request. Not re-confirmed working today; cause still unknown (site slowdown, a regression, or an artifact of this environment). Stopped at two attempts deliberately, not retried further.** |
| ゼロサムオンライン Zero-Sum Online | `zerosumonline` | ja | manga | `zerosumonline.com` |  | ✅ | unverified date | Own protobuf reader for the real API. |
| マンガ図書館Z Manga Toshokan Z | `mangaz` | ja | manga | `mangaz.com` |  | ⚠️ | unverified date | search/series/chapters ✅. Page capture proven (real descrambled page, real `blob:` capture) but a full uninterrupted book (43/43 pages) is unproven — throttling-limited, not a code gap. |
| ranobes.net | `ranobes` | en | novel | `ranobes.net` |  | ❔ no status recorded | — |  |
| 猫耳FM MissEvan | `missevan` | zh | audio_drama | `missevan.com` | sign-in optional | ❔ no status recorded | — |  |
| 饭角 Fanjiao | `fanjiao` | zh | audio_drama | `fanjiao.co` |  | ❔ no status recorded | — |  |
| 轻之国度 (lightnovel.fun) | `lightnovel_fun` | zh | novel | `www.lightnovel.fun` |  | ✅ | 2026-09-30 | Search, series, chapters (across volumes) and chapter text confirmed live 2026-09-30. Public `/book` and `/reader` pages only; locked 轻币 chapters are reported, never unlocked; EPUB/file-locker links never followed. `lightnovel.us` returned 503. |
| 小説家になろう (Syosetu) | `syosetu` | ja | novel | `ncode.syosetu.com` |  | ⚠️ | 2026-09-30 | Page structure read live 2026-09-30 and the adapter run offline against saved pages; no import run through the app. **Terms: the ToS (第14条 23) forbid automated access except via the official API** (recorded, not enforced; shipped by owner decision 2026-09-30). 18+ works (`novel18.syosetu.com`) unsupported; 18+/login/removed-work handling unverified live. |

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
