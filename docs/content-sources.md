# Content sources

This file lists every source the Sources tab can reach, what was
actually checked, and how. Technical findings and terms findings are
kept separate. The app states the facts. Whether a given personal use is
appropriate is your call.

Statuses use the capability set in `sources/models.py`:
`VERIFIED`, `VERIFIED_WITH_AUTH`, `BROWSER_ASSISTED`,
`PARTIALLY_SUPPORTED`, `AUTHENTICATION_REQUIRED`,
`MANUAL_VERIFICATION_REQUIRED`, `PROTECTED`, `TOS_PROHIBITED` and
`UNTESTED`. `UNTESTED` means nobody has checked. It doesn't mean "doesn't
work".

## manhuagui (漫画柜) — `sources/adapters/manhuagui.py`

| | |
|---|---|
| URL patterns | `(www\|tw\|m).manhuagui.com/comic/<id>/[<chapter>.html]`, and the same paths on `mhgui.com` |
| Content type / language | manhua, zh |
| Status | `UNTESTED` until your first real import or a Test Now button. It has only been exercised against offline fixtures (see below). |
| Access tier | `STATIC_HTTP` only. No JavaScript is executed and no browser is needed. |
| Mirrors | `www.manhuagui.com` → `tw.manhuagui.com` → `www.mhgui.com` → `tw.mhgui.com`. The next mirror is tried on network errors, timeouts, or 5xx/429 that persist after retries. It is not tried on a 404 or a verification page. |
| Image CDN | `i.hamreus.com` → `cf.hamreus.com`. Requests need `Referer: <mirror>/`. |
| Auth | None. Adult-flagged works are behind the site's own `isAdult=1` cookie. It is off by default. Turn on **🔞 Include adult-flagged works** for this source (Sources → Sources, health & diagnostics) to send it; it goes to the main site only, never the image CDN. With it off, those works fail with a message naming the toggle. |
| Extraction | **Search:** `/s/<query>_p<page>.html`, `div.book-result > ul > li`. **Series:** `/comic/<id>/`, which has the title, `#intro-all`, author/genre spans, and status. **Chapters:** `[id^=chapter-list-]` sections under their `<h4>` headings (单话 / 单行本 / 番外篇). **Pages:** each chapter page carries a p.a.c.k.e.r-packed `SMH.imgData({...})` call whose word list is LZString-Base64 compressed. It is unpacked the same way the page's own script unpacks it, giving `files`, `path` and `sl.{e,m}`. Image URLs are used exactly as issued, e/m expiry token included. |
| Pacing | Main site: at least **10 s** between requests. This honors its robots.txt `Crawl-delay: 10` for generic clients, and is stricter than Keiyoushi's 10-per-10-s default. Image CDN: the normal 1–3 s default, well under Keiyoushi's 4/s. |
| Terms, recorded separately | robots.txt (from the roadmap's direct check, not re-fetched while building this): `User-agent: *` gets `Crawl-delay: 10` plus two admin paths disallowed. Named crawlers are disallowed separately, including the AI crawlers GPTBot, ClaudeBot, Claude-SearchBot, meta-externalagent, Bytespider and CCBot. This adapter sends a plain desktop-browser identity, like Keiyoushi's extension, so it falls under the `*` rule. The ToS has not been reviewed. |
| Reference | keiyoushi/extensions-source `src/zh/manhuagui` (Apache-2.0). Only the technique was used; no code was ported. |
| Known limits | If the site changes its markup, the adapter reports "layout has changed" rather than guessing. |
| Tests | `tests/test_sources_manhuagui.py`. The fixtures in `tests/manhuagui_fixtures.py` are built in the site's real shapes, and the packed-script fixture was checked against the page's own JavaScript unpacker. |

## Generic "paste a URL" import (no adapter)

| | |
|---|---|
| URL patterns | Anything that isn't a registered source or a known video URL. |
| Content type | Detected per page: comic (≥3 page-sized images), novel (a large main-text block), or video (`og:type` video or a `<video>` tag). |
| Language | Guessed from the script used. |
| Auth | None. A verification page is handed to you, and after completing it in your own browser you can paste the page source to continue. |
| Extraction | **Comic:** every `<img>`/`<source>` in reading order, including `data-src`/`data-original`/`srcset`. The filter then removes images that are: too short to be pages; off the dominant width/aspect cluster; repeated on other chapters of the same site; or from a third-party domain. **Novel:** trafilatura if installed, otherwise the largest text block after nav, header, footer and comment areas are stripped. |
| Known limits | The first chapter from a site can't use the cross-chapter repeat check yet, so a page-sized logo that shares the pages' width can get through. Pages drawn on a canvas or assembled by scripts need the browser tier. trafilatura's CJK extraction hasn't been benchmarked. |
| Tests | `tests/test_sources_workflows.py` |

## Video URLs

YouTube, Bilibili, Vimeo, Twitch VODs and clips, Niconico, TikTok,
Dailymotion and MissEvan links go to the existing
`video_download.download` (yt-dlp). This is the same path as Workspace's
"Video URL" option, with the same cookie settings.

## Demo source (offline)

| | |
|---|---|
| Name | `demo`. Hidden until **Sources → Source settings → Show the demo source** is turned on. |
| What it is | A locally generated three-chapter comic, plus a "Challenge test" series that always answers like a Cloudflare challenge. It goes through the real paced client, so the status view and the hand-off can be tried without the network. |
| Tests | `tests/test_sources_workflows.py`, `tests/test_sources_tab.py` |
