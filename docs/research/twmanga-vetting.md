# twmanga.com vetting (2026-10-08)

Browser-tier check of `https://www.twmanga.com/`, done 2026-10-08 from the cloud sandbox with Playwright's preinstalled Chromium and a normal Chrome user agent. 12 requests in total, at least 5 s apart; no crawling; one image request (a 2 KB `Range`), no bulk download; no challenge was solved. Docs only, nothing was built.

## What it is

`www.twmanga.com` is a regional mirror of `baozimh.com` (footer on every page: "© 2026 BAOZIMH.COM 包子漫畫", `s@baozimh.com`), the same brand and the same Nuxt SSR app as `www.twbzmg.com`. It is **not** the godamh/baozimh.org family the `baozimh` adapter targets. It uses `/comic/<slug>` and `/comic/chapter/<slug>/<section>_<chapter>.html`, not `/manga/<slug>` plus the `api-get-v3.mgsearcher.com` JSON API.

## Evidence

| Check | Result |
|---|---|
| `GET /robots.txt` | HTTP 200, `content-type: text/html; charset=utf-8`, `server: nginx/1.24.0 (Ubuntu)`, `cache-control: max-age=60`. The body is the site's 404 page (`<title>404 - 🌈️包子漫畫</title>`, "This page could not be found"). There is no robots.txt, so no `Disallow` lines to quote. |
| Series page `/comic/xianzuntaxinmochanshen-zhangyuxuhepapd` | 200, 67,947 bytes, title `仙尊她心魔纏身 - 包子漫畫`. Server-rendered: the raw HTML and the rendered DOM both have 14 `<amp-img>` and 1 `<img>` (covers and recommendations), 0 `data-src`, 0 `blob:`. Two chapter links, both `/user/page_direct?comic_id=<slug>&section_slot=0&chapter_slot=N` (N = 0, 1); no direct `/comic/chapter/` hrefs on this page. |
| Chapter page `/comic/chapter/xianzuntaxinmochanshen-zhangyuxuhepapd/0_0.html` | 200, 125,477 bytes. Raw HTML has 53 `<amp-img>` and 47 `<img>`; every page image is an `<img src="https://s1.bzcdn.net/scomic/<slug>/0/1-ipsl/<n>.jpg">` (plus an `<amp-img>` twin with `src` and `data-src` set to the same URL). The URLs are in the static HTML, not injected by script; 0 `blob:`; no token or signature in the URL. |
| CDN | `s1.bzcdn.net` (pages), `static-tw.baozimh.com` (covers, static assets). The page script also probes failover hosts (`s1.baozimh.com`, `s1-*.bzcdn.net`, with `?r=<n>`) for an image of a different comic slug; I blocked those requests. |
| Image fetch | `GET https://s1.bzcdn.net/.../1.jpg` with no Referer and no cookie, `Range: bytes=0-2047`: 206, `content-type: image/jpeg`, `access-control-allow-origin: *`, `server: nginx/1.18.0 (Ubuntu)`. No Referer or cookie needed. |
| Challenge / captcha | None on any twmanga.com or twbzmg.com response: no `cf-*` or challenge headers; no captcha, Turnstile or "just a moment" text in any HTML fetched. |
| Click path | `GET /user/page_direct?comic_id=xianzuntaxinmochanshen-zhangyuxuhepapd&section_slot=0&chapter_slot=1` returns 302 to `https://www.twbzmg.com/comic/chapter/xianzuntaxinmochanshen-zhangyuxuhepapd/0_1.html`, which returns 200 (92,900 bytes). So the chapter click path leaves `twmanga.com` for `twbzmg.com`. |
| `/search?q=仙尊` | 200, server-rendered, 90 distinct `/comic/<slug>` links. Works without script. |
| `/privacy` | 200. A personal-information policy (cookies, server logs, Google Analytics). The only automation-adjacent text forbids third parties collecting *users' personal information*; nothing about automated access or scraping. |
| `/dmca` | 200. A DMCA takedown procedure and a named agent. Nothing about automated access. |
| `https://www.twbzmg.com/` | 200, title `🌈️包子漫畫`, 180,425 bytes, no challenge. |
| `https://www.baozimh.com/` | **403**, `application/json`, body `{"challenge_url":"/__gatekeeper_challenge/start?return=%2F","error":"challenge_required"}`. I did not follow or solve it. This matches the earlier "blocked twice" note. |

## Could not verify

- The owner's report that opening baozimh and clicking a series lands on twmanga.com. From here `baozimh.com` answers with a challenge, so that hop was not observed. The observed click path is twmanga.com to twbzmg.com.
- The site's "服務使用協議" (terms of service), which `/privacy` refers to. I did not find or fetch it, so ToS terms are unreviewed.
- Chapter lists for long series (the one series checked has 2 chapters), pagination, rate limits, and whether the owner's machine sees the same behaviour as this sandbox.
- Only one series, one chapter and one image were checked.

## Verdict: BUILD A NEW ADAPTER (technically feasible; owner's call)

Reasons:

1. The Nuxt SSR HTML carries chapter images as plain `https://s1.bzcdn.net/...jpg` URLs, with no script, token, `blob:`, Referer or cookie needed. `STATIC_HTTP` would be enough; the browser tier is not needed.
2. Search works as a plain GET and the series and chapter pages have no challenge on twmanga.com or twbzmg.com.
3. The markup and URL scheme differ from the `baozimh` adapter, so this would be a new adapter parameterised over `twmanga.com` and `twbzmg.com`, not an added mirror (not "ADD AS MIRROR"). The chapter link needs the `page_direct` redirect resolved, and it crosses hosts.

Risks the owner should weigh: the brand's apex `baozimh.com` is behind a gatekeeper challenge, so these hosts may gain one without notice; the terms of service are unreviewed; the site has a DMCA agent and is an aggregator of third-party works. Until the owner decides, the fallback is the browser extension.
