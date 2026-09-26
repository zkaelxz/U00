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

## 52shuku.net — `sources/adapters/52shuku.py`

| | |
|---|---|
| URL patterns | `52shuku.(net\|top\|vip\|org)/<category>/b/<id>[_<n>].html` |
| Content type / language | novel, zh |
| Status | `UNTESTED` until a real import or a Test Now button. Only exercised against offline fixtures. |
| Access tier | `STATIC_HTTP` only. Plain server-rendered HTML, no JavaScript/decoding step needed (unlike manhuagui's packed-script case). |
| Auth | None hit in testing (a small sample, not a guarantee). No `login()`. |
| Extraction | **Chapters:** the TOC page (`/<category>/b/<id>.html`), `ul.list.clearfix > li.mulu > a`. **Chapter text:** `article.article-content div#text > p`, plain paragraphs. **Series title:** the page's own `<title>` tag (no confirmed dedicated title/author/cover selector was found for this site specifically -- unlike xbanxia's `div.book-describe`, which was) -- kept honest rather than guessed. `search()` is left unsupported: neither reference scraper's search endpoint was read in enough detail to reimplement faithfully. |
| Pacing | Forced to **1 concurrent request**, overriding even a more permissive global Settings value -- `404-novel-project/novel-downloader`'s own source notes this site "is strict about concurrency." Otherwise the normal default pace. |
| Terms, recorded separately | robots.txt (from the roadmap's direct check, not re-fetched while building this): blocks only named crawlers (AhrefsBot, Baiduspider, 360Spider, Sogou) plus a handful of internal paths (`/e/*`, `/d/*`, `/so/*`) -- no blanket `User-agent: *` disallow. The ToS has not been reviewed. |
| Reference | `Moleys/vbook-ext`; `Lieatfhy/spiderNovel`; `AgonyNihility/novel` (plain `requests`+`parsel`). Technique only, no code ported. |
| Known limits | If the site changes its markup, the adapter reports "layout has changed" rather than guessing. Long chapters split across multiple pagination pages are not auto-followed -- each chapter is fetched as the single URL its TOC entry gives. |
| Tests | `tests/test_sources_52shuku.py`, plus the text-import path in `tests/test_sources_workflows.py`'s `TestChapterImport`. |

## xbanxia.cc — `sources/adapters/xbanxia.py`

| | |
|---|---|
| URL patterns | `xbanxia.cc/<id>[/<chapter>.html]` |
| Content type / language | novel, zh |
| Status | `UNTESTED` until a real import or a Test Now button. Only exercised against offline fixtures. |
| Access tier | `STATIC_HTTP` only. Plain server-rendered HTML. |
| Auth | None. `search()` is a POST with a static cookie, not a login. |
| Extraction | **Search:** POST `/modules/article/search_t.php` with `searchkey`/`Submit` form fields, a spoofed Firefox user-agent, and a static `jieqiUserCharset=utf-8` cookie. **Series:** `div.book-describe h1`/`p` (最近更新/最新章節/類型 prefixes), cover `img[data-original]`. **Chapters:** flat `div.book-list ul li a`, no pagination. **Chapter text:** `div#nr1`, falling back to the single largest text block on the page if that id isn't found. |
| Pacing | The normal default pace -- no concurrency-sensitivity signal found for this site. |
| Terms, recorded separately | robots.txt (from the roadmap's direct check, not re-fetched while building this): `User-agent: *` with zero `Disallow` lines -- no restrictions declared at all. The ToS has not been reviewed. |
| Reference | `lncrawl/lightnovel-crawler` (MIT), `sources/zh/xbanxia.py`. Technique only, no code ported. |
| Known limits | **A real, unresolved domain question**: `lncrawl`'s own source targets `xbanxia.com`/`banxia.cc`, not `xbanxia.cc` (the domain actually vetted here). A direct fetch of `xbanxia.cc` succeeded and looked consistent with the same site family, but this was never confirmed by comparing raw HTML template fingerprints across the domains -- see the manual check below. The `div#nr1` chapter-text selector is `lncrawl`'s own selector for the sibling domain, not independently re-derived against `xbanxia.cc`'s markup; the largest-text-block fallback exists specifically to hedge against that id being wrong. |
| Tests | `tests/test_sources_xbanxia.py`. |

## Bilibili — `sources/adapters/bilibili.py`

| | |
|---|---|
| URL patterns | `bilibili.com/video/<BV.../av...>`, `bilibili.com/bangumi/play/...`, `b23.tv/<code>` short links |
| Content type / language | video, zh |
| Status | `VERIFIED`. Backed by yt-dlp's own maintained Bilibili extractor, which is exercised against millions of real downloads outside this project. |
| Access tier | `STATIC_HTTP` -- yt-dlp replicates the same API calls Bilibili's own public web player makes to render a video for an ordinary browser visit (including WBI request signing), not a defeat of any anti-bot challenge. No CAPTCHA solving, no anti-bot-challenge defeat, no signing-system reimplementation happens here. |
| Extraction | yt-dlp's `extract_info(download=False)` for metadata (title, uploader, upload date, description, duration, thumbnail, BVID) and formats before any download. Multipart/anthology videos are detected via yt-dlp's own multi-entry response; an explicit `?p=N` in the pasted URL downloads only that part. Quality (Best/1080p/720p/480p/360p/Audio only) is offered only for resolutions the video's own `list_formats()` result actually reports, falling back to the closest lower one with a plain message when a requested quality isn't available. Subtitle tracks (if any) are offered as an optional pre-transcription reference, tagged `human` or `ai_generated` so an ASR-sourced Bilibili subtitle is never shown as a verbatim human transcript. |
| Auth | Cookie-based (browser selection or a cookie file), reusing Step 9b's planned mechanism -- never a hard-coded credential. A video needing login for higher quality or gated content shows "This video needs Bilibili login -- configure browser cookies in Settings" instead of failing silently. |
| Pacing / retry | Not routed through `sources.http.SourceClient` -- yt-dlp manages its own HTTP end to end and isn't built to run through an injectable transport. Bilibili's own known transient risk-control responses (HTTP 412 and other 4xx/5xx codes) are retried at the adapter's own level instead, with a capped number of attempts and exponential backoff, matching this project's usual "conservative, capped retry" shape rather than aggressive re-hitting. |
| Extraction backend | `yt-dlp` (unmaintained-by-this-project, actively maintained upstream). Not a hand-rolled scraper against Bilibili's private API/signing system -- re-implementing WBI signing/BVID resolution/DASH extraction independently would be fragile and duplicative for no gain. |
| Terms, recorded separately | Not independently re-read for this adapter -- treating ordinary public-video access as supported rests on yt-dlp's own maintained extractor and its long track record, not a fresh reading of Bilibili's ToS. A video that needs authentication is refused with a clear message rather than worked around. |
| Known limits | Multipart/quality/subtitle selection is only exposed through this adapter's own methods and the Sources tab's front door -- Workspace's separate, older "Video URL" quick-import field still calls the generic `video_download.download` path unchanged (same as any other video site with no dedicated adapter), since it isn't part of the Sources-tab front-door architecture Step 23 built. |
| Tests | `tests/test_sources_bilibili.py`, plus `TestBilibiliRouting` in `tests/test_sources_workflows.py` for the front-door wiring. All yt-dlp calls are faked; no real network calls. |

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

Bilibili links go through the dedicated adapter above. YouTube, Vimeo,
Twitch VODs and clips, Niconico, TikTok, Dailymotion and MissEvan links
still go to the existing `video_download.download` (yt-dlp) -- the same
path as Workspace's "Video URL" option, with the same cookie settings.

## Demo source (offline)

| | |
|---|---|
| Name | `demo`. Hidden until **Sources → Source settings → Show the demo source** is turned on. |
| What it is | A locally generated three-chapter comic, plus a "Challenge test" series that always answers like a Cloudflare challenge. It goes through the real paced client, so the status view and the hand-off can be tried without the network. |
| Tests | `tests/test_sources_workflows.py`, `tests/test_sources_tab.py` |

## Manual checks still to do

These have only been run against offline fixtures. Tick them off once
they've been tried against the real site.

- [ ] **manhuagui, normal work:** search a real title, open its chapter
  list, and import one chapter into a manhua drama. Confirm the pages
  show up in Scanlate.
- [ ] **manhuagui, adult-flagged work:** turn on **🔞 Include adult-flagged
  works** for manhuagui. Open a work that was refused with it off, and
  confirm its chapter list loads and a chapter imports. Turn the toggle
  off again and confirm the same work is refused with the message that
  names the toggle.
- [ ] **Generic paste-a-URL:** paste a real chapter URL from a site with
  no adapter, and confirm it either imports the pages or fails with a
  clear message.
- [ ] **Browser tier:** install Playwright (`pip install playwright` then
  `playwright install chromium`), and confirm **Test Browser** on a
  JavaScript-only page records a real result.
- [ ] **Novel text:** confirm trafilatura's extraction quality on a real
  Chinese novel chapter page.
- [ ] **xbanxia domain question:** fetch `xbanxia.cc`, `xbanxia.com`, and
  `banxia.cc` directly and compare real page structure/template to
  confirm which domain(s) this adapter should actually target, rather
  than assuming they're the same service.
- [ ] **52shuku, normal work:** pull a real chapter list and download one
  chapter into a novel drama's raw-novel text, with no changes needed in
  Workspace's existing novel-import pipeline.
- [ ] **xbanxia, normal work:** same check, on `xbanxia.cc` -- and
  confirm the `div#nr1` chapter-text selector actually matches (or that
  the largest-text-block fallback picks up the right content if not).
- [ ] **Bilibili:** paste a real, publicly accessible Bilibili video URL
  into the Sources tab, confirm metadata (title, duration) shows before
  any download, download it, and send it into the existing transcription
  pipeline with no changes needed there. Try a real multipart video too,
  and confirm a `?p=2`-style URL downloads only that part.
