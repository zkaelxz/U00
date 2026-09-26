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

## Bilibili Manga (哔哩哔哩漫画) — `sources/adapters/bilibili_manga.py`

| | |
|---|---|
| URL patterns | `manga.bilibili.com/...` (chapter URL shape guessed as `detail/mc<id>/<chapter>` or `mc<id>/<chapter>` -- not independently confirmed, see Known limits) |
| Content type / language | manhua, zh |
| Status | `UNTESTED`, deliberately not `DISQUALIFIED` or `VERIFIED` -- every authentication-dependent state genuinely hasn't been checked (see the two manual checks below), not guessed at in either direction. |
| Access tier | `RENDERED_BROWSER` as the **minimum**, not an optional fallback -- confirmed fully client-rendered: a real series page (`manga.bilibili.com/detail/mc28793`) returns essentially `<div id="app-vm"></div>` plus a `<noscript>` notice, no server-side content leakage at all. This is the one zh source vetted so far where `STATIC_HTTP` never has anything to offer. |
| Auth | Unverified. No logged-in Bilibili session was available for this research, so whether an ordinary authenticated browser session is sufficient, and whether purchased-chapter tokens behave differently, are open questions -- see the manual checks below. |
| Extraction | **Pages only** (`search`/`get_series`/`get_chapters` are left unsupported -- no server-rendered markup exists to parse, and no rendered-DOM structure for those was independently verified either). `get_pages()` loads the chapter URL through `sources.generic_import.import_comic_page()`, the same generic browser-based comic extraction Step 23 item 6 offers any unsupported site: a real headless-browser render, then every `<img>` candidate on the rendered page, filtered for real page content. **Token reuse, never token generation**: this adapter never calls Bilibili Manga's own `ImageToken` API or constructs a token itself -- it downloads exactly whatever already-signed image URLs the site's own JavaScript legitimately puts on the rendered page, the same "the browser is the source of truth for what the user can actually access" principle the challenge hand-off flow already applies to a CAPTCHA. `download_page()` returns bytes `get_pages()` already fetched during its one rendered-page visit, since the image tokens are short-lived. |
| Protection detected | Signed/expiring image-delivery tokens (a `GetImageIndex` call for paths, then a separate `ImageToken` call appending a short-lived `?token=` to each image URL) -- read directly from `Armo00/bilibili-manga-downloader` and `lihe07/bilibili_comics_downloader`'s own source, not assumed. No tile-shuffling or canvas-rendering step was found in either tool -- the protection is token-based access gating, not image-level obfuscation. Absence of evidence for paid-chapter-specific additional protection isn't proof it doesn't exist -- neither reference tool's source was confirmed tested against a real paid chapter. |
| Terms, recorded separately | Four real agreement URLs exist (`app-agreement.html`, `payment-agreement.html`, `coupon-package-agreement.html`, `privacy-policy-detail.html`, all under `manga.bilibili.com/eden/`) but each is an empty SPA shell whose text loads via client-side JS at runtime -- no JS-execution capability was available for this research pass, so the actual clause text was never read. This is "not yet checked," not "no relevant clause found" -- see the manual check below. `robots.txt`: confirmed a real 404 (no robots.txt exists at all for `manga.bilibili.com`), not a proxy block. |
| Reference | `Armo00/bilibili-manga-downloader`, `lihe07/bilibili_comics_downloader` (protection mechanism only, no code ported). A Keiyoushi/Mihon extension existed but was removed after breakage (issue #6321) -- not available as an actively-maintained reference. |
| Known limits | This adapter's real value is a populated `SourceCapabilities` record (so a future pass doesn't have to re-derive these findings), source-health tracking, and the token-reuse extraction mechanism -- not a full Mihon-style browsing experience. A direct chapter URL still works via `parse_url()` + `get_pages()`, the same "paste one URL, get its pages" path the front door already offers a site with no dedicated adapter at all. The chapter-URL shape `parse_url()` matches is an explicitly-hedged guess, not independently confirmed against a real chapter URL. |
| Tests | `tests/test_sources_bilibili_manga.py`. All fetches (static and rendered) are mocked fixtures; no real Bilibili or Playwright call is made. |

## ToonKor (툰코) — `sources/adapters/toonkor.py`

| | |
|---|---|
| URL patterns | `toonkor<digit?>.(org\|com\|net)/<slug>[.html]` -- anchored loosely on the site's own history of domain rotation, not a hardcoded current numeral |
| Content type / language | manhwa, ko |
| Status | `UNTESTED` until a real import or a Test Now button. Every selector and the decode step were independently re-verified against the live site while building this adapter (not just read from the reference extension or the roadmap). |
| Access tier | `STATIC_HTTP` only. Plain server-rendered HTML; the page-image list is lightly obfuscated (see Extraction) but needs no JavaScript execution to recover. |
| Auth | None observed. No `login()`. |
| Extraction | **Search:** `/bbs/search.php?sfl=wr_subject\|\|wr_content&stx=<query>`, `div.section-item-inner` (shared with the popular-listing markup). **Series:** `table.bt_view1` (`td.bt_title`/`td.bt_over`/`td.bt_thumb img`). **Chapters:** `table.web_list`, rows carrying a `data-role` attribute on `td.content__title` for the chapter URL. **Pages:** a `<script>` tag's `var toon_img = '<base64>'` assignment -- Base64-decoded to an HTML fragment, then every `src="..."` in that fragment, in order. Confirmed end-to-end against a real chapter (128 real page images decoded correctly) while building this adapter. |
| Domain rotation | **A real, confirmed history, not theoretical** -- this is why `base_url` is an overridable constructor argument (same pattern as `xbanxia.py`'s own domain caveat) rather than a hardcoded literal. `toonkor0.org` was re-confirmed live, current, and unchallenged immediately before writing this adapter. |
| Terms, recorded separately | `robots.txt`: `User-agent: *` with `Allow: /` and no `Disallow` lines at all -- fully permissive. Cloudflare observed acting only as a CDN, not an active challenge. (Re-verified by direct fetch while building this adapter, not carried over from an earlier vetting pass.) The ToS has not been reviewed. |
| Reference | `keiyoushi/extensions-source src/ko/toonkor` (Apache-2.0). Technique only, no code ported. |
| Known limits | If the site changes its markup, the adapter reports "layout has changed" rather than guessing. If the domain rotates again, `base_url` needs updating (or passing explicitly) -- `url_patterns` is intentionally loose enough to still route a pasted URL from a same-shaped new domain, but discovery/registration still assumes `toonkor0.org` as the default. |
| Tests | `tests/test_sources_toonkor.py`, including a full Base64-decode round-trip test. |

## 瓜子漫画 Guazimanhua — `sources/adapters/guazimanhua.py`

| | |
|---|---|
| URL patterns | `guazimanhua.com/comic.php?id=<id>`, `guazimanhua.com/chapter.php?id=<id>` |
| Content type / language | manhua, zh |
| Status | `UNTESTED` until a real import or a Test Now button. Every selector was independently re-verified against the live site while building this adapter. |
| Access tier | `STATIC_HTTP` only. |
| Auth | None observed. No `login()`. |
| Extraction | **Search:** `/category.php?keyword=<query>`, `article.card`. **Listing:** same `article.card` shape (`a.cover-wrap` href, `img.cover` src, `h3 a` title, `div.meta` text). **Series:** `div.mobile-comic-title`, `img.mobile-comic-cover`, `p.mobile-comic-desc`, `p.mobile-comic-tags` (`/`-separated genres), `p.mobile-comic-meta` (连载/完结 status), `div.cinema-strip > div` (a `<span>作者</span>` sibling `<b>` for author). **Chapters:** `div.mobile-chapter-grid a`. **Pages:** `section.reader-images img[src]` -- plain, already-absolute image URLs directly in the server response. |
| **A real, confirmed deviation from the roadmap** | The roadmap records this site as needing the browser-rendered tier for `get_pages()`, based on a direct check at vetting time that found chapter images populated only client-side against `chapter.php`/`api.php`. **Re-verifying against the live site while building this adapter found that no longer holds** -- a real chapter fetch now returns real `<img src=...>` page URLs directly inside `section.reader-images` in the raw HTML, matching what the current Keiyoushi extension source itself does (plain Jsoup parsing, no WebView). The site evidently changed between vetting and build time. `get_pages()` is implemented as plain `STATIC_HTTP` accordingly -- see the module's own docstring for the full reasoning, in case a future re-check finds the site has reverted. |
| Terms, recorded separately | `robots.txt`: named bots (including `GPTBot`/`ClaudeBot` specifically) individually disallowed, with a separate, more permissive `User-agent: *` catch-all -- the same posture already accepted for manhuagui. (Re-verified by direct fetch while building this adapter.) The ToS has not been reviewed. |
| Reference | `keiyoushi/extensions-source src/zh/guazimanhua` (Apache-2.0). Technique only, no code ported. |
| Known limits | If the site reverts to client-side image population, `get_pages()` is the one function to change -- nothing else in this adapter assumes either way. |
| Tests | `tests/test_sources_guazimanhua.py`, including a test that specifically asserts the plain-HTTP behavior (one request, no browser-rendering call) rather than the roadmap's original browser-tier expectation. |

## 妙趣漫画 Miaoqumh — `sources/adapters/miaoqumh.py`

| | |
|---|---|
| URL patterns | `miaoqumh.org/<series-slug>` (series), `miaoqumh.org/<series-folder-id>/<chapter-id>.html` (chapter) |
| Content type / language | manhua, zh |
| Status | `UNTESTED` until a real import or a Test Now button. The full page-data decode pipeline was run against a real, live chapter while building this adapter and produced correct image URLs. |
| Access tier | `STATIC_HTTP` only. |
| Auth | None observed. No `login()`. |
| Extraction | **Series/chapters:** the mobile page (`m.miaoqumh.org/<slug>`), `.infobox` (`.title`, first `img`, `.tage` lines prefixed 作者：/类型：/更新于), `.text` for the description, `ul.list > li > a` for chapters. **Pages:** the chapter page's body contains `var DATA='<base64>'`; decoded as base64 → XOR (cyclic, one of 10 fixed 8-byte keys selected by `chapter_id % 10`) → base64 again → JSON `[{"id","url"}, ...]`. |
| **`search()` deliberately left unsupported, not guessed at** | Three real checks were tried while building this adapter: the reference extension's own default search path, the site's own real "search by author" links (copied verbatim from its live markup, not constructed), and the same path on the mobile host. All three returned a plain HTTP 404 -- a currently broken/decommissioned endpoint on the site's own end, not a selector mistake here. |
| Terms, recorded separately | `robots.txt` returned an HTTP 403 on direct fetch -- exact content unconfirmed, matching the roadmap's own earlier finding. The site's actual content pages are reachable and unchallenged regardless. Cloudflare observed acting only as a CDN. The ToS has not been reviewed. |
| Reference | `keiyoushi/extensions-source src/zh/miaoqu/Miaoqu.kt`, built on the shared `MCCMSWeb` multisrc base class (Apache-2.0). Technique only, no code ported. |
| Known limits | If the site's search endpoint is ever restored, this adapter's `search()` should be revisited -- it's currently the base class's `NotSupportedError` default, not a permanent design choice. |
| Tests | `tests/test_sources_miaoqumh.py`, including a full base64/XOR/base64/JSON round-trip test across every key bucket (`chapter_id % 10` from 0 through 9) and a test confirming a mismatched key fails cleanly rather than silently returning wrong URLs. |

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

### Adaptive extraction (Step 23g) — `sources/adaptive.py`, `sources/ai_extract.py`, `sources/profiles.py`

Order tried for a pasted novel/comic URL, stopping at the first that
passes the independent checks:

1. **Saved site profile** (`<library>/source_profiles/<domain>.json`) — 0 AI calls.
2. **Deterministic extraction** (the trafilatura/heuristic extractor and the comic filter above) — 0 AI calls.
3. **AI-assisted fallback**, only when step 2 is empty or ambiguous and an engine is picked under **🤖 AI-assisted fallback** (off by default) — one `call_llm_json` call, cached by a hash of what the model reads. The model answers only with block/link/image ids; text and images are always copied from the page, never written by the model.

| | |
|---|---|
| Confidence | Per field (title, author, chapter title/number, content, next/previous link, page images, page order) as a score and HIGH/MEDIUM/LOW/FAILED. It's checked independently (length, repeat rate, text actually on the page, URL shape, image size and duplicates). The model's own score can only lower a field's confidence. |
| Profiles | Generated from an AI result, or from a profile that stopped fitting. Re-run on the page and validated before saving. Auto-saved only at HIGH, otherwise held for approval. Versions are append-only; a failing version is marked, never deleted; any version can be made active again under **🩺 Sources, health & diagnostics**. |
| Review Extraction | Shown under the import when confidence is low, or always with **Extraction diagnostics mode** (Source settings). Novel: choose the text container, leave out nav/comments/ads, pick the title and next/previous links. Comic: mark each image content/cover/ad/..., renumber pages. Corrections can be saved as the site's profile; the source content itself is never edited. |
| Media on unknown pages | For a video page no adapter or yt-dlp shortcut recognizes: **🔎 Identify media on this page** lists video/audio/manifest/subtitle resources (identify only). The one you pick goes through the normal video import. DRM markers are named, never worked around. |
| Diagnostics | Each attempt is added to the same access-attempt log as the ladder (source `generic`). It records which access tier and which extraction tier worked, how many AI calls were made, what happened with the profile, any protection detected, and the plain-language reason for a failure. |
| Tests | `tests/test_adaptive_extraction.py` — mocked engine, no network. |

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
- [ ] **Bilibili Manga, terms text:** read all four agreement URLs listed
  above in a real, logged-out browser (their text loads via client-side
  JS at runtime, so a plain fetch never sees it) and record whether any
  clause bears on automated access or AI use.
- [ ] **Bilibili Manga, authenticated/paid-chapter behavior:** with a real
  Bilibili account, confirm whether an ordinary authenticated browser
  session is sufficient for a free chapter, and whether a purchased
  chapter's image tokens behave differently or carry extra protection.
  Required before this adapter is trusted for real use -- not just before
  it's marked done in the roadmap's sense (see the batch instruction that
  authorized merging this step with these two checks still pending).
- [ ] **Step 23g, Source Diagnostics:** during a real pasted-URL import,
  open **🩺 Source diagnostics** (and the list under **🩺 Sources, health &
  diagnostics**) and confirm it shows which tier actually worked, whether
  a profile was used or generated, and a plain-language reason on a
  deliberately broken page.
- [ ] **Step 23g, Review Extraction:** with a real AI engine picked,
  import a genuinely ambiguous page, correct one misclassified element
  (e.g. mark an image as an ad), save it to the site's profile, and confirm
  the next chapter from that site uses the corrected profile, with the
  source content itself untouched.
- [ ] **ToonKor, normal work:** search a real title on `toonkor0.org`,
  open its chapter list, and download one chapter into a manhwa drama.
  Confirm the pages show up in Scanlate.
- [ ] **ToonKor, domain check:** confirm `toonkor0.org` is still the
  correct, reachable domain at the time this is checked -- this site has
  a real, confirmed history of moving.
- [ ] **guazimanhua, normal work:** search a real title, open its chapter
  list, and download one chapter into a manhua drama. Confirm the pages
  show up in Scanlate, and specifically confirm they come back on the
  first plain HTTP fetch (no browser-tier fallback needed) -- the check
  that actually validates this adapter's own deviation from the roadmap's
  original browser-tier expectation.
- [ ] **miaoqumh, normal work:** open a real series's chapter list and
  download one chapter into a manhua drama. Confirm the pages show up in
  Scanlate, validating the base64/XOR/base64/JSON decode against real
  (not just fixture) data.
- [ ] **miaoqumh, search endpoint:** periodically re-check whether
  `/search?key=<query>`-shaped requests still 404 on this site -- if the
  endpoint is ever restored, `search()` should be implemented rather than
  left as the unsupported default.
