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
| Status | `VERIFIED` (2026-09-26) -- a live pass ran `search("斗罗大陆")`, `get_series`, `get_chapters`, `get_pages`, and `download_page` end to end against the real site: real search results, a real 51-page chapter, and a real 292KB `.webp` page image downloaded successfully with the packed-script decode intact. No selector or decode drift found. |
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
| URL patterns | `52shuku.(net\|top\|vip\|org)/<category>/<N>_b/<alnum-id>[_<n>].html` (updated 2026-09-26; see Status) |
| Content type / language | novel, zh |
| Status | `VERIFIED` (2026-09-26) -- **two real regressions found in a live pass, both fixed the same day.** The site's real book-URL shape is `/<category>/<N>_b/<alnum-id>.html` (e.g. `/KeHuan/20_b/bkceK.html`) -- `<N>_b/` with an alphanumeric id, not the originally-documented `/<category>/b/<id>.html` with a purely numeric id. `url_patterns`/`parse_url()` now match both shapes (the old one is kept working too), and `parse_url()`'s returned series_id now carries the `.html` suffix `get_series()`/`get_chapters()` actually need -- a second, pre-existing bug fixed alongside it. `get_chapter_text()`'s container also moved to `div.content.contentmargin`; the old `article.article-content div#text`/`div#text` selectors are kept as fallbacks. Re-verified end to end after the fix: real search-routing, 1347 real chapters, and real chapter text all confirmed live. |
| Access tier | `STATIC_HTTP` only. Plain server-rendered HTML, no JavaScript/decoding step needed (unlike manhuagui's packed-script case). |
| Auth | None hit in testing (a small sample, not a guarantee). No `login()`. |
| Extraction | **Chapters:** the TOC page (`/<category>/<N>_b/<alnum-id>.html`), `ul.list.clearfix > li.mulu > a`. **Chapter text:** `div.content.contentmargin` (confirmed live 2026-09-26), falling back to `article.article-content div#text` / `div#text`. **Series title:** the page's own `<title>` tag (no confirmed dedicated title/author/cover selector was found for this site specifically -- unlike xbanxia's `div.book-describe`, which was) -- kept honest rather than guessed. `search()` is left unsupported: neither reference scraper's search endpoint was read in enough detail to reimplement faithfully. |
| Pacing | Forced to **1 concurrent request**, overriding even a more permissive global Settings value -- `404-novel-project/novel-downloader`'s own source notes this site "is strict about concurrency." Otherwise the normal default pace. |
| Terms, recorded separately | robots.txt (from the roadmap's direct check, not re-fetched while building this): blocks only named crawlers (AhrefsBot, Baiduspider, 360Spider, Sogou) plus a handful of internal paths (`/e/*`, `/d/*`, `/so/*`) -- no blanket `User-agent: *` disallow. The ToS has not been reviewed. |
| Reference | `Moleys/vbook-ext`; `Lieatfhy/spiderNovel`; `AgonyNihility/novel` (plain `requests`+`parsel`). Technique only, no code ported. |
| Known limits | If the site changes its markup, the adapter reports "layout has changed" rather than guessing. Long chapters split across multiple pagination pages are not auto-followed -- each chapter is fetched as the single URL its TOC entry gives. |
| Tests | `tests/test_sources_52shuku.py`, plus the text-import path in `tests/test_sources_workflows.py`'s `TestChapterImport`. |

## xbanxia.cc — `sources/adapters/xbanxia.py`

| | |
|---|---|
| URL patterns | `xbanxia.cc/books/<id>[/<chapter>.html]` (updated 2026-09-26; see Status) |
| Content type / language | novel, zh |
| Status | `VERIFIED` (2026-09-26) -- **the domain question is resolved, and four real bugs found in a live pass are all fixed.** `xbanxia.cc` (via `www.xbanxia.cc`) is confirmed the correct, working site. Fixed: (1) `search()` silently returned an empty list -- `BASE_URL` was bare `xbanxia.cc`, which 301-redirects to `www.xbanxia.cc`, and `requests` downgrades a redirected POST to GET by default, dropping the form data; `BASE_URL` now points at `www.xbanxia.cc` directly. (2) `url_patterns`/`parse_url()` never matched a real book/chapter URL -- the real path is `/books/<id>.html`/`/books/<id>/<chapter>.html` (plural "books"), not the `/<id>/` shape assumed; both now match the real shape. (3) A latent bug never previously exercised live: `_series_page()`/`get_chapter_text()` built the series/chapter path as `/{id}/` and `/{id}/{chapter}.html`, which 404 on the real site regardless of what `series_id` held -- both now build `/books/{id}.html` and `/books/{id}/{chapter}.html`. (4) `search()`'s result selectors never matched the real results markup (`li.pop-book2` with two `<a>` tags sharing one href) -- fixed, with the old selectors kept as a fallback. Re-verified end to end after the fixes: 13 real search results, a real series page, 567 real chapters, and real chapter text (`div#nr1`, no fallback needed) all confirmed live. |
| Access tier | `STATIC_HTTP` only. Plain server-rendered HTML. |
| Auth | None. `search()` is a POST with a static cookie, not a login. |
| Extraction | **Search:** POST `/modules/article/search_t.php` with `searchkey`/`Submit` form fields, a spoofed Firefox user-agent, and a static `jieqiUserCharset=utf-8` cookie; results are `li.pop-book2` (confirmed live 2026-09-26), falling back to the original `div.book-list`/`div.result-list`/`a.book-title` guess. **Series:** `/books/<id>.html`, `div.book-describe h1`/`p` (最近更新/最新章節/類型 prefixes), cover `img[data-original]`. **Chapters:** flat `div.book-list ul li a`, no pagination. **Chapter text:** `/books/<id>/<chapter>.html`, `div#nr1`, falling back to the single largest text block on the page if that id isn't found. |
| Pacing | The normal default pace -- no concurrency-sensitivity signal found for this site. |
| Terms, recorded separately | robots.txt (from the roadmap's direct check, not re-fetched while building this): `User-agent: *` with zero `Disallow` lines -- no restrictions declared at all. The ToS has not been reviewed. |
| Reference | `lncrawl/lightnovel-crawler` (MIT), `sources/zh/xbanxia.py`. Technique only, no code ported. |
| Known limits | The domain question and the `div#nr1` selector caveat are both resolved (see Status) -- the largest-text-block fallback is kept regardless, in case that ever changes back. |
| Tests | `tests/test_sources_xbanxia.py`. |

## Bilibili — `sources/adapters/bilibili.py`

| | |
|---|---|
| URL patterns | `bilibili.com/video/<BV.../av...>`, `bilibili.com/bangumi/play/...`, `b23.tv/<code>` short links |
| Content type / language | video, zh |
| Status | `VERIFIED`. Backed by yt-dlp's own maintained Bilibili extractor, which is exercised against millions of real downloads outside this project. **Re-confirmed live (2026-09-26)** against a real public video (`BV11ihW69EYg`): `get_metadata`, `available_qualities`, `get_subtitles` and `get_parts` all returned correct real data (title, uploader, upload date, duration, `480p`/`Audio only` availability, single-part detection). |
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
| Status | `UNTESTED`, deliberately not `DISQUALIFIED` or `VERIFIED` -- every authentication-dependent state genuinely hasn't been checked (see the two manual checks below), not guessed at in either direction. **A live pass (2026-09-26) had real, unrestricted network access (unlike the original build environment) but still couldn't get a conclusive render**: a plain fetch of `manga.bilibili.com/detail/mc28793` still confirms the client-rendered-only shell described above, but headless Chromium's own navigation to that page was intermittently unreliable in this sandbox (succeeded once with a near-empty shell, then repeatedly hit Playwright navigation timeouts on retry) in a way plain HTTP fetches to the same host never were -- see manhuaku's own entry below for the same pattern observed there. The two auth-dependent manual checks are still genuinely open; no real Bilibili account was available here either. |
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
| Status | `VERIFIED` (2026-09-26) -- a live pass ran `search`, `get_series`, `get_chapters` and `get_pages` end to end: real search results for a real title, a real chapter list, and 249 real page-image URLs correctly Base64-decoded from a real chapter. `toonkor0.org` is still the correct, live, unchallenged domain. |
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
| Status | `VERIFIED` (2026-09-26) -- a live pass ran `search`, `get_series`, `get_chapters` and `get_pages` end to end for a real title: real search results, real chapter list, and 36 real page-image URLs read straight out of `section.reader-images` in the raw HTML, confirming this adapter's own deviation from the roadmap's original browser-tier expectation still holds. |
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
| Status | `VERIFIED` (2026-09-26) -- a live pass ran `get_series`, `get_chapters` and `get_pages` end to end for a real series (`shiyemowang`): real metadata, a real chapter list, and 17 real page-image URLs correctly recovered through the full base64/XOR/base64/JSON decode chain. |
| Access tier | `STATIC_HTTP` only. |
| Auth | None observed. No `login()`. |
| Extraction | **Series/chapters:** the mobile page (`m.miaoqumh.org/<slug>`), `.infobox` (`.title`, first `img`, `.tage` lines prefixed 作者：/类型：/更新于), `.text` for the description, `ul.list > li > a` for chapters. **Pages:** the chapter page's body contains `var DATA='<base64>'`; decoded as base64 → XOR (cyclic, one of 10 fixed 8-byte keys selected by `chapter_id % 10`) → base64 again → JSON `[{"id","url"}, ...]`. |
| **`search()` deliberately left unsupported, not guessed at** | Three real checks were tried while building this adapter: the reference extension's own default search path, the site's own real "search by author" links (copied verbatim from its live markup, not constructed), and the same path on the mobile host. All three returned a plain HTTP 404 -- a currently broken/decommissioned endpoint on the site's own end, not a selector mistake here. |
| Terms, recorded separately | `robots.txt` returned an HTTP 403 on direct fetch -- exact content unconfirmed, matching the roadmap's own earlier finding. The site's actual content pages are reachable and unchallenged regardless. Cloudflare observed acting only as a CDN. The ToS has not been reviewed. |
| Reference | `keiyoushi/extensions-source src/zh/miaoqu/Miaoqu.kt`, built on the shared `MCCMSWeb` multisrc base class (Apache-2.0). Technique only, no code ported. |
| Known limits | If the site's search endpoint is ever restored, this adapter's `search()` should be revisited -- it's currently the base class's `NotSupportedError` default, not a permanent design choice. |
| Tests | `tests/test_sources_miaoqumh.py`, including a full base64/XOR/base64/JSON round-trip test across every key bucket (`chapter_id % 10` from 0 through 9) and a test confirming a mismatched key fails cleanly rather than silently returning wrong URLs. |

## 包子漫画 Baozimh/GoDaManhua — `sources/adapters/baozimh.py`

| | |
|---|---|
| URL patterns | `(baozimh.org\|godamh.com\|baozimh.one\|bzmh.org\|g-mh.org)/manga/<slug>` |
| Content type / language | manhua, zh |
| Status | `VERIFIED` (2026-09-26) -- a live pass ran `search`, `get_series`, `get_chapters` and `get_pages` end to end for a real title (`斗破苍穹`): real search results, a real chapter list from the live JSON API, and the custom obfuscation decoder correctly recovered 11 real image URLs from a real chapter's real encoded payload. |
| Access tier | `STATIC_HTTP` only. Listing/search/series are plain HTML; the chapter list and chapter images come from two real JSON API endpoints on a separate, fixed host (`api-get-v3.mgsearcher.com`), not mirrored across the six content domains. |
| Distinct from `baozimh.com` | The similarly-branded `baozimh.com` was confirmed blocked twice and was never built. This adapter targets the technically-open sibling family (`baozimh.org`/`godamh.com` and four more mirrors) the roadmap separately vetted -- don't conflate the two. |
| Auth | None observed. No `login()`. |
| Extraction | **Listing/search:** `.container .cardlist .pb-2 a` (`h3.cardtitle` title, `img.card` src). **Series:** `#mangachapters[data-mid]` for the internal numeric manga id; title/status/author/genre/description read via the site's own positional layout (an `<h1>`'s grandparent container's other `<div>`/`<p>` children, in a fixed order -- ported from the reference extension's own traversal, not guessed). **Chapters:** `GET api-get-v3.mgsearcher.com/api/manga/get?mid=<id>&mode=all`, a real JSON API returning chapters newest-first (reversed here to ascending order). **Pages:** `GET api-get-v3.mgsearcher.com/api/v2/chapter/getinfo?m=<mangaId>&c=<chapterId>`, whose image list is a custom-obfuscated string, decoded by a faithful port of the site's own `chapter-decoder.js` (`ChapterImageDecoder` in the adapter) -- strip a fixed prefix/suffix, reorder three body segments around two marker strings, reverse every second 7-character block, map through a custom base64url alphabet, base64url-decode, parse as JSON. **Run against a real chapter's real obfuscated payload while building this adapter** (17 real image URLs decoded correctly) -- not a guessed shape. |
| Mirrors | Six real, confirmed-reachable domains (`baozimh.org`, `godamh.com`, `m.baozimh.one`, `bzmh.org`, `g-mh.org`, `m.g-mh.org`, all HTTP 200 on direct fetch while building this adapter) used as automatic fallback for the plain-HTML endpoints, same pattern as manhuagui's four mirrors. The two JSON API calls always go to the fixed API host regardless of which content mirror is active -- a real, hardcoded detail of the reference extension, not an oversight here. |
| Terms, recorded separately | `robots.txt`: `User-agent: *` with only `/admin/` disallowed -- no Cloudflare/gatekeeper challenge on direct fetch. (Re-verified by direct fetch while building this adapter.) The ToS has not been reviewed. |
| Reference | `keiyoushi/extensions-source src/zh/baozimhorg` + `lib-multisrc/goda` (Apache-2.0). Technique only, no code ported. |
| Known limits | If the site changes its markup or either API's response shape, the adapter reports "layout has changed" rather than guessing. |
| Tests | `tests/test_sources_baozimh.py`, including a full decode round-trip against a real-shaped obfuscated payload and a mirror-fallback test. |

## 快看漫画 Kuaikan Manhua — `sources/adapters/kuaikan.py`

| | |
|---|---|
| URL patterns | `kuaikanmanhua.com/web/topic/<id>` (series), `kuaikanmanhua.com/web/comic/<id>` or `/webs/comic-next/<id>` (chapter) |
| Content type / language | manhua, zh |
| Status | `VERIFIED` (2026-09-26) -- **was found completely broken against the real site, fixed the same day, and the fix wasn't in this adapter's own code.** Every call (`get_series`, `get_chapters`, `get_pages`) failed immediately with `KeyError: "name='referer_name', domain=None, path=None"`. Root cause, confirmed by reproducing it directly against `requests`: kuaikan's real site sets a cookie named `referer_name` with an **empty string value**. `sources/http.py`'s `_requests_transport` merged a response's cookies with `cookies.update(hop.cookies)` / `cookies.update(r.cookies)` (a plain `dict.update()` against a `requests.cookies.RequestsCookieJar`) -- this called the jar's `__getitem__` per key, and `RequestsCookieJar._find_no_duplicates` treats a falsy cookie value (an empty string) as "not found" and raises `KeyError` instead of returning it (a real quirk in `requests` itself, not something this project wrote). **Fixed** by iterating each jar's own `Cookie` objects directly instead of going through `dict.update()`'s `__getitem__`-based lookup -- this was a shared-infrastructure bug, not a kuaikan-specific one, so the fix lives in `sources/http.py` and protects every other source too. Re-verified end to end after the fix: real series metadata, a real chapter list, and real page-image URLs all decoded correctly from the Nuxt state. **No Keiyoushi/Mihon extension exists for this site** (the old one was removed as broken, upstream issue #507) -- unlike every other adapter built this session, everything here came from direct, repeated live verification, not ported technique. |
| Access tier | `STATIC_HTTP` only -- **a real, confirmed correction to the roadmap**, which expected the browser-rendered tier for cover images. Direct verification found the *visible* DOM is genuinely client-populated (empty chapter lists, src-less cover `<img>` tags), but the page also embeds a `window.__NUXT__=(function(a,b,...){return {...}}(argA,argB,...))` legacy Nuxt.js SSR-state dump containing the *complete* real data -- series metadata, every chapter, and (on a chapter's own reader page) the real, already-signed page-image URLs. The catch is a real but bounded, non-JSON serialization trick (dedup identifiers plus a short list of `ident[n]=value` placeholder-mutation statements for shared substructures) -- not obfuscation, not a security boundary, just an old build tool's compaction trick. Decoded by a small, deterministic literal-plus-identifier parser (`_decode_nuxt_state` in the adapter) that never calls a function, evaluates an operator, or executes anything resembling general JavaScript. **Run against three independent real pages while building this adapter and cross-checked byte-for-byte against the same pages evaluated in a real, sandboxed Node.js `vm` used only for that verification** -- not a guessed shape. Net result: no browser-rendered tier is needed for this adapter at all. |
| Auth | None observed for free chapters. Locked/paid chapters (`locked: true` in the embedded state, `comicImages` empty) raise `ContentHidden` naming that this adapter never bypasses a purchase/entitlement check -- confirmed against a real locked chapter while building this adapter. No `login()`. |
| Extraction | **Series:** the topic page's embedded state, `topicInfo` (title/description/tags/cover/author/status). **Chapters:** the same state's `comics` array -- every chapter, already in ascending order, with real id/title/lock-status, no pagination or scroll-loading needed. **Pages:** the chapter reader page's own embedded state, `comicInfo.comicImages` -- real, already-signed CDN URLs, used exactly as issued (token reuse, never generation, the same principle already applied to Bilibili Manga's image tokens). |
| **`search()` deliberately left unsupported** | No working search endpoint was found from static analysis -- a `/search/result?q=...`-shaped path returns only a content-free `{"code":200,...}` stub, and the visible search widget has no plain `href` to inspect. Left as the base class's default rather than guessed at. |
| **What could not be verified this pass** | A real headless-browser render of this site could not be exercised in this build environment (a sandboxed outbound-network proxy real users' machines won't have) -- irrelevant to this adapter's own extraction path, which never depends on the rendered DOM, but recorded honestly rather than silently assumed. |
| Terms, recorded separately | `robots.txt` permissive (blocks only a few admin paths and query-string URLs). ToS: recorded from the roadmap's own earlier direct read -- no AI/ML-use clause found (a confirmed absence, not an assumption); not re-read while building this adapter. |
| Reference | None -- no Keiyoushi/Mihon extension exists for this site. Built entirely from direct, repeated live verification. |
| Known limits | The Nuxt-state decode is specific to this one legacy serialization shape; if the site migrates off this Nuxt version, the adapter will need re-verifying, not just re-selecting. |
| Tests | `tests/test_sources_kuaikan.py`, including direct tests of the state decoder (nested structures, the placeholder-mutation pattern, and a missing-state failure) built against a synthetic-but-grammar-accurate fixture, plus the locked-chapter `ContentHidden` path. |

## 漫画库 Manhuaku — `sources/adapters/manhuaku.py`

| | |
|---|---|
| URL patterns | `manhuaku.net/<slug>` (series), `manhuaku.net/chapter/<id>.html` (chapter) |
| Content type / language | manhua, zh |
| Status | `get_series`/`get_chapters` `VERIFIED` live (2026-09-26): real title/author/genre metadata and a real chapter list came back correctly. `get_pages()` was exercised live for the first time (a real headless browser, unlike every prior pass) and **found seriously broken for some chapters -- fixed the same day.** Some chapters (apparently the site's baozimh-aggregated ones, per the multi-source note below) return plain, real, directly-fetchable page-image URLs and worked correctly with no changes. Others -- the site's own natively-hosted, `readPic()`-protected chapters -- deliver their real page images as JavaScript `blob:` object URLs (created client-side from the AES-decrypted bytes via `URL.createObjectURL()`), which only exist inside that one browser tab's memory and can never be independently re-fetched over plain HTTP. `get_pages()`'s design (render once, then re-`GET` each rendered `<img src>` as a separate request) can never retrieve these -- every real page-image candidate failed to download and was correctly rejected by `filter_page_images`, but **the filter then silently kept other, unrelated images left on the page instead of failing** -- a live run returned 10 `PageRef`s that were entirely other titles' cover thumbnails from the page's own recommendation sidebar, not this chapter's pages, with no error raised at all. **Fixed**: `get_pages()` now detects any `blob:` candidate before the download step and refuses clearly (`ContentHidden`, `FailureReason.ENCRYPTED_RESOURCE`) naming the real limitation, rather than silently returning wrong content. Full support would need capturing the image bytes from inside the rendered page itself (e.g. via `page.evaluate`) -- not implemented, flagged as a real follow-up rather than guessed at. Separately observed, unrelated to the fix: the default 30s `page_fetch.fetch_rendered` timeout is sometimes too tight for a real, image-heavy, ad-laden page to reach Playwright's `networkidle` state (needed up to 60s in testing), and headless Chromium's own outbound networking was intermittently unreliable in this sandbox (`net::ERR_TOO_MANY_RETRIES` on repeat navigations to the same host that plain HTTP fetches never had trouble with) -- both sandbox/timing characteristics, not code bugs. |
| Access tier | `STATIC_HTTP` for search/series/chapters. **`RENDERED_BROWSER` exclusively, and deliberately, for `get_pages()`** -- a real, confirmed stock MCCMS deployment whose chapter-reader image data is passed through a `readPic(...)` call wrapped in a commercial JS obfuscator (jsjiami.com.v7) and AES-encrypted with an embedded key (corroborated by public CVE-2025-50234 documenting the same scheme server-side in MCCMS's own code). **This adapter never deobfuscates that code or reimplements its AES decryption, even though the key is real and findable** -- the same "let the site's own legitimate execution path produce the result" principle already applied to Bilibili Manga's signed tokens. `get_pages()` calls `page_fetch.fetch_rendered` directly (not the ladder's own static-first escalation, since a static fetch here would "succeed" -- a real, non-shell page -- without ever finding real images, so the ladder would never know to escalate on its own), then scrapes real image URLs from the already-decrypted, rendered DOM. |
| Auth | None observed. No `login()`. |
| Extraction | **Series:** `div.cy_title h1` (title), `span.cy_author a` (author), `span.cy_type a` (genre), `span.cy_serialize font` (连载中/已完结 status), `#comic-description`, `div.cy_info_cover img` (cover). **Chapters:** `ul[id^=mh-chapter-list-ol] li.chapter__item a`. **Multi-source note:** this site aggregates some titles from more than one upstream source (a real "source" tab list was observed naming 催漫画网 and baozimh -- the same baozimh this project has its own dedicated adapter for); only the default/first source's chapter list is read, since other sources' lists appeared to load only on demand rather than being present in the static HTML. |
| **`search()` deliberately left unsupported** | The site has a real, correctly-shaped search endpoint (`/search/<query>`) that renders a genuine "no results" page rather than a 404 or a stub -- but three separate plausible real queries, including a globally well-known title, all came back empty while building this adapter. Left unsupported rather than guessed at, matching this project's own precedent for a real-but-apparently-broken endpoint (`miaoqumh.py`). |
| Terms, recorded separately | `robots.txt` returned a real HTTP 403 (openresty-served) on three separate direct fetches across this project's research and build passes -- reproduced again while building this adapter, not transient. Recorded as "crawl guidance is inaccessible," never as "no restrictions declared." The ToS has not been reviewed. |
| Reference | `chshcms/mccms` (the real, public MCCMS platform source, for the general shape only -- this site's own template selectors are fully custom and were read directly from a live page, not from any Mihon/Keiyoushi extension, since none exists for this site). |
| Known limits | A real headless-browser render of this site was successfully exercised for the first time in the 2026-09-26 live pass (an earlier build environment's sandboxed proxy couldn't reach it at all) -- see Status above for what that found and fixed. `get_pages()` still can't read a `blob:`-protected chapter's actual pages; it refuses clearly instead of guessing, which is the real, current limit, not a build-environment gap. |
| Tests | `tests/test_sources_manhuaku.py`, including a structural (AST-based) check that no AES/crypto-decryption code or library import exists anywhere in the module, a `get_pages()` test mocked against a rendered-DOM fixture, never a raw-HTML one, and a test confirming a `blob:`-only page refuses clearly rather than silently returning unrelated images. |

## ゼロサムオンライン Zero-Sum Online — `sources/adapters/zerosumonline.py`

| | |
|---|---|
| URL patterns | `zerosumonline.com/detail/<slug>` (series; also used for chapters, since the real site has no separate per-chapter URL -- the reader is client-side-only inside the series page, matching the reference extension's own behavior). |
| Content type / language | manga, ja |
| Status | `VERIFIED` (2026-09-26) -- a live pass ran `search`, `get_chapters` and `get_pages` end to end for a real series (`futsuoya`): real listing results, a real two-chapter list, and the from-scratch protobuf reader correctly decoded 38 real page-image URLs from a real `ViewerView` response. |
| Access tier | `STATIC_HTTP`. |
| **Real protocol-level obstacle** | The content API (`api.<domain>/api/v1/...`) returns **Protocol Buffers, not JSON** -- confirmed by decoding real captured responses byte-for-byte, not trusted from the (mislabeled `content-type: application/json`) response header. A small, from-scratch, generic protobuf wire-format reader handles this (tag/varint/length-delimited only -- not a protoc-generated decoder, not a general-purpose library, and not a new dependency), general enough for this site's own small fixed schema. |
| Extraction | `GET /list?category=series&sort=date` or `/search?keyword=<q>` -- `TitleListView` (repeated `ApiTitle`, fields: 2 slug, 3 name, 4 altTitle, 5 authors, 7 description, 8 thumbnail). `GET /title?tag=<slug>` -- `TitleDetailView` (field 2 title, field 3 repeated `ApiChapter`: 1 id, 2 name, 4 publishedAt). Chapters come back **newest-first** (confirmed against a real series) -- reversed to ascending. `POST /viewer?chapter_id=<id>` (empty body) -- `ViewerView` (field 5 repeated `ViewerImage`, field 1 url). |
| Auth | None observed. No `login()`. |
| Terms | `robots.txt` is a real HTTP 404 (this is a Next.js app; confirmed by content it's the app's own catch-all, not a proxy artifact) -- no crawl guidance exists. ToS genuinely unlocatable after real effort (site + Ichijinsha's corporate umbrella) -- stays a candidate on that basis, not a clearance. |
| Reference | `keiyoushi/extensions-source` `src/ja/zerosumonline` (Apache-2.0). |
| Tests | `tests/test_sources_zerosumonline.py` -- a test-only protobuf encoder (the exact inverse of the adapter's decoder) builds every fixture; no request reaches the real site. |

## マンガ図書館Z Manga Toshokan Z — `sources/adapters/mangaz.py`

| | |
|---|---|
| URL patterns | `mangaz.com/series/detail/<id>`, `mangaz.com/book/detail/<id>` |
| Content type / language | manga, ja |
| Status | **`search`/`get_series`/`get_chapters` are `VERIFIED` live (2026-09-26)**: a real listing (`/title/addpage_renewal`), a real series page, and a real 3-chapter list all decoded correctly. `get_pages()`'s real RSA+AES flow is addressed in its own note below. |
| Access tier | `STATIC_HTTP` for search/series/chapters; `get_pages()` runs the site's own real session-scoped RSA+AES exchange. |
| **The most technically involved mechanism found this session** | A fresh 512-bit RSA keypair per session (the site's own real, legacy-weak choice); a ticket (`virgo!__ticket` cookie) + serial (embedded in real, live `app.js`) exchange; the RSA public key is POSTed and the response returns an RSA/PKCS1v1.5-wrapped AES key plus an AES-CBC/PKCS7-encrypted page manifest, decrypted locally. Ported exactly from the real reference extension's own `Crypto.kt`, never approximated. Python's mainstream crypto libraries refuse to *generate* an RSA key below 1024 bits, so this adapter generates the two ~256-bit primes itself with a standard Miller-Rabin test -- ordinary textbook keygen math, not a novel algorithm -- and hands them to `cryptography`'s own key-loading API; every actual crypto *operation* still runs through that real, audited library. |
| Extraction | Search/latest: `GET /title/addpage_renewal` (`.itemList li` cards). Series: `GET /book/detail/<id>` (`.detailAuthor > li`, `.wordbreak`, `.inductionTags a`, `.GA4_booktitle`, `div.detailCover img` -- the last two are adaptations from the reference, which never extracts a standalone title/cover; see the module docstring). Chapters/volumes: `GET /series/detail/<id>` (`.itemList li`, a real CSS *descendant* selector -- `.itemList > .itemSort > ul > li`, not direct children). Pages: the RSA+AES flow above. |
| Auth | None observed for the tier this adapter uses. No `login()`. |
| Known limits | The `.iconContinues`/`.iconEnd` ongoing/completed status markers named in the reference extension were **not found on any real page checked here** (a real, confirmed site change) -- `status` stays `unknown` until the site restores them; not chased further, since it's cosmetic. **A full live RSA+AES decrypt of a real chapter's real encrypted payload was still not completed in the 2026-09-26 live pass -- but the earlier mystery is now resolved, and the remaining one is narrowed down hard.** First finding: the apparent "hang" (350+ seconds, no response) reported earlier this same pass was never a real network freeze -- it was this adapter's own `SourceClient` retrying a real, fast HTTP 500 response up to `max_retries` times, and `vw.mangaz.com`'s own 120-second crawl-delay pacing re-applies before *every* retry, not just the first attempt, so three retries alone account for 350+ seconds of real, expected (if slow) behavior. A raw request bypassing the retry wrapper confirmed this: the server actually responds in ~1 second. Second finding, now the real remaining blocker: that fast response is a genuine, consistent **HTTP 500** (`{"name":"An Internal Error Has Occurred.","message":"An Internal Error Has Occurred.","url":"/virgo/docx/<id>.json"}`, a Laravel-shaped generic error body) -- reproduced identically across **5 different real books** and **6 different request variations** (with/without `Referer`/`Origin` headers -- their absence is what triggers the retry-storm above, since a request lacking them appears to get no response at all rather than a fast 500; the public key sent as PEM vs. raw base64 DER; `SubjectPublicKeyInfo` vs. `PKCS1` key encoding; the two cookies this adapter's own code explicitly forwards vs. the real session's complete, natural cookie set, which turned out to include two more real cookies (`MANGAZ[_VUU_]`, `_MANGAZ_`) this adapter never captures or forwards at all). None of these changed the outcome. This rules out a request-shape mistake as the likely cause and points at either a genuine, current server-side issue with this specific endpoint, or a protocol detail beyond what the reference extension's own decompiled logic (itself possibly stale) captures -- not something further guessing from outside resolved this pass. The real RSA-512/AES-CBC/PKCS7 mechanics are exercised end-to-end in tests against a locally-generated key and a locally-encrypted payload instead (see Tests). |
| Terms | Real `robots.txt` `Crawl-delay: 120` for `User-agent: *` on both `www.mangaz.com` and `vw.mangaz.com` -- respected via `host_min_interval`. No AI/crawling-specific ToS clause found (roadmap's own finding). |
| Reference | `keiyoushi/extensions-source` `src/ja/mangatoshokanz` + its own `Crypto.kt` (Apache-2.0). |
| Tests | `tests/test_sources_mangaz.py`, including a full RSA+AES round trip: a real keypair, a real RSA/PKCS1v1.5 encrypt of a real AES key, and a real AES-CBC/PKCS7 encrypt of a real JSON manifest, all built by the test itself as the exact inverse of what the adapter decrypts. |

## Generic "paste a URL" import (no adapter)

| | |
|---|---|
| URL patterns | Anything that isn't a registered source or a known video URL. |
| Content type | Detected per page: comic (≥3 page-sized images), novel (a large main-text block), or video (`og:type` video or a `<video>` tag). |
| Language | Guessed from the script used. |
| Auth | None by default. A verification page is handed to you, and after completing it in your own browser you can paste the page source to continue. For a page that needs your account, **🔐 Sign in to this site** opens a signed-in browser profile -- see "Signed-in browser sessions (Step 23k)" below. |
| Extraction | **Comic:** every `<img>`/`<source>` in reading order, including `data-src`/`data-original`/`srcset`. The filter then removes images that are: too short to be pages; off the dominant width/aspect cluster; repeated on other chapters of the same site; or from a third-party domain. **Novel:** trafilatura if installed, otherwise the largest text block after nav, header, footer and comment areas are stripped. That fallback scores a container by its *direct* children first, then re-scans counting text nested anywhere beneath it (with link text discounted, so a chapter index can't out-score the chapter) and takes the deeper result only when it finds substantially more prose -- without that second pass, the common "one wrapper element per paragraph" markup yields a single paragraph as the whole chapter. |
| Known limits | The first chapter from a site can't use the cross-chapter repeat check yet, so a page-sized logo that shares the pages' width can get through. Pages drawn on a canvas or assembled by scripts need the browser tier. trafilatura's CJK extraction hasn't been benchmarked. |
| **Pages your browser already translated** | A browser translator (Google Translate, Edge's) **replaces** a page's text rather than annotating it — confirmed against a live translation, after which the original Japanese was gone from the page. Reading such a page would hand this app the translation as though it were the source, so it would "translate" English it believes is Chinese, or save an English chapter as the original — silently, because the import succeeds and the text looks fine. This is now detected and reported, with what to do about it (turn the browser's page translation off for that site and fetch again). It matters most when you paste page source from your own browser. It is a warning, never a failure: the page loaded fine, so it never stops or escalates the access ladder. Detected from the fingerprints a real translation leaves — `translated-ltr`/`translated-rtl` on `<html>`, the `goog-gt-tt`/`goog-gt-vt` elements, and text rewritten into nested `vertical-align:inherit` `<font>` wrappers. A merely *embedded, idle* translate widget is deliberately not matched, or every page offering translation would be flagged. |
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

## Signed-in browser sessions (Step 23k) — `sources/auth_browser.py`, `page_fetch.py`

For pages that only show their content to a signed-in account. The app
never asks for, sees or stores a password.

| | |
|---|---|
| Flow | **🔐 Sign in to this site** (under a pasted URL, or per source under **🩺 Sources, health & diagnostics**): "Authentication required — a browser window will open. Log in normally and open the chapter you want." A real Chromium window opens on the computer running the app. You sign in (and pass any CAPTCHA/MFA) yourself; the app waits with no time limit until you close the window, then reads the page through that same profile and checks the content is really visible before anything is imported. |
| Persistence | One Playwright persistent profile per source: `<library>/profiles/<source>/` (for a pasted URL no adapter covers, `<library>/profiles/<host>/`). Later imports from that source read through it, so you aren't asked to sign in again. What persists is the profile directory; the browser itself is started per read and closed afterwards. **Forget this site's sign-in** deletes the profile. |
| Access tier | `AUTHENTICATED_BROWSER`. Once a source has a saved profile its pages are read only through it (so you see what your account sees, not a signed-out teaser); without one, the ordinary `STATIC_HTTP` → `RENDERED_BROWSER` tiers run as before. The Bilibili Manga adapter's chapter imports go through this path too. Kuaikan's own chapter imports are plain HTTP by design and don't use it; a pasted Kuaikan chapter URL does. |
| Never | Solves a CAPTCHA, bypasses MFA, forges or refreshes a session, or gets around a purchase/entitlement check. A page still asking for a login, a purchase, or showing protection is reported as exactly that. |
| Session data | Stays inside Chromium's profile. Nothing in the app reads cookies or storage state; they are never shown, logged, put in a library backup (the full-backup zip skips `profiles/`), or sent to an AI engine. The only prompt path in `sources/` (`ai_extract.ask_json`) additionally blanks credential-shaped URL parameters (`token=`, `auth_key=`, `sign=`, `X-Amz-*`...) that a signed-in page's image/link URLs can carry. |
| Terms | Checked first, before any window opens or request is sent. Signing in answers "can I see this", never "may the app extract it". See the capability fields and the per-site table below. |
| Tests | `tests/test_sources_auth_browser.py` — fake Playwright objects, no network, no real browser. |

### Capability fields

Each is a separate fact on a source's `SourceCapabilities` record, shown
per source under **🩺 Sources, health & diagnostics**. Each attempt's own
values (authentication, entitlement, resource types found, technical
protection) are shown in that attempt's **🩺 Source diagnostics**.

| Field | Values | Meaning |
|---|---|---|
| `access_method` | `STATIC_HTTP` / `RENDERED_BROWSER` / `AUTHENTICATED_BROWSER` / `USER_ASSISTED_BROWSER` / `OFFICIAL_API` | The access tier that actually worked. A dedicated adapter shows up as the source itself being a registered adapter, not as a separate tier value. |
| `authentication_required` | `REQUIRED` / `NOT_REQUIRED` / `UNKNOWN` | Replaces the old `auth_required: bool` (older stored records migrate automatically). `REQUIRED` once any attempt saw a login wall. |
| `purchase_required` | `REQUIRED` / `NOT_REQUIRED` / `UNKNOWN` | `REQUIRED` once any attempt saw a purchase/unlock prompt. |
| `technical_protection` | `NONE` / `DETECTED` / `UNKNOWN` | DRM, site-side decryption or signed tokens seen. A protected resource is reported as "Protected resource could not be processed without bypassing a technical control (...)", never as a bare "blocked". |
| `automation_permission` | `PERMITTED` / `EXPLICITLY_RESTRICTED` / `UNKNOWN` | What the site's own terms say about automated access. Only a directly-read clause sets `EXPLICITLY_RESTRICTED`; unread terms stay `UNKNOWN`, never `PERMITTED` by default. `EXPLICITLY_RESTRICTED` (or the older `terms.tos_prohibited`) refuses every import, signed in or not. |
| `ai_ml_use` | `ALLOWED_OR_NOT_IDENTIFIED` / `EXPLICITLY_RESTRICTED` / `UNKNOWN` | A clause restricting AI/ML use of the content. `EXPLICITLY_RESTRICTED` refuses imports too. |
| `content_access_status` | `TEXT` / `IMAGES` / `VIDEO` / `AUDIO` / `SUBTITLES` / `MIXED` / ... | Resource types actually found on a page that was reached. |

### Per-site terms for sites with no adapter — `sources/site_terms.py`

| Site | `automation_permission` | What was read |
|---|---|---|
| Naver (`*.naver.com`: Series, Webtoon) | `EXPLICITLY_RESTRICTED` | Umbrella terms ban "automated means (e.g. macro programs, robots/bots, spiders, scrapers)" for collecting content. Webtoon is covered by inference from the umbrella terms. |
| Novelpia (`novelpia.com`) | `EXPLICITLY_RESTRICTED` | Terms ban "computer programs, automated means, scripts, bots"; `robots.txt` disallows all clients except named search engines. |
| JJWXC (`jjwxc.net`) | `EXPLICITLY_RESTRICTED` | §4.3 bans any crawling/scraping (爬取/抓取); §4.9 invokes criminal liability. Never attempted. |
| KakaoPage (`page.kakao.com`) | `UNKNOWN` | Terms page is client-rendered; clause text couldn't be read. Not cleared. |

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

> **2026-09-26 live-verification pass**: this session had real, unrestricted
> network access (a first for this project -- every earlier pass here was
> either offline or behind a sandboxed proxy that couldn't reach these
> sites at all) and drove each adapter's real methods (`search`,
> `get_series`, `get_chapters`, `get_pages`, `download_page`) directly
> against the live sites, bypassing the Streamlit UI. That confirms the
> extraction/decode logic itself against real data -- the one thing no
> earlier pass could do -- but **did not** click through the actual
> Sources tab or confirm imported pages render in Scanlate/Workspace, so
> the UI-integration half of each checklist item below is still open.
> Items are marked accordingly. Full details and reproductions are in
> each source's own table above.

- [x] **manhuagui, normal work:** `search("斗罗大陆")` → `get_series` →
  `get_chapters` → `get_pages` → `download_page` all ran end to end
  against the real site: real search results, a real 51-page chapter, and
  a real 292KB `.webp` page image downloaded intact. **UI import into a
  drama/Scanlate itself not separately driven.**
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
- [x] **xbanxia domain question:** confirmed (2026-09-26) -- `xbanxia.cc`
  redirects to `www.xbanxia.cc`, which is a real, live, working novel
  site (`半夏小說`) with real search results, a real 567-chapter book page,
  and real chapter text. `xbanxia.com` returns HTTP 403 and `banxia.cc`
  redirects elsewhere; `xbanxia.cc`/`www.xbanxia.cc` is confirmed the
  right target, not a guess. Two real bugs found in the process -- see the
  adapter's own table entry above.
- [x] **52shuku, normal work:** confirmed (2026-09-26), including the
  regression this same pass found and fixed -- `get_chapter_text`'s
  container moved to `div.content.contentmargin` (fixed, with the old
  selectors kept as fallbacks) and the URL-routing patterns were updated
  to the site's real current shape. Re-verified end to end after the fix:
  real search-routing, 1347 real chapters, and real chapter text.
  **UI import into a novel drama itself not separately driven.**
- [x] **xbanxia, normal work:** confirmed (2026-09-26) against
  `www.xbanxia.cc` -- `div#nr1` matched a real chapter directly, no
  fallback needed. **UI import into a drama itself not separately
  driven.**
- [x] **Bilibili:** confirmed (2026-09-26) against a real public video
  (`BV11ihW69EYg`, `bilibili.com/video/...`): `matches_url`, `get_metadata`
  (correct title/uploader/date/duration), `available_qualities`
  (`480p`/`Audio only`), `get_subtitles` and `get_parts` (correctly
  reported as single-part) all returned correct real data before any
  download. **Full video download and transcription-pipeline hand-off, and
  a real multipart `?p=2` case, not separately driven.**
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
- [x] **ToonKor, normal work:** confirmed (2026-09-26) -- searched a real
  title on `toonkor0.org`, opened its real chapter list, and decoded 249
  real page-image URLs from a real chapter. **UI import into a drama/
  Scanlate itself not separately driven.**
- [x] **ToonKor, domain check:** confirmed (2026-09-26) -- `toonkor0.org`
  is still live, reachable and unchallenged.
- [x] **guazimanhua, normal work:** confirmed (2026-09-26) -- searched a
  real title, opened a real chapter list, and got 36 real page-image URLs
  straight out of `section.reader-images` in the raw HTML on the first
  plain HTTP fetch (no browser-tier fallback needed), validating this
  adapter's own deviation from the roadmap's browser-tier expectation.
  **UI import into a drama/Scanlate itself not separately driven.**
- [x] **miaoqumh, normal work:** confirmed (2026-09-26) -- opened a real
  series's chapter list and decoded 17 real page-image URLs from a real
  chapter through the full base64/XOR/base64/JSON chain. **UI import into
  a drama/Scanlate itself not separately driven.**
- [ ] **miaoqumh, search endpoint:** not re-checked this pass -- still
  open.
- [x] **baozimh/godamh, normal work:** confirmed (2026-09-26) -- searched
  a real title, opened a real chapter list from the live JSON API, and
  decoded 11 real page-image URLs from a real chapter's real obfuscated
  payload. **UI import into a drama/Scanlate itself not separately
  driven.**
- [ ] **baozimh/godamh, mirror fallback:** with one of the six mirrors
  genuinely unreachable (or simulated via a hosts-file/firewall block),
  confirm a real import still succeeds via the next configured mirror.
- [x] **Kuaikan, normal work:** **was blocked by a real bug (2026-09-26),
  not this adapter's own fault, now fixed.** `get_series`/`get_chapters`/
  `get_pages` all crashed immediately with `KeyError: "name='referer_name',
  domain=None, path=None"` before the `window.__NUXT__` decode was ever
  reached -- root cause was in shared code (`sources/http.py`'s cookie
  merging), triggered by a real empty-value cookie kuaikan's site sets.
  **Fixed** (see the adapter's own table entry above). Re-verified end to
  end: real series metadata, a real chapter list, and real page-image URLs
  all decoded correctly from the Nuxt state. **UI import into a manhua
  drama itself not separately driven.**
- [ ] **Kuaikan, locked-chapter message:** not reached this pass -- the
  chapter used for the normal-work check above was a free one.
- [x] **Kuaikan, browser-tier double-check:** the transport bug that
  blocked this before is fixed, and the normal-work check above confirms
  `window.__NUXT__` really is present and decodes correctly on an ordinary
  real page load -- no browser-rendered tier needed, as designed.
- [ ] **Kuaikan, search:** not re-checked this pass.
- [x] **manhuaku, normal work:** exercised for real (2026-09-26), a first
  for this adapter -- `get_series`/`get_chapters` work correctly against
  the live site. `get_pages()`'s browser-rendered tier was **found seriously
  broken for `blob:`-protected chapters** (returned other titles' cover
  thumbnails with no error) **and fixed the same day** to refuse clearly
  instead. Chapters served from the site's baozimh-aggregated backend
  return plain, real image URLs and work correctly already. Full support
  for `blob:`-protected chapters (reading images from inside the page
  context, e.g. via `page.evaluate`) is a real follow-up, not done here --
  see the adapter's own table entry above.
- [ ] **manhuaku, search endpoint:** not re-checked this pass.
- [ ] **Step 23k, signed-in session (pending a real account):** with a
  real account on a source actually confirmed permitted (Bilibili Manga,
  or a pasted Kuaikan chapter URL), use **🔐 Sign in to this site**:
  complete the sign-in in the window, close it, and confirm the app
  reports the page as visible. Import a second chapter and confirm no
  sign-in window is needed. Confirm **🩺 Source diagnostics** shows
  accurate authentication, entitlement/purchase, resource-type and
  technical-protection lines, and that the source's record under
  **🩺 Sources, health & diagnostics** shows the six capability fields.
  Also confirm a Naver/Novelpia URL is refused at the capability check
  even with a sign-in. Not verifiable in the build environment: no real
  account was available, and real headless-browser rendering doesn't
  work through that environment's network proxy, so only the mocked tests
  in `tests/test_sources_auth_browser.py` have been run. Also unverified:
  that closing the Chromium window fires Playwright's context `close`
  event on every OS, and that a site accepts a session signed in on a
  headed window when it is later read headless.
- [x] **zerosumonline, normal work:** confirmed (2026-09-26) -- searched a
  real series (`futsuoya`), pulled a real 2-chapter list, and the
  from-scratch protobuf reader decoded 38 real page-image URLs from a real
  `ViewerView` response with no changes needed. **UI import into a drama
  itself not separately driven.**
- [ ] **mangaz, full RSA+AES round trip:** `search`/`get_series`/
  `get_chapters` confirmed live (2026-09-26) against real data, and this
  pass got much further than ever before on `get_pages()` -- a real serial
  and a real, valid ticket were both obtained live, and the "hang" reported
  earlier this pass turned out to be `SourceClient`'s own retry logic
  re-triggering the site's 120s crawl-delay pacing on every retry of a
  real, fast HTTP 500 -- not a true network freeze. That 500 itself
  reproduced identically across 5 different real books and 6 request
  variations (headers, cookies, public-key encoding -- see the adapter's
  own "Known limits" entry above for the full list). Still open: get a
  real decrypt to actually complete -- the 500 looks like either a current
  server-side issue with this one endpoint, or a protocol detail beyond
  what this pass's systematic variation-testing covered.
- [ ] **Mag-Comi, raw1001.net, novema.jp, Kakuyomu, Hameln -- generic
  pipeline only, no dedicated adapter:** search a real title on each
  through the existing generic paste-a-URL / adaptive-extraction flow
  (Step 23/23g) and confirm all five work with zero site-specific code --
  Mag-Comi's `#scramble`-flagged pages and Hameln's Cloudflare challenge
  both resolve to the existing browser-rendered-tier routing decision;
  raw1001.net's AJAX-delivered images resolve the same way; novema.jp and
  Kakuyomu are plain HTML the deterministic tier already reads. This is
  the check that actually validates the roadmap's own scope correction
  for this step (originally a 7-site draft, narrowed to the two adapters
  above once the other five were confirmed to need no dedicated code).
