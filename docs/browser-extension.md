# Translate the page you're reading (Step 34 / 34b / 96)

A browser extension that sends what you're looking at into Baihe --
either a comic page, drawing the translation over it in place, or (Step
96) a block of page text, translated in a panel on the page.

This does **not** replace the source adapters in `sources/`. Those do
bulk import, chapter tracking, new-chapter checks and an offline library.
This is "translate what I'm looking at right now." Both exist, and they
answer different questions.

## Why it's worth having, beyond convenience

It reaches content the adapters structurally can't, and it does so
**without this app ever touching a protection mechanism** — the same line
`manhuaku.py` and the former Mangaz adapter (removed since) drew:

- **mangaz.com** serves tile-scrambled pages (a 1190x1684 page arrives as
  a ~4760x421 strip). Only its own reader reassembles them. The Mangaz
  adapter (since removed from the app) drove that reader in a headless browser, which worked but was slow
  (~3 minutes for a 43-page book), flaky in sandboxes, and has to be
  paced carefully to avoid being throttled.
- **manhuaku.net** hands its protected chapters out as `blob:` URLs that
  only exist inside the rendering tab.
- **Bilibili Manga** needs a signed-in session for anything real.

In every one of those, **your own browser has already done the work** —
decrypted, descrambled, authenticated — because you're reading the page
legitimately. The extension reads what's already on your screen. No
headless driving, no pacing games, no session to forge, nothing to
circumvent. It's strictly less invasive than what the adapters do.

It also covers sites with no adapter at all, which is most of them.

The same reasoning applies just as well to text-heavy pages -- a web
novel chapter, or any site you're already logged into and reading
normally. Your browser has already rendered the text; Step 96 adds a mode
that reads it the same way the image mode reads pixels, rather than only
covering comics.

## Setting it up

1. In Baihe: **Settings → Browser extension** → tick **Run the local
   endpoint**. It's off by default because it opens a port.
2. Pick the engine extension pages should be translated with. It uses
   that engine's key from *API keys & endpoints*. With no key set, pages
   still come back with their original text read by OCR, clearly marked
   as untranslated.
3. Copy the **token** shown there.
4. In Chrome or Edge: `chrome://extensions` → **Developer mode** → **Load
   unpacked** → choose this project's `extension/` folder.
5. Open the extension's **options** and paste the token (and the port, if
   you changed it). It'll say *Connected* and how many dramas it can see.

## Using it

Click the extension on a page you're reading:

- **Translate this page** — the largest page-sized image on screen.
- **Translate everything visible** — a spread, or a whole visible strip.
- **Send pages to** — which drama they land in. Remembered per site, so
  reading a long series isn't a per-page decision.
- **Also save the page into that drama** — untick to translate for
  reading only, without importing anything.
- **Draw translations over the page** / **Show / hide translations** — the
  overlay toggle. Click any overlaid bubble to see the original text
  underneath it.

Paging back to something already translated is instant: results are
cached by image content hash, so nothing is ever translated twice.

### Translating a page's text (Step 96)

A separate section of the popup, for prose rather than comic pages:

- Pick a direction (**zh/ja/ko → English**, or **English → zh/ja/ko**) —
  the same directions the **Standalone translate** tab in Baihe itself
  supports, since this reuses that exact pipeline.
- **Translate this page's text** — if you've selected text on the page,
  that selection is what gets sent. With nothing selected, the extension
  captures the page's own largest contiguous block of paragraph text
  (skipping `<nav>`/`<header>`/`<footer>`/`<aside>` and anything too
  short to be real prose), a simple heuristic rather than a full
  readability implementation.
- The translation appears in a small panel drawn onto the page itself —
  not the popup, which is too narrow for a page-length passage and closes
  the moment it loses focus, exactly when you want to keep reading. The
  panel has a **Show original** toggle and a close button, and it's
  reused on the next translate rather than stacking a second one.
- Every translation is also saved into Baihe's own **Standalone
  translate** tab's history (Step 26b), the same table that tab writes
  to -- there is no separate history for the extension.

Unlike the image modes, this always uses the engine configured in
Settings → Browser extension; the extension itself has no engine picker
or key of its own to keep in sync.

## Two things it checks for before capturing

- **A page that's still descrambling/reassembling isn't captured mid-way.**
  Before reading an element's pixels, the extension takes a cheap sample,
  waits, and takes another; it only proceeds once two samples in a row
  match (bounded to 1.5s, then it proceeds anyway rather than hang
  forever). This matters for a page like mangaz's own reader, whose JS
  reassembles a tile-scrambled page onto a canvas after it loads —
  capturing the instant you click, rather than once that's settled, could
  grab a half-drawn frame. An already-static image settles in well under
  a fifth of a second, so this adds no noticeable delay to the ordinary
  case.
- **A verification/CAPTCHA interstitial is recognized and refused, not
  mistranslated.** If the page you click on looks like a Cloudflare/bot
  challenge (by title, visible text, or a known CAPTCHA widget actually
  on screen — not just present somewhere in the DOM), the extension says
  so plainly instead of silently sending whatever image-sized element
  happens to be on that page off for OCR. It never tries to solve or pass
  the challenge, the same posture `sources/http.py`'s `ChallengeDetected`
  already takes server-side — recognize and hand off, never fight it.

## What it can't do

- **A cross-origin image the site draws without CORS can't be read.**
  The browser refuses to let any script read those pixels, and the
  extension respects that rather than working around it. You'll get a
  plain message saying so.
- **Chrome won't let an extension run on some pages at all** — the
  extensions gallery, the built-in PDF viewer, `chrome://` pages.
- **Canvas-only viewers** give no `<img>` to anchor an overlay to, so
  positioning falls back to the canvas element's own box.
- **Two-page spreads and right-to-left order** affect which box belongs
  to which page; the app derives reading order per page, but a spread
  sent as one image is treated as one page.
- **Webtoon strips** are long single images. The app slices them
  server-side, so a very tall strip is best sent with the reader scrolled
  to the part you care about.
- **Firefox is not supported yet.** MV3 covers Chrome and Edge; Firefox
  differs enough to be its own work.
- **The free bubble detector is not usable on colour artwork.** Measured
  on a real mangaz page (see below): it found no bubbles at all on a
  colour 4-koma page full of dialogue, where the ML backend found 63
  regions. If pages come back with nothing overlaid, that's usually
  this, not the extension. It's a Settings choice (**Bubble detection →
  ML**), not something this step changes.
- **For Japanese, use `manga_ocr`, not Tesseract.** Manga is vertical
  text; on the same real page Tesseract returned unreadable fragments
  where `manga_ocr` returned correct dialogue. Auto already picks
  `manga_ocr` for Japanese — just don't override it.
- **(Step 96) The "largest paragraph block" heuristic can pick the wrong
  block on an unusual layout** — a page with no real `<p>` tags (some
  sites lay out prose in bare `<div>`s), or one where a comment section
  happens to out-weigh the actual chapter. Select the passage yourself
  when that happens; an explicit selection always wins.
- **(Step 96) `/text` only translates one side of a pair with English**,
  the same limit the Standalone translate tab already has — a
  non-English-to-non-English page (say, a Japanese site's Chinese fan
  translation) isn't a supported direction.

## How it's put together

```
extension/                     the browser side
  manifest.json                MV3; loopback host permission only
  background.js                the service worker: holds the token, makes the calls
  content.js                   injected on a click: collects images/text, draws overlays/panel
  popup.html / popup.js        pick a drama, send, toggle
  options.html / options.js    paste the token
page_server.py                 the endpoint, on a thread beside the API server
services/extension_service.py  the opt-in switch, the token, the status (routes: api/routers/extension_routes.py)
```

The endpoint's four routes:

| Route | What it does |
|---|---|
| `GET /health` | Confirms the app is up, and lists the dramas to send to. |
| `POST /page` | One image. |
| `POST /pages` | Several — a spread, or everything visible. |
| `POST /text` | A block of raw page text (Step 96). |

Everything funnels into the existing, tested pipeline
(`scanlate.detect_and_ocr_page` → `scanlate.translate_page_bubbles`) and
the same `sources.pipeline.add_page_images` import path Scanlate's own
manual upload uses. There is deliberately no second translation path, and
which images count as real pages is decided by
`sources/generic_import.py`'s existing filter rather than a second
implementation in JavaScript that would drift from it.

`/text` follows the same "one pipeline" rule from the other side: it
funnels into `translate_engines.standalone_translate`, the exact function
`services/translate_service.py`'s standalone translate already calls, rather
than a second translation path for text captured by the extension. It
does no detection or OCR -- the extension already sends real text, not
pixels -- so it's a much thinner route than `/page`/`/pages`: validate
the input, translate it, save it to history, return it.

Stdlib `http.server`, so no new dependency and nothing to register in
`diagnostics.py`'s `OPTIONAL_DEPENDENCIES`.

## Security

The endpoint accepts images and runs OCR, and **any page in any browser
tab can make requests to localhost**. So:

- It binds `127.0.0.1` only, never `0.0.0.0`, and re-checks the peer
  address on every request — so a later change to the bind address can't
  silently open it up.
- Every route, `/health` included, requires the token in the
  `X-Baihe-Token` header, compared with `secrets.compare_digest`. Never
  in a URL, a log line or a stored error, per this repo's existing secret
  rule (`translate_engines.redact_secrets`).
- **Requiring a custom header is itself the cross-site defence.** A web
  page can't set one on a cross-origin request without a successful CORS
  preflight, so the endpoint answers no preflight and sends no
  `Access-Control-Allow-Origin` at all. That's why the extension fetches
  from its *service worker*, which isn't subject to page CORS. If you
  ever hit a CORS error here, move the request into the worker — **never**
  add a permissive header to the server.
- The token lives only in the service worker. The content script, which
  shares a page's world, never sees it. That's still true of the text
  path: `content.js` never imports or reads the token, and gathering the
  page's text and sending it happen in two different worlds, the same
  split as the image path.
- Body size, per-image size and image count are capped, only real image
  content types are accepted, and every connection has a timeout.
- `/text` carries the same posture, sized to what it actually accepts:
  captured text is capped at `MAX_TEXT_CHARS` (20,000 characters, a
  generous chapter), the source/target language pair is validated against
  a fixed set rather than passed through freely, and it shares `/page`'s
  body-size cap, peer check, token check, and lack of a CORS preflight.
- API keys are never written to disk by any of this. The UI hands the
  current engine and key to the server thread in memory on each render,
  the same way `settings_tab` already pushes into
  `background_jobs.set_gpu_limit_enabled`.

`tests/test_extension_manifest.py` pins the browser-side half of that
statically, because none of it can be checked by running the app.

## What was actually verified

The endpoint and the settings bridge have ordinary mocked tests
(`tests/test_page_server.py`),
including every refusal above.

Beyond that, the extension was run for real, and that run is repeatable:

```
python extension/verify_end_to_end.py
```

It loads the extension unpacked into a real Chromium, points it at a real
running endpoint, and drives a page holding both a normal `<img>` and a
`blob:`-backed one. It needs Playwright with a **full** Chromium build
(the headless *shell* can't load extensions) and skips cleanly without
one. It touches no real site and needs no API key. Run it after changing
anything in `extension/` or `page_server.py` — the Python suite cannot
execute any of that JavaScript. All 20 of its checks pass as of this
merge (12 image, 6 text from Step 96, 1 shared, 1 content-stability +
challenge-page detection). What it establishes:

- A **`blob:`-backed page image translated end to end** and landed in the
  library — the case the adapters structurally cannot reach. It still
  does after adding the pre-capture content-stability wait, so that wait
  doesn't hang or corrupt the capture on the case that matters most.
- A box at `x=40, w=220` in image pixels drew at `x=20, w=110` over an
  image displayed at half scale, and stayed exact after a window resize.
- Click-to-see-original and the overlay toggle both behaved.
- The same image appearing twice on a page was sent once, and a second
  translate of an already-translated page came from the cache without
  calling the engine again.
- **(Step 96) With nothing selected, the page's own largest contiguous
  block of paragraph text was captured, and a `<nav>`'s links on the same
  page were not**, even when made deliberately longer than the real
  content -- the tag-based skip, not just the length floor, is what kept
  it out.
- **(Step 96) That captured text ran through the real, unmocked
  `translate_engines.standalone_translate`** (via `TestOfflineEngine`,
  which needs no network or key) rather than a faked pipeline, and the
  result was drawn into a panel on the page and saved into Standalone
  translate's own history table.
- **(Step 96) An explicit text selection overrode the heuristic block** —
  selecting one paragraph sent only that paragraph, not the whole
  captured article.
- A page with its title set to `"Just a moment..."` (a real Cloudflare
  interstitial title) was recognized and refused with `CHALLENGE_DETECTED`
  instead of being sent for OCR/translation.

That run also found two real problems, since fixed: the same image
appearing more than once on a page was encoded and uploaded once per
element, and a rejected inline `data:`/`blob:` URL was echoed back whole
in the response. Reviewing the endpoint afterwards found two more: a
request refused on its headers (an unauthenticated POST) left its body
unread in a keep-alive socket, which would desync the next request on
that connection, and the connection timeout was set on the server rather
than per connection, so a peer that opened a socket and stopped talking
held a worker thread. Both are fixed and covered by tests that were
confirmed to fail without the fix.

### Against the real mangaz.com

(Historical: the Mangaz adapter was removed from the app afterwards; the result below is kept as the evidence for the extension.)

The extension was then pointed at a real chapter on **mangaz.com** — the
sharpest possible test, because its pages are tile-scrambled and only its
own reader reassembles them. A real Chromium, the real viewer, one page
load:

- The page the extension captured was **1190x1684** — a real,
  **descrambled** page, read straight out of the `blob:` the site's own
  reader produced. A scrambled strip would have been ~4760x421. It landed
  in the library as a 4.3MB PNG.
- It took **one page view**, at reading speed. For comparison, the
  adapter's headless path takes ~3 minutes for a 43-page book, is flaky,
  has to be paced against a 120s crawl delay, and its best real run still
  only recovered 38 of 43 pages. Nothing here is unscrambled, driven or
  paced — the browser had already done it.

Then the rest of the chain was run over that captured page (no further
traffic to the site):

- **The free OpenCV detector found nothing.** The page is a colour
  4-koma full of Japanese speech bubbles, and
  `scanlate.detect_bubbles_cv` returned zero regions, rejecting its
  handful of candidates as "too small". Worth knowing, because it's the
  default when the ML weights aren't cached: on artwork like this, the
  free heuristic is not usable.
- **The ML detector found 63 regions** (25 `bubble`, 25 `text_bubble`,
  13 `text_free`) at 0.92–0.97 confidence, in ~10s on CPU.
- **OCR with Tesseract produced garbage** — `だ見さ け当? 全 の子 が`.
  Manga is vertical text, and Tesseract is poor at it.
- **OCR with `manga_ocr`, which is what `auto_ocr_backend("ja")` picks
  anyway, produced correct dialogue**: `おお！それはすごい裏技ケロッ`,
  `勝手に変なトコに入らないでくださいーっ`, `王子様ステキー♥`,
  `ひっ引き返すケロー！！！`. Checked against the page itself.

So the whole chain works on a real page from a real scrambled site:
browser → descrambled `blob:` → capture → ML detection → real Japanese
text. Only translation is unproven, for want of an API key in that
environment.

**Two things to know before trusting the output**, both in `scanlate`'s
existing pipeline rather than anything this step added:

1. **Use the ML detection backend for real artwork.** The free heuristic
   found nothing at all here.
2. **The ML detector double-counts.** It returns a `bubble` and a
   `text_bubble` for the same balloon and nothing dedupes them: 46 of
   those 63 regions overlap another by more than 70%. The same text is
   OCR'd twice (visibly, in the results above), which means roughly
   double the translation cost and two overlay boxes stacked on each
   bubble. This affects the Scanlate tab equally and is worth its own
   fix.

**Also not verified:** manhuaku.net and Bilibili Manga, and the real
toolbar-click flow. Clicking the extension's icon grants `activeTab`,
which is what lets the content script be injected; Playwright can't click
browser chrome, so that one grant was simulated by loading a throwaway
copy of the extension with a host permission for that single site. The
shipped manifest is untouched and stays loopback-only. That copy isn't
committed, deliberately — a script that rewrites the manifest is too easy
to mistake for the real configuration.

The browser-side test suite is static only. There is no automated test
that drives a real browser, on purpose: this project's tests are mocked
throughout and CI has no browser.
