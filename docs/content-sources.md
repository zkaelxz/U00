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
| Status | `VERIFIED` (2026-09-26) -- a live pass found two regressions, both fixed the same day. The real book-URL shape is `/<category>/<N>_b/<alnum-id>.html` (the old `/<category>/b/<id>.html` shape still matches), and `parse_url()`'s series_id now carries the `.html` suffix `get_series()`/`get_chapters()` need. `get_chapter_text()`'s container moved to `div.content.contentmargin` (old selectors kept as fallbacks). Re-verified live: search routing, 1347 chapters, chapter text. |
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
| Status | `VERIFIED` (2026-09-26) -- `xbanxia.cc` (via `www.xbanxia.cc`) is the correct, working site. A live pass found four bugs, all fixed: `BASE_URL` now points at `www.xbanxia.cc` directly (the bare domain's 301 turned the search POST into a GET and dropped the form data, so `search()` returned nothing); URL patterns and series/chapter paths use the real `/books/<id>.html` and `/books/<id>/<chapter>.html`; search selectors match `li.pop-book2` (old ones kept as a fallback). Re-verified live: 13 search results, 567 chapters, chapter text (`div#nr1`). |
| Access tier | `STATIC_HTTP` only. Plain server-rendered HTML. |
| Auth | None. `search()` is a POST with a static cookie, not a login. |
| Extraction | **Search:** POST `/modules/article/search_t.php` with `searchkey`/`Submit` form fields, a spoofed Firefox user-agent, and a static `jieqiUserCharset=utf-8` cookie; results are `li.pop-book2` (confirmed live 2026-09-26), falling back to the original `div.book-list`/`div.result-list`/`a.book-title` guess. **Series:** `/books/<id>.html`, `div.book-describe h1`/`p` (最近更新/最新章節/類型 prefixes), cover `img[data-original]`. **Chapters:** flat `div.book-list ul li a`, no pagination. **Chapter text:** `/books/<id>/<chapter>.html`, `div#nr1`, falling back to the single largest text block on the page if that id isn't found. |
| Domains | A domain list (`sources/domains.py`): `www.xbanxia.cc`, then `xbanxia.cc`, tried in order with the last one that worked first (remembered in `sources.db`, so it survives a restart). The owner can edit the list on the PC (`/api/source-domains`). A result that ended on another host (the bare domain's redirect to `www` included) counts as that domain failing and is not used; a search POST may already have had its form re-sent by a 307/308. If a listed domain redirects to a new https host whose page passes `verify_site` (半夏 in the title, the `search_t.php` search form, three or more `/books/<id>.html` links), that host becomes a pending proposal; only the owner's confirmation adds it to the list. |
| Pacing | The normal default pace -- no concurrency-sensitivity signal found for this site. |
| Terms, recorded separately | robots.txt (from the roadmap's direct check, not re-fetched while building this): `User-agent: *` with zero `Disallow` lines -- no restrictions declared at all. The ToS has not been reviewed. |
| Reference | `lncrawl/lightnovel-crawler` (GPL-3.0 per its current LICENSE, checked 2026-09-30; earlier recorded here as MIT), `sources/zh/xbanxia.py`. Technique only, no code ported. |
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
| Status | `UNTESTED`, deliberately not `DISQUALIFIED` or `VERIFIED` -- every authentication-dependent state is unchecked (see the two manual checks below). A live pass (2026-09-26) confirmed a plain fetch of `manga.bilibili.com/detail/mc28793` returns the client-rendered-only shell described above, but headless Chromium's navigation to it was intermittently unreliable (one near-empty shell, then repeated Playwright timeouts), so there was no conclusive render. No real Bilibili account was available. |
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
| Domain rotation | **A real, confirmed history, not theoretical.** The domain is a list (`sources/domains.py`, default `toonkor0.org`), tried in order with the last one that worked first (remembered in `sources.db`), and editable by the owner on the PC (`/api/source-domains`). A page a listed domain redirects to another host is not used or cached; if that page is https and passes `verify_site` (툰코/ToonKor in the title and three or more `div.section-item-inner` webtoon cards, i.e. a home or listing page), its host becomes a pending proposal the owner confirms or dismisses. If every domain fails and nothing is pending, the source's health shows "all domains unreachable" and, with the maintenance assistant on, one backlog item is filed. `toonkor0.org` answered HTTP 522 (Cloudflare: origin unreachable) on 2026-09-30. |
| Terms, recorded separately | `robots.txt`: `User-agent: *` with `Allow: /` and no `Disallow` lines at all -- fully permissive. Cloudflare observed acting only as a CDN, not an active challenge. (Re-verified by direct fetch while building this adapter, not carried over from an earlier vetting pass.) The ToS has not been reviewed. |
| Reference | `keiyoushi/extensions-source src/ko/toonkor` (Apache-2.0). Technique only, no code ported. |
| Known limits | If the site changes its markup, the adapter reports "layout has changed" rather than guessing. `url_patterns` stays loose (`toonkor<digit>`), so a pasted link from a same-shaped new domain still routes here; a confirmed domain of another shape is used for requests but a pasted link from it isn't recognised until `url_patterns` is widened. |
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
| Status | **UNVERIFIED on the owner's machine (owner report 2026-10):** a plain fetch returned an empty SPA shell and the browser tier has not been run for this site, so "no browser needed" is not confirmed. The 2026-09-26 sandbox pass below is kept as history. `VERIFIED` (2026-09-26) -- a live pass ran `get_series`, `get_chapters` and `get_pages` end to end for a real series (`shiyemowang`): real metadata, a real chapter list, and 17 real page-image URLs correctly recovered through the full base64/XOR/base64/JSON decode chain. |
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
| Status | **UNVERIFIED on the owner's machine (owner report 2026-10):** a plain fetch returned an empty SPA shell and the browser tier has not been run for this site, so "no browser needed" is not confirmed. The 2026-09-26 sandbox pass below is kept as history. `VERIFIED` (2026-09-26) -- a live pass ran `search`, `get_series`, `get_chapters` and `get_pages` end to end for a real title (`斗破苍穹`): real search results, a real chapter list from the live JSON API, and the custom obfuscation decoder correctly recovered 11 real image URLs from a real chapter's real encoded payload. |
| Access tier | `STATIC_HTTP` only. Listing/search/series are plain HTML; the chapter list and chapter images come from two real JSON API endpoints on a separate, fixed host (`api-get-v3.mgsearcher.com`), not mirrored across the six content domains. |
| Distinct from `baozimh.com` | The similarly-branded `baozimh.com` was confirmed blocked twice and was never built. This adapter targets the technically-open sibling family (`baozimh.org`/`godamh.com` and four more mirrors) the roadmap separately vetted -- don't conflate the two. |
| Not covered: `twmanga.com` / `twbzmg.com` | `www.twmanga.com` is a regional mirror of the blocked `baozimh.com` (footer '© 2026 BAOZIMH.COM 包子漫畫', same brand as `www.twbzmg.com`), not part of this family: it uses `/comic/<slug>` and `/comic/chapter/<slug>/<section>_<chapter>.html`, not the JSON API above. No adapter exists; the owner's fallback is the browser extension. Dated vetting (2026-10-08): `docs/research/twmanga-vetting.md`. |
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
| Status | **UNVERIFIED on the owner's machine (owner report 2026-10):** a plain fetch returned an empty SPA shell and the browser tier has not been run for this site, so "no browser needed" is not confirmed. The 2026-09-26 sandbox pass below is kept as history. `VERIFIED` (2026-09-26) -- found broken against the real site and fixed the same day, in shared code rather than this adapter: `sources/http.py`'s `_requests_transport` merged response cookies with `dict.update()`, which raises `KeyError` on a cookie with an empty value (kuaikan sets `referer_name=""`). It now iterates each jar's `Cookie` objects, which protects every source. Re-verified live: series metadata, chapter list, page-image URLs decoded from the Nuxt state. No Keiyoushi/Mihon extension exists for this site (upstream issue #507), so everything here came from direct live verification. |
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
| Status | `VERIFIED` (2026-09-26) -- `get_series`/`get_chapters` live, and `get_pages()` works for both content paths after two same-day fixes. Baozimh-aggregated chapters return plain image URLs. Natively hosted `readPic()` chapters deliver `blob:` object URLs that exist only inside the browser tab; they had returned 10 wrong `PageRef`s (other titles' covers) with no error. Fix 1: `page_fetch.fetch_rendered_resolving_blobs()` captures the blob bytes from inside the page, and an uncapturable blob refuses with `ContentHidden` (`ENCRYPTED_RESOURCE`). Fix 2, shared code: `page_fetch._rendered_page()` scrolls to the bottom once after load, because a scroll-triggered lazy-load left some chapters with no reader images; this helps every browser-tier adapter. Confirmed live: 18 real images and a real 234KB page, twice. |
| Access tier | `STATIC_HTTP` for search/series/chapters. **`RENDERED_BROWSER` exclusively, and deliberately, for `get_pages()`** -- a real, confirmed stock MCCMS deployment whose chapter-reader image data is passed through a `readPic(...)` call wrapped in a commercial JS obfuscator (jsjiami.com.v7) and AES-encrypted with an embedded key (corroborated by public CVE-2025-50234 documenting the same scheme server-side in MCCMS's own code). **This adapter never deobfuscates that code or reimplements its AES decryption, even though the key is real and findable** -- the same "let the site's own legitimate execution path produce the result" principle already applied to Bilibili Manga's signed tokens. `get_pages()` calls `page_fetch.fetch_rendered` directly (not the ladder's own static-first escalation, since a static fetch here would "succeed" -- a real, non-shell page -- without ever finding real images, so the ladder would never know to escalate on its own), then scrapes real image URLs from the already-decrypted, rendered DOM. |
| Auth | None observed. No `login()`. |
| Extraction | **Series:** `div.cy_title h1` (title), `span.cy_author a` (author), `span.cy_type a` (genre), `span.cy_serialize font` (连载中/已完结 status), `#comic-description`, `div.cy_info_cover img` (cover). **Chapters:** `ul[id^=mh-chapter-list-ol] li.chapter__item a`. **Multi-source note:** this site aggregates some titles from more than one upstream source (a real "source" tab list was observed naming 催漫画网 and baozimh -- the same baozimh this project has its own dedicated adapter for); only the default/first source's chapter list is read, since other sources' lists appeared to load only on demand rather than being present in the static HTML. |
| **`search()` deliberately left unsupported** | The site has a real, correctly-shaped search endpoint (`/search/<query>`) that renders a genuine "no results" page rather than a 404 or a stub -- but three separate plausible real queries, including a globally well-known title, all came back empty while building this adapter. Left unsupported rather than guessed at, matching this project's own precedent for a real-but-apparently-broken endpoint (`miaoqumh.py`). |
| Terms, recorded separately | `robots.txt` returned a real HTTP 403 (openresty-served) on three separate direct fetches across this project's research and build passes -- reproduced again while building this adapter, not transient. Recorded as "crawl guidance is inaccessible," never as "no restrictions declared." The ToS has not been reviewed. |
| Reference | `chshcms/mccms` (the real, public MCCMS platform source, for the general shape only -- this site's own template selectors are fully custom and were read directly from a live page, not from any Mihon/Keiyoushi extension, since none exists for this site). |
| Known limits | A real headless-browser render of this site was successfully exercised for the first time in the 2026-09-26 live pass (an earlier build environment's sandboxed proxy couldn't reach it at all) -- see Status above for the two real bugs that pass found and fixed. No known remaining gap in `get_pages()` itself; a `blob:` candidate that genuinely can't be captured (a real render failure, not the lazy-load case Fix 2 addresses) still refuses clearly rather than guessing. |
| Tests | `tests/test_sources_manhuaku.py`, including a structural (AST-based) check that no AES/crypto-decryption code or library import exists anywhere in the module, a `get_pages()` test mocked against a rendered-DOM fixture, never a raw-HTML one, a test confirming a `blob:`-only page refuses clearly when its bytes weren't captured, and a test confirming a captured blob: page is served correctly (never re-fetched over HTTP). |

## ゼロサムオンライン Zero-Sum Online — `sources/adapters/zerosumonline.py`

| | |
|---|---|
| URL patterns | `zerosumonline.com/detail/<slug>` (series; also used for chapters, since the real site has no separate per-chapter URL -- the reader is client-side-only inside the series page, matching the reference extension's own behavior). |
| Content type / language | manga, ja |
| Status | **UNVERIFIED on the owner's machine (owner report 2026-10):** a plain fetch returned an empty SPA shell and the browser tier has not been run for this site, so "no browser needed" is not confirmed. The 2026-09-26 sandbox pass below is kept as history. `VERIFIED` (2026-09-26) -- a live pass ran `search`, `get_chapters` and `get_pages` end to end for a real series (`futsuoya`): real listing results, a real two-chapter list, and the from-scratch protobuf reader correctly decoded 38 real page-image URLs from a real `ViewerView` response. |
| Access tier | `STATIC_HTTP`. |
| **Real protocol-level obstacle** | The content API (`api.<domain>/api/v1/...`) returns **Protocol Buffers, not JSON** -- confirmed by decoding real captured responses byte-for-byte, not trusted from the (mislabeled `content-type: application/json`) response header. A small, from-scratch, generic protobuf wire-format reader handles this (tag/varint/length-delimited only -- not a protoc-generated decoder, not a general-purpose library, and not a new dependency), general enough for this site's own small fixed schema. |
| Extraction | `GET /list?category=series&sort=date` or `/search?keyword=<q>` -- `TitleListView` (repeated `ApiTitle`, fields: 2 slug, 3 name, 4 altTitle, 5 authors, 7 description, 8 thumbnail). `GET /title?tag=<slug>` -- `TitleDetailView` (field 2 title, field 3 repeated `ApiChapter`: 1 id, 2 name, 4 publishedAt). Chapters come back **newest-first** (confirmed against a real series) -- reversed to ascending. `POST /viewer?chapter_id=<id>` (empty body) -- `ViewerView` (field 5 repeated `ViewerImage`, field 1 url). |
| Auth | None observed. No `login()`. |
| Terms | `robots.txt` is a real HTTP 404 (this is a Next.js app; confirmed by content it's the app's own catch-all, not a proxy artifact) -- no crawl guidance exists. ToS genuinely unlocatable after real effort (site + Ichijinsha's corporate umbrella) -- stays a candidate on that basis, not a clearance. |
| Reference | `keiyoushi/extensions-source` `src/ja/zerosumonline` (Apache-2.0). |
| Tests | `tests/test_sources_zerosumonline.py` -- a test-only protobuf encoder (the exact inverse of the adapter's decoder) builds every fixture; no request reaches the real site. |

## 猫耳FM MissEvan — `sources/adapters/missevan.py`

| | |
|---|---|
| URL patterns | `missevan.com/mdrama/<id>` and `missevan.com/mdrama/drama/<id>` (series, both real, live), `missevan.com/sound/<sound_id>` (episode) |
| Content type / language | **First audio_drama adapter** (Step 94 added `ContentType.AUDIO_DRAMA`, previously nonexistent), zh |
| Status | `search`/`get_series`/`get_chapters`/`get_audio_url` all **confirmed live (2026-09-28)** for free content, over three plain JSON HTTP endpoints. The authenticated/paid-episode path is unverified -- no real account was available (see Auth). |
| Access tier | `STATIC_HTTP` only for free content -- no browser rendering needed. `robots.txt` is real and permissive: `Disallow: /files/` and `/backend/` only, no blanket `User-agent: *` disallow, and none of this adapter's API paths are blocked. |
| Extraction | `GET /dramaapi/search?s=<keyword>&p=<page>` (the real param is `s`, not `keyword` -- a `keyword=` request 200s but always reports zero results, which looks like a working-but-empty search unless separately cross-checked). `GET /dramaapi/getdrama?drama_id=<id>` for series + episode list (`info.episodes.{ft,episode,music}`; `get_chapters()` includes `episode`+`ft`, excludes `music`-only soundtrack tracks -- not spoken dialogue). `GET /sound/getsound?soundid=<sound_id>` for the resolved audio, preferring `videourl` > `soundurl_128` > `soundurl`. |
| **Audio isn't a flat file** | `soundurl`/`soundurl_128` are signed, expiring **HLS (`.m3u8`) manifest URLs** (`?...&expire_time=...&token=...`), not direct downloadable files -- playable, but turning one into a local file needs an HLS-aware fetch (e.g. ffmpeg), not implemented here. A `dash.audio[].base_url` structure (signed, fragmented `.m4s` segments) also exists and isn't used. `models.AudioRef.format` distinguishes `"hls"` from `"direct"` for whichever a caller gets. |
| **The paywall is a silent null field, not an error** | Confirmed against a real paid drama (魔道祖师 第三季, `drama_id=22602`, `need_pay:1`/`price:399`) and its first paid episode with no session: `getsound` returns HTTP 200 / `success:true`, but `soundurl`/`soundurl_128` are both `null`, with `need_pay`/`price`/`pay_type`/`limit_type` fields the free response doesn't carry at all. `get_audio_url()` checks these explicitly and raises `ContentHidden`/`FailureReason.PURCHASE_REQUIRED` with a specific message, never a raw failure or a broken URL. |
| Auth | `login()` (inherited, generic) opens `sources/auth_browser.py`'s persistent profile for a real sign-in. `get_audio_url()` retries a locked episode by reading the same `getsound` URL through that profile (`page_fetch.fetch_with_profile`), on the assumption its rendered `page.content()` still carries the raw JSON body. **Unverified** -- no real purchased/VIP MissEvan account was available this pass, matching `bilibili_manga.py`'s own hedge on its authenticated tier. |
| Anti-bot | A real, confirmed wall, but Referer-gated rather than blanket: one `getsound` request sent with no `Referer` header came back HTTP 200 with an Aliyun WAF slide-verification page instead of JSON; this adapter always sends a `Referer`, and `sources/detect.py`'s existing bot-challenge detection (matches "滑动验证") already raises `ChallengeDetected` if it recurs -- no special-casing added. |
| Terms, recorded separately | The real ToS (猫耳FM用户使用协议, `link.missevan.com/rule/duty`, genuinely server-rendered, read in full) **explicitly restricts automated access**: §4.2.11 bans using any automated program/script/bot/spider/crawler to obtain the platform's services, content, or data, for any reason, without prior written permission -- the same class of clause that marks Naver/Novelpia/JJWXC `EXPLICITLY_RESTRICTED` in `site_terms.py`. Recorded in `capabilities()` (`terms.tos_prohibited = True`); per Step 90, `ladder.check_terms()`'s enforcement is currently a deliberate app-wide no-op, so this is recorded for the record rather than enforced -- the same "state facts, the person decides" convention every adapter follows. |
| Known limits | `music`-catalog entries (soundtrack-only) are excluded from `get_chapters()`. HLS manifests are returned as-is, not downloaded/muxed to a file. The authenticated-fallback extraction shape (raw JSON inside a rendered `<pre>`) is a reasonable guess, not confirmed against a real session. |
| Tests | `tests/test_sources_missevan.py`. All fetches are mocked fixtures trimmed from real captured responses; no live network call. |

## 饭角 Fanjiao — `sources/adapters/fanjiao.py`

| | |
|---|---|
| URL patterns | `www.fanjiao.co/pages/share.html?album_id=<id>` (series). The roadmap's "泛娱有声" label was wrong: the platform is 饭角, operated by 深圳热蓝科技有限公司; `known_sites.py`'s old `www.fanjiao.cc` (DNS fails) is now `https://www.fanjiao.co/`. |
| Content type / language | audio_drama, zh (baihe/GL, 18+) |
| Status | **Metadata only: `get_series` and `get_chapters`.** No `search()` (no public search or catalogue) and no `get_audio_url()` (the public page exposes no audio; 收听第一集 opens the app). Built 2026-09-30 against the page's own script (`/files/js/share03.js`); **a live render could not be run from the build container**, so the first real run is still to do (see the manual checks below). |
| Access tier | `RENDERED_BROWSER`. `share.html` is an empty template (title, brief, 参演CV, 收听第一集, 最新评论, 打开APP查看全部内容) until its own script runs. |
| **Protection, recorded not worked around** | The site's API (`api.fanjiao.co/walkman/api/...`) needs an md5 `signature` header computed from the query plus a secret salt; the app adds Shumei risk control and 360 hardening. User decision (2026-09-30): never extract or reimplement the signing. The adapter opens the share page through `page_fetch.api_capture_session` (the guarded browser) and reads only the responses the page's own script already made: `album/album_info` (name, description, cover, author_name, update_frequency), `album/actor_cvs` (`cv_list[].name`/`role_name`, added to the description as 参演CV) and `album/audio` (`audios_list[].audio_id`/`name`, the episode list; the page itself only uses the first id). The rendered DOM (`.title`, `.brieftext`, `.titleimg img`, `.cvname`) is the fallback for the series fields. The same "let the site's own execution path produce the result" rule as Bilibili Manga and manhuaku. |
| Paid / locked | Paid episodes stay in the list with the group `付费 / paid (app only)`. The flag keys aren't confirmed, so a small set of likely ones is checked (`need_pay`, `is_pay`, `pay_type`, `vip`, `is_vip`, `price` > 0, `is_free` = 0, `lock`/`is_lock`). If the page's own album or episode call is refused (a non-200 answer) or answers without a list (paid, 18+, removed or app-only album), `get_series()`/`get_chapters()` raise `ContentHidden`/`PURCHASE_REQUIRED` rather than returning an empty list. The API reports that as reason `PURCHASE_REQUIRED` with the adapter's message, not as the adult-works hint. |
| Paid episodes (user decision, 2026-09-30) | **A purchase is never bypassed.** Paid episodes are listed as metadata only and never fetched: this adapter has no audio path at all. Reading a bought episode would be allowed only through the authenticated-browser tier, with the person signed in on the web and the page itself showing that episode. No web sign-in has been found on fanjiao.co (the share page opens the app; the user agreement is app-only), so no authenticated tier is built. Anything else stays `ContentHidden`. |
| Subtitles (user decision, 2026-09-30) | https://fun.zhufree.fun/sub-download is a **manual route only**: the person downloads the SRT in their own browser and imports it in the Source stage. Baihe never calls that site. It almost certainly makes Fanjiao's signed API calls on the visitor's behalf, has no published API or terms, and is run by the same person as BaiheHub, which Baihe already queries. |
| Finding album ids | baihehub.com's audio-drama records carry a `fjId` field (the Fanjiao album id). The existing BaiheHub search (`title_library.search_baihehub`) doesn't surface it yet; paste the share link instead. |
| Pacing | One render makes four or five calls on the site's side, so renders are spaced 10 s apart (`host_min_interval`), under the client's pacing and concurrency limit (`client.paced`). `get_series` and `get_chapters` share one render per album. |
| From another device | No browser (docs/remote-access-decision.md): the URL preview, preflight, the series listing and "Check now" set the adapter's `allow_browser` from "is this request from this PC" (the scheduled check runs on the PC and may render); with it off, the adapter refuses (`JAVASCRIPT_REQUIRED`) before rendering. Browser failures (Playwright missing, a timeout, the proxy check) come back as a plain `SourceError`. |
| Terms | No robots.txt (every unknown path returns the homepage). The user agreement is only viewable in the app; the public `/pages/useragree.html` is the privacy policy and has no automation clause. `automation_permission` UNKNOWN, `technical_protection` DETECTED, in `capabilities()` and in `site_terms.py` (whose `capabilities_for()` now carries an entry's `technical_protection`). |
| Reference | `tsinglinrain/YuriAudio2Notion` (Apache-2.0), read for field names only; its endpoints/signing approach are not used. |
| Tests | `tests/test_sources_fanjiao.py`. No network or browser: a fake capture returns rendered HTML plus the page's own responses, shaped after `share03.js` and the reference's field list (not a trimmed live capture). |

## 轻之国度 LightNovel — `sources/adapters/lightnovel_fun.py`

| | |
|---|---|
| URL patterns | `lightnovel.fun/book/<bookId>` (series), `lightnovel.fun/reader/<bookId>/<chapterId>` (chapter). Only `www.lightnovel.fun`: `lightnovel.us` returned 503 when vetted, and the site's own footer names lightnovel.fun. |
| Content type / language | novel, zh |
| Status | `search`/`get_series`/`get_chapters`/`get_chapter_text` all **confirmed live (2026-09-30)**: 20 real search results for 百合, book 33139 (13 chapters over 2 volumes, matching the site's count), and book 1338 (84 chapters over 13 volumes, matching the per-volume counts, 25 requests). Scope is a user decision (2026-09-30): personal zh→en reading of the public `/book` and `/reader` pages only. |
| Access tier | `STATIC_HTTP` only. Every page is server-rendered by Nuxt and carries its own state as a `<script id="__NUXT_DATA__">` JSON payload (Nuxt's devalue format, a flat array with index references); the adapter reads that payload. No JavaScript is run and nothing protected is decoded. No Cloudflare challenge seen (a 2023 note in JeffersonQin/lightnovel_epub reports one on lightnovel.us); if one appears it is a wall, handed to the person. |
| Extraction | **Search:** `GET /search?keyword=<q>&page=<n>`, the payload entry keyed by the search parameters (`items`, 20 per page). **Series:** `GET /book/<id>`, `pc-book-detail-<id>.book` (title, author, illustrator, cover, tags, summary, status), plus the first public chapter's reader page (one extra request; skipped when that chapter is locked). That chapter (制作信息, or an EPUB work's resource post) and the summary supply the work's own uploader notice and any posted download links (see Never touched). **Chapters:** the same page's `catalog`. It loads only the first volume's chapters (the HTML grid shows only 8 of them, which is why the payload is read); each other volume comes back `chaptersLoaded: false`. A `/reader` page loads its own chapter's volume and gives `prevChapterId`/`nextChapterId`, so the adapter walks from a loaded volume's last chapter to the next volume's first chapter (backwards via `prevChapterId` if only a later volume is loaded): two reader pages per extra volume. Every volume a walked reader page loads is kept, so a volume the links skip over (no public chapter) doesn't cut off the ones after it. A volume the walk can't reach (no link, or a deleted chapter's page returns an HTTP error) is logged and left out; a challenge still stops everything. **Chapter text:** `GET /reader/<bookId>/<chapterId>`, `reader-bootstrap-…-public.currentChapter.contentHtml` (paragraphs to lines, ruby readings dropped), and refused (`LayoutChanged`) when the payload is missing, since the lock flag lives there and a locked chapter's page still renders its teaser. An illustrations-only chapter (彩页) returns empty text. Every id is checked to be ASCII digits before a URL is built. |
| Never touched | By user decision: the login API (`/api/pc-proxy/api/bff/auth-password-login-v1`) and the paid 轻币 unlock (`…/new-content-read/unlock-chapter`). A locked chapter (`locked`, or a non-public `accessType` such as `coin` or `brave` not unlocked) raises `ContentHidden` (`PURCHASE_REQUIRED`) naming the 轻币 price. **EPUB / file-locker links** (pan.baidu.com, lanzou, 123pan, 阿里云盘, 夸克) a work posts are **listed for the person to open, never downloaded or followed** (user decision, 2026-09-30, the `dl-raw.si` precedent): `SeriesInfo.links` `{label, url, password}`, the 提取码 taken from `?pwd=` or a 提取码/密码 line next to the link. The service passes each URL through `safe_url` (so `?pwd=` is dropped and the code travels as `password`), and the series panel shows them with a pointer to Workspace → Source → Novel text → Attach EPUB. |
| Auth | None. No `login()` override is used for this source. |
| Pacing | The normal default pace. robots.txt has no `Crawl-delay`. Chapter lists and search are never served from cache. |
| Terms, recorded separately | robots.txt (fetched directly 2026-09-30): `User-agent: *` disallows only `/settings/` and `/publish_mgr/`; AhrefsBot, DotBot, MJ12bot and SemrushBot get `Disallow: /`. The site rules page (LK站规) couldn't be located (footer anchors have no href; `/site_rule` 404s), so `automation_permission` stays `UNKNOWN`. Uploaders' per-work notices ("仅供个人学习交流使用，禁作商业用途", "禁止转载", "禁止二改二传") are recorded in the capability `terms` block and in `sources/site_terms.py`, shown under the source's **Terms notes**, and the work's own notice lines (from its summary or first chapter, e.g. 仅供个人学习交流使用 / 下载后请在24小时内删除 / 转载请保留…) open its series description, above the summary, so the series panel's 3-line clamp doesn't hide them; the generic site notice is used only when the work has none. |
| Reference | Read directly from the live site. JeffersonQin/lightnovel_epub consulted for the Cloudflare history only; no code ported. |
| Known limits | If a volume can't be reached through the prev/next chain it is left out of the list (logged, not shown to the person) rather than failing the whole series. Imports and chapter lists keep the adapter's order, which is the site's (user decision 2026-09-30), so unnumbered front matter (制作信息, 彩页, 后记) stays where the site puts it. Only manhuagui opts out (`chapters_in_site_order = False`, its per-section `<ul>` blocks aren't reliably in reading order) and keeps the natural sort within each section; chapters already stored in a library are never reordered. The book record's own `chapterCount` can differ from the per-volume counts (book 1338: 91 vs 84); the adapter lists what the volumes contain. Locked chapters were recorded live: `/reader/31629/318181` (`coin`, 20 轻币, teaser body with `[资源解锁后可用]`) and `/reader/361/260712` (`brave`). |
| Tests | `tests/test_sources_lightnovel_fun.py`, fixtures in `tests/lightnovel_fun_fixtures.py` (recorded 2026-09-30 and trimmed; no network). |

## 小説家になろう Syosetu — `sources/adapters/syosetu.py`

| | |
|---|---|
| URL patterns | `ncode.syosetu.com/<ncode>/` (series, or a one-shot 短編), `ncode.syosetu.com/<ncode>/<n>/` (episode); http or https, with or without the trailing slash or `?p=2`; the ncode (`n0063lr`) is lowercased. Only `ncode.syosetu.com`: `novel18.syosetu.com` (18+) and `syosetu.org` (a different site, not Narou) are not matched. The mobile and other alternate hosts were not checked, so they are not accepted. |
| Content type / language | novel, ja |
| Status | **Read against the live site 2026-09-30** (plain GETs, no cookies): a 133-episode work with part headings, a one-shot with ruby and an afterword, an episode with a preface and afterword, the search page, the 404 page. The adapter was run offline against those saved pages (series, both list pages, chapter text) and gave the expected results; **no import has been run through the app**, so status stays untested. |
| Access tier | `STATIC_HTTP` only. Server-rendered HTML; no script needed. |
| Extraction | **Series:** `h1.p-novel__title`, `.p-novel__author a`, `#novel_ex` (synopsis, `<br>` to newlines). The page has no serial/complete flag, so `status` is `unknown` (a one-shot is `completed`); search results do show 連載中/完結済. **Chapters:** `.p-eplist` on `/<ncode>/?p=<n>`, 100 per page; the page count comes from `a.c-pager__item--last` (`?p=N`), which is only a link when there is a next page. Walked with a cap of 200 pages and de-duplicated by episode number; site order is kept. `div.p-eplist__chapter-title` headings become `ChapterInfo.group`; a page does not repeat a part heading that began on the previous page, so the current one is carried across. Chapter id is the number in the URL. **One-shot:** no `.p-eplist`, the text is on the top page (`/<ncode>/1/` is a 404), so it is one chapter whose URL is the top page. **Text:** `.p-novel__body .p-novel__text`, one line per `<p>` (a `<p><br></p>` is a blank line, leading full-width spaces kept). Ruby keeps the base text only. The preface (`p-novel__text--preface`) and afterword (`--afterword`) are the author's notes and are left out. **Search:** `yomou.syosetu.com/search.php?word=<q>&p=<n>`, `div.searchkekka_box` (20 per page); author, status and episode count go in `SearchResult.extra`. |
| Failures | 404/410 -> "doesn't exist" (removed, private or wrong address). A challenge page -> the usual hand-off. A missing selector -> `LayoutChanged` naming the piece. An empty chapter, an empty list or no episodes -> an error, never an empty import. A redirect off `ncode.syosetu.com`, or a page asking for an age check or a password -> a plain error (`ContentHidden` for 18+ / `AUTHENTICATION_REQUIRED`). Those last three branches are **UNVERIFIED LIVE** (no 18+ ncode was fetched, and no removed or login-only page was seen served with a 200). |
| Not supported | 18+ works: the site keeps them on `novel18.syosetu.com` behind an over-18 switch. This adapter never sends an age cookie. The adapter has no login. |
| Pacing | `host_min_interval` 2 s per host (robots.txt asks `Crawl-delay: 1`). |
| Terms | robots.txt (both hosts, 2026-09-30): `User-agent: *`, `Crawl-delay: 1`, no Disallow lines. **The terms of service (https://syosetu.com/site/rule/, revision 令和8年6月9日) forbid automated access:** 第14条 23 bans "なろうデベロッパーで提供しているAPIを利用する以外の方法で、本サービスに自動化された手段を用いてアクセスしたり、データを収集したりすること". The official API (`api.syosetu.com/novelapi`) returns metadata, not episode text. Recorded as `EXPLICITLY_RESTRICTED` in `capabilities()` and in `sources/site_terms.py`; enforcement is off app-wide (Step 90), so the adapter works, but the finding is shown. |
| Owner decision | 2026-09-30: ship the adapter although the terms forbid automated access; the owner accepts that. Use stays personal-scale and polite: one request at a time through the shared client (random 1-3 s gaps, occasional longer pauses, at least 2 s per host against the site's `Crawl-delay: 1`, retries only on 429/5xx honouring `Retry-After`), an honest User-Agent, no cookies, no login, no challenge solving, no 18+ age cookie, and a 200-page cap on chapter lists. |
| Reference | Read directly from the live site; the selectors in lightnovel-crawler were only a starting hint, all re-checked. No code ported. |
| Tests | `tests/test_sources_syosetu.py` (small invented fixtures shaped like the live pages; no network). |

## 飘天文学 Piaotian — `sources/adapters/piaotian.py`

| | |
|---|---|
| URL patterns | Book page `/bookinfo/<a>/<id>.html`, chapter list `/html/<a>/<id>/` (also without the slash or as `index.html`), chapter `/html/<a>/<id>/<chapterid>.html`, on `www.piaotia.com`, `piaotia.com`, `ptwxz.com` and `www.ptwxz.com`, http or https. `<a>` must be `<id> // 1000` (146 is in `0`, 16054 in `16`), otherwise the URL is not recognised. Every request goes to `https://www.piaotia.com` (ptwxz.com only redirects there and is never contacted). Lookalikes are rejected (`evilptwxz.com`, `ptwxz.com.evil.com`, `ptwwxz.com`, userinfo tricks, other subdomains). |
| Content type / language | novel, zh |
| Status | **Read against the live site 2026-09-30** (about 20 GETs and 3 search POSTs, honest User-Agent, no cookies, 2 s or more apart): the home page, `/help/`, a completed book with volume headings (755 chapters), a serial with 924 chapters on one list page, three chapters, the 404 and the 200 "no such book" page, and search (single hit, many hits, no hit). The adapter was run offline against those saved pages and gave the expected results; **no chapter has been imported through the app**, so status stays a caveat. |
| Access tier | `STATIC_HTTP` only. Server-rendered GBK HTML (declared in a `<meta>`, none in the header; `decode_html` handles it). No challenge seen in the 2026-09-30 pass, but the owner reports a Cloudflare challenge on plain requests (2026-10): the adapter stops by design and a saved page from your own browser is the way in. |
| Extraction | **Series:** `#centerm h1` (title), the cells "类 别：", "作 者：", "文章状态：" (连载中 / 已完成), the text after `span.hottext` "内容简介：" (synopsis, `<br>` to lines), the cover only when it is `/files/article/image/<a>/<id>/…` on the same host. **Chapters:** one page, the whole book; `div.centent` holds `div.list` headings (become `ChapterInfo.group`, including "作品相关" for author notes at the top) and `ul > li > a` with relative `<cid>.html` hrefs; `&nbsp;` padding items are skipped; links outside `.centent` (the footer book recommendations) and links to other books or hosts are ignored; de-duplicated by chapter id; site order kept (ids are not monotonic); more than 10000 chapters is an error, not a truncation. **Text:** there is no container. The text is bare text and `<br>`s after `div.toplink` and the ad tables, up to `div.bottomlink`; a paragraph is `&nbsp;&nbsp;&nbsp;&nbsp;text<br /><br />`, so each becomes one line (indent and the double break dropped). The only boilerplate stripped is a final line that is exactly `www.` (seen once, at the end of one chapter). No leading title is present in the body. **Search:** `POST /modules/article/search.php` (`searchtype=articlename`, `searchkey` as percent-encoded GBK; robots.txt does not disallow it): one exact match answers a 302 to the book page, otherwise a 30-row `table.grid` (title link, latest chapter, author, size, date, 连载/完结). Page 2 and later use the site's own pager GET (`…?searchtype=articlename&searchkey=…&page=<n>`), **unverified live**. |
| Failures | 404/410 -> "doesn't exist". A missing **book page is served with HTTP 200** and the title 出现错误！ ("错误原因：对不起，该文章不存在！"), reported with the site's message. A challenge -> the usual hand-off. A missing selector -> `LayoutChanged` naming the piece. An empty list, an empty chapter, or one under 5 characters -> an error, never an empty import. A redirect off `www.piaotia.com`, to `/login.php`, a password field, or 18+ wording -> a plain error. **Login and age pages were never met** (UNVERIFIED LIVE); those branches only recognise the obvious signs. |
| Pacing | `host_min_interval` 2.5 s for `www.piaotia.com` (robots.txt has no `Crawl-delay`; the owner asked for human-paced use), on top of the shared client's random gaps and breaks. Search sends no cookies, so the site's own search throttle (a `jieqiVisitTime` cookie it sets) is not in play; the adapter's pacing is the only limit, so keep searches occasional. |
| Terms | robots.txt (fetched 2026-09-30; `ptwxz.com/robots.txt` 301s to `www.piaotia.com/robots.txt`): `User-agent: *`, `Disallow: /files/article/attachment/`, `/login.php`, `/modules/article/packshow.php`, `/admin/`; no `Crawl-delay`. The adapter requests none of those paths. **No terms of service page was found.** Checked: the home page and footers (book, list, chapter), and `/help/`, which is a members' help centre (registering, points, avatar) with nothing on automated access or copying. Footer notices: works are user-uploaded or reposted from other sites, copyright stays with the original authors, the site will delete a work when the rights holder asks, and it calls itself non-profit. Recorded as `automation_permission` UNKNOWN (not PERMITTED). |
| Owner decision | 2026-09-30: approved building it, with respectful, human-paced use. Open policy question for the owner: the site republishes copyrighted novels without visible authorisation, and its footer promises removal on request; the adapter reads what the site publicly serves, at personal scale, and takes no action to avoid its rules. |
| Not done | No `site_terms.py` entry (that table records restrictions found in terms; there are none to record). No login, cookies, age handling or proxies. Search by author is not offered. |
| Reference | Read directly from the live site. No code ported. |
| Tests | `tests/test_sources_piaotian.py` (small invented GBK fixtures shaped like the live pages; no network). |

## MangaK — `sources/adapters/mangak.py`

| | |
|---|---|
| URL patterns | `mangak.io/<slug>` (series), `mangak.io/<slug>/<chapter-slug>` (chapter). Site pages (`/search`, `/genres`, `/terms-of-service` and the like) are not treated as series. |
| Content type / language | manga, manhwa, manhua; en (English translations) |
| Status | **Read against the live site 2026-10-03** (plain GETs, no cookies, no login): search, a 152-chapter series, its full chapter list, an 18-page chapter and one image download, all through the adapter. **No import has been run through the app.** |
| Access tier | `STATIC_HTTP` only. A Next.js site whose pages embed their data as JSON in `<script id="__NEXT_DATA__">`; nothing needs JavaScript. |
| Extraction | **Search:** `/search?q=<query>&page=<n>`, `pageProps.ssrItems`. **Series:** `/<slug>`, `pageProps.initialManga` (its `id` and `cv`). **Chapters:** `api.mangak.io/titles/<id>/chapters?cv=<cv>`, JSON `data.chapters`, sorted by the site's own `number` (the series page only embeds the newest 50). **Pages:** `/<slug>/<chapter-slug>`, `pageProps.initialChapter.pages[].url`. **Images:** webp on CDN hosts (`rx.qvzr*.org`); they answer 403 without a `mangak.io` Referer, which the adapter sends. |
| Failures | 404 -> "doesn't exist". A missing `__NEXT_DATA__`, missing keys, an empty chapter list or a chapter with no images -> `LayoutChanged`. A series or chapter slug that isn't a plain slug (letters, digits, `-`, `_`) is refused before it reaches a URL or a file name. |
| Adult works | The site flags some works `isAdult` but serves them without a switch, so there is no toggle; search results carry the flag in `extra["adult"]`. |
| Terms | robots.txt: `mangak.io/robots.txt` and `api.mangak.io/robots.txt` both answer 404 (2026-10-03), so no rules and no crawl delay. **The terms of service (https://mangak.io/terms-of-service, titled MangaBuddy, "Last updated: March 2025") forbid automated access:** section 4 "You agree not to: ... Use automated tools, bots, or scrapers to access the service". Section 2 says the site hosts nothing itself and that content is "sourced from third-party providers and publicly available sources"; series summaries name official English publishers. Recorded as `EXPLICITLY_RESTRICTED` in `capabilities()`; enforcement is off app-wide, so the adapter works, but the finding is shown. |
| Owner decision | 2026-10-03: ship the adapter although the terms forbid automated access; the owner accepts that, for personal storage of chapters. Use stays personal-scale through the shared client's pacing, with no login and no cookies. |
| Reference | `Yui007/mangak-downloader` (MIT), endpoints and field names only, all re-checked live. No code ported. (`Dyslexic-churchschool477/mangak-downloader` is a malware fork of it; never use it.) |
| Tests | `tests/test_sources_mangak.py` (small invented fixtures shaped like the live pages; no network). |

## Generic "paste a URL" import (no adapter)

| | |
|---|---|
| URL patterns | Anything that isn't a registered source or a known video URL. |
| Content type | Detected per page: comic (≥3 page-sized images), novel (a large main-text block), or video (`og:type` video or a `<video>` tag). |
| Language | Guessed from the script used. |
| Auth | None by default. A verification page is handed to you, and after completing it in your own browser you can paste the page source to continue. For a page that needs your account, **🔐 Sign in to this site** opens a signed-in browser profile -- see "Signed-in browser sessions (Step 23k)" below. |
| Extraction | **Comic:** every `<img>`/`<source>` in reading order, including `data-src`/`data-original`/`srcset`. The filter then removes images that are: too short to be pages; off the dominant width/aspect cluster; repeated on other chapters of the same site; or from a third-party domain. **Novel:** trafilatura if installed, otherwise the largest text block after nav, header, footer and comment areas are stripped. That fallback scores a container by its *direct* children first, then re-scans counting text nested anywhere beneath it (with link text discounted, so a chapter index can't out-score the chapter) and takes the deeper result only when it finds substantially more prose -- without that second pass, the common "one wrapper element per paragraph" markup yields a single paragraph as the whole chapter. |
| Known limits | The first chapter from a site can't use the cross-chapter repeat check yet, so a page-sized logo that shares the pages' width can get through. Pages drawn on a canvas or assembled by scripts need the browser tier. trafilatura's CJK extraction hasn't been benchmarked. Confirmed live (2026-09-27, `m.zgzl.net`): a site that splits one chapter across several numbered sub-pages (`.../sb93g.html` page 1 of 5, `.../sb93g_2.html` page 2, ...) offers a "next page" link on every sub-page but no "next chapter" link except on the last one -- and `validate_novel`'s link-shape check correctly refuses to treat "next page" as "next chapter" (their URLs don't resemble sibling chapters), so a book like this can be read sub-page by sub-page but not auto-walked chapter to chapter from a page that isn't the chapter's last one. |
| **Pages your browser already translated** | A browser translator (Google Translate, Edge's) **replaces** a page's text rather than annotating it — confirmed against a live translation, after which the original Japanese was gone from the page. Reading such a page would hand this app the translation as though it were the source, so it would "translate" English it believes is Chinese, or save an English chapter as the original — silently, because the import succeeds and the text looks fine. This is now detected and reported, with what to do about it (turn the browser's page translation off for that site and fetch again). It matters most when you paste page source from your own browser. It is a warning, never a failure: the page loaded fine, so it never stops or escalates the access ladder. Detected from the fingerprints a real translation leaves — `translated-ltr`/`translated-rtl` on `<html>`, the `goog-gt-tt`/`goog-gt-vt` elements, and text rewritten into nested `vertical-align:inherit` `<font>` wrappers. A merely *embedded, idle* translate widget is deliberately not matched, or every page offering translation would be flagged. |
| Tests | `tests/test_sources_workflows.py` |

## lightnovel-crawler (external program, optional) — `services/lncrawl_service.py`

| | |
|---|---|
| What | Source > Novel text > **Import with lightnovel-crawler**: runs the user-installed [lightnovel-crawler](https://github.com/lncrawl/lightnovel-crawler) (`lncrawl`) on a pasted novel URL and attaches the text of the EPUB it makes. Shown only when the program is found (PATH, or Settings > Advanced > Downloads > "lightnovel-crawler program"). User decision 2026-09-30: an optional external tool. |
| Licence | **GPL-3.0-or-later** (checked against the 4.14.0 release's package metadata, 2026-09-30; the older xbanxia note above called the project MIT, which no longer holds). Because of that, Baihe uses it **only as a separate program** (arm's-length use): no lncrawl code is copied, vendored or imported, Diagnostics detects the program rather than the Python module, and it is never offered for one-click install -- the user installs it (`pipx install lightnovel-crawler`, or `pip install lightnovel-crawler` in its own environment). |
| Interface used | The 4.x CLI: `lncrawl crawl --noin --format epub {--all \| --first N \| --last N} -- <url>`, with `LNCRAWL_DATA_PATH` pointed at a temp folder inside the drama folder. Confirmed in the 4.14.0 source: `crawl` defines `--noin`, `--all`, `--first N`, `--last N` and `-f/--format`; `config.py` sets its app folder from `LNCRAWL_DATA_PATH`, and its config file, sqlite database and artifacts (the EPUB) all resolve under that folder. `LNCRAWL_CONFIG` and `DATABASE_URL` could redirect the config or database, so they are removed from its environment. 4.x has no `--source` flag, no output-folder flag and no from–to chapter range, so the UI offers all / first N / latest N. Older 3.x releases (`lncrawl -s <url> ...`) are not supported. |
| Fetching | lncrawl fetches the site itself (its own sources, DNS, redirects and pacing). Baihe checks the pasted URL once with `url_guard` before starting it and cannot pin what lncrawl fetches after that, so the routes are PC-only (`docs/remote-access-decision.md`). |
| Terms | Per title, the user's call -- the panel says so. Site-specific terms are not looked up for lncrawl's sources. |
| Known limits | Never run for real from this repo's tests (the subprocess is mocked). The EPUB lncrawl writes has a cover/intro page and volume headings; their text is attached along with the chapters. lncrawl's own saved config (proxies, logins) is not used, because it runs with a fresh temp data folder; sites that need a login are not supported. |
| Tests | `tests/test_lncrawl_service.py`; `frontend/src/pages/workspace/stages/lncrawlForm.test.ts`; `frontend/e2e/lncrawl-import.spec.ts`, `lncrawl-import.mobile.spec.ts`. |

### "Will this site work?" — `sources/preflight.py`

Paste a URL and press **✅ Will this site work?** in the Sources tab to
find out whether a site can be imported *before* committing to it. One
fetch, through the normal access ladder, answering:

| | |
|---|---|
| Permitted? | `ladder.check_terms`, including that site's own `sources/site_terms` entry |
| Reachable? | which tier got there — plain HTTP or a browser render |
| Readable? | the real deterministic extractor, judged by the same `validate_novel` checks an import uses, so the preflight can never promise something the importer would then refuse |
| Prose or chrome? | the duplicate-paragraph, short-fragment and link-text rates behind that score |
| Navigable? | whether next / previous / contents links were found, i.e. whether a whole series can be followed or chapters must be added one URL at a time |
| Warnings | a page your own browser has already machine-translated |

**Every ordinary "no" is an answer, not an exception.** Prohibited terms,
an unreachable page, a challenge, and text that fails the checks all come
back as a readable finding rather than a traceback.

### Adaptive extraction (Step 23g) — `sources/adaptive.py`, `sources/ai_extract.py`, `sources/profiles.py`

Order tried for a pasted novel/comic URL, stopping at the first that
passes the independent checks:

1. **Saved site profile** (`<library>/source_profiles/<domain>.json`) — 0 AI calls.
2. **Deterministic extraction** (the trafilatura/heuristic extractor and the comic filter above) — 0 AI calls.
3. **AI-assisted fallback**, only when step 2 is empty or ambiguous and an engine is picked under **🤖 AI-assisted fallback** (off by default) — one `call_llm_json` call, cached by a hash of what the model reads. The model answers only with block/link/image ids; text and images are always copied from the page, never written by the model.

| | |
|---|---|
| Confidence | Per field (title, author, chapter title/number, content, next/previous link, page images, page order) as HIGH/MEDIUM/LOW/FAILED, checked independently of the model (length, repeat rate, text on the page, URL shape, image size). The model's own score can only lower it. |
| Profiles | Generated from an AI result or a profile that stopped fitting, re-run on the page and validated before saving. Auto-saved only at HIGH, otherwise held for approval. Versions are append-only; any can be made active again under **🩺 Sources, health & diagnostics**. |
| Review Extraction | Shown when confidence is low, or always with **Extraction diagnostics mode**. Novel: choose the text container, drop nav/comments/ads, pick title and next/previous links. Comic: mark each image content/cover/ad, renumber pages. Corrections can be saved as the site's profile; the source content is never edited. |
| Media on unknown pages | For a video page no adapter or yt-dlp shortcut recognizes: **🔎 Identify media on this page** lists video/audio/manifest/subtitle resources (identify only). The one you pick goes through the normal video import. DRM markers are named, never worked around. |
| Diagnostics | Each attempt goes in the access-attempt log (source `generic`): access tier, extraction tier, AI call count, profile outcome, protection detected, and a plain-language failure reason. |
| AI recovery of a changed layout | When a novel adapter's page loads but its layout no longer matches, the chapter is marked "needs AI help"; after you confirm an engine (one AI call) the page opens in Review extraction, and nothing is written until you import it. Web app and API only (`cli.py` has no sources commands). |
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
| Tests | `tests/test_sources_workflows.py` |

## If a site blocks your IP or region

Nothing in the app needs a proxy: source requests go direct by default. If a
site refuses your IP or region (or throttles it), set the optional source
proxy (`http_proxy_url` in the Sources settings, HTTP or HTTPS only, empty by
default, applied to every source adapter's requests). The API shows only
whether one is set, never the value, and it can only be changed from the PC.
Check the site's terms before routing around a block.

## Manual checks still to do

Open items only. Each source's own table above records what was already checked live. The 2026-09-26 pass drove the adapters' methods directly against the live sites, so the Sources-tab import into a drama is not separately confirmed for them.

- [ ] **manhuagui, adult-flagged work:** turn on **🔞 Include adult-flagged
  works** for manhuagui. Open a work that was refused with it off, and
  confirm its chapter list loads and a chapter imports. Turn the toggle
  off again and confirm the same work is refused with the message that
  names the toggle.
- [ ] **Browser tier:** install Playwright (`pip install playwright` then
  `playwright install chromium`), and confirm **Test Browser** on a
  JavaScript-only page records a real result.
- [ ] **Bilibili Manga, terms text:** read all four agreement URLs listed
  above in a real, logged-out browser (their text loads via client-side
  JS at runtime, so a plain fetch never sees it) and record whether any
  clause bears on automated access or AI use.
- [ ] **Bilibili Manga, authenticated/paid-chapter behavior:** with a real
  Bilibili account, confirm whether an ordinary authenticated browser
  session is sufficient for a free chapter, and whether a purchased
  chapter's image tokens behave differently or carry extra protection.
  Required before this adapter is trusted for real use.
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
- [ ] **miaoqumh, search endpoint:** not re-checked this pass -- still
  open.
- [ ] **baozimh/godamh, mirror fallback:** with one of the six mirrors
  genuinely unreachable (or simulated via a hosts-file/firewall block),
  confirm a real import still succeeds via the next configured mirror.
- [ ] **Kuaikan, locked-chapter message:** not reached this pass -- the
  chapter used for the normal-work check above was a free one.
- [ ] **Kuaikan, search:** not re-checked this pass.
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
- [ ] **missevan, signed-in/paid session (pending a real account):** with
  a real purchased or VIP MissEvan account signed in through this
  source's browser profile, confirm whether `get_audio_url()`'s
  authenticated fallback (reading `getsound` through that profile) really
  does return a usable URL for a paid episode -- and whether the "JSON
  inside a rendered `<pre>`" extraction shape it assumes is actually what
  the browser hands back for this specific endpoint. Not verified this
  pass; no such account was available.
- [ ] **fanjiao, first real render (pending):** on a normal machine,
  open a real album (e.g. `album_id=111601`, found through baihehub's
  `fjId`) through the Sources tab and confirm the page's own
  `album_info`/`actor_cvs`/`album/audio` responses are captured and
  parsed, that `audios_list` items really carry `audio_id`/`name`, and
  which key marks a paid episode. The build container couldn't render
  the page, so the fixtures follow the page's own script, not a capture.
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
