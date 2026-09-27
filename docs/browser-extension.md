# Translate the page you're reading (Step 33 / 33b)

A browser extension that sends the comic page you're looking at into
Baihe, and draws the translation over it in place.

This does **not** replace the source adapters in `sources/`. Those do
bulk import, chapter tracking, new-chapter checks and an offline library.
This is "translate what I'm looking at right now." Both exist, and they
answer different questions.

## Why it's worth having, beyond convenience

It reaches content the adapters structurally can't, and it does so
**without this app ever touching a protection mechanism** — the same line
`manhuaku.py` and `mangaz.py` already draw:

- **mangaz.com** serves tile-scrambled pages (a 1190x1684 page arrives as
  a ~4760x421 strip). Only its own reader reassembles them. The adapter
  drives that reader in a headless browser, which works but is slow
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

## How it's put together

```
extension/                     the browser side
  manifest.json                MV3; loopback host permission only
  background.js                the service worker: holds the token, makes the calls
  content.js                   injected on a click: collects images, draws overlays
  popup.html / popup.js        pick a drama, send, toggle
  options.html / options.js    paste the token
page_server.py                 the endpoint, on a thread beside Streamlit
tabs/settings_tab.py           the opt-in switch, the token, the settings bridge
```

The endpoint's three routes:

| Route | What it does |
|---|---|
| `GET /health` | Confirms the app is up, and lists the dramas to send to. |
| `POST /page` | One image. |
| `POST /pages` | Several — a spread, or everything visible. |

Everything funnels into the existing, tested pipeline
(`scanlate.detect_and_ocr_page` → `scanlate.translate_page_bubbles`) and
the same `sources.pipeline.add_page_images` import path Scanlate's own
manual upload uses. There is deliberately no second translation path, and
which images count as real pages is decided by
`sources/generic_import.py`'s existing filter rather than a second
implementation in JavaScript that would drift from it.

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
  shares a page's world, never sees it.
- Body size, per-image size and image count are capped, only real image
  content types are accepted, and every connection has a timeout.
- API keys are never written to disk by any of this. The UI hands the
  current engine and key to the server thread in memory on each render,
  the same way `settings_tab` already pushes into
  `background_jobs.set_gpu_limit_enabled`.

`tests/test_extension_manifest.py` pins the browser-side half of that
statically, because none of it can be checked by running the app.

## What was actually verified

The endpoint and the settings bridge have ordinary mocked tests
(`tests/test_page_server.py`, `tests/test_page_server_settings.py`),
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
execute any of that JavaScript. All 12 of its checks pass as of this
step. What it establishes:

- A **`blob:`-backed page image translated end to end** and landed in the
  library — the case the adapters structurally cannot reach.
- A box at `x=40, w=220` in image pixels drew at `x=20, w=110` over an
  image displayed at half scale, and stayed exact after a window resize.
- Click-to-see-original and the overlay toggle both behaved.
- The same image appearing twice on a page was sent once, and a second
  translate of an already-translated page came from the cache without
  calling the engine again.

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

**Not verified:** a real run against any of the named sites
(mangaz.com, manhuaku.net, Bilibili Manga). The mechanism is proven on a
local page that reproduces the hard part (`blob:`), but nobody has yet
clicked this extension on a real chapter of a real site, and OCR quality
on real artwork is a separate question from whether the plumbing works.
The browser-side test suite is static only — there is no automated test
that drives a real browser, deliberately, since this project's tests are
mocked throughout and CI has no browser.
