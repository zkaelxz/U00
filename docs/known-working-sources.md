# Known working sources — quick reference

A short, editable status board. `docs/content-sources.md` has the full
technical story (selectors, protocols, fixes) for every registered
adapter; this file is just "can I point the app at this site" at a
glance, plus the ad hoc sites checked outside the adapter list. Add a row
whenever a new site is vetted; update a row's Status the next time it's
re-verified rather than trusting an old date forever — sites change,
break, and get throttled (mangaz, manhuaku).

**Status key:** ✅ confirmed live · ⚠️ confirmed but with a real caveat ·
🔧 reachable, needs a fix/adapter work · ⛔ checked and declined (not a
technical failure — a permission/legitimacy reason) · 🚫 refused outright,
not investigated further

## Registered adapters (`sources/adapters/*.py`)

| Site | Language | Type | Status | Notes |
|---|---|---|---|---|
| manhuagui (漫画柜) | zh | manhua | ✅ | Full chain confirmed live 2026-09-26. |
| 52shuku.net | zh | novel | ✅ | URL-shape + container-selector bugs found and fixed. |
| xbanxia.cc | zh | novel | ✅ | Domain + 3 routing bugs found and fixed; `www.xbanxia.cc` is the real target. |
| Bilibili | zh | video | ✅ | Backed by yt-dlp's maintained extractor. |
| Bilibili Manga (哔哩哔哩漫画) | zh | manhua | ⚠️ | Untested, not disqualified — needs a real signed-in account nobody has supplied. |
| ToonKor (툰코) | ko | manhwa | ✅ | `toonkor0.org` confirmed correct/live domain. |
| 瓜子漫画 Guazimanhua | zh | manhua | ✅ | |
| 妙趣漫画 Miaoqumh | zh | manhua | ✅ | `search()` unsupported — real endpoint, empty for every query tried. |
| 包子漫画 Baozimh/GoDaManhua | zh | manhua | ✅ | Mirrors: `baozimh.org`, `godamh.com`, `baozimh.one`, `bzmh.org`, `g-mh.org`. **`www.twbzmg.com` is the same brand (Taiwan-region deployment) but not yet added/verified as a mirror — don't assume it shares selectors.** |
| 快看漫画 Kuaikan | zh | manhua | ✅ | Was completely broken (shared `requests` cookie bug); fixed in `sources/http.py`, benefits every source. |
| 漫画库 Manhuaku | zh | manhua | ⚠️ | Both content paths (baozimh-aggregated + native `blob:`) confirmed working 2026-09-26 with real page bytes. **2026-09-27: two consecutive live end-to-end runs hung — one past 120s, a retry past 240s. The second produced no output at all, not even the first progress marker printed before any network call, suggesting the stall (or a background-capture buffering issue) may sit earlier than the actual site request. Not re-confirmed working today; cause still unknown (site slowdown, a regression, or an artifact of this environment). Stopped at two attempts deliberately, not retried further.** `search()` unsupported (real endpoint, empty for every query tried). `robots.txt` inaccessible (403), not reviewed for ToS. |
| ゼロサムオンライン Zero-Sum Online | ja | manga | ✅ | Own protobuf reader for the real API. |
| マンガ図書館Z Mangaz | ja | manga | ⚠️ | search/series/chapters ✅. Page capture proven (real descrambled page, real `blob:` capture) but a full uninterrupted book (43/43 pages) is unproven — throttling-limited, not a code gap. |
| 轻之国度 LightNovel (`www.lightnovel.fun`) | zh | novel | ✅ | Search, series, chapters (across volumes) and chapter text confirmed live 2026-09-30. Public `/book` and `/reader` pages only; locked 轻币 chapters are reported, never unlocked; EPUB/file-locker links never followed. `lightnovel.us` returned 503. |
| 小説家になろう Syosetu (`ncode.syosetu.com`) | ja | novel | ⚠️ | Page structure read live 2026-09-30 and the adapter run offline against saved pages; no import run through the app. **Terms: the ToS (第14条 23) forbid automated access except via the official API** (recorded, not enforced). 18+ works (`novel18.syosetu.com`) unsupported; 18+/login/removed-work handling unverified live. |

## Generic paste-a-URL (no adapter) — confirmed on real, specific sites

These went through `sources.preflight.preflight()` / the generic importer
directly, not a dedicated adapter. Re-check before relying on them for a
different site with the same template — "generic works" doesn't mean
every WordPress/reader-template site does.

| Site | Language | Type | Status | Notes |
|---|---|---|---|---|
| `m.zgzl.net` ("文海小说") | zh | novel | ✅ | Real chapter, HIGH confidence via trafilatura. Multi-page chapters: "next page" ≠ "next chapter" link, correctly not auto-followed. |
| `www.51manga.com` | zh | manhua | ✅ | Real chapter page, 13 real page images, `STATIC_HTTP`. |
| `www.mh160mh.com` | zh | manhua | ✅ | Real chapter needs `RENDERED_BROWSER` (JS-loaded); homepage alone gives a false-positive image count (thumbnail grid) — always test an actual chapter URL, not the homepage. |
| `www.goodtoon005.com` | ko | manhwa | ✅ | A real chapter URL (`/manga/gt-<id>/<chapter#>/`) needs `RENDERED_BROWSER`, 56 real page images. **The series-landing URL (`/manga/gt-<id>/`, no chapter number) is NOT a real chapter — it returned a much smaller, misleading image count (cover + episode-list thumbnails) via `STATIC_HTTP`. Always test a URL with an explicit chapter segment.** Homepage links to a cluster of low-reputation ad/redirect domains — real exposure risk when rendering its pages in a browser. |

## Checked and set aside — not a site problem, a fit/legitimacy problem

| Site | Reason |
|---|---|
| `dl-raw.si` | Not a reading aggregator — its own framing is bulk ZIP/RAR volume downloads sourced from third-party file lockers (Rapidgator). Declined to build automated fetch+translate for this: it's volume-scale distribution of commercially published work via a piracy channel, not a page someone is already reading. robots.txt itself is permissive; the refusal is about what the site *is*, not a technical/ToS block. |
| `fucknovelpia.com` | A mirror redistributing translated content from Novelpia, a real paid Korean platform, without anything establishing that's authorized. Not vetted further. |
| `mh03.com` | `robots.txt` names `ClaudeBot` and `Claude-SearchBot` with `Disallow: /` (alongside GPTBot, Bytespider, etc.) — refused outright, not investigated past reading that one file. |
| Novgo | Mirrors Wuxiaworld's licensed *Against the Gods* word for word, including a chapter Wuxiaworld itself serves only as a teaser. Not an independent source. (Vetted 2026-09-30.) |
| NovelFull, NovelBin | English sites, unreachable from the vetting network (403 / DNS failure) on 2026-09-30. Not vetted further. |
| wenku8 | 403 on every page tried on 2026-09-30. Unvetted. |

## SFACG (idea reviewed, site itself out of scope)

Not vetted or built against — the user only wanted the *technique*
(a site protects its API with a per-request signature computed
client-side) applied generically. Built as `page_fetch.api_capture_session()`:
opens a page in a real browser, records the responses its own JS makes
against a URL pattern, so a signature never needs to be known or ported.
Doesn't apply to manhuaku's `blob:` problem — see that row above; that's
a different mechanism (`fetch_rendered_resolving_blobs`), already built
and already in use.
